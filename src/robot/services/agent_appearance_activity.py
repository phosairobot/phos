"""In-memory authenticated MCP activity and appearance arbitration.

This deliberately owns no renderer state.  The tracker is installed in the
MCP/lifecycle process, where a resolved canonical agent identity is available.
"""
from __future__ import annotations

from threading import RLock, Timer
import time

from .agent_identity import AgentIdentity, AgentRegistryProvider


class AgentAppearanceActivityTracker:
    """Select a configured agent from non-persistent activity leases.

    ``candidate_supplier`` returns the current appearance mode and static
    candidate projections.  It is intentionally a read-only service boundary
    so this class neither owns configuration nor another skin registry.
    """
    def __init__(self, registry_provider: AgentRegistryProvider, candidate_supplier, *,
                 clock=time.monotonic, timer_factory=Timer) -> None:
        self._provider = registry_provider
        self._candidate_supplier = candidate_supplier
        self._clock, self._timer_factory = clock, timer_factory
        self._lock = RLock()
        self._leases: dict[str, float] = {}
        self._previous_winner: str | None = None
        self._timer = None
        self._on_change = None
        self._result = self._empty_result()

    @staticmethod
    def _empty_result():
        return {"mode": "global", "selected_agent_id": None,
                "selected_display_name": None, "selected_skin_id": None,
                "selection_reason": "global_mode", "effective_priority": None,
                "remaining_lease_seconds": None, "next_expiry_monotonic": None,
                "agents": []}

    def set_change_callback(self, callback) -> None:
        with self._lock:
            self._on_change = callback

    def record(self, identity: AgentIdentity) -> bool:
        """Refresh only a canonical registered identity's shared lease."""
        registry = self._provider.get()
        registered = registry.get(identity.id) if isinstance(identity, AgentIdentity) else None
        # The MCP adapter passes the exact object returned by the trusted
        # registry-backed authenticator.  Do not accept a lookalike identity
        # constructed from client-controlled fields.
        if registered is None or registered is not identity:
            return False
        with self._lock:
            self._leases[identity.id] = self._clock()
        self.recompute()
        return True

    def forget(self, agent_id: str) -> None:
        with self._lock:
            self._leases.pop(agent_id, None)
            if self._previous_winner == agent_id:
                self._previous_winner = None
        self.recompute()

    def _cancel_timer_locked(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    def _schedule_locked(self, deadline: float | None) -> None:
        self._cancel_timer_locked()
        if deadline is None:
            return
        delay = max(0.0, deadline - self._clock())
        timer = self._timer_factory(delay, self._on_expiry)
        timer.daemon = True
        self._timer = timer
        timer.start()

    def _on_expiry(self) -> None:
        self.recompute()

    def _compute_locked(self) -> dict:
        supplied = self._candidate_supplier()
        mode = supplied["mode"]
        now = self._clock()
        agents, active = [], []
        next_expiry = None
        for candidate in supplied["agents"]:
            policy = candidate["appearance_policy"]
            last = self._leases.get(candidate["agent_id"])
            expiry = None if last is None else last + policy["activity_timeout_seconds"]
            lease_active = expiry is not None and now < expiry
            if lease_active and (next_expiry is None or expiry < next_expiry):
                next_expiry = expiry
            view = {"agent_id": candidate["agent_id"], "display_name": candidate["display_name"],
                    "priority": policy["priority"], "static_eligible": policy["static_eligible"],
                    "static_reason": policy["reason"], "activity_active": lease_active,
                    "remaining_lease_seconds": max(0.0, expiry - now) if lease_active else None}
            agents.append(view)
            if policy["static_eligible"] and lease_active:
                active.append((candidate, expiry))
        if mode != "follow_agent":
            return {"mode": mode, "selected_agent_id": None, "selected_display_name": None,
                    "selected_skin_id": None, "selection_reason": "global_mode",
                    "effective_priority": None, "remaining_lease_seconds": None,
                    "next_expiry_monotonic": next_expiry, "agents": agents}
        if not active:
            self._previous_winner = None
            return {"mode": mode, "selected_agent_id": None, "selected_display_name": None,
                    "selected_skin_id": None, "selection_reason": "no_active_eligible_agents",
                    "effective_priority": None, "remaining_lease_seconds": None,
                    "next_expiry_monotonic": next_expiry, "agents": agents}
        priority = max(item[0]["appearance_policy"]["priority"] for item in active)
        tied = [item for item in active if item[0]["appearance_policy"]["priority"] == priority]
        chosen = next((item for item in tied if item[0]["agent_id"] == self._previous_winner), None)
        reason = "tie_retained" if chosen is not None and len(tied) > 1 else "highest_priority"
        if chosen is None:
            chosen = sorted(tied, key=lambda item: item[0]["agent_id"])[0]
            if len(tied) > 1:
                reason = "stable_tie_breaker"
        candidate, expiry = chosen
        self._previous_winner = candidate["agent_id"]
        return {"mode": mode, "selected_agent_id": candidate["agent_id"],
                "selected_display_name": candidate["display_name"],
                "selected_skin_id": candidate["appearance_policy"]["assigned_skin_id"],
                "selection_reason": reason, "effective_priority": priority,
                "remaining_lease_seconds": max(0.0, expiry - now),
                "next_expiry_monotonic": next_expiry, "agents": agents}

    def recompute(self) -> dict:
        with self._lock:
            self._result = self._compute_locked()
            self._schedule_locked(self._result["next_expiry_monotonic"])
            result, callback = dict(self._result), self._on_change
        if callback is not None:
            callback(result)
        return result

    def status(self) -> dict:
        return self.recompute()

    def close(self) -> None:
        with self._lock:
            self._cancel_timer_locked()
            self._leases.clear()
            self._previous_winner = None
