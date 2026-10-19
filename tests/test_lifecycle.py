"""Lifecycle policy and process channel tests without hardware or OS commands."""
import asyncio
from copy import deepcopy
import json
import logging
import multiprocessing
import os
from pathlib import Path
import subprocess
import sys
from threading import Event, Thread

import pytest

from robot.config import RuntimeConfig, load_document
from robot.lifecycle import LifecycleService, apply_log_level
from robot.lifecycle_channel import LifecycleClient, serve_lifecycle
from robot.services.application import ApplicationError, PhosApplicationService
from robot.ui.skins import SkinRepository, appearance_snapshot


@pytest.fixture
def runtime(tmp_path):
    document = load_document()
    document["logging"]["file"] = None
    path = tmp_path / "phos.json"
    path.write_text(json.dumps(document))
    applied = []
    now = [0.0]
    service = LifecycleService(path, RuntimeConfig.from_file(path), restart_supported=True,
                               log_level_setter=applied.append, clock=lambda: now[0])
    return path, service, applied, now


def update(path, edit):
    document = load_document(path)
    edit(document)
    path.write_text(json.dumps(document))


def test_reload_applies_only_safe_changes_and_reports_pending(runtime):
    path, service, applied, _ = runtime
    update(path, lambda d: (d["logging"].update(level="DEBUG"), d["display"].update(fps=20)))
    before = path.read_bytes()
    result = service.execute("reload")
    assert result["ok"] and result["applied"] == ["logging.level"]
    assert applied == ["DEBUG"]
    assert result["active"]["logging"]["level"] == "DEBUG"
    assert result["active"]["display"]["fps"] == 30
    assert result["restart_required"] == ["display.fps"]
    assert result["loaded_at"] and result["config_path"] == str(path)
    assert path.read_bytes() == before
    assert service.execute("reload")["applied"] == []


def test_appearance_change_is_reloadable_and_not_restart_required(runtime):
    path, service, _, _ = runtime
    applied = []
    service.register_appearance_applier(lambda config: applied.append(config.iris_color))
    update(path, lambda d: d["display"].update(iris_color="violet"))

    result = service.execute("reload")

    assert result["ok"] and result["applied"] == ["display.iris_color"]
    assert applied == ["violet"]
    assert result["active"]["display"]["iris_color"] == "violet"
    assert result["reloadable"] == []
    assert result["restart_required"] == []


def test_eye_safe_layer_reload_uses_the_registered_runtime_boundary(runtime):
    path, service, _, _ = runtime
    applied = []
    service.register_eye_safe_layer_applier(lambda config: applied.append(
        (config.eye_safe_layer_enabled, config.eye_safe_layer_mode)))
    update(path, lambda d: d["display"].update(eye_safe_layer={"enabled": False, "mode": "expressive"}))

    result = service.execute("reload")

    assert result["ok"]
    assert result["applied"] == ["display.eye_safe_layer.enabled", "display.eye_safe_layer.mode"]
    assert applied == [(False, "expressive")]


def test_backgrounds_reload_through_the_registered_runtime_boundary(runtime):
    path, service, _, _ = runtime
    directory = path.parent / "backgrounds"
    directory.mkdir()
    # A valid 1x1 PNG; configuration validation deliberately happens before
    # the display/runtime applier is invoked.
    (directory / "idle.png").write_bytes(
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde"
        b"\x00\x00\x00\x0cIDAT\x08\xd7c\xf8\xcf\xc0\x00\x00\x03\x01\x01\x00\x18\xdd\x8d\xb4\x00\x00\x00\x00IEND\xaeB`\x82")
    applied = []
    service.register_backgrounds_applier(lambda config: applied.append(config.background_idle.name))
    update(path, lambda d: d["display"].update(backgrounds={"enabled": True, "idle": "backgrounds/idle.png",
           "listening": None, "thinking": None, "speaking": None, "sleeping": None, "error": None}))

    result = service.execute("reload")

    assert result["ok"] and result["applied"] == ["display.backgrounds.enabled", "display.backgrounds.idle"]
    assert applied == ["idle.png"]
    assert result["active"]["display"]["backgrounds"]["idle"] == "backgrounds/idle.png"


