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
import yaml
from flask import Flask

from robot.config import ConfigurationError, RuntimeConfig, load_document
from robot.lifecycle import LifecycleService, apply_log_level
from robot.lifecycle_channel import LifecycleClient, serve_lifecycle
from robot.services.application import ApplicationError, PhosApplicationService, RemoteApplicationService
from robot.services import (AgentAppearanceActivityTracker, AgentCredentialService, AgentManagementService,
                            AgentRegistryProvider)
from robot.secrets import SecretsService
from robot.ui.skins import SkinRepository, appearance_snapshot
from robot.web.api import create_api


@pytest.fixture
def runtime(tmp_path):
    document = load_document()
    # The isolated legacy fixture intentionally has no colocated declarative
    # catalog; absence remains a supported configuration.
    document.pop("appearance", None)
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


def test_presentation_owner_operations_use_the_lifecycle_agent_service(runtime):
    path, lifecycle, _, _ = runtime
    config = lifecycle.config_repository.load()
    provider = AgentRegistryProvider(config.agent_registry)
    management = AgentManagementService(lifecycle.config_repository, provider,
                                        AgentCredentialService(provider, SecretsService(path.parent / ".secrets")))
    lifecycle.register_agent_management_service(management)

    selected = lifecycle.execute({
        "operation": "application.set_presentation_owner", "payload": {"agent_id": "local-agent"},
    })
    assert selected == {"ok": True, "result": {"agent_id": "local-agent", "display_name": "Local MCP agent"}}
    resolved = lifecycle.execute({"operation": "application.effective_skin", "payload": {}})
    assert resolved["ok"] and resolved["result"]["configured_owner_id"] == "local-agent"
    cleared = lifecycle.execute({"operation": "application.clear_presentation_owner", "payload": {}})
    assert cleared == {"ok": True, "result": {"agent_id": None, "display_name": None}}


def test_lifecycle_exposes_read_only_appearance_arbitration_without_renderer_changes(runtime):
    path, lifecycle, _, _ = runtime
    config = lifecycle.config_repository.load()
    provider = AgentRegistryProvider(config.agent_registry)
    management = AgentManagementService(lifecycle.config_repository, provider,
                                        AgentCredentialService(provider, SecretsService(path.parent / ".secrets")))
    tracker = AgentAppearanceActivityTracker(provider, management.appearance_arbitration_candidates)
    management.register_appearance_activity_tracker(tracker)
    lifecycle.register_agent_management_service(management)
    lifecycle.register_appearance_activity_tracker(tracker)

    result = lifecycle.execute({"operation": "application.appearance_arbitration", "payload": {}})
    assert result["ok"] is True
    assert result["result"]["selected_agent_id"] is None
    assert result["result"]["selection_reason"] == "global_mode"
    tracker.close()


def test_presentation_owner_applies_transient_skin_and_restores_global_appearance(runtime):
    path, lifecycle, _, _ = runtime
    config = lifecycle.config_repository.load()
    provider = AgentRegistryProvider(config.agent_registry)
    management = AgentManagementService(lifecycle.config_repository, provider,
                                        AgentCredentialService(provider, SecretsService(path.parent / ".secrets")))
    applied = []
    lifecycle.register_appearance_applier(lambda value: applied.append(("iris", value.iris_color)))
    lifecycle.register_backgrounds_applier(lambda value: applied.append(("backgrounds", value.backgrounds_enabled)))
    lifecycle.register_eye_safe_layer_applier(lambda value: applied.append(("safe", value.eye_safe_layer_mode)))
    lifecycle.register_state_effects_applier(lambda value: applied.append(("effects", value.state_effects_palette)))
    lifecycle.register_agent_management_service(management)
    management.update_agent("local-agent", "Local MCP agent", "standard", {"enabled": True, "id": "ambient"})
    before_display = lifecycle.config_repository.load().to_dict()["display"]

    management.set_presentation_owner("local-agent")
    assert ("iris", "#A670F5") in applied
    assert lifecycle.config_repository.load().to_dict()["display"] == before_display

    management.clear_presentation_owner()
    assert applied[-4:] == [("iris", before_display["iris_color"]),
                            ("backgrounds", before_display["backgrounds"]["enabled"]),
                            ("safe", before_display["eye_safe_layer"]["mode"]),
                            ("effects", before_display["state_effects"]["palette"])]


