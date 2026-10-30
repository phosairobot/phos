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
from uuid import uuid4

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
        self._subscriptions: dict[str, SemanticSubscription] = {}
        self._lock = RLock()
        self._deduplication_window_seconds = deduplication_window_seconds
        self._clock = clock

    @property
    def active_subscription_count(self) -> int:
        with self._lock:
            return len(self._subscriptions)

    def subscribe(self, listener: Callable[[SemanticEvent], None], *, event_types=None) -> "SemanticSubscription":
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
        subscription = SemanticSubscription(self, listener, allowed)
        with self._lock:
            self._subscriptions[subscription.id] = subscription
        return subscription

    def _remove(self, subscription: "SemanticSubscription") -> None:
        with self._lock:
            self._subscriptions.pop(subscription.id, None)

    def publish(self, event_type: str, source: str, payload: Mapping[str, Any]) -> SemanticEvent:
        if event_type not in SEMANTIC_EVENT_TYPES:
            raise ValueError(f"Unsupported semantic event type: {event_type}")
        if not isinstance(source, str) or not source:
            raise ValueError("Semantic event source is required")
        event = SemanticEvent(event_type, source, dict(payload))
        with self._lock:
            subscriptions = tuple(self._subscriptions.values())
        for subscription in subscriptions:
            try:
                subscription._deliver(event, self._clock(), self._deduplication_window_seconds)
            except Exception:
                # Consumers are observers and cannot affect PHOS behavior.
                continue
        return event


class SemanticSubscription:
    """Explicit ownership handle for one filtered semantic event stream.

    The publisher keeps a strong reference while this subscription is active;
    consumers must call :meth:`close` (or use the context manager) to release
    it. A publish already snapshotted before close may finish its callback, but
    no later delivery is started after ``close`` returns.
    """
    def __init__(self, publisher: SemanticEventPublisher, handler: Callable[[SemanticEvent], None], allowed) -> None:
        self.id = uuid4().hex
        self._publisher, self._handler, self._allowed = publisher, handler, allowed
        self._last_fingerprint = self._last_at = None
        self._active = True
        self._lock = RLock()

    @property
    def active(self) -> bool:
        with self._lock:
            return self._active

    def close(self) -> None:
        with self._lock:
            if not self._active:
                return
            self._active = False
            publisher = self._publisher
            self._publisher = None
            self._handler = None
            self._allowed = None
            self._last_fingerprint = self._last_at = None
        publisher._remove(self)

    def __call__(self) -> None:
        """Compatibility with the prior unsubscribe callback return value."""
        self.close()

    def __enter__(self) -> "SemanticSubscription":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def _deliver(self, event: SemanticEvent, now: float, window: float) -> None:
        with self._lock:
            if not self._active or (self._allowed is not None and event.type not in self._allowed):
                return
            fingerprint = _fingerprint(event)
            if (fingerprint == self._last_fingerprint and self._last_at is not None
                    and now - self._last_at <= window):
                return
            self._last_fingerprint, self._last_at = fingerprint, now
            handler = self._handler
        handler(event)


def _fingerprint(event: SemanticEvent) -> str:
    """Stable semantic identity; timestamps and runtime identities are absent."""
    return json.dumps({"type": event.type, "source": event.source, "payload": event.payload},
                      sort_keys=True, separators=(",", ":"), ensure_ascii=True)
