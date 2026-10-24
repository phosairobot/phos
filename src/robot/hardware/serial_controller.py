"""Reconnectable USB/serial transport for the controller-neutral phos-hw v1 protocol."""
from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from enum import Enum
from typing import Callable

from robot.core.behaviors import Behavior
from robot.hardware.controller_protocol import (ControllerMessage, JsonLinesDecoder, ProtocolError,
                                                safe_frame_repr)
from robot.ui.touch import TouchEvent, TouchKind

logger = logging.getLogger(__name__)


class ControllerConnection(str, Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"


class SerialTransportError(OSError):
    """Serial failure annotated with the I/O operation and configured device."""
    def __init__(self, operation: str, device: str, error: BaseException):
        super().__init__(str(error))
        self.operation, self.device = operation, device
        self.original_type = type(error).__name__


class SerialControllerTransport:
    """Small blocking-pyserial adapter; callers own its asyncio lifecycle."""
    def __init__(self, device: str, baud_rate: int, *, serial_factory=None) -> None:
        self.device, self.baud_rate = device, baud_rate
        self._serial_factory = serial_factory
        self._serial = None
        self._write_lock = asyncio.Lock()

    def open(self) -> None:
        if self._serial_factory is None:
            try:
                import serial
            except ImportError as error:
                raise RuntimeError("pyserial is not installed") from error
            self._serial_factory = serial.Serial
        try:
            self._serial = self._serial_factory(self.device, self.baud_rate, timeout=.25, write_timeout=1)
        except OSError as error:
            raise SerialTransportError("open", self.device, error) from error

    def read(self, size=1024) -> bytes:
        if self._serial is None:
            raise OSError("serial transport is closed")
        try:
            return self._serial.read(size)
        except OSError as error:
            raise SerialTransportError("read", self.device, error) from error

    async def write(self, data: bytes) -> None:
        if self._serial is None:
            raise OSError("serial transport is closed")
        async with self._write_lock:
            try:
                await asyncio.to_thread(self._serial.write, data)
                await asyncio.to_thread(self._serial.flush)
            except OSError as error:
                raise SerialTransportError("write", self.device, error) from error

    def close(self) -> None:
        serial, self._serial = self._serial, None
        if serial is not None:
            serial.close()


class ControllerService(Behavior):
    """Own a controller connection and translate supported pushed sensors semantically."""
    name = "hardware-controller"

    def __init__(self, core, *, enabled: bool, device: str, baud_rate: int,
                 reconnect_delay_seconds: float, heartbeat_interval_seconds: float,
                 heartbeat_timeout_seconds: float, touch_sink: Callable[[TouchEvent], None],
                 sensor_update_sink: Callable[[str, int | float], None] = lambda _name, _value: None,
                 imu_update_sink: Callable[[ControllerMessage], None] = lambda _message: None,
                 provider_disconnected_sink: Callable[[], None] = lambda: None,
                 transport_factory=SerialControllerTransport) -> None:
        self._core, self._enabled = core, enabled
        self._device, self._baud_rate = device, baud_rate
        self._reconnect_delay, self._heartbeat_interval, self._heartbeat_timeout = reconnect_delay_seconds, heartbeat_interval_seconds, heartbeat_timeout_seconds
        self._touch_sink, self._sensor_update_sink, self._imu_update_sink = touch_sink, sensor_update_sink, imu_update_sink
        self._provider_disconnected_sink, self._factory = provider_disconnected_sink, transport_factory
        self._state = ControllerConnection.DISCONNECTED
        self._task = None
        self._transport = None
        self._last_peer_activity = 0.0

    @property
    def state(self) -> ControllerConnection: return self._state

    async def start(self) -> None:
        if self._enabled:
            self._task = self._core.tasks.create(self._run(), name="hardware-controller")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
        if self._transport is not None:
            await asyncio.to_thread(self._transport.close)
            self._transport = None
        self._state = ControllerConnection.DISCONNECTED

    async def _run(self) -> None:
        while True:
            self._state = ControllerConnection.CONNECTING
            transport = self._factory(self._device, self._baud_rate)
            try:
                await asyncio.to_thread(transport.open)
                self._transport, self._state = transport, ControllerConnection.CONNECTED
                self._last_peer_activity = time.monotonic()
                logger.info("PHOS CONTROLLER: connected device=%s", self._device)
                await self._send("request_capabilities", {})
                await self._read_connected()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self._provider_disconnected_sink()
                logger.warning("PHOS CONTROLLER: disconnected device=%s operation=%s exception=%s message=%s",
                               getattr(error, "device", self._device), getattr(error, "operation", "lifecycle"),
                               getattr(error, "original_type", type(error).__name__), self._safe_error_message(error))
            finally:
                if self._transport is transport:
                    self._transport = None
                await asyncio.to_thread(transport.close)
                self._state = ControllerConnection.DISCONNECTED
            await asyncio.sleep(self._reconnect_delay)

    async def _read_connected(self) -> None:
        decoder, next_heartbeat = JsonLinesDecoder(), time.monotonic() + self._heartbeat_interval
        while True:
            if time.monotonic() - self._last_peer_activity > self._heartbeat_timeout:
                raise OSError("controller heartbeat timed out")
            if time.monotonic() >= next_heartbeat:
                await self._send("ping", {})
                next_heartbeat = time.monotonic() + self._heartbeat_interval
            data = await asyncio.to_thread(self._transport.read)
            if not data:
                continue
            try:
                messages = decoder.feed(data)
            except ProtocolError as error:
                logger.warning("PHOS CONTROLLER: rejected input code=%s type=%s reason=%s",
                               error.code, error.message_type or "unparseable", self._safe_error_message(error))
                if error.frame is not None:
                    logger.debug("PHOS CONTROLLER: rejected frame=%s", safe_frame_repr(error.frame))
                continue
            for message in messages:
                self._last_peer_activity = time.monotonic()
                await self._message(message)

    async def _send(self, kind: str, payload: dict) -> None:
        message = ControllerMessage(kind, payload).document()
        await self._transport.write(json.dumps(message, separators=(",", ":")).encode("utf-8") + b"\n")

    async def _message(self, message: ControllerMessage) -> None:
        if message.type in {"motion_update", "orientation_update"}:
            logger.debug("PHOS CONTROLLER RX IMU raw=%s", safe_frame_repr(message.raw_frame or b"", limit=8192))
            try:
                self._imu_update_sink(message)
            except Exception as error:
                logger.warning("PHOS CONTROLLER: IMU integration failure type=%s exception=%s message=%s",
                               message.type, type(error).__name__, self._safe_error_message(error))
            return
        if message.type != "sensor_update":
            return
        if message.payload.get("sensor") == "push":
            if message.payload.get("event") != "pressed":
                return
            now = time.monotonic()
            event = TouchEvent(TouchKind.TAP, 0, 0, .5, .5, now, now, 0, 0, 0, source="external_controller")
            self._touch_sink(event)
            return
        sensor = message.payload.get("sensor")
        expected_units = {"temperature": "c", "pressure": "hpa", "humidity": "%",
                          "eco2": "ppm", "tvoc": "ppb"}
        if sensor not in expected_units:
            self._invalid_sensor_update(message, "unsupported semantic sensor")
            return
        value, unit = message.payload.get("value"), message.payload.get("unit")
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            self._invalid_sensor_update(message, "value must be a finite number")
            return
        if unit != expected_units[sensor]:
            self._invalid_sensor_update(message, f"unit must be {expected_units[sensor]!r}")
            return
        if sensor in {"eco2", "tvoc"} and type(value) is not int:
            self._invalid_sensor_update(message, "value must be an integer")
            return
        try:
            self._sensor_update_sink(sensor, value)
        except Exception as error:
            logger.warning("PHOS CONTROLLER: sensor integration failure sensor=%s value=%r unit=%r exception=%s message=%s raw=%s",
                           sensor, value, unit, type(error).__name__, self._safe_error_message(error),
                           safe_frame_repr(message.raw_frame or b"", limit=8192))
            logger.debug("PHOS CONTROLLER: sensor integration traceback", exc_info=True)

    @staticmethod
    def _invalid_sensor_update(message: ControllerMessage, reason: str) -> None:
        payload = message.payload
        raw = safe_frame_repr(message.raw_frame or b"", limit=8192)
        logger.warning("PHOS CONTROLLER: invalid sensor update raw=%s sensor=%r value=%r unit=%r reason=%s",
                       raw, payload.get("sensor"), payload.get("value"), payload.get("unit"), reason)

    @staticmethod
    def _safe_error_message(error: BaseException, *, limit: int = 240) -> str:
        message = str(error).replace("\n", " ").replace("\r", " ")
        return message[:limit] + ("..." if len(message) > limit else "")
