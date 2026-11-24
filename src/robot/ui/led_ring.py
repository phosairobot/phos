# SPDX-License-Identifier: Apache-2.0
"""Provider-neutral semantic LED ring controller."""
from __future__ import annotations

from dataclasses import dataclass
import logging
import math
from threading import Event, Lock, Thread
import time
from typing import Callable, Optional, Tuple

from robot.config import LED_RING_COLOR_RGB
from robot.hardware.ws2812b import LEDRingProvider, RGB

from .state import EnvironmentalLEDIntent, FaceState, TransientVisualEffect, VisualAccent

logger = logging.getLogger(__name__)

_ACCENT_COLORS = {
    VisualAccent.WARM: LED_RING_COLOR_RGB["turquoise"],
    VisualAccent.COOL: LED_RING_COLOR_RGB["blue"],
    VisualAccent.CURIOUS: LED_RING_COLOR_RGB["cyan"],
    VisualAccent.ALERT: LED_RING_COLOR_RGB["yellow"],
    VisualAccent.SLEEPY: LED_RING_COLOR_RGB["violet"],
    VisualAccent.ERROR: LED_RING_COLOR_RGB["red"],
}
_ENVIRONMENTAL_COLORS = {
    EnvironmentalLEDIntent.COLD: LED_RING_COLOR_RGB["blue"],
    EnvironmentalLEDIntent.WARM: LED_RING_COLOR_RGB["orange"],
    EnvironmentalLEDIntent.AIR_QUALITY_WARNING: LED_RING_COLOR_RGB["yellow"],
    EnvironmentalLEDIntent.AIR_QUALITY_BAD: LED_RING_COLOR_RGB["red"],
}


@dataclass(frozen=True)
class LEDRingSettings:
    enabled: bool
    led_count: int
    gpio_pin: int
    brightness: float
    base_color: str
    follow_visual_state: bool
    update_rate_hz: float
    imu_reactions_enabled: bool = True
    directional_strength: float = .65
    directional_sector_size: int = 3
    shake_strength: float = .85
    impact_strength: float = 1.0
    imu_animation_color: str = "yellow"
    directional_animation_speed: float = 12.0
    bottom_led_index: int = 0
    forward_led_index: int = 0
    clockwise: bool = True


@dataclass(frozen=True)
class LEDRingFrame:
    semantic_state: str
    effect: str
    pixels: Tuple[RGB, ...]
    directional_target: Optional[str] = None


def scale_rgb(color: RGB, brightness: float) -> RGB:
    """Scale all logical RGB channels equally; transport conversion is elsewhere."""
    return tuple(round(channel * brightness) for channel in color)


