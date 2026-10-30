"""Semantic PHOS commands and read models.

This module deliberately knows only Core, BehaviorEngine, lifecycle and the
runtime's read-only snapshot boundary.  HTTP, WebSocket, voice and MCP adapters
must use this service instead of reaching into device providers or renderers.
"""
from __future__ import annotations

import asyncio
from enum import Enum
import logging
import math
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timezone
import platform
import time
import os
import tempfile
import re
from threading import RLock
from typing import Any, Callable
from pathlib import Path

from robot.core import Event, RobotState
from robot.core.state import InvalidStateTransition, allowed_next_states, transition_graph, writable_states
from robot.semantics import VisualSource
from robot.core.environmental import AirQualityOverlay, EnvironmentalState, TemperatureOverlay
from robot.core.runtime import STATE_CHANGED
from robot.core.presence import PRESENCE_CHANGED, PERSON_ENTERED, PERSON_LEFT
from robot.core.attention import (ATTENTION_CHANGED, ATTENTION_TARGET_ACQUIRED,
                                  ATTENTION_TARGET_CHANGED, ATTENTION_TARGET_LOST)
from robot.vision.pipeline import OBSERVED_EXPRESSION_CHANGED
from robot.core.expression_reaction import (EXPRESSION_REACTION_STARTED, EXPRESSION_REACTION_COMPLETED,
                                            EXPRESSION_REACTION_SUPPRESSED)
from robot.core.touch import TOUCH_EVENT_NAMES
from robot.voice.session import (VOICE_ERROR, VOICE_LISTENING_STARTED, VOICE_SESSION_CANCELLED, VOICE_SPEECH_ENDED,
                                 VOICE_SPEECH_STARTED, VOICE_TRANSCRIPTION_COMPLETED, VOICE_TRANSCRIPTION_STARTED, VOICE_EMPTY_UTTERANCE)
from robot.interaction import (INTERACTION_INTENT_RESOLVED, INTERACTION_INTENT_UNRESOLVED,
                               INTERACTION_RESOLUTION_FAILED)
from robot.voice.capture import CaptureDeviceUnavailable, UnsupportedCaptureRate
from robot.voice.normalization import SpeechTextNormalizer
from robot.voice.tts import TTSBusyError, TTSConfigurationError, TTSProviderError, TTSError
from robot.voice import AudioOutputProviderFactory, STTProviderFactory, TTSProviderFactory
from robot.config import ConfigurationError, RuntimeConfig
from robot.admin_access import RecoveryState
from robot.services.physical_context import SemanticPhysicalContextService
from robot.motion import MotionState
from robot.ui.state import FaceExpression
from robot.ui.skins import (SkinRepository, appearance_snapshot, apply_appearance_snapshot,
                            appearance_semantically_matches, available_appearance_snapshot,
                            validate_appearance_snapshot)

logger = logging.getLogger(__name__)

_MAX_BACKGROUND_DIMENSIONS = (1920, 1080)


def _header_image_details(content: bytes) -> tuple[str, int, int] | None:
    """Return a conservatively parsed PNG/JPEG header when OpenCV is absent."""
    if content.startswith(b"\x89PNG\r\n\x1a\n") and len(content) >= 24 and content[12:16] == b"IHDR":
        width, height = int.from_bytes(content[16:20], "big"), int.from_bytes(content[20:24], "big")
        return (".png", width, height) if width and height else None
    if not content.startswith(b"\xff\xd8"):
        return None
    offset = 2
    while offset + 9 <= len(content):
        if content[offset] != 0xFF:
            return None
        while offset < len(content) and content[offset] == 0xFF:
            offset += 1
        marker = content[offset]
        offset += 1
        if marker in {0xD8, 0xD9}:
            continue
        if offset + 2 > len(content):
            return None
        length = int.from_bytes(content[offset:offset + 2], "big")
        if length < 2 or offset + length > len(content):
            return None
        if marker in {*range(0xC0, 0xC4), *range(0xC5, 0xC8), *range(0xC9, 0xCC), *range(0xCD, 0xD0)}:
            height, width = int.from_bytes(content[offset + 3:offset + 5], "big"), int.from_bytes(content[offset + 5:offset + 7], "big")
            return (".jpg", width, height) if width and height else None
        offset += length
    return None

_speech_text_normalizer = SpeechTextNormalizer()

STATE_COMMAND_TIMEOUT_SECONDS = 2.0


# These are enum members, not duplicated wire values. ERROR is lifecycle-only;
# the current visual-event command deliberately supports only reactions that the
# BehaviorEngine maps from a semantic expression event.
WRITABLE_ROBOT_STATES = writable_states()
WRITABLE_EXPRESSIONS = (FaceExpression.NEUTRAL, FaceExpression.HAPPY, FaceExpression.CURIOUS,
                        FaceExpression.SURPRISED, FaceExpression.SAD, FaceExpression.ANGRY,
                        FaceExpression.FOCUSED, FaceExpression.TIRED, FaceExpression.CONFUSED,
                        FaceExpression.SCARED, FaceExpression.SKEPTICAL)
WRITABLE_VISUAL_SOURCES = tuple(VisualSource)


@dataclass(frozen=True)
class CommandDefinition:
    """The canonical validation and transport contract for one semantic command."""

    method: str
    endpoint: str
    field: str
    allowed_members: tuple[Enum, ...]

    @property
    def allowed_values(self) -> list[str]:
        return [item.value for item in self.allowed_members]


# This registry is the sole application-level rule for semantic command input.
# The web capabilities document is projected from it and command handlers
# validate against it; adapters therefore do not maintain parallel enum lists.
COMMANDS = {
    "set_robot_state": CommandDefinition("POST", "/api/v1/state", "state", WRITABLE_ROBOT_STATES),
    "set_expression": CommandDefinition("POST", "/api/v1/expression", "expression", WRITABLE_EXPRESSIONS),
    "set_visual_source": CommandDefinition("POST", "/api/v1/visual-source", "source", WRITABLE_VISUAL_SOURCES),
}

OVERLAY_COMMANDS = {
    "set_overlay": {"method": "POST", "endpoint": "/api/v1/overlay", "fields": {
        "temperature": {"allowed_values": [item.value for item in TemperatureOverlay]},
        "air_quality": {"allowed_values": [item.value for item in AirQualityOverlay]},
        "duration_ms": {"type": "integer", "optional": True},
    }},
    "clear_overlay": {"method": "DELETE", "endpoint": "/api/v1/overlay"},
}

# Canonical semantic LED signal vocabulary. Adapters must reuse this rather
# than inferring hardware animations or maintaining a second enum.
USER_SIGNAL_INTENTS = frozenset({"success", "warning", "error", "attention"})


class ApplicationError(Exception):
    """A stable adapter-safe error."""
    def __init__(self, code: str, message: str, details: dict | None = None, status: int = 400):
        super().__init__(message)
        self.code, self.message, self.details, self.status = code, message, details or {}, status

    def document(self) -> dict:
        return {"error": {"code": self.code, "message": self.message, "details": self.details}}


@dataclass(frozen=True)
class SpeechConfigurationResult:
    configured_provider: str | None
    active_provider: str | None
    enabled: bool
    available: bool
    def document(self): return asdict(self)