@pytest.mark.parametrize("assignment", [{"enabled": True, "id": "placeholder"}, {"enabled": False, "id": "placeholder"}])
def test_assigned_custom_skin_cannot_be_deleted_through_application_service(runtime, assignment):
    path, lifecycle, _, _ = runtime
    repository = SkinRepository(path)
    skin = repository.create("Assigned", "", appearance_snapshot(load_document()))
    assignment["id"] = skin.id
    config = lifecycle.config_repository.load()
    provider = AgentRegistryProvider(config.agent_registry)
    management = AgentManagementService(lifecycle.config_repository, provider,
                                        AgentCredentialService(provider, SecretsService(path.parent / ".secrets")))
    management.create_agent("skin-agent", "Skin Agent", "observer", assignment)
    lifecycle.register_agent_management_service(management)
    application = object.__new__(PhosApplicationService); application._lifecycle = lifecycle
    before = path.read_bytes()
    with pytest.raises(ApplicationError) as error:
        application.delete_skin(skin.id)
    assert error.value.code == "skin_in_use" and error.value.status == 409
    assert repository.get(skin.id).id == skin.id
    assert management.get_agent("skin-agent")["skin"] == assignment
    assert path.read_bytes() == before


def test_unreferenced_custom_skin_deletes_through_application_service(runtime):
    path, lifecycle, _, _ = runtime
    skin = SkinRepository(path).create("Unused", "", appearance_snapshot(load_document()))
    application = object.__new__(PhosApplicationService); application._lifecycle = lifecycle
    assert application.delete_skin(skin.id) == {"deleted": skin.id}
    with pytest.raises(ConfigurationError): SkinRepository(path).get(skin.id)


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
    assert applied == ["#A670F5"]
    assert result["active"]["display"]["iris_color"] == "#A670F5"
    assert result["reloadable"] == []
    assert result["restart_required"] == []


def test_runtime_settings_apply_preview_immediately_then_persist(runtime):
    path, service, applied_levels, _ = runtime
    preview = []
    service.register_camera_preview_applier(lambda config: preview.append(config.camera_preview_position))
    document = load_document(path)
    document["vision"]["camera_preview"]["position"] = "bottom_left"
    document["logging"]["level"] = "DEBUG"

    result = service.apply_runtime_settings(RuntimeConfig.from_dict(document, base_dir=path.parent))

    assert result["ok"]
    assert result["applied"] == ["vision.camera_preview.position", "logging.level"]
    assert preview == ["bottom_left"]
    assert applied_levels == ["DEBUG"]
    assert RuntimeConfig.from_file(path).camera_preview_position == "bottom_left"
    assert result["active"]["vision"]["camera_preview"]["position"] == "bottom_left"


def test_runtime_settings_apply_every_preview_control_through_one_runtime_boundary(runtime):
    path, service, _, _ = runtime
    received = []
    service.register_camera_preview_applier(lambda config: received.append({
        "enabled": config.camera_preview_enabled, "position": config.camera_preview_position,
        "scale": config.camera_preview_scale, "max_fps": config.camera_preview_max_fps,
        "show_face_box": config.camera_preview_show_face_box,
        "show_expression": config.camera_preview_show_expression,
        "show_confidence": config.camera_preview_show_confidence,
    }))
    document = load_document(path)
    document["vision"]["camera_preview"].update(enabled=True, position="top_left", scale=.3,
                                                     max_fps=4, show_face_box=False,
                                                     show_expression=False, show_confidence=False)

    result = service.apply_runtime_settings(RuntimeConfig.from_dict(document, base_dir=path.parent))

    assert result["ok"]
    assert result["applied"] == [
        "vision.camera_preview.enabled", "vision.camera_preview.position", "vision.camera_preview.scale",
        "vision.camera_preview.max_fps", "vision.camera_preview.show_face_box",
        "vision.camera_preview.show_expression", "vision.camera_preview.show_confidence"]
    assert received == [{"enabled": True, "position": "top_left", "scale": .3, "max_fps": 4,
                         "show_face_box": False, "show_expression": False, "show_confidence": False}]


def test_failed_runtime_preview_update_neither_persists_nor_changes_active_config(runtime):
    path, service, _, _ = runtime
    service.register_camera_preview_applier(lambda _config: (_ for _ in ()).throw(RuntimeError("display unavailable")))
    document = load_document(path)
    document["vision"]["camera_preview"]["position"] = "bottom_left"

    result = service.apply_runtime_settings(RuntimeConfig.from_dict(document, base_dir=path.parent))

    assert not result["ok"]
    assert RuntimeConfig.from_file(path).camera_preview_position == "bottom_right"
    assert service.active["vision"]["camera_preview"]["position"] == "bottom_right"


