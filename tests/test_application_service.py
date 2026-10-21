import asyncio
import base64
import logging
import threading
import time
from types import SimpleNamespace

import pytest

from robot.core.runtime import RobotCore
from robot.core.behavior_engine import BehaviorEngine
from robot.services.application import COMMANDS, OVERLAY_COMMANDS, PhosApplicationService
from robot.core.environmental import EnvironmentalState
from robot.core.state import InvalidStateTransition, RobotState, RobotStateMachine, transition_graph, writable_states
from robot.motion import MotionState
from robot.vision.provider import ObservedExpression
from robot.vision.pipeline import OBSERVED_EXPRESSION_CHANGED
from robot.core import Event
from robot.web.openapi import _resolve, load_spec
from robot.config import RuntimeConfig, load_document
from robot.services.application import ApplicationError


_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/"
    "f8G9aAAAAABJRU5ErkJggg=="
)


class Runtime:
    def __init__(self):
        self.core = RobotCore()
        self._behavior_engine = BehaviorEngine(self.core.events)

    def sensor_status(self):
        return {
            "environmental": {"status": "available", "available": True, "measurements": {"temperature_c": 21.5}},
            "ccs811": {"status": "warming_up", "available": False},
            "imu": {"status": "unavailable", "available": False},
        }

    def apply_base_visual_source(self, config):
        self._behavior_engine.configure_base_visual_source(config.base_visual_source)


class RecoveryRuntime(Runtime):
    def __init__(self, *, available=True):
        super().__init__()
        self.available = available
        self.shown = []
        self.cleared = 0

    def admin_overlay_available(self):
        return self.available

    def show_recovery_code(self, code, expires_at):
        self.shown.append((code, expires_at))

    def clear_admin_overlay(self):
        self.cleared += 1


def test_status_has_semantic_environment_and_health():
    service = PhosApplicationService(Runtime())
    status = service.status()
    assert status["environment"]["status"] == "available"
    assert status["health"]["subsystems"]["ccs811"]["state"] == "warming_up"
    assert status["health"]["subsystems"]["imu"]["state"] == "unavailable"


def test_recovery_code_is_shown_only_locally_and_never_returned():
    runtime = RecoveryRuntime()
    service = PhosApplicationService(runtime)
    service._admin_recovery.token = lambda: "483921"

    result = service.request_admin_password_recovery()

    assert result == {"status": "requested"}
    assert runtime.shown and runtime.shown[0][0] == "483921"
    assert "483921" not in repr(result)


def test_recovery_does_not_activate_when_the_display_is_unavailable():
    service = PhosApplicationService(RecoveryRuntime(available=False))

    with pytest.raises(ApplicationError, match="Recovery display is unavailable") as error:
        service.request_admin_password_recovery()

    assert error.value.code == "service_unavailable"
    assert service._admin_recovery.code is None


def test_admin_network_and_overlay_use_the_runtime_boundary():
    class AdminRuntime(Runtime):
        def __init__(self): super().__init__(); self.shown = 0
        def admin_network_info(self):
            return {"hostname": "phos", "ip_address": "192.168.1.42", "interface": "wlan0",
                    "port": 8080, "admin_url": "http://192.168.1.42:8080/", "status": "connected"}
        def show_admin_access_overlay(self): self.shown += 1

    runtime = AdminRuntime()
    service = PhosApplicationService(runtime)

    assert service.get_admin_network_info()["admin_url"] == "http://192.168.1.42:8080/"
    assert service.show_admin_access_overlay() == {"status": "shown"}
    assert runtime.shown == 1