def led_frame(state: FaceState, settings: LEDRingSettings, now: float) -> LEDRingFrame:
    """Map semantic visual and motion intent to a provider-neutral frame."""
    state = state.normalized()
    accent = state.accent if settings.follow_visual_state else VisualAccent.NEUTRAL
    environmental_intent = state.environmental_led_intent if settings.follow_visual_state else None
    color = _ENVIRONMENTAL_COLORS.get(environmental_intent, _ACCENT_COLORS.get(accent, LED_RING_COLOR_RGB[settings.base_color]))
    base_color = LED_RING_COLOR_RGB[settings.base_color]
    effect, multiplier = "steady", 1.0
    if environmental_intent is not None:
        effect, multiplier = "steady", 1.0
    elif accent in {VisualAccent.WARM, VisualAccent.COOL, VisualAccent.CURIOUS}:
        effect, multiplier = "pulse", .88 + .12 * (1 + math.sin(now * (1.5 if accent is VisualAccent.WARM else 1.0))) / 2
    elif accent is VisualAccent.ALERT:
        effect, multiplier = "alert_pulse", .65 + .35 * max(state.reaction_strength, .5) * (1 + math.sin(now * 12)) / 2
    elif accent is VisualAccent.SLEEPY:
        effect, multiplier = "fade", .25 + .10 * (1 + math.sin(now * .7)) / 2
    elif accent is VisualAccent.ERROR:
        effect, multiplier = "steady", .85
    scale = max(0.0, min(1.0, settings.brightness * multiplier))
    pixels = [scale_rgb(color, scale)] * settings.led_count
    # Error/sleep semantic states remain above IMU reactions. Motion is supplied
    # by BehaviorEngine, never inferred from raw sensor input in this layer.
    if (settings.imu_reactions_enabled and accent not in {VisualAccent.ERROR, VisualAccent.SLEEPY}
            and state.motion_state):
        motion = state.motion_state
        if motion in {"tilt_left", "tilt_right", "tilt_forward", "tilt_back"}:
            pixels = [scale_rgb(color, scale)] * settings.led_count
            pixels = _directional_pixels(pixels, LED_RING_COLOR_RGB[settings.imu_animation_color], settings,
                                         motion, now, state.motion_started_at)
            effect = motion
        elif motion == "moving":
            activity = .88 + .12 * (1 + math.sin(now * 4)) / 2
            pixels = [scale_rgb(color, min(1.0, scale * activity))] * settings.led_count
            effect = "moving_pulse"
        elif motion in {"shake", "impact"}:
            started_at = now if state.motion_event_at is None else state.motion_event_at
            elapsed = max(0.0, now - started_at)
            # The behavior owns duration; this short envelope avoids restarting per frame.
            period, pulses = (.13, 3) if motion == "shake" else (.10, 1)
            phase = elapsed / period
            envelope = max(0.0, 1.0 - phase / pulses)
            pulse = max(0.0, math.sin(math.pi * (phase % 1.0))) * envelope
            strength = settings.shake_strength if motion == "shake" else settings.impact_strength
            alert = _ACCENT_COLORS[VisualAccent.ALERT]
            pixels = [scale_rgb(alert, min(1.0, scale + strength * max(.25, pulse)))] * settings.led_count
            effect = motion
    # Presence is below error/sleep and impact/shake, but above persistent state.
    transient = state.transient_effect
    if (transient is not None and state.transient_effect_duration_seconds is not None
            and state.transient_effect_duration_seconds > 0
            and accent not in {VisualAccent.ERROR, VisualAccent.SLEEPY}
            and state.motion_state not in {"shake", "impact"}):
        started = state.transient_effect_started_at or now
        duration = state.transient_effect_duration_seconds
        elapsed = max(0.0, now - started)
        if elapsed < duration:
            progress = min(settings.led_count, max(1, int(settings.led_count * elapsed / duration) + 1))
            cyan = LED_RING_COLOR_RGB["cyan"]
            pixels = [scale_rgb(color, scale)] * settings.led_count
            direction = 1 if state.transient_effect_direction == "clockwise" else -1
            for offset in range(progress):
                strength = 1.0 if transient is TransientVisualEffect.PRESENCE_ENTERED else max(.25, 1 - offset / settings.led_count)
                pixels[(settings.forward_led_index + direction * offset) % settings.led_count] = scale_rgb(cyan, min(1.0, settings.brightness * strength))
            effect = transient.value
    target = state.motion_state.removeprefix("tilt_") if (settings.imu_reactions_enabled and state.motion_state
                                                             and state.motion_state.startswith("tilt_")
                                                             and accent not in {VisualAccent.ERROR, VisualAccent.SLEEPY}) else None
    semantic_state = environmental_intent.value if environmental_intent is not None else accent.value
    return LEDRingFrame(semantic_state, effect, tuple(pixels), target)


def _direction_index(direction: str, settings: LEDRingSettings) -> int:
    """Map forward-relative direction to pixels; clockwise controls index ordering."""
    count = settings.led_count
    forward = settings.forward_led_index % count
    quarter = max(1, round(count / 4))
    sign = 1 if settings.clockwise else -1
    offsets = {"tilt_forward": 0, "tilt_back": 2 * quarter,
               "tilt_right": sign * quarter, "tilt_left": -sign * quarter}
    return (forward + offsets[direction]) % count


