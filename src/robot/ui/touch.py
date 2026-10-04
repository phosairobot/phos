"""Provider-neutral recognition of one-pointer face-display interactions."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import time
from typing import Callable, Optional


class TouchKind(str, Enum):
    TAP = "tap"
    LONG_PRESS = "long_press"
    SWIPE_LEFT = "swipe_left"
    SWIPE_RIGHT = "swipe_right"


@dataclass(frozen=True)
class TouchEvent:
    kind: TouchKind
    x: int; y: int; normalized_x: float; normalized_y: float
    started_at: float; ended_at: float; duration_ms: int
    delta_x: int; delta_y: int; source: str = "face_display"


class TouchInputAdapter:
    """Classify completed pointer interactions without exposing Tk events."""
    def __init__(self, emit: Callable[[TouchEvent], None], *, width=800, height=600,
                 tap_max_duration_ms=350, tap_max_movement_px=20,
                 long_press_min_duration_ms=800, long_press_max_movement_px=20, swipe_min_distance_px=100,
                 swipe_max_vertical_drift_px=80, swipe_max_duration_ms=1000,
                 clock=time.monotonic) -> None:
        self._emit, self._width, self._height, self._clock = emit, width, height, clock
        self._tap_duration, self._tap_movement, self._long, self._long_movement, self._swipe_distance, self._swipe_drift, self._swipe_duration = tap_max_duration_ms, tap_max_movement_px, long_press_min_duration_ms, long_press_max_movement_px, swipe_min_distance_px, swipe_max_vertical_drift_px, swipe_max_duration_ms
        self._down: Optional[tuple[int, int, float]] = None
    def down(self, x: int, y: int) -> None: self._down = (x, y, self._clock())
    def release(self, x: int, y: int) -> Optional[TouchEvent]:
        if self._down is None: return None
        sx, sy, started = self._down; self._down = None; ended = self._clock(); duration = round((ended-started)*1000); dx, dy = x-sx, y-sy
        kind = None
        if duration <= self._swipe_duration and abs(dx) >= self._swipe_distance and abs(dy) <= self._swipe_drift: kind = TouchKind.SWIPE_RIGHT if dx > 0 else TouchKind.SWIPE_LEFT
        elif duration >= self._long and max(abs(dx), abs(dy)) <= self._long_movement: kind = TouchKind.LONG_PRESS
        elif duration <= self._tap_duration and max(abs(dx), abs(dy)) <= self._tap_movement: kind = TouchKind.TAP
        if kind is None: return None
        event = TouchEvent(kind, x, y, min(1,max(0,x/self._width)), min(1,max(0,y/self._height)), started, ended, duration, dx, dy)
        self._emit(event); return event
