# SPDX-License-Identifier: Apache-2.0
"""Bounded, identity-owned visual-state leases for future agent adapters.

This module intentionally does not know about RobotCore events, rendering,
permissions, or MCP.  It resolves a presentation-only state around the actual
Core state without ever mutating that state.
"""
from __future__ import annotations

from dataclasses import dataclass
from threading import RLock, Timer
import time
from typing import Callable, Protocol

from .state import RobotState


MIN_THINKING_DURATION_MS = 250
MAX_THINKING_DURATION_MS = 60_000


class _ScheduledCall(Protocol):
    def cancel(self) -> None: ...


@dataclass(frozen=True)
class VisualStateLeaseResult:
    """Caller-safe outcome of one request or release."""

    accepted: bool
    requested_state: str
    effective_state: str
    lease_active: bool
    remaining_expiry_ms: int | None
    suppressed_by: str | None


@dataclass(frozen=True)
class _Lease:
    expires_at: float
    sequence: int


def _schedule_timer(delay: float, callback: Callable[[], None]) -> _ScheduledCall:
    timer = Timer(delay, callback)
    timer.daemon = True
    timer.start()
    return timer


class AgentVisualStateLeaseManager:
    """Resolve temporary ``thinking`` presentation leases without polling.

    ``scheduler`` schedules exactly one callback for the next expiry and must
    return a cancellable handle.  It is injectable so expiry behavior can be
    tested without wall-clock sleeps.
    """

    def __init__(self, *, clock: Callable[[], float] = time.monotonic,
                 scheduler: Callable[[float, Callable[[], None]], _ScheduledCall] = _schedule_timer) -> None:
        self._clock, self._scheduler = clock, scheduler
        self._lock = RLock()
        self._leases: dict[str, _Lease] = {}
        self._sequence = self._deadline_generation = 0
        self._scheduled: _ScheduledCall | None = None
        self._scheduled_deadline: float | None = None
        self._closed = False

    def request(self, agent_id: str, state: str, *, duration_ms: int | None = None,
                actual_state: RobotState | str = RobotState.IDLE) -> VisualStateLeaseResult:
        """Accept caller-owned thinking, or release the caller with idle."""
        self._validate_agent_id(agent_id)
        if state not in {RobotState.THINKING.value, RobotState.IDLE.value}:
            raise ValueError("Agent visual state must be thinking or idle.")
        if state == RobotState.THINKING.value:
            if type(duration_ms) is not int or not MIN_THINKING_DURATION_MS <= duration_ms <= MAX_THINKING_DURATION_MS:
                raise ValueError("thinking duration_ms must be an integer from 250 to 60000.")

        with self._lock:
            self._require_open_locked()
            now = self._clock()
            self._prune_expired_locked(now)
            if state == RobotState.THINKING.value:
                self._sequence += 1
                self._leases[agent_id] = _Lease(now + duration_ms / 1000, self._sequence)
            else:
                self._leases.pop(agent_id, None)
            self._reschedule_locked(now)
            return self._result_locked(agent_id, state, actual_state, now)

    def resolve(self, actual_state: RobotState | str) -> RobotState:
        """Return the effective visual state while preserving actual Core state."""
        with self._lock:
            self._require_open_locked()
            now = self._clock()
            self._prune_expired_locked(now)
            self._reschedule_locked(now)
            return self._effective_state_locked(actual_state)

    def shutdown(self) -> None:
        """Cancel the one pending deadline and forget all ephemeral leases."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._leases.clear()
            self._deadline_generation += 1
            if self._scheduled is not None:
                self._scheduled.cancel()
                self._scheduled = None
            self._scheduled_deadline = None

    close = shutdown

    @staticmethod
    def _validate_agent_id(agent_id: str) -> None:
        if not isinstance(agent_id, str) or not agent_id:
            raise ValueError("agent_id must be a non-empty authenticated identity.")

    def _require_open_locked(self) -> None:
        if self._closed:
            raise RuntimeError("Agent visual state lease manager is closed.")

    def _prune_expired_locked(self, now: float) -> None:
        for agent_id, lease in tuple(self._leases.items()):
            if lease.expires_at <= now:
                del self._leases[agent_id]

    def _winner_locked(self) -> _Lease | None:
        return max(self._leases.values(), key=lambda lease: lease.sequence, default=None)

    def _effective_state_locked(self, actual_state: RobotState | str) -> RobotState:
        try:
            actual = RobotState(actual_state)
        except (TypeError, ValueError) as error:
            raise ValueError("actual_state must be a RobotState.") from error
        if actual in {RobotState.ERROR, RobotState.SLEEPING, RobotState.SPEAKING, RobotState.LISTENING}:
            return actual
        return RobotState.THINKING if actual is RobotState.IDLE and self._winner_locked() is not None else actual

    def _result_locked(self, agent_id: str, requested_state: str,
                       actual_state: RobotState | str, now: float) -> VisualStateLeaseResult:
        lease = self._leases.get(agent_id)
        effective = self._effective_state_locked(actual_state)
        try:
            actual = RobotState(actual_state)
        except (TypeError, ValueError) as error:
            raise ValueError("actual_state must be a RobotState.") from error
        suppressed = actual.value if lease is not None and effective is not RobotState.THINKING else None
        return VisualStateLeaseResult(
            accepted=True,
            requested_state=requested_state,
            effective_state=effective.value,
            lease_active=lease is not None,
            remaining_expiry_ms=(None if lease is None else max(0, round((lease.expires_at - now) * 1000))),
            suppressed_by=suppressed,
        )

    def _reschedule_locked(self, now: float) -> None:
        deadline = None if self._closed or not self._leases else min(lease.expires_at for lease in self._leases.values())
        # Resolving the visual state occurs at render cadence.  Keep the
        # already-scheduled earliest deadline instead of creating a new timer
        # for every read.
        if deadline == self._scheduled_deadline and self._scheduled is not None:
            return
        self._deadline_generation += 1
        generation = self._deadline_generation
        if self._scheduled is not None:
            self._scheduled.cancel()
            self._scheduled = None
        self._scheduled_deadline = deadline
        if deadline is None:
            return
        self._scheduled = self._scheduler(max(0.0, deadline - now),
                                          lambda: self._expire_callback(generation))

    def _expire_callback(self, generation: int) -> None:
        with self._lock:
            # A cancelled Timer can already be ready to execute.  Its old
            # generation must not remove a replacement lease or reschedule it.
            if self._closed or generation != self._deadline_generation:
                return
            now = self._clock()
            self._scheduled = None
            self._scheduled_deadline = None
            self._prune_expired_locked(now)
            self._reschedule_locked(now)