def test_invalid_background_candidate_is_rejected_before_runtime_application(runtime):
    path, service, _, _ = runtime
    applied = []
    service.register_backgrounds_applier(lambda config: applied.append(config))
    update(path, lambda d: d["display"].update(backgrounds={"enabled": True, "idle": "backgrounds/missing.png",
           "listening": None, "thinking": None, "speaking": None, "sleeping": None, "error": None}))

    result = service.execute("reload")

    assert not result["ok"] and not applied
    assert service.active["display"]["backgrounds"]["enabled"] is False


def test_state_effects_reload_through_the_registered_runtime_boundary(runtime):
    path, service, _, _ = runtime
    applied = []
    service.register_state_effects_applier(lambda config: applied.append(
        (config.state_effects_enabled, config.state_effects_preset,
         config.state_effects_palette, config.state_effects_reduce_motion)))
    update(path, lambda d: d["display"].update(state_effects={
        "enabled": True, "preset": "expressive", "palette": "violet", "reduce_motion": True}))

    result = service.execute("reload")

    assert result["ok"]
    assert result["applied"] == ["display.state_effects.enabled", "display.state_effects.preset",
                                 "display.state_effects.palette", "display.state_effects.reduce_motion"]
    assert applied == [(True, "expressive", "violet", True)]


def test_state_effects_application_transaction_persists_or_rolls_back(runtime):
    path, lifecycle, _, _ = runtime
    applied = []
    lifecycle.register_state_effects_applier(lambda config: applied.append(config.state_effects_preset))
    application = object.__new__(PhosApplicationService)
    application._lifecycle = lifecycle
    application._runtime = object()

    result = application.configure_state_effects({"enabled": True, "preset": "subtle"})

    assert result["saved"]["preset"] == "subtle"
    assert applied == ["subtle"]
    assert RuntimeConfig.from_file(path).state_effects_preset == "subtle"

    with pytest.raises(ApplicationError) as error:
        application.configure_state_effects({"preset": "unknown"})
    assert error.value.code == "invalid_configuration"
    assert RuntimeConfig.from_file(path).state_effects_preset == "subtle"

    with pytest.raises(ApplicationError) as error:
        application.configure_state_effects({"enabled": "yes"})
    assert error.value.code == "invalid_configuration"
    assert RuntimeConfig.from_file(path).state_effects_preset == "subtle"

    lifecycle.register_state_effects_applier(lambda _config: (_ for _ in ()).throw(RuntimeError("unavailable")))
    with pytest.raises(ApplicationError) as error:
        application.configure_state_effects({"preset": "expressive"})
    assert error.value.code == "runtime_unavailable"
    assert RuntimeConfig.from_file(path).state_effects_preset == "subtle"


def test_lifecycle_channel_dispatches_state_effects_configuration(runtime):
    _, service, _, _ = runtime
    received = []

    class Application:
        def configure_state_effects(self, payload):
            received.append(payload)
            return {"saved": payload}

    service.register_application_service(Application())
    service.mark_application_ready()
    parent, child = multiprocessing.Pipe()
    stop = Event()
    thread = Thread(target=serve_lifecycle, args=(parent, service, stop))
    thread.start()
    try:
        client = LifecycleClient(child)
        payload = {"enabled": True, "preset": "balanced", "reduce_motion": True}
        assert client.execute("application.configure_state_effects", payload) == {"ok": True, "result": {"saved": payload}}
        assert received == [payload]
    finally:
        stop.set()
        child.close()
        thread.join(timeout=2)
        assert not thread.is_alive()


def test_lifecycle_channel_dispatches_semantic_state_command(runtime):
    _, service, _, _ = runtime
    received = []

    class Application:
        def set_state(self, state):
            received.append(state)
            return {"state": state, "accepted": True}

    service.register_application_service(Application())
    service.mark_application_ready()
    parent, child = multiprocessing.Pipe()
    stop = Event()
    thread = Thread(target=serve_lifecycle, args=(parent, service, stop))
    thread.start()
    try:
        client = LifecycleClient(child)
        assert client.execute("application.set_state", {"state": "listening"}) == {
            "ok": True, "result": {"state": "listening", "accepted": True}}
        assert received == ["listening"]
    finally:
        stop.set()
        child.close()
        thread.join(timeout=2)
        assert not thread.is_alive()