class RemoteApplicationService:
    """Web-worker proxy for the parent-owned application service."""
    def __init__(self, lifecycle):
        self._lifecycle = lifecycle
        self._listeners, self._last = [], {}

    def _call(self, name, payload=None):
        response = self._lifecycle.execute(f"application.{name}", payload)
        if response.get("ok"):
            # Lifecycle transport can carry a provider snapshot containing a
            # non-finite float.  JSON has no representation for it; expose it
            # as unavailable rather than emitting invalid browser JSON.
            return PhosApplicationService._plain(response["result"])
        error = response.get("error", {})
        if name in {"presence", "attention"}:
            logger.debug("%s API: available=%s service=%s state=%s reason=%s", name.upper(), False,
                         type(self._lifecycle).__name__, None, error)
        if isinstance(error, dict):
            raise ApplicationError(error.get("code", "runtime_error"), error.get("message", "PHOS request failed."),
                                   error.get("details", {}), response.get("status", 400))
        raise ApplicationError("service_unavailable", error, status=response.get("status", 503))

    def status(self): return self._call("status")
    def robot_state(self): return self._call("state")
    def environment(self): return self._call("environment")
    def motion(self): return self._call("motion")
    def presence(self): return self._call("presence")
    def attention(self): return self._call("attention")
    def voice(self): return self._call("voice")
    def start_listening(self): return self._call("start_listening")
    def stop_listening(self): return self._call("stop_listening")
    def cancel_voice_session(self): return self._call("cancel_voice_session")
    def speak(self, text): return self._call("speak", {"text": text})
    def observed_expression(self): return self._call("observed_expression")
    def health(self): return self._call("health")
    def capabilities(self): return self._call("capabilities")
    def config(self): return self._call("config")
    def update_config(self, value): return self._call("update_config", value)
    def configure_stt(self, value): return self._call("configure_stt", value)
    def configure_tts(self, value): return self._call("configure_tts", value)
    def configure_audio_output(self, value): return self._call("configure_audio_output", value)
    def configure_backgrounds(self, value): return self._call("configure_backgrounds", value)
    def configure_eyes(self, value): return self._call("configure_eyes", value)
    def mcp_settings(self): return self._call("mcp_settings")
    def configure_mcp(self, value): return self._call("configure_mcp", value)
    def controller_status(self): return self._call("controller_status")
    def controller_serial_ports(self): return self._call("controller_serial_ports")
    def controller_recent_messages(self): return self._call("controller_recent_messages")
    def clear_controller_recent_messages(self): return self._call("clear_controller_recent_messages")
    def request_admin_password_recovery(self): return self._call("request_admin_password_recovery")
    def verify_admin_recovery_code(self, value): return self._call("verify_admin_recovery_code", {"code": value})
    def clear_admin_overlay(self): return self._call("clear_admin_overlay")
    def get_admin_network_info(self): return self._call("get_admin_network_info")
    def show_admin_access_overlay(self): return self._call("show_admin_access_overlay")
    def configure_eye_safe_layer(self, value): return self._call("configure_eye_safe_layer", value)
    def skins(self): return self._call("skins")
    def create_skin(self, value): return self._call("create_skin", value)
    def delete_skin(self, value): return self._call("delete_skin", {"id": value})
    def preview_skin(self, value): return self._call("preview_skin", {"id": value})
    def apply_skin(self, value): return self._call("apply_skin", {"id": value})
    def import_skin(self, value): return self._call("import_skin", value)
    def export_skin(self, value): return self._call("export_skin", {"id": value})
    def appearance_preview(self, value): return self._call("appearance_preview", value)
    def configure_state_effects(self, value): return self._call("configure_state_effects", value)
    def background_assets(self): return self._call("background_assets")
    def upload_background(self, value): return self._call("upload_background", value)
    def delete_background(self, value): return self._call("delete_background", {"name": value})
    def set_expression(self, value): return self._call("expression", {"expression": value})
    def set_state(self, value):
        started_at = time.monotonic()
        logger.debug("PHOS COMMAND: state target=%s request_received", value)
        logger.debug("PHOS COMMAND: state target=%s submitted_ms=0.0", value)
        result = self._call("set_state", {"state": value})
        logger.debug("PHOS COMMAND: state target=%s response_ms=%.1f", value,
                    (time.monotonic() - started_at) * 1000)
        return result
    def set_visual_source(self, value): return self._call("visual_source", {"source": value})
    def signal_user(self, value): return self._call("signal_user", {"signal": value})
    def overlay(self): return self._call("overlay")
    def set_overlay(self, value): return self._call("set_overlay", value)
    def clear_overlay(self): return self._call("clear_overlay")
    def subscribe(self, listener):
        self._listeners.append(listener)
        def unsubscribe():
            if listener in self._listeners:
                self._listeners.remove(listener)
        return unsubscribe

    def emit_snapshot_changes(self):
        """Bridge the parent snapshot into bounded events for a WSGI worker.

        The process channel cannot carry a permanently subscribed callback. The
        SSE endpoint invokes this at its 15-second keepalive boundary, so a
        connected browser gets only changed semantic snapshots, never raw
        provider readings or a second high-rate polling loop.
        """
        try:
            current = self.status()
            payloads = {
                "robot_state_changed": current.get("robot", {}),
                "visual_state_changed": current.get("visual", {}),
                "environmental_state_changed": current.get("environment", {}),
                "sensors_changed": current.get("sensors", {}),
                "motion_state_changed": current.get("motion", {}),
                "health_changed": current.get("health", {}),
                "overlay_changed": current.get("overlay", {}),
                "presence_changed": current.get("presence", {}),
                "attention_changed": current.get("attention", {}),
                "observed_expression_changed": current.get("observed_expression", {}),
                "expression_reaction_changed": current.get("expression_reaction", {}),
                "voice_changed": current.get("voice", {}),
            }
        except ApplicationError:
            return
        for event_type, payload in payloads.items():
            frozen = repr(payload)
            if self._last.get(event_type) == frozen:
                continue
            self._last[event_type] = frozen
            if event_type == "observed_expression_changed":
                logger.debug("EXPR SSE SNAPSHOT: available=%s label=%s confidence=%s",
                            payload.get("available"), payload.get("label"), payload.get("confidence"))
            if event_type == "expression_reaction_changed":
                logger.debug("SSE OUT: type=%s payload=%s", event_type, payload)
            event = {"type": event_type,
                     "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                     "payload": payload}
            for listener in tuple(self._listeners):
                listener(event)
        # A separate WSGI worker receives snapshots over the bounded lifecycle
        # channel.  Convert only a newly observed completed gesture into the
        # same edge event emitted by an in-process application service.
        touch = current.get("touch", {})
        touch_key = touch.get("last_event_at")
        kind = touch.get("last_event")
        if touch_key is not None and kind in TOUCH_EVENT_NAMES and self._last.get("touch_gesture") != touch_key:
            self._last["touch_gesture"] = touch_key
            payload = {key: touch.get(key) for key in ("x", "y", "normalized_x", "normalized_y", "duration_ms")}
            payload["kind"] = kind
            event = {"type": TOUCH_EVENT_NAMES[kind],
                     "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds"), "payload": payload}
            for listener in tuple(self._listeners):
                listener(event)


