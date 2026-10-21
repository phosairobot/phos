"""Hardware-agnostic PHOS external-controller protocol v1.

This module owns only bounded JSON Lines framing and semantic messages.  A
serial implementation may provide the transport later; it must feed resulting
sensor events through the existing runtime services rather than drivers.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
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


class ProtocolError(ValueError):
    """Bounded protocol validation failure suitable for a controller ack/error."""
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ControllerMessage:
    type: str
    payload: dict[str, Any]
    message_id: str | None = None
    timestamp_ms: int | None = None
    reply_to: str | None = None

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
    if not isinstance(value, dict) or value.get("protocol") != PROTOCOL: raise ProtocolError("invalid_envelope", "Invalid protocol envelope.")
    if value.get("version") != VERSION: raise ProtocolError("protocol_version_unsupported", "Unsupported protocol version.")
    if value.get("type") not in CONTROLLER_EVENTS | CONTROLLER_COMMANDS: raise ProtocolError("unknown_message_type", "Unknown message type.")
    if not isinstance(value.get("payload", {}), dict): raise ProtocolError("invalid_payload", "Payload must be an object.")
    return ControllerMessage(value["type"], value.get("payload", {}), value.get("id"), value.get("timestamp_ms"), value.get("reply_to"))


class JsonLinesDecoder:
    """Incremental bounded decoder; malformed lines do not poison later frames."""
    def __init__(self): self._buffer = b""
    def feed(self, data: bytes) -> list[ControllerMessage]:
        self._buffer += data
        if len(self._buffer) > MAX_FRAME_BYTES and b"\n" not in self._buffer: self._buffer = b""; raise ProtocolError("frame_too_large", "Unterminated frame exceeds protocol limit.")
        frames, self._buffer = self._buffer.split(b"\n")[:-1], self._buffer.rsplit(b"\n", 1)[-1]
        return [decode_frame(frame) for frame in frames if frame]


def validate_led_animation(payload: dict[str, Any]) -> None:
    if payload.get("animation") not in LED_ANIMATIONS: raise ProtocolError("unsupported_animation", "Unsupported LED animation.")
    color, brightness = payload.get("color"), payload.get("brightness")
    if color is not None and (not isinstance(color, str) or len(color) != 7 or not color.startswith("#")): raise ProtocolError("invalid_parameter", "color must be #RRGGBB.")
    if brightness is not None and (not isinstance(brightness, (int, float)) or not 0 <= brightness <= 1): raise ProtocolError("invalid_parameter", "brightness must be 0..1.")
    for name in ("min_intensity", "max_intensity"):
        value = payload.get(name)
        if value is not None and (not isinstance(value, (int, float)) or not 0 <= value <= 1): raise ProtocolError("invalid_parameter", f"{name} must be 0..1.")
    if payload.get("duration_ms", 0) < 0 or payload.get("period_ms", 1) <= 0: raise ProtocolError("invalid_parameter", "Invalid duration or period.")


class ControllerTransport(Protocol):
    def write(self, data: bytes) -> None: ...