def test_imu_behavior_settings_reload_without_restart(runtime):
    path, service, _, _ = runtime
    applied = []
    service.register_imu_behavior_applier(lambda config: applied.append(
        (config.imu_tilt_gaze_strength, config.imu_tilt_eye_asymmetry_strength,
         config.imu_impact_reaction_strength)))
    update(path, lambda d: d["behavior"].update(imu_tilt_gaze_strength=.8, imu_tilt_eye_asymmetry_strength=.2,
                                                  imu_impact_reaction_strength=.95,
                                                  imu_impact_reaction_duration_seconds=1.1))

    result = service.execute("reload")

    assert result["ok"]
    assert result["applied"] == ["behavior.imu_tilt_gaze_strength", "behavior.imu_tilt_eye_asymmetry_strength",
                                  "behavior.imu_impact_reaction_strength",
                                  "behavior.imu_impact_reaction_duration_seconds"]
    assert not result["restart_required"] and applied == [(.8, .2, .95)]


def test_led_ring_visual_settings_reload_and_hardware_fields_require_restart(runtime):
    path, service, _, _ = runtime
    applied = []
    service.register_led_ring_applier(lambda config: applied.append((config.led_ring_enabled, config.led_ring_brightness)))
    update(path, lambda d: d["led_ring"].update(led_count=16, brightness=.5, base_color="violet"))

    result = service.execute("reload")

    assert result["ok"]
    assert result["applied"] == ["led_ring.brightness", "led_ring.base_color"]
    assert result["restart_required"] == ["led_ring.led_count"]
    assert applied == [(False, .5)]


def test_led_ring_animation_settings_reload_without_restart(runtime):
    path, service, _, _ = runtime
    applied = []
    service.register_led_ring_applier(lambda config: applied.append(
        (config.led_ring_directional_animation_speed, config.led_ring_bottom_led_index)))
    update(path, lambda d: d["led_ring"].update(directional_animation_speed=8,
                                                   bottom_led_index=1, forward_led_index=2))

    result = service.execute("reload")

    assert result["ok"] and not result["restart_required"]
    assert result["applied"] == ["led_ring.directional_animation_speed", "led_ring.bottom_led_index",
                                  "led_ring.forward_led_index"]
    assert applied == [(8, 1)]


def test_led_ring_animation_color_reloads_without_restart(runtime):
    path, service, _, _ = runtime
    applied = []
    service.register_led_ring_applier(lambda config: applied.append(config.led_ring_imu_animation_color))
    update(path, lambda d: d["led_ring"].update(imu_animation_color="magenta"))
    result = service.execute("reload")
    assert result["ok"] and result["applied"] == ["led_ring.imu_animation_color"]
    assert applied == ["magenta"]


def test_mixed_appearance_logging_and_provider_changes_keep_restart_pending(runtime):
    path, service, logs, _ = runtime
    applied = []
    service.register_appearance_applier(lambda config: applied.append(config.iris_color))
    update(path, lambda d: (d["display"].update(iris_color="amber"),
                            d["logging"].update(level="DEBUG"),
                            d["expression"].update(provider="aws")))

    result = service.execute("reload")

    assert result["ok"]
    assert result["applied"] == ["display.iris_color", "logging.level"]
    assert applied == ["amber"] and logs == ["DEBUG"]
    assert result["active"]["display"]["iris_color"] == "amber"
    assert result["active"]["expression"]["provider"] == "local"
    assert result["restart_required"] == ["expression.provider"]


def test_failed_runtime_appearance_update_does_not_commit_active_values(runtime):
    path, service, logs, _ = runtime
    def fail(_config):
        raise RuntimeError("render loop is stopping")
    service.register_appearance_applier(fail)
    update(path, lambda d: (d["display"].update(iris_color="violet"),
                            d["logging"].update(level="ERROR")))

    result = service.execute("reload")

    assert not result["ok"]
    assert "render loop is stopping" not in result["error"]
    assert service.active["display"]["iris_color"] == "cyan"
    assert service.active["logging"]["level"] == "INFO"
    assert not logs


