"""Bounded, source-owned transient presentation overlays."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import Enum
import time

from .behaviors import Behavior


class SemanticOverlayIntent(str, Enum):
    SUCCESS = "success"
    INFO = "info"
    ATTENTION = "attention"
    WARNING = "warning"
    ERROR = "error"
    SENSOR_ALERT = "sensor_alert"


_PRIORITY = {SemanticOverlayIntent.ERROR: 60, SemanticOverlayIntent.WARNING: 50,
             SemanticOverlayIntent.SENSOR_ALERT: 40, SemanticOverlayIntent.ATTENTION: 30,
             SemanticOverlayIntent.SUCCESS: 20, SemanticOverlayIntent.INFO: 10}
_DEFAULT_SECONDS = {SemanticOverlayIntent.SUCCESS: 2.0, SemanticOverlayIntent.INFO: 3.0,
                    SemanticOverlayIntent.ATTENTION: 2.5, SemanticOverlayIntent.WARNING: 3.0,
                    SemanticOverlayIntent.ERROR: 4.0, SemanticOverlayIntent.SENSOR_ALERT: 3.0}
_MIN_SECONDS, _MAX_SECONDS = .5, 10.0
_MAX_SOURCES, _MAX_MESSAGE = 32, 120


@dataclass(frozen=True)
class OverlayViewState:
    """Renderer-facing semantic state, deliberately free of drawing details."""
    visible: bool = False
    intent: SemanticOverlayIntent | None = None
    message: str | None = None


@dataclass(frozen=True)
class _Request:
    intent: SemanticOverlayIntent
    message: str | None
    expires_at: float
    sequence: int


class SemanticOverlayService(Behavior):
    """Arbitrate one automatically expiring overlay per semantic source."""
    name = "semantic-overlay"

    def __init__(self, events=None, *, clock=time.monotonic):
        self._clock = clock
        self._events = events
        self._requests: dict[str, _Request] = {}
        self._sequence = 0
        self._task: asyncio.Task | None = None
        self._unsubscribers = []

    @staticmethod
    def _message(value: str | None) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str) or len(value) > _MAX_MESSAGE:
            raise ValueError("Overlay message is invalid")
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise ValueError("Overlay message must be single-line plain text")
        return value.strip() or None

    async def request(self, source: str, intent: SemanticOverlayIntent | str, *, message: str | None = None,
                      duration_seconds: float | None = None) -> OverlayViewState:
        if not isinstance(source, str) or not source or len(source) > 64:
            raise ValueError("Overlay source is invalid")
        intent = SemanticOverlayIntent(intent)
        duration = _DEFAULT_SECONDS[intent] if duration_seconds is None else duration_seconds
        if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not _MIN_SECONDS <= duration <= _MAX_SECONDS:
            raise ValueError("Overlay duration is invalid")
        self._expire()
        if source not in self._requests and len(self._requests) >= _MAX_SOURCES:
            raise ValueError("Overlay source limit reached")
        self._sequence += 1
        self._requests[source] = _Request(intent, self._message(message), self._clock() + float(duration), self._sequence)
        return self.view_state()

    async def clear(self, source: str) -> OverlayViewState:
        self._requests.pop(source, None)
        return self.view_state()

    def _expire(self) -> None:
        now = self._clock()
        for source in [name for name, request in self._requests.items() if request.expires_at <= now]:
            self._requests.pop(source, None)

    def view_state(self) -> OverlayViewState:
        self._expire()
        if not self._requests:
            return OverlayViewState()
        request = max(self._requests.values(), key=lambda item: (_PRIORITY[item.intent], item.sequence))
        return OverlayViewState(True, request.intent, request.message)

    async def start(self) -> None:
        if self._task is None:
            if self._events is not None:
                from .behavior_engine import ENVIRONMENTAL_STATE_CHANGED
                self._unsubscribers = [self._events.subscribe(ENVIRONMENTAL_STATE_CHANGED, self._environment)]
            self._task = asyncio.create_task(self._expiry_loop(), name="semantic-overlay")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
        for unsubscribe in self._unsubscribers:
            unsubscribe()
        self._unsubscribers = []
        self._requests.clear()

    async def _environment(self, event) -> None:
        """Use existing interpreted environment state, never raw sensor providers."""
        message = {"air_quality_warning": "Air quality warning", "air_quality_bad": "Air quality alert"}.get(
            event.data.get("state"))
        if message is None:
            await self.clear("sensor")
        else:
            await self.request("sensor", SemanticOverlayIntent.SENSOR_ALERT, message=message)

    async def _expiry_loop(self) -> None:
        while True:
            self._expire()
            delay = min((request.expires_at for request in self._requests.values()), default=self._clock() + 1.0) - self._clock()
            await asyncio.sleep(max(.05, min(delay, 1.0)))