def test_runtime_settings_rollback_live_preview_when_persistence_fails(runtime, monkeypatch):
    path, service, _, _ = runtime
    applied = []
    service.register_camera_preview_applier(lambda config: applied.append(config.camera_preview_position))
    document = load_document(path)
    document["vision"]["camera_preview"]["position"] = "bottom_left"
    monkeypatch.setattr(service.config_repository, "save", lambda _config: (_ for _ in ()).throw(OSError("disk full")))

    result = service.apply_runtime_settings(RuntimeConfig.from_dict(document, base_dir=path.parent))

    assert not result["ok"]
    assert applied == ["bottom_left", "bottom_right"]
    assert RuntimeConfig.from_file(path).camera_preview_position == "bottom_right"
    assert service.active["vision"]["camera_preview"]["position"] == "bottom_right"


def test_hardware_controller_connection_settings_hot_apply_without_restart(runtime):
    path, service, _, _ = runtime
    applied = []
    service.register_hardware_controller_applier(
        lambda config: applied.append((config.hardware_controller_serial_device,
                                       config.hardware_controller_serial_baud_rate)))
    update(path, lambda d: d["hardware_controller"]["serial"].update(device="/dev/ttyUSB1", baud_rate=57600))

    result = service.execute("reload")

    assert result["ok"]
    assert result["applied"] == ["hardware_controller.serial.device", "hardware_controller.serial.baud_rate"]
    assert result["restart_required"] == []
    assert applied == [("/dev/ttyUSB1", 57600)]
    assert service.active["hardware_controller"]["serial"] == {"device": "/dev/ttyUSB1", "baud_rate": 57600}


@pytest.mark.parametrize("submitted, expected", [("green", "#41CD7D"), ("#7a5cff", "#7A5CFF")])
def test_configure_eyes_validates_persists_and_applies_canonical_color(runtime, submitted, expected):
    path, lifecycle, _, _ = runtime
    applied = []
    lifecycle.register_appearance_applier(lambda config: applied.append(config.iris_color))
    application = object.__new__(PhosApplicationService)
    application._lifecycle = lifecycle
    application._runtime = object()

    result = application.configure_eyes({"iris_color": submitted, "base_visual_source": "manual",
                                         "environment_overlays_enabled": True})

    assert result["saved"]["iris_color"] == expected
    assert RuntimeConfig.from_file(path).iris_color == expected
    assert lifecycle.active["display"]["iris_color"] == expected
    assert applied == [expected]


def test_configure_eyes_hot_applies_iris_source_and_environment_overlays_together(runtime):
    path, lifecycle, _, _ = runtime
    applied = []
    lifecycle.register_appearance_applier(lambda config: applied.append(("iris", config.iris_color)))
    lifecycle.register_base_visual_source_applier(
        lambda config: applied.append(("source", config.base_visual_source)))
    lifecycle.register_environment_overlays_applier(
        lambda config: applied.append(("overlays", config.environment_overlays_enabled)))
    application = object.__new__(PhosApplicationService)
    application._lifecycle = lifecycle
    application._runtime = object()

    result = application.configure_eyes({"iris_color": "violet", "base_visual_source": "state",
                                         "environment_overlays_enabled": False})

    assert result["applied"] == ["display.iris_color", "display.base_visual_source",
                                 "display.environment_overlays_enabled"]
    assert result["saved"] == {"iris_color": "#A670F5", "base_visual_source": "state",
                               "environment_overlays_enabled": False}
    assert applied == [("iris", "#A670F5"), ("source", "state"), ("overlays", False)]
    saved = RuntimeConfig.from_file(path)
    assert (saved.iris_color, saved.base_visual_source, saved.environment_overlays_enabled) == (
        "#A670F5", "state", False)
    assert (lifecycle.active["display"]["iris_color"], lifecycle.active["display"]["base_visual_source"],
            lifecycle.active["display"]["environment_overlays_enabled"]) == ("#A670F5", "state", False)


def test_configure_eyes_rejects_invalid_color_before_persistence(runtime):
    path, lifecycle, _, _ = runtime
    application = object.__new__(PhosApplicationService)
    application._lifecycle = lifecycle
    application._runtime = object()

    with pytest.raises(ApplicationError) as error:
        application.configure_eyes({"iris_color": "#FFF", "base_visual_source": "manual",
                                    "environment_overlays_enabled": True})

    assert error.value.code == "invalid_configuration"
    assert RuntimeConfig.from_file(path).iris_color == "#28CEEB"