def _directional_pixels(pixels, color, settings, direction, now, started_at):
    """Cumulatively replace base pixels using the configured physical layout."""
    count = settings.led_count
    # FaceState records state activation so a newly established tilt always
    # begins at its physical start point, independent of worker cadence.
    elapsed = max(0.0, now - (now if started_at is None else started_at))
    filled = min(count, 1 + int(elapsed * settings.directional_animation_speed))
    peak = min(1.0, settings.brightness + settings.directional_strength)
    clockwise = 1 if settings.clockwise else -1
    if direction == "tilt_left":
        start, steps = _direction_index(direction, settings), range(filled)
        for offset in steps:
            pixels[(start + clockwise * offset) % count] = scale_rgb(color, peak)
    elif direction == "tilt_right":
        start, steps = _direction_index(direction, settings), range(filled)
        for offset in steps:
            pixels[(start - clockwise * offset) % count] = scale_rgb(color, peak)
    else:
        # Back grows from bottom; forward grows from the configured top/forward
        # reference. Both advance one clockwise and one counter-clockwise front.
        start = settings.bottom_led_index % count if direction == "tilt_back" else settings.forward_led_index % count
        for offset in range(filled):
            pixels[(start + clockwise * offset) % count] = scale_rgb(color, peak)
            pixels[(start - clockwise * offset) % count] = scale_rgb(color, peak)
    return pixels


class LEDRingController:
    """Dedicated low-rate worker; provider failures never affect PHOS rendering."""

    def __init__(self, settings: LEDRingSettings, state_supplier: Callable[[], FaceState],
                 provider_factory: Callable[[LEDRingSettings], LEDRingProvider], *, clock=time.monotonic) -> None:
        self._settings, self._state_supplier, self._factory, self._clock = settings, state_supplier, provider_factory, clock
        self._lock, self._stop = Lock(), Event()
        self._thread: Optional[Thread] = None
        self._status = {"status": "disabled" if not settings.enabled else "starting", "semantic_state": None,
                        "effect": None, "directional_target": None, "error": None}

    def start(self) -> None:
        with self._lock:
            if not self._settings.enabled or self._thread is not None:
                return
            self._stop.clear()
            self._status.update(status="starting", error=None)
            self._thread = Thread(target=self._run, name="phos-led-ring", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        with self._lock:
            thread = self._thread
            self._thread = None
            self._stop.set()
        if thread is not None:
            thread.join(timeout=2)
        with self._lock:
            if not self._settings.enabled:
                self._status.update(status="disabled", semantic_state=None, effect=None, error=None)

    def configure(self, settings: LEDRingSettings) -> None:
        """Apply visual settings live; lifecycle classifies pin/count as restart-only."""
        with self._lock:
            previous_enabled = self._settings.enabled
            self._settings = settings
        if previous_enabled != settings.enabled:
            self.stop()
            if settings.enabled:
                self.start()

    def snapshot(self) -> dict:
        with self._lock:
            return dict(self._status)

    def _run(self) -> None:
        provider = None
        last_warning = float("-inf")
        try:
            with self._lock:
                settings = self._settings
            provider = self._factory(settings)
            provider.start()
            with self._lock:
                self._status.update(status="available", error=None)
            while not self._stop.is_set():
                with self._lock:
                    settings = self._settings
                frame = led_frame(self._state_supplier(), settings, self._clock())
                try:
                    provider.write(frame.pixels)
                    with self._lock:
                        self._status.update(status="available", semantic_state=frame.semantic_state,
                                            effect=frame.effect, directional_target=frame.directional_target, error=None)
                except Exception as error:
                    now = self._clock()
                    with self._lock:
                        self._status.update(status="unavailable", error=type(error).__name__)
                    if now - last_warning >= 60:
                        logger.warning("LED ring unavailable (%s: %s); continuing without LED output", type(error).__name__, error)
                        last_warning = now
                self._stop.wait(1.0 / settings.update_rate_hz)
        except Exception as error:
            with self._lock:
                self._status.update(status="unavailable", error=type(error).__name__)
            logger.warning("LED ring unavailable (%s: %s); continuing without LED output", type(error).__name__, error)
        finally:
            if provider is not None:
                try:
                    provider.close()
                except Exception:
                    logger.debug("LED ring cleanup failed", exc_info=True)