def test_background_uploads_are_a_directory_backed_collection(tmp_path):
    class Repository:
        def __init__(self, config): self.config = config
        def load(self): return self.config

    class BackgroundRuntime:
        def application_status(self): return {"backgrounds": {}}

    config = RuntimeConfig.from_dict(load_document(), base_dir=tmp_path)
    lifecycle = SimpleNamespace(config_repository=Repository(config), path=tmp_path / "phos.yaml")
    service = object.__new__(PhosApplicationService)
    service._runtime, service._lifecycle = BackgroundRuntime(), lifecycle

    for name in ("a.png", "b.png", "c.png"):
        assert service.upload_background({"name": name, "content": _PNG})["name"] == name
    assert [asset["name"] for asset in service.background_assets()] == ["a.png", "b.png", "c.png"]

    # Deletion leaves other directory entries intact, and a later upload does
    # not reuse or overwrite an unrelated name.
    assert service.delete_background("b.png")["name"] == "b.png"
    assert [asset["name"] for asset in service.background_assets()] == ["a.png", "c.png"]
    service.upload_background({"name": "later.png", "content": _PNG})
    assert [asset["name"] for asset in service.background_assets()] == ["a.png", "c.png", "later.png"]
    with pytest.raises(ApplicationError, match="already exists"):
        service.upload_background({"name": "a.png", "content": _PNG})


def test_interaction_response_speech_awaits_the_runtime_speech_path(caplog):
    caplog.set_level(logging.INFO)
    runtime = Runtime()
    spoken = []

    async def speak(text):
        spoken.append(text)

    runtime.speak = speak
    runtime.application_status = lambda: {"speech": {"tts": {"active_provider": "local"}}}
    result = asyncio.run(PhosApplicationService(runtime).speak_interaction_response(
        " Temperatura 24 °C ", "it", intent="get_temperature"))

    assert spoken == ["Temperatura ventiquattro gradi Celsius"]
    assert result == {"status": "completed", "language": "it"}
    assert "response speech started intent=get_temperature language=it provider=local" in caplog.text


def test_cartesia_tts_configuration_failures_have_safe_semantic_reasons():
    reason = PhosApplicationService._speech_configuration_reason
    assert reason("tts", "cartesia", RuntimeError("Cartesia credential is not configured.")) == "cartesia_credential_missing"
    assert reason("tts", "cartesia", RuntimeError("Cartesia voice ID is required.")) == "cartesia_voice_id_missing"
    assert reason("tts", "cartesia", RuntimeError("Cartesia client could not be initialized.")) == "cartesia_client_initialization_failed"
    assert reason("tts", "cartesia", RuntimeError("untrusted detail")) == "cartesia_invalid_configuration"


def test_live_audio_output_switch_uses_pipewire_candidate_and_keeps_old_backend_on_failure(tmp_path, monkeypatch):
    class Repository:
        def __init__(self, config): self.config = config
        def load(self): return self.config
        def save(self, config): self.config = config

    class AudioRuntime:
        def __init__(self): self._secrets_service = None; self.output = object(); self.config = None
        def replace_audio_output(self, output, *, config=None): self.output, self.config = output, config

    config = RuntimeConfig.from_dict(load_document(), base_dir=tmp_path)
    lifecycle = SimpleNamespace(config_repository=Repository(config), path=tmp_path / "phos.yaml")
    runtime = AudioRuntime()
    service = object.__new__(PhosApplicationService)
    service._runtime, service._lifecycle = runtime, lifecycle
    monkeypatch.setattr("robot.voice.tts.shutil.which", lambda command: "/usr/bin/pw-play" if command == "pw-play" else None)

    result = service.configure_audio_output({**config.to_dict()["tts"]["audio_output"], "player": "pipewire"})
    assert result == {"configured_provider": "pipewire", "active_provider": "pipewire", "enabled": True, "available": True}
    assert runtime.output.name == "pipewire"
    assert runtime.config.tts_audio_output_player == "pipewire"

    previous = runtime.output
    monkeypatch.setattr("robot.voice.tts.shutil.which", lambda command: None)
    with pytest.raises(ApplicationError, match="Could not activate"):
        service.configure_audio_output({**lifecycle.config_repository.load().to_dict()["tts"]["audio_output"], "player": "pipewire"})
    assert runtime.output is previous


def test_status_reads_the_runtime_observed_expression_instance():
    runtime = Runtime()
    observation = ObservedExpression(True, "happy", 1.0, "local", "FER+", 42.0)
    runtime._vision_pipeline = SimpleNamespace(observed_expression=observation)
    service = PhosApplicationService(runtime)

    assert service.observed_expression() == observation.document()
    assert service.status()["observed_expression"] == observation.document()
    assert service.status()["expression_reaction"]["active"] is False


