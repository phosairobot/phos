"""Hardware-agnostic PHOS external-controller protocol v1.

This module owns only bounded JSON Lines framing and semantic messages.  A
serial implementation may provide the transport later; it must feed resulting
sensor events through the existing runtime services rather than drivers.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import json
import logging
import math
from typing import Any, Protocol

PROTOCOL = "phos-hw"
VERSION = 1
MAX_FRAME_BYTES = 8192
CONTROLLER_EVENTS = frozenset({"hello", "capabilities", "sensor_update", "motion_update",
                               "orientation_update", "controller_status", "warning", "error",
                               "heartbeat", "ack"})
CONTROLLER_COMMANDS = frozenset({"request_capabilities", "ping", "configure_sensor_reporting",
                                 "set_led_animation", "stop_led_animation", "set_led_brightness", "clear_led"})
LED_ANIMATIONS = frozenset({"off", "solid", "blink", "pulse", "breathe", "chase", "spin", "wipe"})
LED_DIRECTIONS = frozenset({"clockwise", "counterclockwise", "outward", "inward"})
LED_MODES = frozenset({"circular", "symmetric"})
LED_ORIGINS = frozenset({"top", "bottom", "left", "right"})
MOTION_UPDATE_STATES = frozenset({"stationary", "moving", "tilt_left", "tilt_right", "tilt_forward", "tilt_backward", "shake", "impact"})
MOTION_INTENSITIES = frozenset({"low", "medium", "high"})

logger = logging.getLogger(__name__)


class ProtocolError(ValueError):
    """Bounded protocol validation failure suitable for a controller ack/error."""
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.frame: bytes | None = None
        self.message_type: str | None = None


def safe_frame_repr(frame: bytes, *, limit: int = 512) -> str:
    """Return a bounded, log-safe representation of untrusted serial input."""
    clipped = frame[:limit]
    return repr(clipped) + ("...<truncated>" if len(frame) > limit else "")


@dataclass(frozen=True)
class ControllerMessage:
    type: str
    payload: dict[str, Any]
    message_id: str | None = None
    timestamp_ms: int | None = None
    reply_to: str | None = None
    raw_frame: bytes | None = None

    def document(self) -> dict[str, Any]:
        result = {"protocol": PROTOCOL, "version": VERSION, "type": self.type, "payload": self.payload}
        if self.message_id is not None: result["id"] = self.message_id
        if self.timestamp_ms is not None: result["timestamp_ms"] = self.timestamp_ms
        if self.reply_to is not None: result["reply_to"] = self.reply_to
        return result


def decode_frame(frame: bytes) -> ControllerMessage:
    if len(frame) > MAX_FRAME_BYTES: raise ProtocolError("frame_too_large", "Frame exceeds protocol limit.")
    try: value = json.loads(frame.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error: raise ProtocolError("malformed_frame", "Frame is not valid UTF-8 JSON.") from error
    message_type = value.get("type") if isinstance(value, dict) and isinstance(value.get("type"), str) else None
    try:
        if not isinstance(value, dict) or value.get("protocol") != PROTOCOL: raise ProtocolError("invalid_envelope", "protocol must be phos-hw.")
        if value.get("version") != VERSION: raise ProtocolError("protocol_version_unsupported", "Unsupported protocol version.")
        if value.get("type") not in CONTROLLER_EVENTS | CONTROLLER_COMMANDS: raise ProtocolError("unknown_message_type", "Unknown message type.")
        if "payload" not in value: raise ProtocolError("invalid_payload", "missing payload field.")
        if not isinstance(value["payload"], dict): raise ProtocolError("invalid_payload", "payload must be an object.")
        validate_controller_payload(value["type"], value["payload"])
    except ProtocolError as error:
        error.message_type = message_type
        raise
    return ControllerMessage(value["type"], value.get("payload", {}), value.get("id"), value.get("timestamp_ms"), value.get("reply_to"))


def validate_controller_payload(kind: str, payload: dict[str, Any]) -> None:
    if kind == "set_led_animation":
        validate_led_animation(payload)
        return
    if kind == "motion_update":
        if set(payload) != {"state", "intensity"}:
            raise ProtocolError("invalid_payload", "motion_update requires only state and intensity.")
        if not isinstance(payload["state"], str) or payload["state"] not in MOTION_UPDATE_STATES:
            raise ProtocolError("invalid_payload", "motion_update state is unsupported.")
        if not isinstance(payload["intensity"], str) or payload["intensity"] not in MOTION_INTENSITIES:
            raise ProtocolError("invalid_payload", "motion_update intensity is unsupported.")
    if kind == "orientation_update":
        if set(payload) != {"acceleration_m_s2", "angular_velocity_dps"}:
            raise ProtocolError("invalid_payload", "orientation_update requires acceleration_m_s2 and angular_velocity_dps.")
        for vector in ("acceleration_m_s2", "angular_velocity_dps"):
            values = payload[vector]
            if not isinstance(values, dict) or set(values) != {"x", "y", "z"}:
                raise ProtocolError("invalid_payload", f"orientation_update {vector} requires x, y and z.")
            if any(isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(item)
                   for item in values.values()):
                raise ProtocolError("invalid_payload", f"orientation_update {vector} axes must be finite numbers.")


class JsonLinesDecoder:
    """Incremental bounded decoder; malformed lines do not poison later frames."""
    def __init__(self): self._buffer = b""
    def feed(self, data: bytes) -> list[ControllerMessage]:
        self._buffer += data
        if len(self._buffer) > MAX_FRAME_BYTES and b"\n" not in self._buffer: self._buffer = b""; raise ProtocolError("frame_too_large", "Unterminated frame exceeds protocol limit.")
        frames, self._buffer = self._buffer.split(b"\n")[:-1], self._buffer.rsplit(b"\n", 1)[-1]
        messages = []
        for frame in frames:
            if not frame:
                continue
            try:
                logger.debug("PHOS CONTROLLER RX RAW: %s", safe_frame_repr(frame, limit=MAX_FRAME_BYTES))
                messages.append(replace(decode_frame(frame), raw_frame=frame))
            except ProtocolError as error:
                error.frame = frame
                raise
        return messages


def validate_led_animation(payload: dict[str, Any]) -> None:
    allowed = {"animation", "color", "brightness", "period_ms", "min_intensity", "max_intensity", "duration_ms",
               "direction", "mode", "origin"}
    unknown = set(payload) - allowed
    if unknown:
        raise ProtocolError("invalid_parameter", "Unsupported LED animation field.")
    animation = payload.get("animation")
    if animation not in LED_ANIMATIONS: raise ProtocolError("unsupported_animation", "Unsupported LED animation.")
    color, brightness = payload.get("color"), payload.get("brightness")
    if color is not None and (not isinstance(color, str) or len(color) != 7 or not color.startswith("#")): raise ProtocolError("invalid_parameter", "color must be #RRGGBB.")
    if brightness is not None and (isinstance(brightness, bool) or not isinstance(brightness, (int, float)) or not 0 <= brightness <= 1): raise ProtocolError("invalid_parameter", "brightness must be 0..1.")
    for name in ("min_intensity", "max_intensity"):
        value = payload.get(name)
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1): raise ProtocolError("invalid_parameter", f"{name} must be 0..1.")
    if ("duration_ms" in payload and (type(payload["duration_ms"]) is not int or payload["duration_ms"] < 0)
            or ("period_ms" in payload and (type(payload["period_ms"]) is not int or payload["period_ms"] <= 0))):
        raise ProtocolError("invalid_parameter", "Invalid duration or period.")
    if ("min_intensity" in payload and "max_intensity" in payload
            and payload["min_intensity"] > payload["max_intensity"]):
        raise ProtocolError("invalid_parameter", "min_intensity must not exceed max_intensity.")

    direction, mode, origin = (payload.get("direction"), payload.get("mode"), payload.get("origin"))
    if direction is not None and direction not in LED_DIRECTIONS:
        raise ProtocolError("invalid_parameter", "Unsupported LED direction.")
    if mode is not None and mode not in LED_MODES:
        raise ProtocolError("invalid_parameter", "Unsupported LED mode.")
    if origin is not None and origin not in LED_ORIGINS:
        raise ProtocolError("invalid_parameter", "Unsupported LED origin.")
    if animation in {"off", "solid", "blink", "pulse", "breathe"}:
        if any(value is not None for value in (direction, mode, origin)):
            raise ProtocolError("invalid_parameter", f"{animation} does not support direction, mode or origin.")
        return
    if animation == "spin":
        if mode not in {None, "circular"} or direction not in {None, "clockwise", "counterclockwise"} or origin is not None:
            raise ProtocolError("invalid_parameter", "spin supports only circular clockwise/counterclockwise direction.")
        return
    # chase and wipe default to circular/clockwise when all three fields are omitted.
    actual_mode = mode or "circular"
    if actual_mode == "circular":
        if direction not in {None, "clockwise", "counterclockwise"}:
            raise ProtocolError("invalid_parameter", "Circular LED animations require clockwise or counterclockwise direction.")
        if animation == "chase" and origin is not None:
            raise ProtocolError("invalid_parameter", "Circular chase does not support an origin.")
        return
    if origin is None or direction not in {"outward", "inward"}:
        raise ProtocolError("invalid_parameter", "Symmetric LED animations require origin and outward or inward direction.")


class ControllerTransport(Protocol):
    def write(self, data: bytes) -> None: ...

