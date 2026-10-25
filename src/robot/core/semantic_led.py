"""Semantic LED arbitration for optional external controller output."""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Awaitable, Callable

from robot.core.behavior_engine import ENVIRONMENTAL_STATE_CHANGED
from robot.core.behaviors import Behavior
from robot.core.events import Event
from robot.core.state import RobotState
from robot.core.runtime import STATE_CHANGED


logger = logging.getLogger(__name__)


class SemanticLedIntent(str, Enum):
    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    SLEEPING = "sleeping"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"
    ATTENTION = "attention"
    AMBIENT = "ambient"


_PRIORITY = {SemanticLedIntent.ERROR: 100, SemanticLedIntent.WARNING: 90,
             SemanticLedIntent.SUCCESS: 70, SemanticLedIntent.ATTENTION: 65,
             SemanticLedIntent.LISTENING: 50, SemanticLedIntent.SPEAKING: 50,
             SemanticLedIntent.THINKING: 45, SemanticLedIntent.SLEEPING: 40,
             SemanticLedIntent.IDLE: 20, SemanticLedIntent.AMBIENT: 10}
_STATE_INTENTS = {RobotState.IDLE: SemanticLedIntent.IDLE, RobotState.LISTENING: SemanticLedIntent.LISTENING,
                  RobotState.THINKING: SemanticLedIntent.THINKING, RobotState.SPEAKING: SemanticLedIntent.SPEAKING,
                  RobotState.SLEEPING: SemanticLedIntent.SLEEPING, RobotState.ERROR: SemanticLedIntent.ERROR}
_COMMANDS = {
    SemanticLedIntent.IDLE: {"animation": "solid", "color": "#00E5FF", "brightness": .20, "duration_ms": 0},
    SemanticLedIntent.LISTENING: {"animation": "breathe", "color": "#007BFF", "brightness": .45, "period_ms": 1600, "min_intensity": .15, "max_intensity": 1.0, "duration_ms": 0},
    SemanticLedIntent.THINKING: {"animation": "spin", "color": "#A020F0", "brightness": .40, "period_ms": 2200, "direction": "clockwise", "duration_ms": 0},
    SemanticLedIntent.SPEAKING: {"animation": "pulse", "color": "#00E5FF", "brightness": .45, "period_ms": 900, "duration_ms": 0},
    SemanticLedIntent.SLEEPING: {"animation": "breathe", "color": "#A020F0", "brightness": .12, "period_ms": 3000, "min_intensity": .05, "max_intensity": .5, "duration_ms": 0},
    SemanticLedIntent.SUCCESS: {"animation": "pulse", "color": "#00FF40", "brightness": .55, "period_ms": 500, "duration_ms": 0},
    SemanticLedIntent.WARNING: {"animation": "blink", "color": "#FFD400", "brightness": .60, "period_ms": 700, "duration_ms": 0},
    SemanticLedIntent.ERROR: {"animation": "blink", "color": "#FF1A1A", "brightness": .70, "period_ms": 450, "duration_ms": 0},
    SemanticLedIntent.ATTENTION: {"animation": "pulse", "color": "#00E5FF", "brightness": .50, "period_ms": 700, "duration_ms": 0},
    SemanticLedIntent.AMBIENT: {"animation": "solid", "color": "#FF7A00", "brightness": .20, "duration_ms": 0},
}


@dataclass(frozen=True)
class _Request:
    intent: SemanticLedIntent
    expires_at: float | None