def test_fresh_observed_expression_event_is_forwarded_to_remote_subscribers():
    runtime = Runtime()
    observation = ObservedExpression(True, "happy", 1.0, "local", "FER+", 42.0)
    runtime._vision_pipeline = SimpleNamespace(observed_expression=observation)
    service = PhosApplicationService(runtime)
    received = []
    service.subscribe(received.append)

    asyncio.run(runtime.core.events.publish(Event(OBSERVED_EXPRESSION_CHANGED, observation.document())))

    assert received[-1]["type"] == "observed_expression_changed"
    assert received[-1]["payload"] == observation.document()


def test_visual_source_rejects_hardware_command_and_events_are_deduplicated():
    service = PhosApplicationService(Runtime())
    events = []
    service.subscribe(events.append)
    service.set_visual_source("environment")
    service.set_visual_source("environment")
    assert [event["type"] for event in events] == ["visual_state_changed"]
    try:
        service.set_visual_source("gpio18")
    except Exception as error:
        assert error.code == "invalid_visual_source"
    else:
        raise AssertionError("hardware command was accepted")


def test_capabilities_are_derived_from_canonical_semantic_enums():
    service = PhosApplicationService(Runtime())
    capabilities = service.capabilities()
    observable = capabilities["observable_states"]
    assert observable["robot_state"] == [item.value for item in RobotState]
    assert observable["motion_state"] == [item.value for item in MotionState]
    assert observable["environmental_state"] == [item.value for item in EnvironmentalState]
    assert observable["environmental_overlays"]["temperature"] == ["none", "cold", "warm"]
    assert observable["environmental_overlays"]["air_quality"] == ["none", "warning", "bad"]
    for name, definition in COMMANDS.items():
        expected = {
            "method": definition.method, "endpoint": definition.endpoint,
            "field": definition.field, "allowed_values": definition.allowed_values,
        }
        if name == "set_robot_state":
            expected["transitions"] = {
                "idle": ["listening", "sleeping"], "listening": ["idle", "thinking", "sleeping"],
                "thinking": ["idle", "speaking"], "speaking": ["idle", "listening"], "sleeping": ["idle"],
            }
        assert capabilities["commands"][name] == expected
    assert "error" not in capabilities["commands"]["set_robot_state"]["allowed_values"]


def test_command_validation_capabilities_and_openapi_enums_stay_synchronized():
    capabilities = PhosApplicationService(Runtime()).capabilities()
    spec = load_spec()
    command_schemas = spec["components"]["schemas"]["Capabilities"]["properties"]["commands"]["properties"]
    for name, definition in COMMANDS.items():
        capability_schema = _resolve(spec, command_schemas[name]["$ref"])
        capability_values = capability_schema["properties"]["allowed_values"]["items"]["enum"]
        request_schema = spec["paths"][definition.endpoint][definition.method.lower()]["requestBody"]["content"]["application/json"]["schema"]
        request_schema = _resolve(spec, request_schema["$ref"]) if "$ref" in request_schema else request_schema
        request_values = request_schema["properties"][definition.field]
        request_values = _resolve(spec, request_values["$ref"]) if "$ref" in request_values else request_values
        assert definition.allowed_values == capabilities["commands"][name]["allowed_values"]
        assert definition.allowed_values == capability_values == request_values["enum"]


def test_runtime_only_or_unmapped_values_are_not_accepted_as_writable_commands():
    service = PhosApplicationService(Runtime())
    for command, value, code in ((service.set_state, "error", "unsupported_state_command"),
                                 (service.set_expression, "worried", "unsupported_expression_command")):
        try:
            command(value)
        except Exception as error:
            assert error.code == code
        else:
            raise AssertionError(f"{value} was accepted despite not being writable")


def test_invalid_robot_transition_becomes_a_structured_application_error(monkeypatch):
    import robot.services.application as application
    runtime = Runtime()
    runtime._loop = object()
    service = PhosApplicationService(runtime)

    class Rejected:
        def result(self, timeout):
            from robot.core.state import InvalidStateTransition
            raise InvalidStateTransition("Cannot transition from idle to thinking.")

    def reject(coroutine, loop):
        coroutine.close()
        return Rejected()

    monkeypatch.setattr(application.asyncio, "run_coroutine_threadsafe", reject)
    try:
        service.set_state("thinking")
    except Exception as error:
        assert error.code == "invalid_state_transition"
        assert error.status == 409
        assert error.details == {"current": "idle", "target": "thinking"}
    else:
        raise AssertionError("invalid state transition was accepted")


