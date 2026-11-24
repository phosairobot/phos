# SPDX-License-Identifier: Apache-2.0
"""Lazy Raspberry Pi adapter for WS2812B LEDs."""
from __future__ import annotations

import json
import socket
from pathlib import Path
from typing import Protocol, Sequence, Tuple


RGB = Tuple[int, int, int]


class LEDRingProvider(Protocol):
    def start(self) -> None: ...
    def write(self, pixels: Sequence[RGB]) -> None: ...
    def close(self) -> None: ...


class WS2812BProvider:
    """Adapt rpi-ws281x without importing it on non-Pi systems."""

    def __init__(self, *, led_count: int, gpio_pin: int) -> None:
        self._led_count = led_count
        self._gpio_pin = gpio_pin
        self._strip = None

    def start(self) -> None:
        if self._strip is not None:
            return
        try:
            from rpi_ws281x import Color, PixelStrip, ws
        except ImportError as error:
            raise RuntimeError("rpi-ws281x is not installed; install PHOS with the led-ring extra") from error
        # Color accepts logical RGB; the explicit strip type performs the
        # standard WS2812B GRB ordering on the wire.
        strip = PixelStrip(self._led_count, self._gpio_pin, brightness=255,
                           strip_type=ws.WS2811_STRIP_GRB)
        self._color = Color
        strip.begin()
        self._strip = strip

    def write(self, pixels: Sequence[RGB]) -> None:
        if self._strip is None:
            raise RuntimeError("WS2812B provider is not started")
        if len(pixels) != self._led_count:
            raise ValueError("WS2812B pixel count does not match configured LED count")
        for index, (red, green, blue) in enumerate(pixels):
            self._strip.setPixelColor(index, self._color(int(red), int(green), int(blue)))
        self._strip.show()

    def close(self) -> None:
        if self._strip is None:
            return
        try:
            self.write([(0, 0, 0)] * self._led_count)
        finally:
            self._strip = None


class LEDRingSocketProvider:
    """Unprivileged client for the root-owned WS2812B helper socket."""

    def __init__(self, *, led_count: int, socket_path: str = "/run/phos-led.sock") -> None:
        self._led_count = led_count
        self._socket_path = socket_path
        self._socket = None

    def start(self) -> None:
        if not Path(self._socket_path).exists():
            raise RuntimeError("WS2812B helper socket is unavailable")
        self._socket = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)

    def write(self, pixels: Sequence[RGB]) -> None:
        if self._socket is None:
            raise RuntimeError("WS2812B socket provider is not started")
        if len(pixels) != self._led_count:
            raise ValueError("WS2812B pixel count does not match configured LED count")
        payload = json.dumps({"pixels": pixels}, separators=(",", ":")).encode()
        self._socket.sendto(payload, self._socket_path)

    def close(self) -> None:
        if self._socket is not None:
            try:
                self.write([(0, 0, 0)] * self._led_count)
            except OSError:
                pass
            self._socket.close()
            self._socket = None
