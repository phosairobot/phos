# SPDX-License-Identifier: Apache-2.0
"""Lightweight, provider-neutral interpretation of IMU samples."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import logging
import math
from typing import TYPE_CHECKING

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from robot.sensors import IMUReading


class MotionState(str, Enum):
    STILL = "still"
    MOVING = "moving"
    TILT_LEFT = "tilt_left"
    TILT_RIGHT = "tilt_right"
    TILT_FORWARD = "tilt_forward"
    TILT_BACK = "tilt_back"
    SHAKE = "shake"
    IMPACT = "impact"


@dataclass(frozen=True)
class MotionSettings:
    movement_threshold_m_s2: float
    tilt_threshold_m_s2: float
    shake_threshold_deg_s: float
    impact_threshold_m_s2: float
    confirmation_seconds: float
    cooldown_seconds: float
    tilt_exit_threshold_m_s2: float = 3.0
    lateral_axis: str = "x"
    forward_axis: str = "y"


@dataclass(frozen=True)
class MotionEvent:
    state: MotionState
    at_monotonic: float


class MotionInterpreter:
    """Classify filtered samples without device access or orientation fusion."""

    _FILTER_TIME_CONSTANT_SECONDS = .2
    _STANDARD_GRAVITY = 9.80665

    def __init__(self, settings: MotionSettings):
        self.configure(settings)
        self._filtered_acceleration = None
        self._state = MotionState.STILL
        self._candidate = None
        self._candidate_since = None
        self._last_event_at = float("-inf")
        self._last_spin = None
        self._last_sample_at = None
        self._last_diagnostic_at = float("-inf")

    @property
    def state(self):
        return self._state

    def configure(self, settings: MotionSettings):
        self._settings = settings
        self._filtered_acceleration = None
        self._last_sample_at = None
        self._candidate = self._candidate_since = None
        self._tilt = None

    @staticmethod
    def _magnitude(values):
        return math.sqrt(sum(value * value for value in values))

    def _filtered(self, reading, now):
        sample = (reading.acceleration_x_m_s2, reading.acceleration_y_m_s2, reading.acceleration_z_m_s2)
        if self._filtered_acceleration is None:
            self._filtered_acceleration = sample
        else:
            alpha = 1 - math.exp(-max(0, now - self._last_sample_at) / self._FILTER_TIME_CONSTANT_SECONDS)
            self._filtered_acceleration = tuple(alpha * value + (1 - alpha) * old
                                                for value, old in zip(sample, self._filtered_acceleration))
        self._last_sample_at = now
        return self._filtered_acceleration

    def _shake(self, reading, now):
        gyros = (reading.angular_velocity_x_deg_s, reading.angular_velocity_y_deg_s,
                 reading.angular_velocity_z_deg_s)
        axis = max(range(3), key=lambda index: abs(gyros[index]))
        value = gyros[axis]
        if abs(value) < self._settings.shake_threshold_deg_s:
            return False
        current = (axis, 1 if value > 0 else -1, now)
        previous, self._last_spin = self._last_spin, current
        return previous is not None and previous[0] == axis and previous[1] != current[1] and now - previous[2] <= 2.0

    def _classify(self, reading, now):
        acceleration = self._filtered(reading, now)
        magnitude = self._magnitude(acceleration)
        movement = abs(magnitude - self._STANDARD_GRAVITY)
        raw_magnitude = self._magnitude((reading.acceleration_x_m_s2, reading.acceleration_y_m_s2,
                                         reading.acceleration_z_m_s2))

        def component(axis):
            value = acceleration["xyz".index(axis[-1])]
            return (-value if axis.startswith("-") else value) / max(magnitude, 1e-9)

        lateral = component(self._settings.lateral_axis)
        forward = component(self._settings.forward_axis)
        # Normalized gravity components are sin(roll/pitch), independent of scale.
        roll = math.degrees(math.asin(max(-1, min(1, lateral))))
        pitch = math.degrees(math.asin(max(-1, min(1, forward))))
        metrics = {MotionState.TILT_LEFT: -lateral, MotionState.TILT_RIGHT: lateral,
                   MotionState.TILT_FORWARD: forward, MotionState.TILT_BACK: -forward}
        enter = self._settings.tilt_threshold_m_s2 / self._STANDARD_GRAVITY
        leave = self._settings.tilt_exit_threshold_m_s2 / self._STANDARD_GRAVITY
        tilt = None
        if magnitude >= self._STANDARD_GRAVITY * .25:
            # Retain the direction through the hysteresis band and diagonal noise.
            if self._tilt is not None and metrics[self._tilt] >= leave:
                tilt = self._tilt
            else:
                strongest = max(metrics, key=metrics.get)
                if metrics[strongest] >= enter:
                    tilt = strongest
        if raw_magnitude >= self._settings.impact_threshold_m_s2:
            winner, reason = MotionState.IMPACT, "impact priority"
        elif self._shake(reading, now):
            winner, reason = MotionState.SHAKE, "shake priority"
        elif tilt is not None:
            winner, reason = tilt, "tilt priority over movement"
        elif movement > self._settings.movement_threshold_m_s2:
            winner = MotionState.MOVING
            reason = "gravity too small" if magnitude < self._STANDARD_GRAVITY * .25 else "tilt below enter/exit threshold"
        else:
            winner, reason = MotionState.STILL, "level and below movement threshold"
        self._diagnostic = (acceleration, pitch, roll, movement, enter, leave, tilt, winner, reason)
        return winner

    def observe(self, reading: IMUReading, now: float) -> tuple[MotionState, MotionEvent | None]:
        """Return a debounced state and, at most, one cooldown-limited event."""
        candidate = self._classify(reading, now)
        result = self._confirm(candidate, now)
        if now - self._last_diagnostic_at >= 1.0:
            self._last_diagnostic_at = now
            logger.debug("IMU motion filtered=%s pitch=%.1f roll=%.1f moving_metric=%.3f "
                         "tilt_enter_g=%.3f tilt_exit_g=%.3f tilt_candidate=%s winner=%s reason=%s "
                         "confirmed=%s confirmation_seconds=%.3f",
                         *self._diagnostic, self._state.value, self._settings.confirmation_seconds)
        return result

    def _confirm(self, candidate, now):
        if candidate is MotionState.IMPACT:
            self._candidate = None
            self._candidate_since = None
            self._state = candidate
            self._tilt = candidate if tilt_direction(candidate) else None
            return self._event(candidate, now)
        # An opposite high-rate pair is its own temporal confirmation.
        if candidate is MotionState.SHAKE:
            self._candidate = None
            self._candidate_since = None
            self._state = candidate
            self._tilt = candidate if tilt_direction(candidate) else None
            return self._event(candidate, now)
        if candidate is not self._candidate:
            self._candidate, self._candidate_since = candidate, now
            return self._state, None
        if now - self._candidate_since >= self._settings.confirmation_seconds:
            self._tilt = candidate if tilt_direction(candidate) else None
            if candidate is not self._state:
                self._state = candidate
                return self._event(candidate, now)
        return self._state, None

    def _event(self, state, now):
        if now - self._last_event_at < self._settings.cooldown_seconds:
            return state, None
        self._last_event_at = now
        return state, MotionEvent(state, now)


def tilt_direction(state: MotionState) -> str | None:
    return state.value if state in {MotionState.TILT_LEFT, MotionState.TILT_RIGHT,
                                    MotionState.TILT_FORWARD, MotionState.TILT_BACK} else None