def test_state_command_acknowledges_commit_without_waiting_for_slow_downstream_event():
    runtime = Runtime()
    loop = asyncio.new_event_loop()
    runtime._loop = loop
    event_started = threading.Event()

    async def slow_observer(_event):
        event_started.set()
        await asyncio.sleep(.25)

    runtime.core.events.subscribe("core.state_changed", slow_observer)
    thread = threading.Thread(target=loop.run_forever)
    thread.start()
    try:
        started = time.monotonic()
        result = PhosApplicationService(runtime).set_state("listening")
        assert result["state"] == "listening"
        assert runtime.core.state is RobotState.LISTENING
        assert time.monotonic() - started < .15
        assert event_started.wait(.1)
        # A repeated request for an already committed target is safe and does
        # not enqueue another transition/event side effect.
        assert PhosApplicationService(runtime).set_state("listening")["state"] == "listening"
        time.sleep(.30)
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=1)
        loop.close()


def test_timed_out_queued_state_command_is_cancelled_before_it_can_apply(monkeypatch):
    import robot.services.application as application
    runtime = Runtime()
    runtime._loop = object()
    service = PhosApplicationService(runtime)

    class Queued:
        cancelled = False
        def result(self, timeout): raise TimeoutError()
        def cancel(self):
            self.cancelled = True
            return True

    queued = Queued()

    def submit(coroutine, _loop):
        coroutine.close()
        return queued

    monkeypatch.setattr(application.asyncio, "run_coroutine_threadsafe", submit)
    with pytest.raises(ApplicationError) as error:
        service.set_state("listening")
    assert error.value.code == "runtime_unavailable"
    assert queued.cancelled is True
    assert runtime.core.state is RobotState.IDLE


def test_robot_state_model_and_capabilities_share_canonical_transition_graph():
    service = PhosApplicationService(Runtime())
    state = service.robot_state()
    command = service.capabilities()["commands"]["set_robot_state"]
    assert state == {"state": "idle", "current": "idle", "running": False,
                     "writable": ["idle", "listening", "thinking", "speaking", "sleeping"],
                     "allowed_next": ["listening", "sleeping"]}
    assert command["transitions"][state["current"]] == state["allowed_next"]


def test_every_exported_transition_is_accepted_and_other_writable_targets_rejected():
    graph = transition_graph()
    for source, targets in graph.items():
        for target in targets:
            assert RobotStateMachine(RobotState(source)).transition_to(RobotState(target)).current.value == target
        for target in writable_states():
            if target.value == source or target.value in targets:
                continue
            try:
                RobotStateMachine(RobotState(source)).transition_to(target)
            except InvalidStateTransition:
                pass
            else:
                raise AssertionError(f"{source} -> {target.value} was missing from the exported graph")


def test_manual_expression_is_behavior_owned_persistent_intent_not_a_renderer_mutation():
    now = [0.0]
    runtime = Runtime()
    runtime._behavior_engine = BehaviorEngine(runtime.core.events, clock=lambda: now[0])
    service = PhosApplicationService(runtime)
    applied = service.set_expression("curious")
    assert applied["manual_override"]["expression"] == "curious"
    assert service.visual_state()["expression"] == "curious"
    # The override outlives ordinary environmental/source arbitration and the
    # previous 30-second deadline; only another explicit command replaces it.
    runtime._behavior_engine.configure_base_visual_source("manual")
    assert service.visual_state()["expression"] == "curious"
    now[0] = 31.0
    assert service.visual_state()["expression"] == "curious"
    service.set_expression("neutral")
    assert service.visual_state()["expression"] == "neutral"


def test_phase_one_manual_expressions_are_writable_without_changing_robot_state():
    now = [0.0]
    runtime = Runtime()
    runtime._behavior_engine = BehaviorEngine(runtime.core.events, clock=lambda: now[0])
    service = PhosApplicationService(runtime)
    for expression in ("sad", "angry", "focused", "tired"):
        applied = service.set_expression(expression)
        assert applied["expression"] == expression
        assert applied["visual"]["expression"] == expression
        assert runtime._behavior_engine.robot_state is RobotState.IDLE


