import asyncio
import logging
from types import SimpleNamespace

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


def test_status_has_semantic_environment_and_health():
    service = PhosApplicationService(Runtime())
    status = service.status()
    assert status["environment"]["status"] == "available"
    assert status["health"]["subsystems"]["ccs811"]["state"] == "warming_up"
    assert status["health"]["subsystems"]["imu"]["state"] == "unavailable"


def test_interaction_response_speech_awaits_the_runtime_speech_path(caplog):
    caplog.set_level(logging.INFO)
    runtime = Runtime()
    spoken = []

    async def speak(text):
        spoken.append(text)

    runtime.speak = speak
    runtime.application_status = lambda: {"speech": {"tts": {"active_provider": "local"}}}
    result = asyncio.run(PhosApplicationService(runtime).speak_interaction_response(
        " Ciao ", "it", intent="get_temperature"))

    assert spoken == ["Ciao"]
    assert result == {"status": "completed", "language": "it"}
    assert "response speech started intent=get_temperature language=it provider=local" in caplog.text


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


def test_manual_expression_is_behavior_owned_bounded_intent_not_a_renderer_mutation():
    now = [0.0]
    runtime = Runtime()
    runtime._behavior_engine = BehaviorEngine(runtime.core.events, clock=lambda: now[0])
    service = PhosApplicationService(runtime)
    applied = service.set_expression("curious")
    assert applied["manual_override"]["expression"] == "curious"
    assert service.visual_state()["expression"] == "curious"
    # The override outlives ordinary environmental/source arbitration, then
    # expires without a Web Admin or renderer callback.
    runtime._behavior_engine.configure_base_visual_source("manual")
    assert service.visual_state()["expression"] == "curious"
    now[0] = 31.0
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
