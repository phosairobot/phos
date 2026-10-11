"""Explicit, testable robot interaction states."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from robot.semantics import VisualSource


class RobotState(str, Enum):
    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    SLEEPING = "sleeping"
    ERROR = "error"


@dataclass(frozen=True)
class StateTransition:
    previous: RobotState
    current: RobotState
    reason: str | None = None


class InvalidStateTransition(ValueError):
    """Raised when a requested state change is not permitted."""


_ALLOWED_TRANSITIONS = {
    RobotState.IDLE: {RobotState.LISTENING, RobotState.SPEAKING, RobotState.SLEEPING, RobotState.ERROR},
    RobotState.LISTENING: {RobotState.IDLE, RobotState.THINKING, RobotState.SLEEPING, RobotState.ERROR},
    RobotState.THINKING: {RobotState.IDLE, RobotState.SPEAKING, RobotState.ERROR},
    RobotState.SPEAKING: {RobotState.IDLE, RobotState.LISTENING, RobotState.ERROR},
    RobotState.SLEEPING: {RobotState.IDLE, RobotState.ERROR},
    RobotState.ERROR: {RobotState.IDLE, RobotState.SLEEPING},
}


def writable_states() -> tuple[RobotState, ...]:
    """Stable remote-command vocabulary, in canonical enum order."""
    return tuple(state for state in RobotState if state is not RobotState.ERROR)


def allowed_next_states(current: RobotState) -> tuple[RobotState, ...]:
    """Canonical reachable targets, excluding lifecycle-only ERROR."""
    return tuple(state for state in RobotState
                 if state is not RobotState.ERROR and state in _ALLOWED_TRANSITIONS[current])


def transition_graph() -> dict[str, list[str]]:
    """JSON-ready, deterministic projection of the authoritative graph."""
    return {state.value: [target.value for target in allowed_next_states(state)]
            for state in writable_states()}


class RobotStateMachine:
    def __init__(self, initial_state: RobotState = RobotState.IDLE) -> None:
        self._state = initial_state

    @property
    def state(self) -> RobotState:
        return self._state

    def transition_to(self, target: RobotState, *, reason: str | None = None) -> StateTransition:
        if target == self._state:
            return StateTransition(self._state, target, reason)
        if target not in _ALLOWED_TRANSITIONS[self._state]:
            raise InvalidStateTransition(f"Cannot transition from {self._state.value} to {target.value}.")

        transition = StateTransition(self._state, target, reason)
        self._state = target
        return transition