def test_invalid_iris_color_is_rejected_before_any_application(runtime):
    path, service, logs, _ = runtime
    applied = []
    service.register_appearance_applier(lambda config: applied.append(config.iris_color))
    update(path, lambda d: (d["display"].update(iris_color="#00FFFF"),
                            d["logging"].update(level="ERROR")))

    result = service.execute("reload")

    assert not result["ok"]
    assert not applied and not logs
    assert service.active["display"]["iris_color"] == "cyan"
    assert service.active["logging"]["level"] == "INFO"


def test_camera_preview_changes_reload_and_mixed_provider_change_stays_pending(runtime):
    path, service, _, _ = runtime
    applied = []
    service.register_camera_preview_applier(lambda config: applied.append(config.camera_preview_position))
    update(path, lambda d: (d["vision"]["camera_preview"].update(enabled=True, position="top_left"),
                            d["expression"].update(provider="aws")))

    result = service.execute("reload")

    assert result["ok"]
    assert result["applied"] == ["vision.camera_preview.enabled", "vision.camera_preview.position"]
    assert result["reloadable"] == []
    assert result["restart_required"] == ["expression.provider"]
    assert applied == ["top_left"]
    assert result["active"]["vision"]["camera_preview"]["enabled"] is True
    assert result["active"]["expression"]["provider"] == "local"


def test_invalid_camera_preview_is_rejected_before_runtime_application(runtime):
    path, service, _, _ = runtime
    applied = []
    service.register_camera_preview_applier(lambda config: applied.append(config))
    update(path, lambda d: d["vision"]["camera_preview"].update(scale=.5))

    result = service.execute("reload")

    assert not result["ok"]
    assert not applied
    assert service.active["vision"]["camera_preview"]["scale"] == .25


@pytest.mark.parametrize("bad", ["number", "path", "json", "secret"])
def test_invalid_configuration_never_partially_applies_or_restarts(runtime, bad):
    path, service, applied, _ = runtime
    previous = deepcopy(service.active)
    loaded_at = service.loaded_at
    def invalid(d):
        d["logging"]["level"] = "DEBUG"
        if bad == "number":
            d["display"]["fps"] = 0
        elif bad == "path":
            d["expression"]["enabled"] = True
            d["expression"]["local"]["model_path"] = "absent.onnx"
        else:
            d["AWS_SECRET_ACCESS_KEY"] = "secret-value"
    update(path, invalid)
    if bad == "json":
        path.write_text('{')
    for operation in ("reload", "restart"):
        result = service.execute(operation)
        assert not result["ok"] and "secret-value" not in str(result)
    assert service.active == previous and service.loaded_at == loaded_at
    assert not applied and service.restart_at is None


def test_restart_and_allowlist(runtime):
    _, service, _, now = runtime
    for operation in ("reboot", "sh -c anything", {"command": "anything"}, None):
        assert not service.execute(operation)["ok"]
    assert service.restart_at is None
    assert service.execute("restart")["restart_requested"]
    assert not service.restart_due
    deadline = service.restart_at
    now[0] += .5
    assert service.execute("restart")["ok"]
    assert service.restart_at == deadline
    assert not service.execute("reload")["ok"]
    now[0] += 1
    assert service.restart_due


def test_manual_start_cannot_request_supervised_restart(runtime):
    _, service, _, _ = runtime
    service.restart_supported = False
    assert not service.execute("restart")["ok"]
    assert service.restart_at is None
    assert service.execute("reload")["ok"]


def test_log_reload_retains_sdk_suppression():
    root, boto, botocore = logging.getLogger(), logging.getLogger("boto3"), logging.getLogger("botocore")
    original = root.level, boto.level, botocore.level
    try:
        apply_log_level("DEBUG")
        assert root.level == logging.DEBUG
        assert boto.level == botocore.level == logging.WARNING
    finally:
        root.setLevel(original[0])
        boto.setLevel(original[1])
        botocore.setLevel(original[2])