class PhosApplicationService:
    """Small provider-neutral command boundary for a running PHOS instance."""
    def __init__(self, runtime, *, lifecycle=None, clock=time.monotonic):
        self._runtime = runtime
        self._core = runtime.core
        self._behavior = runtime._behavior_engine  # composition-owned semantic collaborator
        self._lifecycle = lifecycle
        self._mcp_manager = None
        self._admin_recovery = RecoveryState(clock=clock)
        self._physical_context = SemanticPhysicalContextService()
        self._clock = clock
        self._started = clock()
        self._listeners: list[Callable[[dict], None]] = []
        self._last: dict[str, object] = {}
        self._lock = RLock()
        self._unsubscribers = [
            self._core.events.subscribe(STATE_CHANGED, lambda event: self._emit("robot_state_changed", event.data)),
            self._core.events.subscribe(PRESENCE_CHANGED, lambda event: self._emit("presence_changed", event.data)),
            self._core.events.subscribe(PERSON_ENTERED, lambda event: self._emit("person_entered", event.data)),
            self._core.events.subscribe(PERSON_LEFT, lambda event: self._emit("person_left", event.data)),
            self._core.events.subscribe(ATTENTION_CHANGED, lambda event: self._emit("attention_changed", event.data)),
            self._core.events.subscribe(ATTENTION_TARGET_ACQUIRED, lambda event: self._emit("attention_target_acquired", event.data)),
            self._core.events.subscribe(ATTENTION_TARGET_CHANGED, lambda event: self._emit("attention_target_changed", event.data)),
            self._core.events.subscribe(ATTENTION_TARGET_LOST, lambda event: self._emit("attention_target_lost", event.data)),
            self._core.events.subscribe(OBSERVED_EXPRESSION_CHANGED, self._on_observed_expression_changed),
            self._core.events.subscribe(EXPRESSION_REACTION_STARTED, lambda event: self._emit("expression_reaction_changed", self.expression_reaction())),
            self._core.events.subscribe(EXPRESSION_REACTION_COMPLETED, lambda event: self._emit("expression_reaction_changed", self.expression_reaction())),
            self._core.events.subscribe(EXPRESSION_REACTION_SUPPRESSED, lambda event: self._emit("expression_reaction_changed", self.expression_reaction())),
            *[self._core.events.subscribe(name, lambda event, name=name: self._emit(name, dict(event.data), force=True))
              for name in TOUCH_EVENT_NAMES.values()],
            *[self._core.events.subscribe(name, lambda event, name=name: self._emit(name, dict(event.data), force=True))
              for name in (VOICE_LISTENING_STARTED, VOICE_SPEECH_STARTED, VOICE_SPEECH_ENDED, VOICE_TRANSCRIPTION_STARTED, VOICE_TRANSCRIPTION_COMPLETED, VOICE_EMPTY_UTTERANCE, VOICE_SESSION_CANCELLED, VOICE_ERROR)],
            *[self._core.events.subscribe(name, lambda event, name=name: self._emit(name, dict(event.data), force=True))
              for name in (INTERACTION_INTENT_RESOLVED, INTERACTION_INTENT_UNRESOLVED, INTERACTION_RESOLUTION_FAILED)],
        ]

    def close(self):
        for unsubscribe in self._unsubscribers:
            unsubscribe()
        self._unsubscribers = []

    def subscribe(self, listener: Callable[[dict], None]) -> Callable[[], None]:
        with self._lock:
            self._listeners.append(listener)
        def unsubscribe():
            with self._lock:
                if listener in self._listeners:
                    self._listeners.remove(listener)
        return unsubscribe

    def _emit(self, event_type: str, payload: dict, *, force: bool = False):
        # State-change events can arrive through more than one runtime path;
        # suppress identical consecutive payloads before crossing adapters.
        frozen = repr(payload)
        with self._lock:
            if not force and self._last.get(event_type) == frozen:
                return
            self._last[event_type] = frozen
            listeners = tuple(self._listeners)
        event = {"type": event_type, "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                 "payload": payload}
        for listener in listeners:
            try:
                listener(event)
            except Exception:
                # A disconnected remote client cannot affect robot behavior.
                continue

    def _on_observed_expression_changed(self, event) -> None:
        """Trace the exact runtime snapshot forwarded to remote adapters."""
        self._emit("observed_expression_changed", event.data)
        snapshot = self.observed_expression()
        logger.debug("EXPR SNAPSHOT: state_id=%s available=%s label=%s confidence=%s",
                    id(getattr(getattr(self._runtime, "_vision_pipeline", None), "observed_expression", None)),
                    snapshot.get("available"), snapshot.get("label"), snapshot.get("confidence"))

    @staticmethod
    def _plain(value: Any):
        if isinstance(value, float) and not math.isfinite(value):
            return None
        if is_dataclass(value):
            return {key: PhosApplicationService._plain(item) for key, item in asdict(value).items()}
        if hasattr(value, "value"):
            return value.value
        if isinstance(value, dict):
            return {str(key): PhosApplicationService._plain(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [PhosApplicationService._plain(item) for item in value]
        return value

    def visual_state(self) -> dict:
        state = self._behavior.face_state
        return self._plain(state)

    def robot_state(self) -> dict:
        current = self._core.state
        return {"state": current.value, "current": current.value, "running": self._core.is_running,
                "writable": [item.value for item in WRITABLE_ROBOT_STATES],
                "allowed_next": [item.value for item in allowed_next_states(current)]}

    def sensors(self) -> dict:
        return self._plain(self._runtime.sensor_status())

    def environment(self) -> dict:
        return self.sensors().get("environmental", {"status": "unavailable", "available": False})

    def motion(self) -> dict:
        return self.sensors().get("imu", {"status": "unavailable", "available": False})

    def presence(self) -> dict:
        interpreter = getattr(self._runtime, "_presence_interpreter", None)
        state = getattr(interpreter, "state", None)
        return self._plain(state) if state is not None else {"state": "no_one", "people_count": 0,
                                                             "primary_candidate_id": None, "visible_since": None,
                                                             "last_seen": None, "confidence": None}

    def attention(self) -> dict:
        manager = getattr(self._runtime, "_attention_manager", None)
        state = getattr(manager, "state", None)
        if state is None:
            return {"state": "idle", "target": None}
        plain = self._plain(state)
        return {"state": plain["state"], "target": {"id": plain["target_id"], "x": plain["target_x"],
                "y": plain["target_y"], "confidence": plain["confidence"]}, "acquired_at": plain["acquired_at"],
                "last_seen": plain["last_seen"]}

    def get_physical_context(self) -> dict:
        """Compact perception context; distinct from operational status/capabilities."""
        return self._physical_context.build(robot=self.robot_state(), sensors=self.sensors(),
                                            presence=self.presence(), attention=self.attention(),
                                            observed_expression=self.observed_expression()).document()

    def touch(self) -> dict:
        reader = getattr(self._runtime, "touch_status", None)
        if reader is not None:
            return self._plain(reader())
        return {"enabled": False, "last_event": None, "last_event_at": None,
                "x": None, "y": None, "normalized_x": None, "normalized_y": None,
                "duration_ms": None}

    def voice(self) -> dict:
        reader = getattr(self._runtime, "voice_status", None)
        return self._plain(reader()) if reader is not None else {"enabled": False, "state": "idle", "listening": False, "speech_detected": False, "stt_provider": None, "stt_available": False, "last_transcript": None, "last_confidence": None, "language": None, "last_transcription_at": None, "last_transcription_duration_ms": None, "last_error": None}

    def observed_expression(self) -> dict:
        pipeline = getattr(self._runtime, "_vision_pipeline", None)
        observation = getattr(pipeline, "observed_expression", None)
        if observation is None:
            logger.debug("OBSERVED STATE READER: pipeline=%s state=%s reason=vision_unavailable",
                         id(pipeline) if pipeline is not None else None, None)
            return {"available": False, "label": None, "confidence": None, "provider": None,
                    "model": None, "observed_at": None, "unavailable_reason": "unavailable"}
        logger.debug("OBSERVED STATE READER: pipeline=%s state=%s available=%s label=%s confidence=%s",
                     id(pipeline), id(observation), observation.available, observation.label, observation.confidence)
        return self._plain(observation)

    def expression_reaction(self) -> dict:
        return self._plain(self._behavior.expression_reaction_state())

    def health(self) -> dict:
        snapshots = self.sensors()
        mapping = {"environmental": "environmental", "ccs811": "ccs811", "imu": "mpu6050", "led_ring": "led_ring"}
        subsystems = {}
        for name, source in mapping.items():
            item = snapshots.get(source)
            raw = item.get("status", "unavailable") if item else "unavailable"
            state = {"available": "ok", "warming_up": "warming_up", "stale": "stale"}.get(raw, "unavailable")
            subsystems[name] = {"state": state, "detail": raw}
        subsystems["runtime"] = {"state": "ok" if self._core.is_running else "unavailable"}
        return {"uptime_seconds": max(0.0, self._clock() - self._started), "python": platform.python_version(),
                "subsystems": subsystems}

    def capabilities(self) -> dict:
        """Operation-oriented semantic contract derived from domain validation."""
        return {
            # This is the stable writable vocabulary. Current-state transition
            # validation is separate, and observable ERROR remains absent.
            "commands": {**{name: _command(definition) for name, definition in COMMANDS.items()}, **OVERLAY_COMMANDS},
            "observable_states": {
                "robot_state": [item.value for item in RobotState],
                "motion_state": [item.value for item in MotionState],
                "environmental_state": [item.value for item in EnvironmentalState],
                "environmental_overlays": {
                    "temperature": [item.value for item in TemperatureOverlay],
                    "air_quality": [item.value for item in AirQualityOverlay],
                },
            },
            "features": {"vision": getattr(self._runtime, "_vision_pipeline", None) is not None,
                         "presence": getattr(self._runtime, "_presence_interpreter", None) is not None,
                         "attention": getattr(self._runtime, "_attention_manager", None) is not None},
        }

    def overlay(self) -> dict:
        environmental, override, resolved = self._behavior.overlay_state()
        return {
            "environmental": self._plain(environmental),
            "override": {"active": override is not None,
                         "temperature": None if override is None else override.temperature,
                         "air_quality": None if override is None else override.air_quality,
                         "expires_at": None if override is None else override.expires_at_iso},
            "resolved": self._plain(resolved),
        }

    def set_overlay(self, value: dict) -> dict:
        if not isinstance(value, dict):
            raise ApplicationError("invalid_overlay", "Overlay override must be a JSON object.")
        allowed_keys = {"temperature", "air_quality", "duration_ms"}
        unknown = set(value) - allowed_keys
        if unknown:
            raise ApplicationError("invalid_overlay", "Unsupported overlay field.", {"fields": sorted(unknown)})
        if "temperature" not in value and "air_quality" not in value:
            raise ApplicationError("invalid_overlay", "At least one overlay field is required.")
        temperature = value.get("temperature")
        air_quality = value.get("air_quality")
        if "temperature" in value and temperature not in OVERLAY_COMMANDS["set_overlay"]["fields"]["temperature"]["allowed_values"]:
            raise ApplicationError("invalid_overlay_temperature", "Unsupported temperature overlay.", {"temperature": temperature})
        if "air_quality" in value and air_quality not in OVERLAY_COMMANDS["set_overlay"]["fields"]["air_quality"]["allowed_values"]:
            raise ApplicationError("invalid_overlay_air_quality", "Unsupported air-quality overlay.", {"air_quality": air_quality})
        duration_ms = value.get("duration_ms")
        if duration_ms is not None and (type(duration_ms) is not int or duration_ms <= 0):
            raise ApplicationError("invalid_overlay_duration", "duration_ms must be a positive integer.", {"duration_ms": duration_ms})
        self._behavior.set_overlay_override(temperature=temperature, air_quality=air_quality, duration_ms=duration_ms)
        result = self.overlay()
        self._emit("overlay_changed", result)
        return self._plain(result)

    def clear_overlay(self) -> dict:
        self._behavior.clear_overlay_override()
        result = self.overlay()
        self._emit("overlay_changed", result)
        return result

    def status(self) -> dict:
        # Runtime supplies this same provider-neutral snapshot to the local
        # lifecycle status service consumed by Web Admin.
        snapshot = getattr(self._runtime, "application_status", None)
        result = snapshot() if snapshot is not None else {
            "robot": self.robot_state(), "visual": self.visual_state(),
            "environment": self.environment(), "motion": self.motion(),
        }
        result["robot"] = self.robot_state()
        result["health"] = self.health()
        result["overlay"] = self.overlay()
        result["presence"] = self.presence()
        result["attention"] = self.attention()
        result["observed_expression"] = self.observed_expression()
        result["expression_reaction"] = self.expression_reaction()
        result["touch"] = self.touch()
        result["voice"] = self.voice()
        interaction = getattr(self._runtime, "interaction_status", None)
        result["interaction"] = self._plain(interaction()) if interaction is not None else {
            "enabled": False, "configured_resolver": None, "active_resolver": None,
            "available": False, "default_language": None, "supported_languages": [],
            "last_resolution": {"status": None, "intent": None, "source_language": None},
            "unavailable_reason": "unconfigured"}
        result.setdefault("sensors", self.sensors())
        self._emit("visual_state_changed", result["visual"])
        return result

    def controller_status(self) -> dict:
        reader = getattr(self._runtime, "controller_status", None)
        return self._plain(reader()) if reader is not None else {"state": "unavailable"}

    def controller_serial_ports(self) -> list[dict]:
        reader = getattr(self._runtime, "controller_serial_ports", None)
        return self._plain(reader()) if reader is not None else []

    def controller_recent_messages(self) -> list[dict]:
        reader = getattr(self._runtime, "controller_recent_messages", None)
        return self._plain(reader()) if reader is not None else []

    def clear_controller_recent_messages(self) -> dict:
        action = getattr(self._runtime, "clear_controller_recent_messages", None)
        if action is not None:
            action()
        return {"cleared": True}

    def emit_snapshot_changes(self) -> None:
        """Publish bounded semantic snapshots when an adapter asks for one.

        This is intentionally pull-driven: it creates no telemetry thread and
        never streams raw samples.  A WebSocket adapter can call it after a
        state-changing command or its low-rate client heartbeat.
        """
        self._emit("environmental_state_changed", self.environment())
        self._emit("sensors_changed", self.sensors())
        self._emit("motion_state_changed", self.motion())
        self._emit("health_changed", self.health())
        self._emit("presence_changed", self.presence())
        self._emit("attention_changed", self.attention())
        self._emit("observed_expression_changed", self.observed_expression())
        self._emit("expression_reaction_changed", self.expression_reaction())
        self._emit("touch_changed", self.touch())
        self._emit("voice_changed", self.voice())

    def _voice_command(self, method, *, cancelled=False, operation):
        loop = self._runtime._loop
        if loop is None: raise ApplicationError("runtime_unavailable", "PHOS runtime is not running.", status=503)
        try:
            return asyncio.run_coroutine_threadsafe(method(cancelled=cancelled) if method.__name__ == "stop_listening" else method(), loop).result(timeout=2)
        except UnsupportedCaptureRate as error:
            logger.exception("VOICE SESSION: failed operation=%s exception_type=%s exception_message=%s",
                             operation, type(error).__name__, error)
            raise ApplicationError("unsupported_capture_rate", "The selected microphone does not support a usable PCM16 capture rate.",
                                   {"requested_rate": error.requested_rate, "device_default_rate": error.device_default_rate}, 503) from error
        except CaptureDeviceUnavailable as error:
            raise ApplicationError("capture_device_unavailable", "The configured microphone is unavailable.",
                                   {"reason": str(error)}, 503) from error
        except Exception as error:
            logger.exception("VOICE SESSION: failed operation=%s exception_type=%s exception_message=%s",
                             operation, type(error).__name__, error)
            raise ApplicationError("voice_unavailable", "Voice session could not be started or stopped.", {"reason": type(error).__name__}, 503) from error

    def start_listening(self): return self._voice_command(self._runtime.start_listening, operation="start")
    def stop_listening(self): return self._voice_command(self._runtime.stop_listening, operation="stop")
    def cancel_voice_session(self): return self._voice_command(self._runtime.stop_listening, cancelled=True, operation="cancel")

    def speak(self, text: str):
        text = self._validated_speech_text(text)
        loop = self._runtime._loop
        if loop is None:
            raise ApplicationError("runtime_unavailable", "PHOS runtime is not running.", status=503)
        try:
            asyncio.run_coroutine_threadsafe(self._runtime.accept_speech(text), loop).result(timeout=2)
            return {"status": "accepted"}
        except TTSBusyError as error:
            raise ApplicationError("tts_busy", "PHOS is already speaking.", status=409) from error
        except TTSProviderError as error:
            # The provider already logged its sanitized stable code.
            raise ApplicationError("tts_unavailable", "Text-to-speech is currently unavailable.", status=503) from error
        except (TTSConfigurationError, TTSError) as error:
            logger.warning("TTS: unavailable exception_type=%s", type(error).__name__)
            raise ApplicationError("tts_unavailable", "Text-to-speech is currently unavailable.", status=503) from error
        except Exception as error:
            logger.exception("TTS: failed exception_type=%s", type(error).__name__)
            raise ApplicationError("internal_error", "PHOS could not process the speech request.", status=500) from error

    async def speak_async(self, text: str):
        """Accept speech without blocking the runtime loop that consumes it."""
        text = self._validated_speech_text(text)
        loop = self._runtime._loop
        if loop is None:
            raise ApplicationError("runtime_unavailable", "PHOS runtime is not running.", status=503)
        if loop is not asyncio.get_running_loop():
            return await asyncio.to_thread(self.speak, text)
        try:
            await self._runtime.accept_speech(text)
            return {"status": "accepted"}
        except TTSBusyError as error:
            raise ApplicationError("tts_busy", "PHOS is already speaking.", status=409) from error
        except TTSProviderError as error:
            raise ApplicationError("tts_unavailable", "Text-to-speech is currently unavailable.", status=503) from error
        except (TTSConfigurationError, TTSError) as error:
            logger.warning("TTS: unavailable exception_type=%s", type(error).__name__)
            raise ApplicationError("tts_unavailable", "Text-to-speech is currently unavailable.", status=503) from error
        except Exception as error:
            logger.exception("TTS: failed exception_type=%s", type(error).__name__)
            raise ApplicationError("internal_error", "PHOS could not process the speech request.", status=500) from error

    @staticmethod
    def _validated_speech_text(text: str) -> str:
        if not isinstance(text, str) or not (text := text.strip()):
            raise ApplicationError("invalid_speech", "Speech text must not be empty.")
        if len(text) > 500:
            raise ApplicationError("invalid_speech", "Speech text exceeds the 500 character limit.")
        return text

    async def speak_interaction_response(self, text: str, language: str, *, intent: str) -> dict:
        """Speak a rendered response through the normal runtime TTS lifecycle.

        Language remains response metadata because the provider-neutral TTS
        contract currently accepts text only.
        """
        text = self._validated_speech_text(text)
        speech_text = _speech_text_normalizer.normalize(text, language)
        try:
            provider = "unknown"
            runtime_status = getattr(self._runtime, "application_status", None)
            if callable(runtime_status):
                speech = runtime_status().get("speech", {})
                provider = speech.get("tts", {}).get("active_provider") or provider
            logger.info("PHOS INTERACTION: response speech started intent=%s language=%s provider=%s",
                        intent, language, provider)
            logger.debug("PHOS TTS NORMALIZATION: language=%s normalizer=%s changed=%s", language,
                         _speech_text_normalizer.normalizer_name(language), str(speech_text != text).lower())
            await self._runtime.speak(speech_text)
            return {"status": "completed", "language": language}
        except TTSBusyError as error:
            raise ApplicationError("tts_busy", "PHOS is already speaking.", status=409) from error
        except TTSProviderError as error:
            raise ApplicationError("tts_unavailable", "Text-to-speech is currently unavailable.", status=503) from error
        except (TTSConfigurationError, TTSError) as error:
            logger.warning("TTS: unavailable exception_type=%s", type(error).__name__)
            raise ApplicationError("tts_unavailable", "Text-to-speech is currently unavailable.", status=503) from error
        except Exception as error:
            logger.exception("TTS: failed exception_type=%s", type(error).__name__)
            raise ApplicationError("internal_error", "PHOS could not process the speech request.", status=500) from error

    def config(self) -> dict:
        if self._lifecycle is None:
            raise ApplicationError("service_unavailable", "Configuration service is unavailable.", status=503)
        result = self._lifecycle.execute("status")
        if not result.get("ok"):
            raise ApplicationError("service_unavailable", result.get("error", "Configuration service is unavailable."), status=503)
        saved = self._lifecycle.config_repository.load().to_dict()
        return {"saved": saved, "active": result["active"], "pending": result["changed"]}

    def register_mcp_manager(self, manager) -> None:
        self._mcp_manager = manager

    def mcp_settings(self) -> dict:
        if self._lifecycle is None or self._mcp_manager is None:
            raise ApplicationError("service_unavailable", "MCP runtime service is unavailable.", status=503)
        return self._mcp_manager.status()

    def configure_mcp(self, patch: dict) -> dict:
        if (self._lifecycle is None or self._mcp_manager is None or not isinstance(patch, dict)
                or set(patch) - {"enabled", "port"}):
            raise ApplicationError("invalid_mcp_settings", "MCP settings are invalid.")
        if type(patch.get("enabled")) is not bool or type(patch.get("port")) is not int or not 1 <= patch["port"] <= 65535:
            raise ApplicationError("invalid_mcp_settings", "MCP port must be an integer between 1 and 65535.")
        previous = self._lifecycle.config_repository.load()
        document = previous.to_dict()
        document["mcp"].update(patch)
        candidate = RuntimeConfig.from_dict(document, base_dir=self._lifecycle.path.parent)
        try:
            result = self._mcp_manager.apply(enabled=candidate.mcp_enabled, port=candidate.mcp_port)
        except Exception as error:
            current = self._mcp_manager.status()
            message = (f"MCP could not start on port {patch['port']}. "
                       f"MCP remains available on port {current['bound_port']}." if current.get("bound_port")
                       else "MCP could not start on the requested port.")
            raise ApplicationError("port_in_use", message, status=409) from error
        try:
            self._lifecycle.config_repository.save(candidate)
        except OSError as error:
            # Keep runtime and persistence coherent if storage fails.
            self._mcp_manager.apply(enabled=previous.mcp_enabled, port=previous.mcp_port)
            raise ApplicationError("configuration_unavailable", "MCP settings could not be saved.", status=503) from error
        self._lifecycle.active["mcp"] = candidate.to_dict()["mcp"]
        return result

    def request_admin_password_recovery(self):
        show = getattr(self._runtime, "show_recovery_code", None)
        if not callable(show):
            raise ApplicationError("service_unavailable", "Recovery display is unavailable.", status=503)
        available = getattr(self._runtime, "admin_overlay_available", None)
        if not callable(available) or not available():
            raise ApplicationError("service_unavailable", "Recovery display is unavailable.", status=503)
        code = self._admin_recovery.request()
        try:
            show(code, self._admin_recovery.expires)
        except Exception as error:
            self._admin_recovery.clear()
            raise ApplicationError("service_unavailable", "Recovery display is unavailable.", status=503) from error
        logger.info("PHOS ADMIN: recovery requested")
        return {"status": "requested"}

    def verify_admin_recovery_code(self, code):
        try: self._admin_recovery.verify(code)
        except ValueError as error: raise ApplicationError(str(error), "Recovery code could not be verified.") from error
        return {"status": "verified"}

    def clear_admin_overlay(self):
        clear = getattr(self._runtime, "clear_admin_overlay", None)
        if callable(clear): clear()
        return {"status": "cleared"}

    def get_admin_network_info(self):
        return self._runtime.admin_network_info()

    def show_admin_access_overlay(self):
        if not self._runtime.admin_network_info().get("admin_url"):
            raise ApplicationError("network_unavailable", "Admin network is unavailable.", status=503)
        try:
            self._runtime.show_admin_access_overlay()
        except RuntimeError as error:
            raise ApplicationError("service_unavailable", str(error), status=503) from error
        return {"status": "shown"}

    @staticmethod
    def _speech_configuration_reason(subsystem: str, provider: str | None, error: Exception) -> str:
        """Classify setup failures without exposing exception details or secrets."""
        if subsystem != "tts" or provider != "cartesia":
            return type(error).__name__
        message = str(error).casefold()
        if "credential" in message:
            return "cartesia_credential_missing"
        if "voice id" in message or "voice_id" in message:
            return "cartesia_voice_id_missing"
        if "model" in message:
            return "cartesia_model_missing"
        if "client could not be initialized" in message:
            return "cartesia_client_initialization_failed"
        return "cartesia_invalid_configuration"

    def _speech_transaction(self, patch, *, subsystem):
        """Build first, persist atomically, then publish; publication failure rolls back.

        This is intentionally main-process only.  IPC/Web wiring is a later step.
        """
        if self._lifecycle is None:
            raise ApplicationError("service_unavailable", "Speech configuration is unavailable.", status=503)
        try:
            previous = self._lifecycle.config_repository.load()
        except (ConfigurationError, OSError) as error:
            raise ApplicationError("service_unavailable", "Speech configuration is unavailable.", status=503) from error
        document = previous.to_dict(); _merge(document, patch)
        provider = (document.get("voice", {}).get("stt", {}).get("provider") if subsystem == "stt"
                    else document.get("tts", {}).get("provider") if subsystem == "tts"
                    else document.get("tts", {}).get("audio_output", {}).get("player"))
        try:
            requested = RuntimeConfig.from_dict(document, base_dir=self._lifecycle.path.parent)
            if subsystem == "stt":
                enabled, provider = requested.voice_enabled, requested.voice_stt_provider
                logger.info("PHOS SPEECH CONFIG: configuring subsystem=%s provider=%s", subsystem, provider)
                candidate = STTProviderFactory.create(requested, getattr(self._runtime, "_secrets_service", None)) if enabled else None
            elif subsystem == "tts":
                enabled, provider = requested.tts_enabled, requested.tts_provider
                logger.info("PHOS SPEECH CONFIG: configuring subsystem=%s provider=%s", subsystem, provider)
                candidate = TTSProviderFactory.create(requested, getattr(self._runtime, "_secrets_service", None)) if enabled else None
            else:
                logger.info("PHOS SPEECH CONFIG: configuring subsystem=%s provider=%s", subsystem, provider)
                candidate = AudioOutputProviderFactory.create(requested)
                enabled = True
        except (ConfigurationError, TTSError, ValueError, ImportError) as error:
            logger.warning("PHOS SPEECH CONFIG: configuration failed subsystem=%s provider=%s reason=%s",
                           subsystem, provider or "unknown",
                           self._speech_configuration_reason(subsystem, provider, error))
            raise ApplicationError("invalid_configuration", "Could not activate requested speech configuration.", status=400) from error
        try:
            self._lifecycle.config_repository.save(requested)
        except OSError as error:
            raise ApplicationError("persistence_failed", "Speech configuration could not be saved.", status=503) from error
        try:
            if subsystem == "stt":
                if candidate is not None: self._runtime.replace_stt_provider(candidate)
                self._runtime.set_stt_enabled(enabled)
            elif subsystem == "tts":
                if candidate is not None: self._runtime.replace_tts_provider(candidate)
                self._runtime.set_tts_enabled(enabled)
            else: self._runtime.replace_audio_output(candidate, config=requested)
        except Exception as error:
            # ConfigRepository uses atomic writes; restore the previous complete snapshot.
            try: self._lifecycle.config_repository.save(previous)
            except OSError: logger.exception("Speech configuration rollback failed")
            logger.warning("PHOS SPEECH CONFIG: configuration failed subsystem=%s provider=%s reason=%s",
                           subsystem, provider or "unknown",
                           self._speech_configuration_reason(subsystem, provider, error))
            raise ApplicationError("activation_failed", "Speech configuration could not be activated.", status=503) from error
        logger.info("PHOS SPEECH CONFIG: configured subsystem=%s provider=%s", subsystem, provider or "audio_output")
        return SpeechConfigurationResult(provider, provider, enabled, candidate is not None).document()

    def configure_stt(self, patch): return self._speech_transaction({"voice": patch}, subsystem="stt")
    def configure_tts(self, patch): return self._speech_transaction({"tts": patch}, subsystem="tts")
    def configure_audio_output(self, patch): return self._speech_transaction({"tts": {"audio_output": patch}}, subsystem="audio")

    def configure_eyes(self, patch: dict) -> dict:
        """Atomically save and apply the canonical Eyes fields."""
        if self._lifecycle is None:
            raise ApplicationError("service_unavailable", "Eyes configuration is unavailable.", status=503)
        allowed = {"iris_color", "base_visual_source", "environment_overlays_enabled"}
        if not isinstance(patch, dict) or set(patch) != allowed:
            raise ApplicationError("invalid_configuration", "Complete Eyes configuration is required.")
        try:
            previous = self._lifecycle.config_repository.load()
        except (ConfigurationError, OSError) as error:
            raise ApplicationError("service_unavailable", "Eyes configuration is unavailable.", status=503) from error
        document = previous.to_dict()
        _merge(document["display"], patch)
        try:
            candidate = RuntimeConfig.from_dict(document, base_dir=self._lifecycle.path.parent)
        except ConfigurationError as error:
            raise ApplicationError("invalid_configuration", "Eyes configuration was rejected.", {"reason": str(error)}) from error
        try:
            self._lifecycle.config_repository.save(candidate)
            result = self._lifecycle.apply_eyes(candidate)
            if not result.get("ok"):
                raise RuntimeError(result.get("error", "Eyes configuration could not be applied."))
        except Exception as error:
            try:
                self._lifecycle.config_repository.save(previous)
            except OSError:
                logger.exception("PHOS EYES: configuration rollback failed")
            logger.exception("PHOS EYES: operation=appearance_eyes_update failed exception_type=%s message=%s",
                             type(error).__name__, str(error)[:256])
            raise ApplicationError("runtime_unavailable", "Eyes configuration could not be applied.", status=503) from error
        logger.info("PHOS EYES: configured iris_color=%s", candidate.iris_color)
        return {"saved": {name: candidate.to_dict()["display"][name] for name in sorted(allowed)},
                "applied": result.get("applied", [])}

    def configure_backgrounds(self, patch: dict) -> dict:
        """Atomically save and publish the canonical display.backgrounds block."""
        if self._lifecycle is None:
            raise ApplicationError("service_unavailable", "Configuration service is unavailable.", status=503)
        allowed = {"enabled", "idle", "listening", "thinking", "speaking", "sleeping", "error"}
        if not isinstance(patch, dict) or set(patch) - allowed:
            raise ApplicationError("invalid_configuration", "Unsupported background configuration.")
        previous = self._lifecycle.config_repository.load()
        document = previous.to_dict()
        _merge(document["display"]["backgrounds"], patch)
        try:
            candidate = RuntimeConfig.from_dict(document, base_dir=self._lifecycle.path.parent)
        except ConfigurationError as error:
            raise ApplicationError("invalid_configuration", "Background configuration was rejected.", {"reason": str(error)}) from error
        try:
            self._lifecycle.config_repository.save(candidate)
            result = self._lifecycle.execute("reload")
            if not result.get("ok"):
                raise RuntimeError(result.get("error", "Background configuration could not be applied."))
        except Exception as error:
            try:
                self._lifecycle.config_repository.save(previous)
            except OSError:
                logger.exception("PHOS VISUAL: background configuration rollback failed")
            raise ApplicationError("runtime_unavailable", "Background configuration could not be applied.", status=503) from error
        logger.info("PHOS VISUAL: backgrounds configured enabled=%s", candidate.backgrounds_enabled)
        return {"saved": candidate.to_dict()["display"]["backgrounds"], "applied": result.get("applied", []),
                "status": self._runtime.application_status().get("backgrounds", {})}

    def configure_state_effects(self, patch: dict) -> dict:
        if self._lifecycle is None:
            raise ApplicationError("service_unavailable", "Configuration service is unavailable.", status=503)
        allowed = {"enabled", "preset", "palette", "reduce_motion"}
        if not isinstance(patch, dict) or set(patch) - allowed:
            raise ApplicationError("invalid_configuration", "Unsupported state effects configuration.")
        previous = self._lifecycle.config_repository.load()
        document = previous.to_dict()
        _merge(document["display"]["state_effects"], patch)
        try:
            candidate = RuntimeConfig.from_dict(document, base_dir=self._lifecycle.path.parent)
        except ConfigurationError as error:
            raise ApplicationError("invalid_configuration", "State effects configuration was rejected.", {"reason": str(error)}) from error
        try:
            self._lifecycle.config_repository.save(candidate)
            result = self._lifecycle.execute("reload")
            if not result.get("ok"):
                raise RuntimeError(result.get("error", "State effects configuration could not be applied."))
        except Exception as error:
            try: self._lifecycle.config_repository.save(previous)
            except OSError: logger.exception("PHOS VISUAL: state effects rollback failed")
            raise ApplicationError("runtime_unavailable", "State effects configuration could not be applied.", status=503) from error
        return {"saved": candidate.to_dict()["display"]["state_effects"], "applied": result.get("applied", [])}

    def configure_eye_safe_layer(self, patch: dict) -> dict:
        if self._lifecycle is None or not isinstance(patch, dict) or set(patch) - {"enabled", "mode"}:
            raise ApplicationError("invalid_configuration", "Unsupported eye safe layer configuration.")
        previous = self._lifecycle.config_repository.load()
        document = previous.to_dict()
        _merge(document["display"]["eye_safe_layer"], patch)
        try:
            candidate = RuntimeConfig.from_dict(document, base_dir=self._lifecycle.path.parent)
            self._lifecycle.config_repository.save(candidate)
            result = self._lifecycle.execute("reload")
            if not result.get("ok"):
                raise RuntimeError(result.get("error", "Eye safe layer could not be applied."))
        except (ConfigurationError, OSError, RuntimeError) as error:
            try: self._lifecycle.config_repository.save(previous)
            except OSError: logger.exception("PHOS VISUAL: eye safe layer rollback failed")
            raise ApplicationError("runtime_unavailable", "Eye safe layer could not be applied.", status=503) from error
        return {"saved": candidate.to_dict()["display"]["eye_safe_layer"], "applied": result.get("applied", [])}

    def appearance_preview(self, options: dict) -> dict:
        if self._lifecycle is None or not isinstance(options, dict):
            raise ApplicationError("invalid_preview", "Preview options are invalid.")
        try:
            from robot.ui.appearance_preview import render_preview
            current = self._lifecycle.config_repository.load()
            skin_id = options.get("skin_id")
            if skin_id is not None:
                skin = SkinRepository(self._lifecycle.path).get(skin_id)
                snapshot, _ = available_appearance_snapshot(skin.appearance, self._lifecycle.path.parent)
                document = apply_appearance_snapshot(current.to_dict(), snapshot)
                current = RuntimeConfig.from_dict(document, base_dir=self._lifecycle.path.parent)
            return {"svg": render_preview(current, options)}
        except (ValueError, ConfigurationError, OSError) as error:
            raise ApplicationError("preview_unavailable", "Appearance preview could not be rendered.", status=400) from error

    def _skins(self) -> SkinRepository:
        if self._lifecycle is None:
            raise ApplicationError("service_unavailable", "Skin service is unavailable.", status=503)
        return SkinRepository(self._lifecycle.path)

    @staticmethod
    def _owns_live_backgrounds(skin_id: str, backgrounds: dict) -> bool:
        prefix = f"backgrounds/skins/{skin_id}/"
        return any(isinstance(backgrounds.get(state), str) and backgrounds[state].startswith(prefix)
                   for state in ("idle", "listening", "thinking", "speaking", "sleeping", "error"))

    def skins(self) -> list[dict]:
        try:
            document = self._lifecycle.config_repository.load().to_dict()
            current_appearance = appearance_snapshot(document)
            current_backgrounds = document["display"]["backgrounds"]
            result = []
            for skin in self._skins().list():
                item = skin.document()
                _, item["missing_backgrounds"] = available_appearance_snapshot(skin.appearance, self._lifecycle.path.parent)
                item["in_use"] = appearance_semantically_matches(current_appearance, skin.appearance, self._lifecycle.path.parent)
                item["delete_blocked"] = not skin.built_in and self._owns_live_backgrounds(skin.id, current_backgrounds)
                result.append(item)
            return result
        except ConfigurationError as error: raise ApplicationError("invalid_skin", str(error)) from error

    def create_skin(self, payload: dict) -> dict:
        if not isinstance(payload, dict) or set(payload) - {"name", "description"}:
            raise ApplicationError("invalid_skin", "Skin details are invalid.")
        try:
            skin = self._skins().create(payload.get("name", ""), payload.get("description", ""),
                                        appearance_snapshot(self._lifecycle.config_repository.load().to_dict()))
            return skin.document()
        except (ConfigurationError, OSError) as error: raise ApplicationError("invalid_skin", str(error)) from error

    def delete_skin(self, skin_id: str) -> dict:
        try:
            current = self._lifecycle.config_repository.load().to_dict()["display"]["backgrounds"]
            if self._owns_live_backgrounds(skin_id, current):
                raise ApplicationError("skin_in_use", "This skin is currently used by PHOS. Apply another skin before deleting it.", status=409)
            self._skins().delete(skin_id); return {"deleted": skin_id}
        except ApplicationError:
            raise
        except (ConfigurationError, OSError) as error: raise ApplicationError("invalid_skin", str(error)) from error

    def preview_skin(self, skin_id: str) -> dict:
        """Apply a Skin only to the live display; never save configuration."""
        if not isinstance(skin_id, str): raise ApplicationError("invalid_skin", "Skin identifier is invalid.")
        try:
            current = self._lifecycle.config_repository.load()
            skin = self._skins().get(skin_id)
            snapshot, missing = available_appearance_snapshot(skin.appearance, self._lifecycle.path.parent)
            candidate = RuntimeConfig.from_dict(apply_appearance_snapshot(current.to_dict(), snapshot),
                                                base_dir=self._lifecycle.path.parent)
            result = self._lifecycle.apply_temporary_appearance(candidate)
            if not result.get("ok"): raise RuntimeError(result.get("error", "Skin preview could not be applied."))
        except (ConfigurationError, OSError, RuntimeError) as error:
            raise ApplicationError("skin_preview_failed", "Skin preview could not be applied; saved appearance was unchanged.", status=503) from error
        return {"skin": skin.document(), "applied": result.get("applied", []), "missing_backgrounds": missing}

    def apply_skin(self, skin_id: str) -> dict:
        """Validate the entire skin before one canonical save/reload transaction."""
        if not isinstance(skin_id, str): raise ApplicationError("invalid_skin", "Skin identifier is invalid.")
        previous = self._lifecycle.config_repository.load()
        try:
            skin = self._skins().get(skin_id)
            snapshot, missing_backgrounds = available_appearance_snapshot(skin.appearance, self._lifecycle.path.parent)
            document = apply_appearance_snapshot(previous.to_dict(), snapshot)
            candidate = RuntimeConfig.from_dict(document, base_dir=self._lifecycle.path.parent)
            self._lifecycle.config_repository.save(candidate)
            result = self._lifecycle.execute("reload")
            if not result.get("ok"): raise RuntimeError(result.get("error", "Skin could not be applied."))
        except (ConfigurationError, OSError, RuntimeError) as error:
            try: self._lifecycle.config_repository.save(previous)
            except OSError: logger.exception("PHOS SKIN: rollback failed")
            raise ApplicationError("skin_apply_failed", "Skin could not be applied; appearance was unchanged.", {"reason": str(error)}, status=503) from error
        return {"skin": skin.document(), "applied": result.get("applied", []), "missing_backgrounds": missing_backgrounds}

    def import_skin(self, payload: dict) -> dict:
        if (not isinstance(payload, dict) or set(payload) != {"content"}
                or not isinstance(payload["content"], (bytes, bytearray))):
            raise ApplicationError("invalid_skin", "Skin import is invalid.")
        try: return self._skins().import_package(payload["content"]).document()
        except (ConfigurationError, OSError) as error: raise ApplicationError("invalid_skin", str(error)) from error

    def export_skin(self, skin_id: str) -> dict:
        if not isinstance(skin_id, str): raise ApplicationError("invalid_skin", "Skin identifier is invalid.")
        try: return {"content": self._skins().export(skin_id)}
        except (ConfigurationError, OSError) as error: raise ApplicationError("invalid_skin", str(error)) from error

    def background_assets(self) -> list[dict]:
        """List only safe, existing images in the canonical asset directory."""
        if self._lifecycle is None:
            raise ApplicationError("service_unavailable", "Configuration service is unavailable.", status=503)
        directory = (self._lifecycle.path.parent / "backgrounds").resolve()
        if not directory.is_dir():
            return []
        return [{"name": path.name, "format": path.suffix[1:].lower()}
                for path in sorted(directory.iterdir())
                if path.is_file() and path.suffix.lower() in {".png", ".jpg", ".jpeg"}]

    def upload_background(self, upload: dict) -> dict:
        if self._lifecycle is None or not isinstance(upload, dict):
            raise ApplicationError("service_unavailable", "Background upload is unavailable.", status=503)
        content, name = upload.get("content"), upload.get("name")
        if (not isinstance(name, str) or not name or len(name) > 96 or name != Path(name).name
                or ".." in name or any(ord(char) < 32 for char in name)
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._ -]*\.(?:png|jpe?g)", name, re.IGNORECASE)):
            raise ApplicationError("invalid_filename", "Choose a safe PNG or JPEG filename.")
        maximum_bytes = self._lifecycle.config_repository.load().background_max_upload_bytes
        if not isinstance(content, (bytes, bytearray)) or not content:
            raise ApplicationError("invalid_image", "Image must not be empty.")
        if len(content) > maximum_bytes:
            raise ApplicationError("file_too_large", f"Image exceeds the {maximum_bytes // (1024 * 1024)} MiB limit.", status=413)
        try:
            import cv2, numpy as np
            image = cv2.imdecode(np.frombuffer(content, dtype=np.uint8), cv2.IMREAD_COLOR)
            if (image is None or not image.shape[0] or not image.shape[1]
                    or image.shape[1] > _MAX_BACKGROUND_DIMENSIONS[0]
                    or image.shape[0] > _MAX_BACKGROUND_DIMENSIONS[1]):
                raise ValueError
            extension = ".png" if content.startswith(b"\x89PNG\r\n\x1a\n") else ".jpg" if content.startswith(b"\xff\xd8") else None
        except ImportError:
            details = _header_image_details(content)
            if details is None:
                extension = None
            else:
                extension, width, height = details
                if width > _MAX_BACKGROUND_DIMENSIONS[0] or height > _MAX_BACKGROUND_DIMENSIONS[1]:
                    extension = None
        except ValueError:
            extension = None
        if extension is None:
            raise ApplicationError("invalid_image", "Upload a valid PNG or JPEG image.")
        directory = (self._lifecycle.path.parent / "backgrounds").resolve()
        directory.mkdir(mode=0o750, exist_ok=True)
        if Path(name).suffix.lower() != extension and not (extension == ".jpg" and Path(name).suffix.lower() == ".jpeg"):
            raise ApplicationError("invalid_image", "Filename extension does not match the decoded image.")
        target = directory / name
        if target.exists():
            raise ApplicationError("asset_exists", f'A background named "{name}" already exists.', status=409)
        fd, temporary = tempfile.mkstemp(prefix=".upload-", dir=directory)
        try:
            with os.fdopen(fd, "wb") as output:
                output.write(content); output.flush(); os.fsync(output.fileno())
            # Refuse a concurrent winner rather than overwriting it.
            if target.exists():
                raise ApplicationError("asset_exists", f'A background named "{name}" already exists.', status=409)
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary): os.unlink(temporary)
        return {"name": name, "format": extension[1:]}

    def delete_background(self, name: str) -> dict:
        """Clear canonical references before removing one managed asset."""
        if self._lifecycle is None or not isinstance(name, str):
            raise ApplicationError("service_unavailable", "Background deletion is unavailable.", status=503)
        if (not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._ -]*\.(?:png|jpe?g)", name, re.IGNORECASE)
                or name != Path(name).name or ".." in name):
            raise ApplicationError("invalid_asset", "Invalid background asset name.")
        directory = (self._lifecycle.path.parent / "backgrounds").resolve()
        target = directory / name
        if not target.is_file():
            raise ApplicationError("asset_not_found", "Background asset was not found.", status=404)
        current = self._lifecycle.config_repository.load()
        assignments = {state: None for state in ("idle", "listening", "thinking", "speaking", "sleeping", "error")
                       if getattr(current, f"background_{state}") == Path("backgrounds") / name}
        if assignments:
            self.configure_backgrounds(assignments)
        try:
            target.unlink()
        except OSError as error:
            raise ApplicationError("deletion_failed", "Background assignments were reset, but the file could not be removed.", status=503) from error
        return {"name": name, "reset_states": sorted(assignments), "status": self._runtime.application_status().get("backgrounds", {})}

    def update_config(self, patch: dict) -> dict:
        """Validate and persist a partial canonical-config edit, then reload safely."""
        if self._lifecycle is None:
            raise ApplicationError("service_unavailable", "Configuration service is unavailable.", status=503)
        if not isinstance(patch, dict):
            raise ApplicationError("invalid_configuration", "Configuration update must be a JSON object.")
        document = self._lifecycle.config_repository.load().to_dict()
        _merge(document, patch)
        try:
            self._lifecycle.config_repository.save(RuntimeConfig.from_dict(document, base_dir=self._lifecycle.path.parent))
        except (ConfigurationError, OSError) as error:
            raise ApplicationError("invalid_configuration", "Configuration update was rejected.", {"reason": str(error)}) from error
        result = self._lifecycle.execute("reload")
        if not result.get("ok"):
            raise ApplicationError("runtime_unavailable", result.get("error", "Configuration was saved but could not be applied."), status=503)
        return {"saved": document, "applied": result.get("applied", []), "pending": result.get("restart_required", [])}

    def set_visual_source(self, source: str) -> dict:
        if source not in COMMANDS["set_visual_source"].allowed_values:
            raise ApplicationError("invalid_visual_source", "Unsupported visual source.", {"source": source})
        if self._lifecycle is not None:
            # This is a canonical runtime setting.  Persist and validate it
            # through the existing configuration/lifecycle boundary rather
            # than leaving a browser command to be lost at the next restart.
            self.update_config({"display": {"base_visual_source": source}})
        else:
            # Direct service construction (tests/local embedding) has no
            # canonical persistence boundary, but still uses the runtime's
            # semantic source application service.
            self._runtime.apply_base_visual_source(type("Config", (), {"base_visual_source": source})())
        payload = {"source": source, "active_visual_source": self._behavior.base_visual_source,
                   "visual": self.visual_state()}
        self._emit("visual_state_changed", payload)
        return payload

    def signal_user(self, signal: str) -> dict:
        if signal not in USER_SIGNAL_INTENTS:
            raise ApplicationError("invalid_signal", "Unsupported user signal.", {"signal": signal})
        loop = self._runtime._loop
        if loop is None:
            raise ApplicationError("runtime_unavailable", "PHOS runtime is not running.", status=503)
        try:
            return self._plain(asyncio.run_coroutine_threadsafe(self._runtime.signal_user(signal), loop).result(timeout=2))
        except Exception as error:
            raise ApplicationError("led_unavailable", "PHOS could not apply the semantic LED signal.", status=503) from error

    async def signal_user_async(self, signal: str) -> dict:
        """Apply a semantic signal without synchronously blocking its runtime loop."""
        if signal not in USER_SIGNAL_INTENTS:
            raise ApplicationError("invalid_signal", "Unsupported user signal.", {"signal": signal})
        loop = self._runtime._loop
        if loop is None:
            raise ApplicationError("runtime_unavailable", "PHOS runtime is not running.", status=503)
        if loop is not asyncio.get_running_loop():
            return await asyncio.to_thread(self.signal_user, signal)
        try:
            return self._plain(await self._runtime.signal_user(signal))
        except Exception as error:
            raise ApplicationError("led_unavailable", "PHOS could not apply the semantic LED signal.", status=503) from error

    def request_transient_overlay(self, value: dict) -> dict:
        """Submit semantic presentation context without exposing renderer controls."""
        if not isinstance(value, dict) or set(value) - {"intent", "source", "message", "duration_seconds"}:
            raise ApplicationError("invalid_overlay_request", "Overlay request has unsupported fields.")
        intent, source = value.get("intent"), value.get("source")
        if intent not in {"success", "info", "attention", "warning", "error", "sensor_alert"}:
            raise ApplicationError("invalid_overlay_intent", "Unsupported overlay intent.")
        if not isinstance(source, str) or not source:
            raise ApplicationError("invalid_overlay_source", "Overlay source is required.")
        loop = self._runtime._loop
        if loop is None:
            raise ApplicationError("runtime_unavailable", "PHOS runtime is not running.", status=503)
        try:
            return self._plain(asyncio.run_coroutine_threadsafe(
                self._runtime.request_transient_overlay(intent, source, message=value.get("message"),
                                                        duration_seconds=value.get("duration_seconds")), loop).result(timeout=2))
        except ValueError as error:
            raise ApplicationError("invalid_overlay_request", str(error)) from error
        except Exception as error:
            raise ApplicationError("overlay_unavailable", "PHOS could not apply the semantic overlay.", status=503) from error

    def clear_transient_overlay(self, source: str) -> dict:
        loop = self._runtime._loop
        if loop is None:
            raise ApplicationError("runtime_unavailable", "PHOS runtime is not running.", status=503)
        try:
            return self._plain(asyncio.run_coroutine_threadsafe(self._runtime.clear_transient_overlay(source), loop).result(timeout=2))
        except Exception as error:
            raise ApplicationError("overlay_unavailable", "PHOS could not clear the semantic overlay.", status=503) from error

    def set_expression(self, expression: str) -> dict:
        try:
            target = FaceExpression(expression)
        except (TypeError, ValueError) as error:
            raise ApplicationError("invalid_expression", "Unsupported semantic expression.", {"expression": expression}) from error
        if target not in COMMANDS["set_expression"].allowed_members:
            raise ApplicationError("unsupported_expression_command", "Expression is not writable through this command.",
                                   {"expression": expression})
        self._behavior.set_manual_expression(target)
        payload = {"expression": target.value, "manual_override": self._behavior.manual_expression_state(),
                   "visual": self.visual_state()}
        self._emit("expression_changed", payload)
        return payload

    async def _accept_state_command(self, target: RobotState, submitted_at: float):
        """Run on the runtime loop and acknowledge immediately after commit."""
        dequeued_at = time.monotonic()
        logger.debug("PHOS COMMAND: state target=%s dequeued_ms=%.1f", target.value,
                    (dequeued_at - submitted_at) * 1000)
        transition = await self._core.accept_transition_to(target, reason="remote_api")
        logger.debug("PHOS COMMAND: state target=%s applied_ms=%.1f", target.value,
                    (time.monotonic() - submitted_at) * 1000)
        return transition

    def set_state(self, state: str) -> dict:
        try:
            target = RobotState(state)
        except (TypeError, ValueError) as error:
            raise ApplicationError("invalid_robot_state", "Unsupported robot state.", {"state": state}) from error
        if target not in COMMANDS["set_robot_state"].allowed_members:
            raise ApplicationError("unsupported_state_command", "Robot state is runtime-only.", {"state": state})
        loop = self._runtime._loop
        if loop is None:
            raise ApplicationError("runtime_unavailable", "PHOS runtime is not running.", status=503)
        submitted_at = time.monotonic()
        if self._core.state is target:
            logger.info("PHOS COMMAND: state target=%s accepted already_current=true", target.value)
            return self.robot_state()
        future = asyncio.run_coroutine_threadsafe(self._accept_state_command(target, submitted_at), loop)
        try:
            future.result(timeout=STATE_COMMAND_TIMEOUT_SECONDS)
        except InvalidStateTransition as error:
            raise ApplicationError("invalid_state_transition", str(error),
                                   {"current": self._core.state.value, "target": target.value}, status=409) from error
        except TimeoutError as error:
            # A queued coroutine must never execute after its requester was
            # told that PHOS did not accept it. If it is already running, the
            # state commit is synchronous and can be acknowledged truthfully.
            cancelled = future.cancel()
            if not cancelled and self._core.state is target:
                logger.info("PHOS COMMAND: state target=%s accepted after_timeout=true", target.value)
                return self.robot_state()
            phase = "queued_cancelled" if cancelled else "runtime_unresponsive"
            logger.warning("PHOS COMMAND: state timeout target=%s phase=%s elapsed_ms=%.1f", target.value, phase,
                           (time.monotonic() - submitted_at) * 1000)
            raise ApplicationError("runtime_unavailable", "PHOS did not accept the state command in time.", status=503) from error
        logger.info("PHOS COMMAND: state target=%s accepted", target.value)
        return self.robot_state()

    async def set_state_async(self, state: str) -> dict:
        """Apply a state command from the runtime loop without self-deadlock."""
        try:
            target = RobotState(state)
        except (TypeError, ValueError) as error:
            raise ApplicationError("invalid_robot_state", "Unsupported robot state.", {"state": state}) from error
        if target not in COMMANDS["set_robot_state"].allowed_members:
            raise ApplicationError("unsupported_state_command", "Robot state is runtime-only.", {"state": state})
        loop = self._runtime._loop
        if loop is None:
            raise ApplicationError("runtime_unavailable", "PHOS runtime is not running.", status=503)
        if loop is not asyncio.get_running_loop():
            return await asyncio.to_thread(self.set_state, state)
        if self._core.state is target:
            return self.robot_state()
        submitted_at = time.monotonic()
        try:
            await asyncio.wait_for(self._accept_state_command(target, submitted_at), STATE_COMMAND_TIMEOUT_SECONDS)
        except InvalidStateTransition as error:
            raise ApplicationError("invalid_state_transition", str(error),
                                   {"current": self._core.state.value, "target": target.value}, status=409) from error
        except asyncio.TimeoutError as error:
            raise ApplicationError("runtime_unavailable", "PHOS did not accept the state command in time.", status=503) from error
        return self.robot_state()


def _merge(target: dict, patch: dict) -> None:
    for key, value in patch.items():
        if key not in target:
            raise ConfigurationError(f"Unknown configuration field: {key}")
        if isinstance(value, dict):
            if not isinstance(target[key], dict):
                raise ConfigurationError(f"Configuration field is not an object: {key}")
            _merge(target[key], value)
        else:
            target[key] = value


def _command(definition: CommandDefinition) -> dict:
    command = {"method": definition.method, "endpoint": definition.endpoint,
               "field": definition.field, "allowed_values": definition.allowed_values}
    if definition is COMMANDS["set_robot_state"]:
        command["transitions"] = transition_graph()
    return command
