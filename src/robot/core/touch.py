"""Provider-neutral read model for completed face-display gestures."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional


TOUCH_EVENT = "touch.event"
TOUCH_TAP = "touch_tap"
TOUCH_LONG_PRESS = "touch_long_press"
TOUCH_SWIPE_LEFT = "touch_swipe_left"
TOUCH_SWIPE_RIGHT = "touch_swipe_right"

TOUCH_EVENT_NAMES = {
    "tap": TOUCH_TAP,
    "long_press": TOUCH_LONG_PRESS,
    "swipe_left": TOUCH_SWIPE_LEFT,
    "swipe_right": TOUCH_SWIPE_RIGHT,
}


@dataclass(frozen=True)
class TouchStatus:
    """The last accepted physical gesture, safe for application adapters."""

    enabled: bool
    last_event: Optional[str] = None
    last_event_at: Optional[str] = None
    x: Optional[int] = None
    y: Optional[int] = None
    normalized_x: Optional[float] = None
    normalized_y: Optional[float] = None
    duration_ms: Optional[int] = None

    def record(self, event, *, occurred_at: Optional[str] = None) -> "TouchStatus":
        return TouchStatus(
            enabled=self.enabled, last_event=event.kind.value,
            last_event_at=occurred_at or datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            x=event.x, y=event.y, normalized_x=event.normalized_x,
            normalized_y=event.normalized_y, duration_ms=event.duration_ms,
        )

    def document(self) -> dict:
        return {
            "enabled": self.enabled, "last_event": self.last_event,
            "last_event_at": self.last_event_at, "x": self.x, "y": self.y,
            "normalized_x": self.normalized_x, "normalized_y": self.normalized_y,
            "duration_ms": self.duration_ms,
        }