def test_configure_eyes_preserves_custom_color_in_yaml(tmp_path):
    path = tmp_path / "phos.yaml"
    path.write_text(yaml.safe_dump(load_document(), sort_keys=False))
    lifecycle = LifecycleService(path, RuntimeConfig.from_file(path))
    lifecycle.register_appearance_applier(lambda _config: None)
    application = object.__new__(PhosApplicationService)
    application._lifecycle = lifecycle
    application._runtime = object()

    application.configure_eyes({"iris_color": "#7a5cff", "base_visual_source": "manual",
                                "environment_overlays_enabled": True})

    assert yaml.safe_load(path.read_text())["display"]["iris_color"] == "#7A5CFF"
    assert RuntimeConfig.from_file(path).iris_color == "#7A5CFF"


def test_skin_preview_updates_runtime_without_saving_or_changing_active_config(runtime):
    path, lifecycle, _, _ = runtime
    applied = []
    lifecycle.register_appearance_applier(lambda config: applied.append(("iris", config.iris_color)))
    lifecycle.register_backgrounds_applier(lambda config: applied.append(("backgrounds", config.backgrounds_enabled)))
    lifecycle.register_eye_safe_layer_applier(lambda config: applied.append(("safe", config.eye_safe_layer_mode)))
    lifecycle.register_state_effects_applier(lambda config: applied.append(("effects", config.state_effects_enabled)))
    application = object.__new__(PhosApplicationService)
    application._lifecycle = lifecycle
    application._runtime = object()
    before, active = path.read_bytes(), deepcopy(lifecycle.active)

    result = application.preview_skin("ambient")

    assert result["applied"] == ["temporary_appearance"]
    assert ("iris", "#A670F5") in applied and ("effects", True) in applied
    assert path.read_bytes() == before and lifecycle.active == active


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


def test_lifecycle_channel_dispatches_runtime_configuration_and_rejects_unrelated_operation(runtime):
    _, service, _, _ = runtime
    received = []

    class Application:
        def configure_runtime(self, payload):
            received.append(payload)
            return {"saved": payload, "applied": ["vision.camera_preview.position"]}

    service.register_application_service(Application())
    service.mark_application_ready()
    parent, child = multiprocessing.Pipe()
    stop = Event()
    thread = Thread(target=serve_lifecycle, args=(parent, service, stop))
    thread.start()
    try:
        client = LifecycleClient(child)
        payload = {"vision": {"camera_preview": {"position": "bottom_right"}}}
        assert client.execute("application.configure_runtime", payload) == {
            "ok": True, "result": {"saved": payload, "applied": ["vision.camera_preview.position"]}}
        assert received == [payload]
        assert client.execute("application.unrelated", {}) == {"ok": False, "error": "Unsupported lifecycle operation."}
    finally:
        stop.set()
        child.close()
        thread.join(timeout=2)
        assert not thread.is_alive()


def test_lifecycle_channel_forwards_agent_presentation_reads_and_owner_updates(runtime):
    path, service, _, _ = runtime
    config = service.config_repository.load()
    provider = AgentRegistryProvider(config.agent_registry)
    management = AgentManagementService(service.config_repository, provider,
                                        AgentCredentialService(provider, SecretsService(path.parent / ".secrets")))
    service.register_agent_management_service(management)
    parent, child = multiprocessing.Pipe(); stop = Event()
    thread = Thread(target=serve_lifecycle, args=(parent, service, stop)); thread.start()
    try:
        client = LifecycleClient(child)
        assert any(item["id"] == "ambient" for item in client.execute("application.agent_skins", {})["result"])
        assert client.execute("application.set_presentation_owner", {"agent_id": "local-agent"})["ok"]
        assert client.execute("application.presentation_owner", {})["result"]["agent_id"] == "local-agent"
        assert client.execute("application.effective_skin", {})["ok"]
        assert client.execute("application.presentation_activation", {})["ok"]
    finally:
        stop.set(); child.close(); thread.join(timeout=2)
        assert not thread.is_alive()