class SemanticLedService(Behavior):
    """One source-owned semantic arbiter; the controller executes its output."""
    name = "semantic-led"

    def __init__(self, events, sender: Callable[[dict], Awaitable[bool]], state_supplier: Callable[[], RobotState], *, clock=time.monotonic):
        self._events, self._sender, self._state_supplier, self._clock = events, sender, state_supplier, clock
        self._requests: dict[str, _Request] = {}
        self._last_command = None
        self._task = None
        self._unsubscribers = []

    async def start(self):
        self._unsubscribers = [self._events.subscribe(STATE_CHANGED, self._robot_state),
                               self._events.subscribe(ENVIRONMENTAL_STATE_CHANGED, self._environment)]
        await self.set_intent("robot_state", _STATE_INTENTS[self._state_supplier()])
        self._task = asyncio.create_task(self._expiry_loop(), name="semantic-led")

    async def stop(self):
        for unsubscribe in self._unsubscribers: unsubscribe()
        self._unsubscribers = []
        if self._task is not None:
            self._task.cancel(); await asyncio.gather(self._task, return_exceptions=True); self._task = None

    async def set_intent(self, source: str, intent: SemanticLedIntent | str, *, duration_seconds: float | None = None):
        if not isinstance(source, str) or not source or len(source) > 64: raise ValueError("LED intent source is invalid")
        intent = SemanticLedIntent(intent)
        if duration_seconds is not None and duration_seconds <= 0: raise ValueError("LED intent duration is invalid")
        self._requests[source] = _Request(intent, None if duration_seconds is None else self._clock() + duration_seconds)
        await self._reconcile()

    async def clear_intent(self, source: str):
        self._requests.pop(source, None); await self._reconcile()

    async def signal_user(self, signal: str):
        intent = SemanticLedIntent(signal)
        if intent not in {SemanticLedIntent.SUCCESS, SemanticLedIntent.WARNING, SemanticLedIntent.ERROR, SemanticLedIntent.ATTENTION}:
            raise ValueError("Unsupported user signal")
        durations = {SemanticLedIntent.SUCCESS: 2.0, SemanticLedIntent.WARNING: 5.0, SemanticLedIntent.ATTENTION: 3.0,
                     SemanticLedIntent.ERROR: 5.0}
        await self.set_intent("user_signal", intent, duration_seconds=durations[intent])

    async def reapply(self, *, reason: str = "controller_session"):
        request = self._effective()
        logger.debug("PHOS SEMANTIC LED: reapply reason=%s winner=%s intent=%s command=%s last_dedup=%s",
                     reason, self._winner_source(), request.intent.value if request else None,
                     None if request is None else _COMMANDS[request.intent], self._last_command)
        # The controller reports capability per connection.  A new session has
        # no assumed hardware state, even when its command matches the prior
        # session's winner.
        self._last_command = None
        await self._reconcile()

    def status(self):
        request = self._effective()
        return {"active_intent": request.intent.value if request else None,
                "winning_source": self._winner_source(), "resolved_animation": None if request is None else dict(_COMMANDS[request.intent])}

    async def _robot_state(self, event: Event):
        try: await self.set_intent("robot_state", _STATE_INTENTS[RobotState(event.data["current"])])
        except (KeyError, ValueError): return

    async def _environment(self, event: Event):
        # Environmental context is deliberately lowest priority and never owns a hardware effect directly.
        if event.data.get("state") == "normal": await self.clear_intent("environment")
        else: await self.set_intent("environment", SemanticLedIntent.AMBIENT)

    def _active(self):
        now = self._clock()
        expired = [source for source, request in self._requests.items() if request.expires_at is not None and request.expires_at <= now]
        for source in expired: self._requests.pop(source, None)
        return self._requests

    def _winner_source(self):
        active = self._active()
        return min(active, key=lambda source: (-_PRIORITY[active[source].intent], source)) if active else None

    def _effective(self):
        source = self._winner_source()
        return None if source is None else self._requests[source]

    async def _reconcile(self):
        request = self._effective()
        command = None if request is None else _COMMANDS[request.intent]
        if command == self._last_command:
            logger.debug("PHOS SEMANTIC LED: send suppressed reason=deduplicated winner=%s command=%s",
                         self._winner_source(), command)
            return
        if command is None:
            self._last_command = None
            return
        if await self._sender(command):
            self._last_command = dict(command)
            logger.debug("PHOS SEMANTIC LED: command sent winner=%s command=%s",
                         self._winner_source(), command)
        else:
            logger.debug("PHOS SEMANTIC LED: command not sent reason=controller_output_unavailable winner=%s command=%s",
                         self._winner_source(), command)

    async def _expiry_loop(self):
        while True:
            await asyncio.sleep(.25); await self._reconcile()
