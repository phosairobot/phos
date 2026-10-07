"""Provider-neutral, event-free startup readiness state."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import time


class StartupState(str, Enum):
    STARTING = "starting"
    READY = "ready"
    DEGRADED = "degraded"
    FAILED = "failed"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class StartupComponent:
    state: StartupState
    reason: str | None = None
    required: bool = False


class StartupReadiness:
    """One-shot startup state driven by actual component outcomes."""
    def __init__(self, clock=time.monotonic) -> None:
        self._clock = clock
        self.started_at = clock()
        self.ready_at = None
        self.components: dict[str, StartupComponent] = {}

    @property
    def overall_state(self) -> StartupState:
        values = tuple(self.components.values())
        if any(item.required and item.state is StartupState.FAILED for item in values):
            return StartupState.FAILED
        if not values or any(item.state is StartupState.STARTING for item in values):
            return StartupState.STARTING
        if any(item.state in {StartupState.DEGRADED, StartupState.UNAVAILABLE, StartupState.FAILED} for item in values):
            return StartupState.DEGRADED
        return StartupState.READY

    def begin(self, name: str, *, required: bool = False) -> None:
        self.components[name] = StartupComponent(StartupState.STARTING, required=required)

    def set(self, name: str, state: StartupState, reason: str | None = None, *, required: bool | None = None) -> None:
        previous = self.components.get(name)
        self.components[name] = StartupComponent(state, reason, previous.required if required is None and previous else bool(required))
        if self.overall_state in {StartupState.READY, StartupState.DEGRADED} and self.ready_at is None:
            self.ready_at = self._clock()

    def document(self) -> dict:
        return {"overall_state": self.overall_state.value, "components": {
            name: {"state": value.state.value, "reason": value.reason, "required": value.required}
            for name, value in self.components.items()}, "started_at": self.started_at,
            "ready_at": self.ready_at, "degraded_reasons": [value.reason for value in self.components.values()
            if value.reason and value.state in {StartupState.DEGRADED, StartupState.UNAVAILABLE, StartupState.FAILED}]}