def test_lifecycle_channel_persists_agent_skin_metadata(runtime):
    path, service, _, _ = runtime
    config = service.config_repository.load()
    provider = AgentRegistryProvider(config.agent_registry)
    management = AgentManagementService(service.config_repository, provider,
                                        AgentCredentialService(provider, SecretsService(path.parent / ".secrets")))
    service.register_agent_management_service(management)
    parent, child = multiprocessing.Pipe(); stop = Event()
    thread = Thread(target=serve_lifecycle, args=(parent, service, stop)); thread.start()
    try:
        client = LifecycleClient(child)
        payload = {"agent_id": "styled", "display_name": "Styled", "permission_profile": "observer",
                   "skin": {"enabled": True, "id": "ambient"}}
        assert client.execute("application.create_agent", payload)["ok"]
        assert RuntimeConfig.from_file(path).agent_registry.get("styled").skin == payload["skin"]
        updated = {**payload, "display_name": "Styled Again", "skin": {"enabled": False, "id": "ambient"}}
        assert client.execute("application.update_agent", updated)["ok"]
        assert RuntimeConfig.from_file(path).agent_registry.get("styled").skin == updated["skin"]
    finally:
        stop.set(); child.close(); thread.join(timeout=2)
        assert not thread.is_alive()


def test_lifecycle_channel_persists_and_validates_appearance_policy(runtime):
    path, service, _, _ = runtime
    config = service.config_repository.load(); provider = AgentRegistryProvider(config.agent_registry)
    management = AgentManagementService(service.config_repository, provider, AgentCredentialService(provider, SecretsService(path.parent / ".secrets")))
    service.register_agent_management_service(management)
    parent, child = multiprocessing.Pipe(); stop = Event(); thread = Thread(target=serve_lifecycle, args=(parent, service, stop)); thread.start()
    try:
        client = LifecycleClient(child)
        create = {"agent_id": "policy", "display_name": "Policy", "permission_profile": "observer", "skin": {"enabled": True, "id": "ambient"}, "appearance": {"participate": True, "priority": 85, "activity_timeout_seconds": 10}}
        assert client.execute("application.create_agent", create)["ok"]
        update = {**create, "appearance": {"participate": False, "priority": 25, "activity_timeout_seconds": 80}}
        assert client.execute("application.update_agent", update)["ok"]
        assert RuntimeConfig.from_file(path).agent_registry.get("policy").appearance == update["appearance"]
        omitted = {"agent_id": "policy", "display_name": "Policy Updated", "permission_profile": "observer"}
        assert client.execute("application.update_agent", omitted)["ok"]
        before = path.read_bytes()
        invalid = {**update, "appearance": {"participate": True, "priority": 101, "activity_timeout_seconds": 80}}
        assert not client.execute("application.update_agent", invalid)["ok"]
        assert path.read_bytes() == before
    finally:
        stop.set(); child.close(); thread.join(timeout=2)


def test_lifecycle_channel_dispatches_eyes_configuration(runtime):
    _, service, _, _ = runtime
    received = []

    class Application:
        def configure_eyes(self, payload):
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
        payload = {"iris_color": "#7A5CFF", "base_visual_source": "manual",
                   "environment_overlays_enabled": True}
        assert client.execute("application.configure_eyes", payload) == {"ok": True, "result": {"saved": payload}}
        assert received == [payload]
    finally:
        stop.set()
        child.close()
        thread.join(timeout=2)
        assert not thread.is_alive()


def test_lifecycle_channel_dispatches_controller_read_models(runtime):
    _, service, _, _ = runtime

    class Application:
        def controller_status(self): return {"state": "connected", "configured_device": "/dev/ttyUSB0"}
        def controller_recent_messages(self): return [{"type": "sensor_update"}]
        def controller_serial_ports(self): return [{"device": "/dev/ttyUSB0"}]
        def clear_controller_recent_messages(self): return {"cleared": True}

    service.register_application_service(Application())
    service.mark_application_ready()
    parent, child = multiprocessing.Pipe()
    stop = Event()
    thread = Thread(target=serve_lifecycle, args=(parent, service, stop))
    thread.start()
    try:
        client = LifecycleClient(child)
        assert client.execute("application.controller_status")["result"]["state"] == "connected"
        assert client.execute("application.controller_recent_messages")["result"] == [{"type": "sensor_update"}]
        assert client.execute("application.controller_serial_ports")["result"] == [{"device": "/dev/ttyUSB0"}]
        assert client.execute("application.clear_controller_recent_messages")["result"] == {"cleared": True}
    finally:
        stop.set()
        child.close()
        thread.join(timeout=2)
        assert not thread.is_alive()


