"""Composition root for the hardware-independent robot core."""

from __future__ import annotations

import asyncio
import logging

from .behaviors import Behavior, BehaviorManager
from .events import Event, EventBus
from .state import RobotState, RobotStateMachine, StateTransition
from .tasks import BackgroundTasks

CORE_STARTED = "core.started"
CORE_STOPPED = "core.stopped"
STATE_CHANGED = "core.state_changed"

logger = logging.getLogger(__name__)


class RobotCore:
    """Coordinates lifecycle, state and local subsystem communication."""

    def __init__(self) -> None:
        self.events = EventBus()
        self.state_machine = RobotStateMachine()
        self.behaviors = BehaviorManager()
        self.tasks = BackgroundTasks()
        self._running = False

    @property
    def state(self) -> RobotState:
        return self.state_machine.state

    @property
    def is_running(self) -> bool:
        return self._running

    def add_behavior(self, behavior: Behavior) -> None:
        if self._running:
            raise RuntimeError("Behaviors must be added before the core starts.")
        self.behaviors.add(behavior)

    async def start(self) -> None:
        if self._running:
            raise RuntimeError("Robot core is already running.")
        await self.behaviors.start_all()
        self._running = True
        await self.events.publish(Event(CORE_STARTED))

    async def stop(self) -> None:
        if not self._running:
            return
        failures: list[Exception] = []
        try:
            await self.tasks.shutdown()
        except Exception as error:
            failures.append(error)
        try:
            await self.behaviors.stop_all()
        except Exception as error:
            failures.append(error)
        self._running = False
        try:
            await self.events.publish(Event(CORE_STOPPED))
        except Exception as error:
            failures.append(error)
        if failures:
            raise failures[0]

    async def transition_to(self, target: RobotState, *, reason: str | None = None) -> StateTransition:
        transition = self.state_machine.transition_to(target, reason=reason)
        if transition.previous != transition.current:
            await self.events.publish(
                Event(
                    STATE_CHANGED,
                    {
                        "previous": transition.previous.value,
                        "current": transition.current.value,
                        "reason": reason,
                    },
                )
            )
        return transition

    async def accept_transition_to(self, target: RobotState, *, reason: str | None = None) -> StateTransition:
        """Commit a semantic command before scheduling downstream observers.

        Remote command acknowledgement means the state machine has accepted
        and committed the target state.  Rendering, browser notifications, and
        other event consumers remain downstream and must not hold that
        acknowledgement hostage.
        """
        transition = self.state_machine.transition_to(target, reason=reason)
        if transition.previous != transition.current:
            task = asyncio.create_task(self.events.publish(Event(
                STATE_CHANGED,
                {"previous": transition.previous.value, "current": transition.current.value, "reason": reason},
            )))
            task.add_done_callback(self._report_state_event_failure)
        return transition

    @staticmethod
    def _report_state_event_failure(task: asyncio.Task) -> None:
        try:
            task.result()
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("PHOS CORE: state-change event delivery failed")
