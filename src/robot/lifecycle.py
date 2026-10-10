"""Shared validated reload and supervisor-owned restart operations."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import logging
from pathlib import Path
from threading import RLock
import time

from robot.config import ConfigRepository, ConfigurationError, RuntimeConfig

RESTART_EXIT_CODE = 75
PREVIEW_RELOADABLE = frozenset({
    "vision.camera_preview.enabled", "vision.camera_preview.position", "vision.camera_preview.scale",
    "vision.camera_preview.max_fps", "vision.camera_preview.show_face_box",
    "vision.camera_preview.show_expression", "vision.camera_preview.show_confidence",
})
IMU_MOTION_RELOADABLE = frozenset({
    "sensors.imu.motion.tilt_exit_threshold_m_s2",
    "sensors.imu.motion.lateral_axis",
    "sensors.imu.motion.forward_axis",
    "sensors.imu.motion.movement_threshold_m_s2", "sensors.imu.motion.tilt_threshold_m_s2",
    "sensors.imu.motion.shake_threshold_deg_s", "sensors.imu.motion.impact_threshold_m_s2",
    "sensors.imu.motion.confirmation_seconds", "sensors.imu.motion.cooldown_seconds",
})
IMU_BEHAVIOR_RELOADABLE = frozenset({
    "behavior.imu_reaction_strength", "behavior.imu_tilt_gaze_strength",
    "behavior.imu_tilt_eye_asymmetry_strength",
    "behavior.imu_shake_reaction_strength", "behavior.imu_impact_reaction_strength",
    "behavior.imu_shake_reaction_duration_seconds", "behavior.imu_impact_reaction_duration_seconds",
    "behavior.imu_reaction_cooldown_seconds",
})
LED_RING_RELOADABLE = frozenset({
    "led_ring.enabled", "led_ring.brightness", "led_ring.base_color", "led_ring.follow_visual_state",
    "led_ring.update_rate_hz", "led_ring.imu_reactions_enabled", "led_ring.directional_strength",
    "led_ring.directional_sector_size", "led_ring.shake_strength", "led_ring.impact_strength",
    "led_ring.imu_animation_color",
    "led_ring.directional_animation_speed", "led_ring.bottom_led_index",
    "led_ring.forward_led_index", "led_ring.clockwise",
})
ENVIRONMENTAL_BEHAVIOR_RELOADABLE = frozenset({
    "behavior.environmental.enabled", "behavior.environmental.cold_enter_temperature",
    "behavior.environmental.cold_exit_temperature", "behavior.environmental.warm_enter_temperature",
    "behavior.environmental.warm_exit_temperature", "behavior.environmental.air_quality_warning_eco2",
    "behavior.environmental.air_quality_warning_tvoc", "behavior.environmental.air_quality_bad_eco2",
    "behavior.environmental.air_quality_bad_tvoc", "behavior.environmental.confirmation_seconds",
    "behavior.environmental.recovery_seconds",
})
RELOADABLE = frozenset({"logging.level", "display.iris_color", "display.base_visual_source", "display.environment_overlays_enabled", *PREVIEW_RELOADABLE, *IMU_MOTION_RELOADABLE,
                        *IMU_BEHAVIOR_RELOADABLE, *LED_RING_RELOADABLE, *ENVIRONMENTAL_BEHAVIOR_RELOADABLE})


def changed_fields(active, saved, prefix=""):
    result = []
    for key, value in saved.items():
        name = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            result.extend(changed_fields(active[key], value, name))
        elif active[key] != value:
            result.append(name)
    return result


def apply_log_level(level):
    logging.getLogger().setLevel(level)
    # Changing PHOS verbosity must never enable AWS SDK request/credential logs.
    logging.getLogger("boto3").setLevel(logging.WARNING)
    logging.getLogger("botocore").setLevel(logging.WARNING)


class LifecycleService:
    def __init__(self, config_repository, active_config, *, restart_supported=False,
                 log_level_setter=apply_log_level, clock=time.monotonic):
        self.config_repository = (config_repository if isinstance(config_repository, ConfigRepository)
                                  else ConfigRepository(config_repository))
        self.path = self.config_repository.active_path
        self.active = active_config.to_dict()
        self.loaded_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.restart_supported = restart_supported
        self.restart_at = None
        self._set_log_level = log_level_setter
        self._apply_appearance = None
        self._apply_base_visual_source = None
        self._apply_environment_overlays = None
        self._apply_camera_preview = None
        self._apply_imu_motion = None
        self._apply_imu_behavior = None
        self._apply_led_ring = None
        self._apply_environmental_behavior = None
        self._sensor_status = None
        self._application_status = None
        self._application_service = None
        self._clock = clock
        self._lock = RLock()

    def register_appearance_applier(self, applier):
        """Register the running application service's renderer update boundary."""
        with self._lock:
            self._apply_appearance = applier

    def register_base_visual_source_applier(self, applier):
        with self._lock:
            self._apply_base_visual_source = applier

    def register_environment_overlays_applier(self, applier):
        with self._lock: self._apply_environment_overlays = applier

    def register_camera_preview_applier(self, applier):
        """Register runtime service for applying validated preview settings."""
        with self._lock:
            self._apply_camera_preview = applier

    def register_imu_motion_applier(self, applier):
        with self._lock:
            self._apply_imu_motion = applier

    def register_imu_behavior_applier(self, applier):
        with self._lock:
            self._apply_imu_behavior = applier

    def register_led_ring_applier(self, applier):
        with self._lock:
            self._apply_led_ring = applier

    def register_environmental_behavior_applier(self, applier):
        with self._lock:
            self._apply_environmental_behavior = applier

    @property
    def restart_due(self):
        with self._lock:
            return self.restart_at is not None and self._clock() >= self.restart_at

    def register_sensor_status(self, supplier):
        """Register a nonblocking snapshot supplier, never a hardware callback."""
        with self._lock:
            self._sensor_status = supplier

    def register_application_status(self, supplier):
        """Register the runtime's provider-neutral application status model."""
        with self._lock:
            self._application_status = supplier

    def register_application_service(self, service):
        """Register the semantic command/status boundary for remote adapters."""
        with self._lock:
            self._application_service = service

    def _snapshot(self, saved):
        changed = changed_fields(self.active, saved)
        return {"active": deepcopy(self.active), "config_path": str(self.path),
                "loaded_at": self.loaded_at, "changed": changed,
                "reloadable": sorted(set(changed) & RELOADABLE),
                "restart_required": sorted(set(changed) - RELOADABLE),
                "restart_supported": self.restart_supported,
                "restart_requested": self.restart_at is not None,
                "sensors": self._sensor_status() if self._sensor_status is not None else {},
                "runtime": self._application_status() if self._application_status is not None else {}}

    def execute(self, operation):
        """Fixed allowlist; no command/path/config payload is accepted from adapters."""
        with self._lock:
            if isinstance(operation, dict):
                return self._execute_application(operation)
            if not isinstance(operation, str) or operation not in {"status", "reload", "restart"}:
                return {"ok": False, "error": "Unsupported lifecycle operation."}
            try:
                config = self.config_repository.load()
                saved = config.to_dict()
            except (ConfigurationError, OSError):
                # Do not echo arbitrary config contents to adapters or logs.
                return {"ok": False, "error": "Saved configuration is invalid or unavailable. No settings were applied; repair it before reload or restart."}
            if operation == "restart":
                if not self.restart_supported:
                    return {"ok": False, "error": "Restart PHOS requires the documented user systemd service. For a terminal launch, stop PHOS and run the startup command again."}
                if self.restart_at is None:
                    # Let the worker deliver its HTTP acknowledgement before exit.
                    self.restart_at = self._clock() + 1.0
                return {"ok": True, **self._snapshot(saved), "applied": []}
            applied = []
            if operation == "reload":
                if self.restart_at is not None:
                    return {"ok": False, "error": "Restart is already requested. Wait for PHOS to start again."}
                if (self.active["display"]["iris_color"] != saved["display"]["iris_color"]
                        and self._apply_appearance is None):
                    return {"ok": False, "error": "Runtime appearance service is not ready. No settings were applied; retry reload shortly."}
                if (self.active["display"]["base_visual_source"] != saved["display"]["base_visual_source"]
                        and self._apply_base_visual_source is None):
                    return {"ok": False, "error": "Runtime visual source service is not ready. No settings were applied; retry reload shortly."}
                if (self.active["display"]["environment_overlays_enabled"] != saved["display"]["environment_overlays_enabled"] and self._apply_environment_overlays is None):
                    return {"ok": False, "error": "Runtime environmental overlay service is not ready. No settings were applied; retry reload shortly."}
                preview_paths = changed_fields(self.active["vision"]["camera_preview"],
                    saved["vision"]["camera_preview"], "vision.camera_preview")
                preview_changed = bool(preview_paths)
                if preview_changed and self._apply_camera_preview is None:
                    return {"ok": False, "error": "Runtime camera preview service is not ready. No settings were applied; retry reload shortly."}
                motion_paths = changed_fields(self.active["sensors"]["imu"]["motion"],
                    saved["sensors"]["imu"]["motion"], "sensors.imu.motion")
                if motion_paths and self._apply_imu_motion is None:
                    return {"ok": False, "error": "Runtime IMU motion service is not ready. No settings were applied; retry reload shortly."}
                behavior_paths = [path for path in changed_fields(self.active["behavior"], saved["behavior"], "behavior")
                                  if path in IMU_BEHAVIOR_RELOADABLE]
                if behavior_paths and self._apply_imu_behavior is None:
                    return {"ok": False, "error": "Runtime IMU behavior service is not ready. No settings were applied; retry reload shortly."}
                led_paths = [path for path in changed_fields(self.active["led_ring"], saved["led_ring"], "led_ring")
                             if path in LED_RING_RELOADABLE]
                if led_paths and self._apply_led_ring is None:
                    return {"ok": False, "error": "Runtime LED ring service is not ready. No settings were applied; retry reload shortly."}
                environmental_paths = changed_fields(self.active["behavior"]["environmental"],
                    saved["behavior"]["environmental"], "behavior.environmental")
                if environmental_paths and self._apply_environmental_behavior is None:
                    return {"ok": False, "error": "Runtime environmental behavior service is not ready. No settings were applied; retry reload shortly."}
                if self.active["display"]["iris_color"] != saved["display"]["iris_color"]:
                    try:
                        self._apply_appearance(config)
                    except Exception:
                        return {"ok": False, "error": "The running display could not accept the appearance update. No active configuration was recorded; retry reload or restart PHOS."}
                    self.active["display"]["iris_color"] = saved["display"]["iris_color"]
                    applied.append("display.iris_color")
                if self.active["display"]["base_visual_source"] != saved["display"]["base_visual_source"]:
                    self._apply_base_visual_source(config)
                    self.active["display"]["base_visual_source"] = saved["display"]["base_visual_source"]
                    applied.append("display.base_visual_source")
                if self.active["display"]["environment_overlays_enabled"] != saved["display"]["environment_overlays_enabled"]:
                    self._apply_environment_overlays(config)
                    self.active["display"]["environment_overlays_enabled"] = saved["display"]["environment_overlays_enabled"]
                    applied.append("display.environment_overlays_enabled")
                if preview_changed:
                    try:
                        self._apply_camera_preview(config)
                    except Exception:
                        logging.getLogger(__name__).exception("Camera preview reload failed")
                        return {"ok": False, **self._snapshot(saved), "applied": applied,
                                "error": "Camera preview could not be applied. Earlier appearance changes may already be active. Check PHOS logs, then retry reload or restart."}
                    self.active["vision"]["camera_preview"] = deepcopy(saved["vision"]["camera_preview"])
                    applied.extend(preview_paths)
                if motion_paths:
                    try:
                        self._apply_imu_motion(config)
                    except Exception:
                        logging.getLogger(__name__).exception("IMU motion reload failed")
                        return {"ok": False, **self._snapshot(saved), "applied": applied,
                                "error": "IMU motion settings could not be applied. No IMU hardware was reinitialized; retry reload or restart PHOS."}
                    self.active["sensors"]["imu"]["motion"] = deepcopy(saved["sensors"]["imu"]["motion"])
                    applied.extend(motion_paths)
                if behavior_paths:
                    self._apply_imu_behavior(config)
                    for path in behavior_paths:
                        self.active["behavior"][path.rsplit(".", 1)[1]] = saved["behavior"][path.rsplit(".", 1)[1]]
                    applied.extend(behavior_paths)
                if led_paths:
                    try:
                        self._apply_led_ring(config)
                    except Exception:
                        logging.getLogger(__name__).exception("LED ring reload failed")
                        return {"ok": False, **self._snapshot(saved), "applied": applied,
                                "error": "LED ring settings could not be applied. PHOS continues without LED output; retry reload or restart."}
                    for path in led_paths:
                        self.active["led_ring"][path.rsplit(".", 1)[1]] = saved["led_ring"][path.rsplit(".", 1)[1]]
                    applied.extend(led_paths)
                if environmental_paths:
                    self._apply_environmental_behavior(config)
                    self.active["behavior"]["environmental"] = deepcopy(saved["behavior"]["environmental"])
                    applied.extend(environmental_paths)
                if self.active["logging"]["level"] != saved["logging"]["level"]:
                    self._set_log_level(saved["logging"]["level"])
                    self.active["logging"]["level"] = saved["logging"]["level"]
                    applied.append("logging.level")
                self.loaded_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            return {"ok": True, **self._snapshot(saved), "applied": applied}

    def _execute_application(self, request):
        name, payload = request.get("operation"), request.get("payload")
        handlers = {
            "application.status": lambda: self._application_service.status(),
            "application.state": lambda: self._application_service.robot_state(),
            "application.environment": lambda: self._application_service.environment(),
            "application.motion": lambda: self._application_service.motion(),
            "application.presence": lambda: self._application_service.presence(),
            "application.attention": lambda: self._application_service.attention(),
            "application.voice": lambda: self._application_service.voice(),
            "application.start_listening": lambda: self._application_service.start_listening(),
            "application.stop_listening": lambda: self._application_service.stop_listening(),
            "application.cancel_voice_session": lambda: self._application_service.cancel_voice_session(),
            "application.speak": lambda: self._application_service.speak(payload.get("text")),
            "application.observed_expression": lambda: self._application_service.observed_expression(),
            "application.health": lambda: self._application_service.health(),
            "application.capabilities": lambda: self._application_service.capabilities(),
            "application.overlay": lambda: self._application_service.overlay(),
            "application.set_overlay": lambda: self._application_service.set_overlay(payload),
            "application.clear_overlay": lambda: self._application_service.clear_overlay(),
            "application.config": lambda: self._application_service.config(),
            "application.update_config": lambda: self._application_service.update_config(payload),
            "application.expression": lambda: self._application_service.set_expression(payload.get("expression")),
            "application.set_state": lambda: self._application_service.set_state(payload.get("state")),
            "application.visual_source": lambda: self._application_service.set_visual_source(payload.get("source")),
        }
        if name not in handlers or not isinstance(payload, (dict, type(None))):
            return {"ok": False, "error": "Unsupported application operation."}
        if self._application_service is None:
            return {"ok": False, "error": "Application service is unavailable.", "status": 503}
        try:
            return {"ok": True, "result": handlers[name]()}
        except Exception as error:
            document = getattr(error, "document", None)
            if document is not None:
                return {"ok": False, **document(), "status": getattr(error, "status", 400)}
            logging.getLogger(__name__).exception("Application service failed")
            return {"ok": False, "error": "PHOS could not process the request.", "status": 500}