def test_local_channel_updates_parent_service(runtime):
    path, service, applied, _ = runtime
    parent, child = multiprocessing.Pipe()
    stop = Event()
    thread = Thread(target=serve_lifecycle, args=(parent, service, stop))
    thread.start()
    try:
        client = LifecycleClient(child)
        assert client.execute("status")["ok"]
        update(path, lambda d: d["logging"].update(level="ERROR"))
        assert client.execute("reload")["applied"] == ["logging.level"]
        assert applied == ["ERROR"]
        assert not client.execute("reboot")["ok"]
        assert client.execute("restart")["restart_requested"]
        assert service.restart_at is not None
    finally:
        stop.set()
        child.close()
        thread.join(timeout=2)
        assert not thread.is_alive()


def test_application_channel_recovers_after_runtime_command_boundary_becomes_ready(runtime):
    _, service, _, _ = runtime

    class Application:
        def start_listening(self):
            return {"listening": True}

    parent, child = multiprocessing.Pipe()
    stop = Event()
    thread = Thread(target=serve_lifecycle, args=(parent, service, stop))
    thread.start()
    try:
        client = LifecycleClient(child)
        before_registration = client.execute("application.start_listening")
        assert before_registration == {
            "ok": False,
            "error": {
                "code": "service_unavailable",
                "message": "PHOS runtime command service is starting. Retry shortly.",
                "details": {"reason": "runtime_starting"},
            },
            "status": 503,
        }
        assert client.available is True

        service.register_application_service(Application())
        before_runtime_start = client.execute("application.start_listening")
        assert before_runtime_start["error"]["details"] == {"reason": "runtime_starting"}
        assert client.available is True

        service.mark_application_ready()
        assert client.execute("application.start_listening") == {
            "ok": True, "result": {"listening": True}}
    finally:
        stop.set()
        child.close()
        thread.join(timeout=2)
        assert not thread.is_alive()


def test_lifecycle_channel_forwards_eye_safe_layer_configuration(runtime):
    _, service, _, _ = runtime

    class Application:
        def __init__(self): self.calls = []
        def configure_eye_safe_layer(self, payload):
            self.calls.append(payload)
            return {"saved": payload}

    application = Application()
    service.register_application_service(application)
    service.mark_application_ready()
    parent, child = multiprocessing.Pipe()
    stop = Event()
    thread = Thread(target=serve_lifecycle, args=(parent, service, stop))
    thread.start()
    try:
        client = LifecycleClient(child)
        payload = {"enabled": True, "mode": "expressive"}
        assert client.execute("application.configure_eye_safe_layer", payload) == {"ok": True, "result": {"saved": payload}}
        assert application.calls == [payload]
    finally:
        stop.set()
        child.close()
        thread.join(timeout=2)
        assert not thread.is_alive()


def test_lifecycle_dispatcher_survives_one_unexpected_service_failure(caplog):
    class Service:
        def __init__(self):
            self.calls = 0

        def execute(self, operation):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("simulated startup failure")
            return {"ok": True, "operation": operation}

    parent, child = multiprocessing.Pipe()
    stop, ready = Event(), Event()
    thread = Thread(target=serve_lifecycle, args=(parent, Service(), stop, ready))
    thread.start()
    try:
        assert ready.wait(1)
        client = LifecycleClient(child)
        with caplog.at_level(logging.ERROR):
            assert client.execute("status") == {
                "ok": False, "error": "PHOS could not process the lifecycle request.", "status": 500}
        assert thread.is_alive()
        assert client.execute("status") == {"ok": True, "operation": "status"}
        assert "Lifecycle dispatcher failed" in caplog.text
    finally:
        stop.set()
        child.close()
        thread.join(timeout=2)
        assert not thread.is_alive()


def test_lifecycle_channel_forwards_presence_and_attention_read_models(runtime):
    _, service, _, _ = runtime

    class Application:
        def presence(self): return {"state": "no_one", "people_count": 0}
        def attention(self): return {"state": "idle", "target": None}

    service.register_application_service(Application())
    service.mark_application_ready()
    parent, child = multiprocessing.Pipe()
    stop = Event()
    thread = Thread(target=serve_lifecycle, args=(parent, service, stop))
    thread.start()
    try:
        client = LifecycleClient(child)
        assert client.execute("application.presence") == {
            "ok": True, "result": {"state": "no_one", "people_count": 0}}
        assert client.execute("application.attention") == {
            "ok": True, "result": {"state": "idle", "target": None}}
    finally:
        stop.set()
        child.close()
        thread.join(timeout=2)
        assert not thread.is_alive()


