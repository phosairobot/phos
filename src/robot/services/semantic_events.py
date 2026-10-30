"""Transport-neutral, agent-facing semantic transition events.

This is deliberately not the Core event bus and does not know about MCP,
hardware providers, or transport sessions.  Adapters may subscribe to it later.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from threading import RLock
import time
from typing import Any, Callable, Mapping

SEMANTIC_EVENT_TYPES = frozenset({
    "robot_state_changed", "environment_condition_changed", "air_quality_condition_changed",
    "motion_state_changed", "presence_changed", "attention_changed",
    "observed_expression_changed", "capability_changed",
})


@dataclass(frozen=True)
class SemanticEvent:
    """A stable description of one meaningful PHOS transition."""
    type: str
    source: str
    payload: Mapping[str, Any]
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="milliseconds"))

    def document(self) -> dict[str, Any]:
        return {"type": self.type, "timestamp": self.timestamp, "source": self.source,
                "payload": dict(self.payload)}


class SemanticEventPublisher:
    """Small in-process fan-out boundary for semantic consumers.

    Publishers provide already-interpreted values.  It intentionally has no
    polling, buffering, hardware access, or transport responsibility.
    """
    def __init__(self, *, deduplication_window_seconds: float = 1.0, clock=time.monotonic) -> None:
        if deduplication_window_seconds < 0:
            raise ValueError("Semantic event deduplication window must be nonnegative")
        self._listeners: list[Callable[[SemanticEvent], None]] = []
        self._lock = RLock()
        self._deduplication_window_seconds = deduplication_window_seconds
        self._clock = clock

    def subscribe(self, listener: Callable[[SemanticEvent], None], *, event_types=None) -> Callable[[], None]:
        """Subscribe to all events or a validated semantic event-type allow-list.

        Deduplication is deliberately per subscription: one consumer cannot
        suppress another's event stream.  Only adjacent, semantically identical
        delivered events inside the short window are suppressed, preserving
        real A → B → A transitions.
        """
        if event_types is None:
            allowed = None
        else:
            if isinstance(event_types, str):
                raise ValueError("Semantic event types must be an iterable, not a string")
            try:
                allowed = frozenset(event_types)
            except TypeError as error:
                raise ValueError("Semantic event types must be an iterable") from error
            if not all(isinstance(event_type, str) for event_type in allowed):
                raise ValueError("Semantic event types must be strings")
            unsupported = allowed - SEMANTIC_EVENT_TYPES
            if unsupported:
                raise ValueError(f"Unsupported semantic event types: {', '.join(sorted(unsupported))}")
        last_fingerprint = None
        last_at = None

        def filtered_listener(event: SemanticEvent) -> None:
            nonlocal last_fingerprint, last_at
            if allowed is not None and event.type not in allowed:
                return
            fingerprint = _fingerprint(event)
            now = self._clock()
            if (fingerprint == last_fingerprint and last_at is not None
                    and now - last_at <= self._deduplication_window_seconds):
                return
            last_fingerprint, last_at = fingerprint, now
            listener(event)

        with self._lock:
            self._listeners.append(filtered_listener)
        def unsubscribe() -> None:
            with self._lock:
                if filtered_listener in self._listeners:
                    self._listeners.remove(filtered_listener)
        return unsubscribe

    def publish(self, event_type: str, source: str, payload: Mapping[str, Any]) -> SemanticEvent:
        if event_type not in SEMANTIC_EVENT_TYPES:
            raise ValueError(f"Unsupported semantic event type: {event_type}")
        if not isinstance(source, str) or not source:
            raise ValueError("Semantic event source is required")
        event = SemanticEvent(event_type, source, dict(payload))
        with self._lock:
            listeners = tuple(self._listeners)
        for listener in listeners:
            try:
                listener(event)
            except Exception:
                # Consumers are observers and cannot affect PHOS behavior.
                continue
        return event


def _fingerprint(event: SemanticEvent) -> str:
    """Stable semantic identity; timestamps and runtime identities are absent."""
    return json.dumps({"type": event.type, "source": event.source, "payload": event.payload},
                      sort_keys=True, separators=(",", ":"), ensure_ascii=True)
