"""Bounded, process-local physical observation history.

This deliberately stores compact semantic facts, never provider packets, media,
or identity.  It is synchronous and lock protected so sensor workers can submit
without touching the asyncio loop.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import math
from threading import RLock
import time
from typing import Callable

_RETENTION_SECONDS = 6 * 60 * 60
_SAMPLE_INTERVAL_SECONDS = 60
_MAX_SAMPLES = 360
_MAX_EVENTS = 500
_MAX_METRICS = 16
_MAX_RESULTS = 360


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse_utc(value: str | None, name: str) -> datetime | None:
    if value is None: return None
    if not isinstance(value, str): raise ValueError(f"{name} must be an RFC 3339 timestamp")
    try: parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error: raise ValueError(f"{name} must be an RFC 3339 timestamp") from error
    if parsed.tzinfo is None: raise ValueError(f"{name} must include a timezone")
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True, slots=True)
class Measurement:
    metric: str
    value: float
    unit: str
    observed_at: str
    source: str
    quality: str = "valid"

    def document(self) -> dict:
        return {"metric": self.metric, "value": self.value, "unit": self.unit,
                "observed_at": self.observed_at, "source": self.source, "quality": self.quality}


@dataclass(frozen=True, slots=True)
class Transition:
    type: str
    current: object
    previous: object
    observed_at: str
    source: str

    def document(self) -> dict:
        return {"type": self.type, "current": self.current, "previous": self.previous,
                "observed_at": self.observed_at, "source": self.source}


class TemporalHistoryService:
    """Six-hour RAM-only history with deterministic injection points for tests."""
    def __init__(self, *, clock: Callable[[], float] = time.monotonic,
                 utc_now: Callable[[], str] = _utc_now) -> None:
        self._clock, self._utc_now = clock, utc_now
        self._samples: dict[str, deque[tuple[float, Measurement]]] = {}
        self._events: deque[tuple[float, Transition]] = deque(maxlen=_MAX_EVENTS)
        self._lock = RLock()

    def close(self) -> None:
        with self._lock:
            self._samples.clear(); self._events.clear()

    def _expire(self, now: float) -> None:
        cutoff = now - _RETENTION_SECONDS
        for metric in tuple(self._samples):
            values = self._samples[metric]
            while values and values[0][0] < cutoff: values.popleft()
            if not values: del self._samples[metric]
        while self._events and self._events[0][0] < cutoff: self._events.popleft()

    def record_measurement(self, metric: str, value: object, unit: str, *, source: str,
                           quality: str = "valid") -> bool:
        if (not isinstance(metric, str) or not metric or len(metric) > 64 or not isinstance(unit, str)
                or len(unit) > 24 or isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value) or quality != "valid"):
            return False
        now = self._clock()
        with self._lock:
            self._expire(now)
            values = self._samples.get(metric)
            if values is None:
                if len(self._samples) >= _MAX_METRICS: return False
                values = self._samples[metric] = deque(maxlen=_MAX_SAMPLES)
            if values and (now < values[-1][0] or now - values[-1][0] < _SAMPLE_INTERVAL_SECONDS): return False
            values.append((now, Measurement(metric, float(value), unit, self._utc_now(), source)))
        return True

    def record_transition(self, event_type: str, current: object, previous: object, *, source: str) -> bool:
        if (not isinstance(event_type, str) or not event_type or len(event_type) > 80 or current == previous
                or not isinstance(source, str) or len(source) > 64): return False
        now = self._clock()
        with self._lock:
            self._expire(now)
            if self._events and now < self._events[-1][0]: return False
            self._events.append((now, Transition(event_type, current, previous, self._utc_now(), source)))
        return True

    def record_semantic_event(self, event) -> bool:
        payload = getattr(event, "payload", {})
        if not isinstance(payload, dict): return False
        return self.record_transition(event.type, payload.get("current"), payload.get("previous"), source=event.source)

    def query_measurements(self, metric: str, *, start: str | None = None, end: str | None = None,
                           limit: int = _MAX_RESULTS) -> dict:
        if not isinstance(limit, int) or not 1 <= limit <= _MAX_RESULTS: raise ValueError("limit must be from 1 to 360")
        start_at, end_at = _parse_utc(start, "start"), _parse_utc(end, "end")
        now_at = _parse_utc(self._utc_now(), "clock")
        if start_at and end_at and start_at > end_at: raise ValueError("start must not be after end")
        if start_at and end_at and end_at - start_at > timedelta(seconds=_RETENTION_SECONDS): raise ValueError("window must not exceed six hours")
        if start_at and start_at > now_at: raise ValueError("start must not be in the future")
        with self._lock:
            self._expire(self._clock())
            records = [item.document() for _, item in self._samples.get(metric, ())
                       if (start_at is None or _parse_utc(item.observed_at, "observation") >= start_at)
                       and (end_at is None or _parse_utc(item.observed_at, "observation") <= end_at)][-limit:]
        values = [item["value"] for item in records]
        result = {"metric": metric, "samples": records, "count": len(records)}
        if values:
            result["summary"] = {"minimum": min(values), "maximum": max(values), "average": sum(values) / len(values),
                                 "delta": values[-1] - values[0] if len(values) > 1 else None,
                                 "coverage": "sufficient" if len(values) > 1 else "insufficient",
                                 "start": records[0], "end": records[-1]}
        else: result["summary"] = None
        return result

    def list_transitions(self, *, event_type: str | None = None, limit: int = 100) -> dict:
        if not isinstance(limit, int) or not 1 <= limit <= _MAX_RESULTS: raise ValueError("limit must be from 1 to 360")
        with self._lock:
            self._expire(self._clock())
            records = [item.document() for _, item in self._events if event_type is None or item.type == event_type]
        return {"transitions": records[-limit:], "count": min(len(records), limit)}