def test_lifecycle_channel_acknowledges_sequential_speak_commands(runtime):
    _, service, _, _ = runtime
    received = []

    class Application:
        def speak(self, text):
            received.append(text)
            return {"status": "accepted"}
        def status(self): return {"running": True}

    service.register_application_service(Application())
    service.mark_application_ready()
    parent, child = multiprocessing.Pipe()
    stop = Event()
    thread = Thread(target=serve_lifecycle, args=(parent, service, stop))
    thread.start()
    try:
        client = LifecycleClient(child)
        assert client.execute("application.speak", {"text": "One"}) == {"ok": True, "result": {"status": "accepted"}}
        assert client.execute("application.speak", {"text": "Two"}) == {"ok": True, "result": {"status": "accepted"}}
        assert received == ["One", "Two"]
        assert client.execute("application.status")["ok"]
    finally:
        stop.set()
        child.close()
        thread.join(timeout=2)
        assert not thread.is_alive()


def test_restart_request_stops_runtime_through_existing_stop_event(runtime, monkeypatch, caplog):
    from robot import main
    _, service, _, now = runtime
    caplog.set_level(logging.INFO)
    service.execute("restart")
    now[0] = 2.0
    stopped = []
    class Runtime:
        def apply_appearance(self, config):
            pass
        async def run(self, stop):
            await asyncio.wait_for(stop.wait(), timeout=1)
            stopped.append(True)
    monkeypatch.setattr(main, "build_application", lambda **kw: Runtime())
    monkeypatch.setattr(main, "_install_shutdown_handlers", lambda *args: None)
    asyncio.run(main.async_main(lifecycle=service))
    assert stopped == [True]
    assert "PHOS LIFECYCLE: restart requested" in caplog.messages
    assert "PHOS MAIN: restart_due detected" in caplog.messages
    assert "PHOS MAIN: stop_event received" in caplog.messages


def test_main_exits_with_restart_code_after_worker_cleanup(runtime, monkeypatch, caplog):
    from robot import main
    path, service, _, _ = runtime
    caplog.set_level(logging.INFO)
    calls = []
    class Worker:
        lifecycle = service
        def __init__(self, *args):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *args):
            calls.append("cleanup")
    async def run(**kwargs):
        assert kwargs["lifecycle"].execute("restart")["ok"]
    monkeypatch.setattr("robot.web.server.WebServer", Worker)
    monkeypatch.setattr(main, "async_main", run)
    monkeypatch.setattr(main.logging, "basicConfig", lambda **kw: None)
    monkeypatch.setattr("sys.argv", ["phos", "--config", str(path)])
    with pytest.raises(SystemExit) as error:
        main.main()
    assert error.value.code == 75 and calls == ["cleanup"]
    assert "PHOS LIFECYCLE: restart requested" in caplog.messages
    assert "PHOS MAIN: exiting for managed restart exit_code=75" in caplog.messages