def test_signal_api_reaches_semantic_application_service_over_lifecycle_channel(runtime):
    """The web worker must not reject semantic signals as lifecycle commands."""
    _, service, _, _ = runtime
    received = []

    class Application:
        def signal_user(self, signal):
            if signal not in {"success", "warning", "attention", "error"}:
                raise ApplicationError("invalid_signal", "Unsupported user signal.", {"signal": signal})
            received.append(signal)
            return {"active_intent": signal}

    service.register_application_service(Application())
    service.mark_application_ready()
    parent, child = multiprocessing.Pipe()
    stop = Event()
    thread = Thread(target=serve_lifecycle, args=(parent, service, stop))
    thread.start()
    app = Flask(__name__)
    app.register_blueprint(create_api(RemoteApplicationService(LifecycleClient(child))))
    try:
        client = app.test_client()
        for signal in ("success", "warning", "attention", "error"):
            response = client.post("/api/v1/signal", json={"signal": signal})
            assert response.status_code == 200
            assert response.json == {"active_intent": signal}
        assert received == ["success", "warning", "attention", "error"]

        unknown = client.post("/api/v1/signal", json={"signal": "rainbow"})
        assert unknown.status_code == 400
        assert unknown.json["error"]["code"] == "invalid_signal"
        raw = client.post("/api/v1/signal", json={"signal": "success", "animation": "spin"})
        assert raw.status_code == 400
        assert raw.json["error"]["code"] == "invalid_signal"
        assert received == ["success", "warning", "attention", "error"]
    finally:
        stop.set()
        child.close()
        thread.join(timeout=2)
        assert not thread.is_alive()


def test_lifecycle_channel_dispatches_admin_network_and_overlay_operations(runtime):
    _, service, _, _ = runtime

    class Application:
        def __init__(self): self.shown = 0; self.cleared = 0
        def get_admin_network_info(self):
            return {"hostname": "phos", "ip_address": "192.168.1.42", "interface": "wlan0",
                    "port": 8080, "admin_url": "http://192.168.1.42:8080/", "status": "connected"}
        def show_admin_access_overlay(self): self.shown += 1; return {"status": "shown"}
        def clear_admin_overlay(self): self.cleared += 1; return {"status": "cleared"}

    application = Application()
    service.register_application_service(application)
    service.mark_application_ready()
    parent, child = multiprocessing.Pipe()
    stop = Event()
    thread = Thread(target=serve_lifecycle, args=(parent, service, stop))
    thread.start()
    try:
        client = LifecycleClient(child)
        assert client.execute("application.get_admin_network_info")["result"]["ip_address"] == "192.168.1.42"
        assert client.execute("application.show_admin_access_overlay") == {"ok": True, "result": {"status": "shown"}}
        assert client.execute("application.clear_admin_overlay") == {"ok": True, "result": {"status": "cleared"}}
        assert (application.shown, application.cleared) == (1, 1)
    finally:
        stop.set()
        child.close()
        thread.join(timeout=2)
        assert not thread.is_alive()


def test_lifecycle_eyes_unexpected_failure_is_logged_and_bounded(caplog):
    class Application:
        def configure_eyes(self, _payload):
            raise RuntimeError("renderer queue unavailable")

    service = object.__new__(LifecycleService)
    service._application_service = Application()
    service._application_ready = True
    with caplog.at_level(logging.ERROR):
        result = service._execute_application({"operation": "application.configure_eyes", "payload": {
            "iris_color": "#7A5CFF", "base_visual_source": "manual", "environment_overlays_enabled": True,
        }})

    assert result == {"ok": False, "error": {"code": "internal_error",
                      "message": "PHOS could not process the request.",
                      "details": {"operation": "application.configure_eyes"}}, "status": 500}
    assert "operation=application.configure_eyes failed exception_type=RuntimeError" in caplog.text
    assert "renderer queue unavailable" in caplog.text and "Traceback" in caplog.text


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
    assert applied == ["#F4AB3D"] and logs == ["DEBUG"]
    assert result["active"]["display"]["iris_color"] == "#F4AB3D"
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
    assert service.active["display"]["iris_color"] == "#28CEEB"
    assert service.active["logging"]["level"] == "INFO"
    assert not logs


def test_invalid_iris_color_is_rejected_before_any_application(runtime):
    path, service, logs, _ = runtime
    applied = []
    service.register_appearance_applier(lambda config: applied.append(config.iris_color))
    update(path, lambda d: (d["display"].update(iris_color="#FFF"),
                            d["logging"].update(level="ERROR")))

    result = service.execute("reload")

    assert not result["ok"]
    assert not applied and not logs
    assert service.active["display"]["iris_color"] == "#28CEEB"
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
