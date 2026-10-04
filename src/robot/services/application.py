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
from threading import RLock
from typing import Any, Callable

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
from robot.config import ConfigurationError, RuntimeConfig
from robot.motion import MotionState
from robot.ui.state import FaceExpression

logger = logging.getLogger(__name__)


# These are enum members, not duplicated wire values. ERROR is lifecycle-only;
# the current visual-event command deliberately supports only reactions that the
# BehaviorEngine maps from a semantic expression event.
WRITABLE_ROBOT_STATES = writable_states()
WRITABLE_EXPRESSIONS = (FaceExpression.NEUTRAL, FaceExpression.HAPPY, FaceExpression.CURIOUS, FaceExpression.SURPRISED)
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


class ApplicationError(Exception):
    """A stable adapter-safe error."""
    def __init__(self, code: str, message: str, details: dict | None = None, status: int = 400):
        super().__init__(message)
        self.code, self.message, self.details, self.status = code, message, details or {}, status

    def document(self) -> dict:
        return {"error": {"code": self.code, "message": self.message, "details": self.details}}


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
    def observed_expression(self): return self._call("observed_expression")
    def health(self): return self._call("health")
    def capabilities(self): return self._call("capabilities")
    def config(self): return self._call("config")
    def update_config(self, value): return self._call("update_config", value)
    def set_expression(self, value): return self._call("expression", {"expression": value})
    def set_state(self, value): return self._call("set_state", {"state": value})
    def set_visual_source(self, value): return self._call("visual_source", {"source": value})
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
                "motion_state_changed": current.get("motion", {}),
                "health_changed": current.get("health", {}),
                "overlay_changed": current.get("overlay", {}),
                "presence_changed": current.get("presence", {}),
                "attention_changed": current.get("attention", {}),
                "observed_expression_changed": current.get("observed_expression", {}),
                "expression_reaction_changed": current.get("expression_reaction", {}),
            }
        except ApplicationError:
            return
        for event_type, payload in payloads.items():
            frozen = repr(payload)
            if self._last.get(event_type) == frozen:
                continue
            self._last[event_type] = frozen
            if event_type == "observed_expression_changed":
                logger.info("EXPR SSE SNAPSHOT: available=%s label=%s confidence=%s",
                            payload.get("available"), payload.get("label"), payload.get("confidence"))
            if event_type == "expression_reaction_changed":
                logger.info("SSE OUT: type=%s payload=%s", event_type, payload)
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
        logger.info("EXPR SNAPSHOT: state_id=%s available=%s label=%s confidence=%s",
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

    def touch(self) -> dict:
        reader = getattr(self._runtime, "touch_status", None)
        if reader is not None:
            return self._plain(reader())
        return {"enabled": False, "last_event": None, "last_event_at": None,
                "x": None, "y": None, "normalized_x": None, "normalized_y": None,
                "duration_ms": None}

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
        result.setdefault("sensors", self.sensors())
        self._emit("visual_state_changed", result["visual"])
        return result

    def emit_snapshot_changes(self) -> None:
        """Publish bounded semantic snapshots when an adapter asks for one.

        This is intentionally pull-driven: it creates no telemetry thread and
        never streams raw samples.  A WebSocket adapter can call it after a
        state-changing command or its low-rate client heartbeat.
        """
        self._emit("environmental_state_changed", self.environment())
        self._emit("motion_state_changed", self.motion())
        self._emit("health_changed", self.health())
        self._emit("presence_changed", self.presence())
        self._emit("attention_changed", self.attention())
        self._emit("observed_expression_changed", self.observed_expression())
        self._emit("expression_reaction_changed", self.expression_reaction())
        self._emit("touch_changed", self.touch())

    def config(self) -> dict:
        if self._lifecycle is None:
            raise ApplicationError("service_unavailable", "Configuration service is unavailable.", status=503)
        result = self._lifecycle.execute("status")
        if not result.get("ok"):
            raise ApplicationError("service_unavailable", result.get("error", "Configuration service is unavailable."), status=503)
        saved = RuntimeConfig.from_file(self._lifecycle.path).to_dict()
        return {"saved": saved, "active": result["active"], "pending": result["changed"]}

    def update_config(self, patch: dict) -> dict:
        """Validate and persist a partial canonical-config edit, then reload safely."""
        if self._lifecycle is None:
            raise ApplicationError("service_unavailable", "Configuration service is unavailable.", status=503)
        if not isinstance(patch, dict):
            raise ApplicationError("invalid_configuration", "Configuration update must be a JSON object.")
        document = RuntimeConfig.from_file(self._lifecycle.path).to_dict()
        _merge(document, patch)
        try:
            RuntimeConfig.from_dict(document, base_dir=self._lifecycle.path.parent).save(self._lifecycle.path)
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

    def set_expression(self, expression: str) -> dict:
        try:
            target = FaceExpression(expression)
        except (TypeError, ValueError) as error:
            raise ApplicationError("invalid_expression", "Unsupported semantic expression.", {"expression": expression}) from error
        if target not in COMMANDS["set_expression"].allowed_members:
            raise ApplicationError("unsupported_expression_command", "Expression is not writable through this command.",
                                   {"expression": expression})
        self._behavior.set_manual_expression(target, duration_seconds=30.0)
        payload = {"expression": target.value, "manual_override": self._behavior.manual_expression_state(),
                   "visual": self.visual_state()}
        self._emit("expression_changed", payload)
        return payload

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
        try:
            asyncio.run_coroutine_threadsafe(self._core.transition_to(target, reason="remote_api"), loop).result(timeout=2)
        except InvalidStateTransition as error:
            raise ApplicationError("invalid_state_transition", str(error),
                                   {"current": self._core.state.value, "target": target.value}, status=409) from error
        except TimeoutError as error:
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