def test_phase_two_manual_expressions_are_writable_without_changing_robot_state():
    runtime = Runtime()
    runtime._behavior_engine = BehaviorEngine(runtime.core.events)
    service = PhosApplicationService(runtime)
    for expression in ("confused", "scared", "skeptical", "tired"):
        assert service.set_expression(expression)["expression"] == expression
        assert runtime._behavior_engine.robot_state is RobotState.IDLE


def test_manual_sad_and_tired_survive_time_and_robot_state_reconstruction():
    now = [0.0]
    runtime = Runtime()
    runtime._behavior_engine = BehaviorEngine(runtime.core.events, clock=lambda: now[0])
    service = PhosApplicationService(runtime)

    service.set_expression("sad")
    now[0] = 3600.0
    assert service.visual_state()["expression"] == "sad"
    asyncio.run(runtime._behavior_engine._on_robot_state(Event("core.state_changed", {"current": "thinking"})))
    assert service.visual_state()["expression"] == "sad"

    service.set_expression("tired")
    asyncio.run(runtime._behavior_engine._on_robot_state(Event("core.state_changed", {"current": "speaking"})))
    assert service.visual_state()["expression"] == "tired"
    service.set_expression("neutral")
    assert service.visual_state()["expression"] == "neutral"


def test_overlay_override_arbitrates_independent_channels_and_restores_current_environment():
    runtime = Runtime()
    service = PhosApplicationService(runtime)
    from robot.ui import AmbientOverlayState
    runtime._behavior_engine._overlay_arbiter.set_environmental(AmbientOverlayState("warm", "warning"))
    assert service.overlay()["resolved"] == {"temperature": "warm", "air_quality": "warning"}
    applied = service.set_overlay({"temperature": "cold", "duration_ms": 1000})
    assert applied["override"]["active"] is True
    assert applied["resolved"] == {"temperature": "cold", "air_quality": "warning"}
    runtime._behavior_engine._overlay_arbiter.set_environmental(AmbientOverlayState("none", "bad"))
    assert service.clear_overlay()["resolved"] == {"temperature": "none", "air_quality": "bad"}


def test_overlay_expiry_resolves_against_current_environment_not_cached_intent():
    from robot.core.overlay import OverlayArbiter
    from robot.ui import AmbientOverlayState
    now = [0.0]
    arbiter = OverlayArbiter(clock=lambda: now[0])
    arbiter.set_environmental(AmbientOverlayState("warm", "none"))
    arbiter.set_override(temperature="cold", duration_ms=1000)
    arbiter.set_environmental(AmbientOverlayState("none", "warning"))
    now[0] = 1.0
    assert arbiter.resolved() == AmbientOverlayState("none", "warning")


def test_overlay_validation_and_capabilities_use_canonical_overlay_enums():
    service = PhosApplicationService(Runtime())
    capabilities = service.capabilities()
    spec = load_spec()
    capability_schema = _resolve(spec, spec["components"]["schemas"]["Capabilities"]["properties"]["commands"]["properties"]["set_overlay"]["$ref"])
    request = spec["components"]["schemas"]["OverlayOverrideRequest"]["properties"]
    for field in ("temperature", "air_quality"):
        values = OVERLAY_COMMANDS["set_overlay"]["fields"][field]["allowed_values"]
        assert capabilities["commands"]["set_overlay"]["fields"][field]["allowed_values"] == values
        assert capability_schema["properties"]["fields"]["properties"][field]["properties"]["allowed_values"]["items"]["enum"] == values
        assert request[field]["enum"] == values
    for value, code in (({"temperature": "hot"}, "invalid_overlay_temperature"),
                        ({"air_quality": "toxic"}, "invalid_overlay_air_quality"),
                        ({"temperature": "warm", "duration_ms": 0}, "invalid_overlay_duration"),
                        ({}, "invalid_overlay")):
        try:
            service.set_overlay(value)
        except Exception as error:
            assert error.code == code
        else:
            raise AssertionError("invalid overlay was accepted")