def test_deployment_contract_and_restart_capability(runtime, monkeypatch):
    from configparser import ConfigParser
    from pathlib import Path
    from robot.lifecycle import RESTART_EXIT_CODE
    from robot.web.server import WebServer
    path, _, _, _ = runtime
    unit = ConfigParser(interpolation=None)
    unit.read(Path(__file__).resolve().parents[1] / "deploy/phos.service")
    assert unit["Unit"]["After"] == "graphical-session-pre.target"
    assert unit["Unit"]["PartOf"] == "graphical-session.target"
    assert unit["Unit"]["ConditionEnvironment"] == "PHOS_LOCAL_GRAPHICAL_SESSION=1"
    assert unit["Service"]["RestartForceExitStatus"] == str(RESTART_EXIT_CODE)
    assert unit["Service"]["ExecStart"] == (
        "%h/phos/.venv/bin/python -m robot.main --config %h/phos/config/phos.yaml"
    )
    environment = unit["Service"]["Environment"]
    assert "PYTHONPATH=%h/phos/src" in environment
    assert "PHOS_SERVICE_MANAGED=1" in environment
    assert "/root" not in environment
    assert "phos.json" not in unit["Service"]["ExecStart"]
    assert "src/robot/main.py" not in unit["Service"]["ExecStart"]
    config = RuntimeConfig.from_file(path)
    monkeypatch.delenv("INVOCATION_ID", raising=False)
    monkeypatch.setenv("PHOS_SERVICE_MANAGED", "1")
    assert not WebServer(path, config).lifecycle.restart_supported
    monkeypatch.setenv("INVOCATION_ID", "test-systemd-invocation")
    assert WebServer(path, config).lifecycle.restart_supported
    monkeypatch.delenv("PHOS_SERVICE_MANAGED")
    assert not WebServer(path, config).lifecycle.restart_supported


def test_module_startup_resolves_stdlib_secrets_with_deployed_pythonpath():
    root = Path(__file__).resolve().parents[1]
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(root / "src")
    result = subprocess.run(
        [sys.executable, "-m", "robot.main", "--help"],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    probe = subprocess.run(
        [sys.executable, "-c", "import secrets; assert hasattr(secrets, 'token_bytes')"],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert probe.returncode == 0, probe.stderr


def test_skin_delete_blocks_live_owned_assets_then_cleans_only_its_own_directory(runtime):
    path, lifecycle, _, _ = runtime
    source = path.parent / "backgrounds" / "idle.png"
    source.parent.mkdir()
    source.write_bytes(
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde"
        b"\x00\x00\x00\x0cIDAT\x08\xd7c\xf8\xcf\xc0\x00\x00\x03\x01\x01\x00\x18\xdd\x8d\xb4\x00\x00\x00\x00IEND\xaeB`\x82")
    appearance = appearance_snapshot(load_document())
    appearance["backgrounds"].update({"enabled": True, "idle": "backgrounds/idle.png"})
    repository = SkinRepository(path)
    skin = repository.create("Owned", "", appearance)
    owned_asset = skin.appearance["backgrounds"]["idle"]
    unrelated = path.parent / "backgrounds" / "unrelated.png"
    unrelated.write_bytes(b"unrelated")
    update(path, lambda document: document["display"]["backgrounds"].update(
        {"enabled": True, "idle": owned_asset}
    ))
    application = object.__new__(PhosApplicationService)
    application._lifecycle = lifecycle

    assert next(item for item in application.skins() if item["id"] == skin.id)["in_use"] is True
    with pytest.raises(ApplicationError) as error:
        application.delete_skin(skin.id)
    assert error.value.code == "skin_in_use" and error.value.status == 409
    assert "Apply another skin" in error.value.message

    update(path, lambda document: document["display"]["backgrounds"].update({"enabled": False, "idle": None}))
    assert application.delete_skin(skin.id) == {"deleted": skin.id}
    assert not (path.parent / "backgrounds" / "skins" / skin.id).exists()
    assert unrelated.read_bytes() == b"unrelated"


def test_skin_in_use_is_a_semantic_appearance_match_for_builtin_and_custom_skins(runtime):
    path, lifecycle, _, _ = runtime
    application = object.__new__(PhosApplicationService)
    application._lifecycle = lifecycle

    assert [item["id"] for item in application.skins() if item["in_use"]] == ["phos-default"]
    update(path, lambda document: document["display"].update(
        iris_color="white", eye_safe_layer={"enabled": True, "mode": "subtle"}
    ))
    assert [item["id"] for item in application.skins() if item["in_use"]] == ["minimal"]
    update(path, lambda document: document["display"].update(
        iris_color="violet", eye_safe_layer={"enabled": True, "mode": "balanced"},
        state_effects={"enabled": True, "preset": "balanced", "palette": "aqua", "reduce_motion": False}
    ))
    assert [item["id"] for item in application.skins() if item["in_use"]] == ["ambient"]
    update(path, lambda document: document["display"].update(iris_color="green"))
    assert not [item for item in application.skins() if item["in_use"]]
