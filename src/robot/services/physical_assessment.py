"""Read-only evidence assessment for bounded physical measurement history."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Callable, Mapping

from robot.services.temporal_history import TemporalHistoryService


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse_utc(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


_METRICS = {
    "temperature_c": ("environmental", "environment", "environment_availability_changed"),
    "pressure_hpa": ("environmental", "environment", "environment_availability_changed"),
    "humidity_percent": ("environmental", "environment", "environment_availability_changed"),
    "eco2_ppm": ("ens160", "air_quality", "air_quality_availability_changed"),
    "tvoc_ppb": ("ens160", "air_quality", "air_quality_availability_changed"),
    "aqi_uba": ("ens160", "air_quality", "air_quality_availability_changed"),
}


class PhysicalContextAssessmentService:
    """Assess retained evidence without polling or mutating its sources.

    The returned outcome describes assessment readiness, not a physical trend or
    condition.  Measurement timestamps are PHOS receipt/retention timestamps.
    """

    def __init__(self, history: TemporalHistoryService, *, utc_now: Callable[[], str] = _utc_now) -> None:
        self._history, self._utc_now = history, utc_now

    def assess(self, sensors: Mapping[str, object]) -> dict:
        now = _parse_utc(self._utc_now())
        if now is None:  # Defensive only: production clock always emits RFC 3339 UTC.
            now = datetime.now(timezone.utc)
        return {
            "assessment_version": "pci-01a",
            "timestamp_semantics": "phos_receipt_time",
            "metrics": {metric: self.assess_metric(metric, sensors=sensors, now=now) for metric in _METRICS},
        }

    def assess_metric(self, metric: str, *, sensors: Mapping[str, object], now: datetime | None = None) -> dict:
        """Return bounded evidence for one supported historical metric."""
        definition = _METRICS.get(metric)
        if definition is None:
            return self._unknown(metric, "unsupported_metric")
        sensor_key, event_source, event_type = definition
        snapshot = sensors.get(sensor_key) if isinstance(sensors, Mapping) else None
        current = self._current_source(sensor_key, snapshot)
        records = self._history.query_measurements(metric)["samples"]
        now = now or _parse_utc(self._utc_now()) or datetime.now(timezone.utc)
        requested_start = now - timedelta(seconds=self._history.retention_seconds)
        base = {
            "metric": metric,
            "unit": records[0].get("unit") if records else None,
            "source": records[0].get("source") if records else event_source,
            "requested_window": {"start": requested_start.isoformat(), "end": now.isoformat(),
                                 "retention_seconds": self._history.retention_seconds},
            "current_source": current,
            "evidence": self._evidence(records, event_type, event_source),
        }
        base["recent_continuous_segment"] = self._recent_segment(
            records, event_type, event_source, current, base["evidence"])
        if current["status"] == "stale":
            return {**base, "outcome": "stale", "reason": "source_stale"}
        if current["status"] == "unknown":
            return {**base, "outcome": "unknown", "reason": "source_snapshot_missing"}
        if not current["available"]:
            return {**base, "outcome": "unavailable", "reason": "source_unavailable"}
        if not records:
            return {**base, "outcome": "unknown", "reason": "no_retained_samples"}
        if not self._consistent(records, "unit"):
            return {**base, "outcome": "unknown", "reason": "inconsistent_unit"}
        if not self._consistent(records, "source"):
            return {**base, "outcome": "unknown", "reason": "inconsistent_source"}
        evidence = base["evidence"]
        if evidence["availability_discontinuity"]:
            return {**base, "outcome": "insufficient_data", "reason": "availability_discontinuity"}
        if evidence["sample_count"] < 3:
            return {**base, "outcome": "insufficient_data", "reason": "insufficient_sample_count"}
        if evidence["coverage_seconds"] is None or evidence["coverage_seconds"] < 2 * self._history.sample_interval_seconds:
            return {**base, "outcome": "insufficient_data", "reason": "insufficient_coverage"}
        if evidence["largest_gap_seconds"] is None or evidence["largest_gap_seconds"] > 2 * self._history.sample_interval_seconds:
            return {**base, "outcome": "insufficient_data", "reason": "observed_gap"}
        return {**base, "outcome": "valid", "reason": "evidence_ready"}

    @staticmethod
    def _unknown(metric: str, reason: str) -> dict:
        return {"metric": metric, "unit": None, "source": None, "requested_window": None,
                "current_source": {"key": None, "status": "unknown", "available": False,
                                   "last_update": None, "age_seconds": None},
                "evidence": {"sample_count": 0, "first_observed_at": None, "last_observed_at": None,
                             "coverage_seconds": None, "largest_gap_seconds": None,
                             "continuity": "unknown", "availability_discontinuity": False},
                "recent_continuous_segment": {"start": None, "end": None, "recovery_boundary": None,
                                              "sample_count": 0, "coverage_seconds": None,
                                              "largest_gap_seconds": None, "continuity": "unknown",
                                              "current_source": None, "outcome": "unknown", "reason": reason},
                "outcome": "unknown", "reason": reason}

    @staticmethod
    def _current_source(key: str, snapshot: object) -> dict:
        if not isinstance(snapshot, Mapping):
            return {"key": key, "status": "unknown", "available": False, "last_update": None, "age_seconds": None}
        status = snapshot.get("status")
        if not isinstance(status, str):
            status = "unknown"
        return {"key": key, "status": status, "available": bool(snapshot.get("available", False)),
                "last_update": snapshot.get("last_update"), "age_seconds": snapshot.get("age_seconds")}

    def _evidence(self, records: list[dict], event_type: str, event_source: str) -> dict:
        times = [_parse_utc(record.get("observed_at")) for record in records]
        if any(value is None for value in times):
            return {"sample_count": len(records), "first_observed_at": records[0].get("observed_at") if records else None,
                    "last_observed_at": records[-1].get("observed_at") if records else None,
                    "coverage_seconds": None, "largest_gap_seconds": None, "continuity": "unknown",
                    "availability_discontinuity": False}
        parsed = [value for value in times if value is not None]
        gaps = [(later - earlier).total_seconds() for earlier, later in zip(parsed, parsed[1:])]
        discontinuity = self._availability_discontinuity(event_type, event_source, parsed[0], parsed[-1]) if parsed else False
        return {"sample_count": len(records), "first_observed_at": records[0].get("observed_at") if records else None,
                "last_observed_at": records[-1].get("observed_at") if records else None,
                "coverage_seconds": (parsed[-1] - parsed[0]).total_seconds() if len(parsed) > 1 else 0,
                "largest_gap_seconds": max(gaps) if gaps else 0,
                "continuity": "observed_discontinuity" if discontinuity else "no_observed_discontinuity",
                "availability_discontinuity": discontinuity}

    def _availability_discontinuity(self, event_type: str, source: str, first: datetime, last: datetime) -> bool:
        transitions = self._history.list_transitions(event_type=event_type, limit=360)["transitions"]
        for transition in transitions:
            observed = _parse_utc(transition.get("observed_at"))
            if transition.get("source") != source or observed is None or not first < observed <= last:
                continue
            if transition.get("current") != "available" or transition.get("previous") != "available":
                return True
        return False

    def _recent_segment(self, records: list[dict], event_type: str, event_source: str,
                        current: dict, historical: dict) -> dict:
        """Describe the latest safely ordered segment without rewriting history."""
        common = {"current_source": current}
        if current["status"] == "stale":
            return self._segment([], None, "unknown", "stale", "source_stale", **common)
        if current["status"] == "unknown":
            return self._segment([], None, "unknown", "unknown", "source_snapshot_missing", **common)
        if not current["available"]:
            return self._segment([], None, "unknown", "unavailable", "source_unavailable", **common)
        if not historical["availability_discontinuity"]:
            return self._segment_outcome(records, None, "no_observed_discontinuity", event_type, event_source, **common)
        boundary, reason = self._latest_recovery_boundary(event_type, event_source)
        if boundary is None:
            return self._segment([], None, "unknown", "insufficient_data", reason, **common)
        samples = []
        for record in records:
            observed = _parse_utc(record.get("observed_at"))
            # Equality is intentionally excluded: history timestamps may only
            # have second precision, so equal timestamps cannot prove ordering.
            if observed is not None and observed > boundary["at"]:
                samples.append(record)
        if not samples:
            return self._segment([], boundary, "recovery_boundary_verified", "insufficient_data",
                                 "no_post_recovery_samples", **common)
        return self._segment_outcome(samples, boundary, "recovery_boundary_verified", event_type, event_source, **common)

    def _latest_recovery_boundary(self, event_type: str, source: str) -> tuple[dict | None, str]:
        transitions = self._history.list_transitions(event_type=event_type, limit=360)["transitions"]
        relevant = [item for item in transitions if item.get("source") == source]
        parsed = []
        previous_at = None
        for transition in relevant:
            observed = _parse_utc(transition.get("observed_at"))
            if observed is None:
                return None, "recovery_boundary_timestamp_unknown"
            if previous_at is not None and observed < previous_at:
                return None, "recovery_boundary_ordering_unknown"
            previous_at = observed
            parsed.append((transition, observed))
        latest_loss = None
        latest_recovery = None
        for transition, observed in parsed:
            current, previous = transition.get("current"), transition.get("previous")
            if current != "available":
                latest_loss = observed
            elif previous is not None and previous != "available":
                latest_recovery = {"observed_at": transition.get("observed_at"), "at": observed}
        if latest_recovery is None:
            return None, "recovery_boundary_missing"
        if latest_loss is not None and latest_recovery["at"] <= latest_loss:
            return None, "recovery_boundary_missing"
        return latest_recovery, "recovery_boundary_verified"

    def _segment_outcome(self, records: list[dict], boundary: dict | None, continuity: str,
                         event_type: str, event_source: str, **common) -> dict:
        evidence = self._evidence(records, event_type, event_source)
        if not records:
            return self._segment(records, boundary, continuity, "insufficient_data", "no_post_recovery_samples", **common)
        if not self._consistent(records, "unit"):
            return self._segment(records, boundary, continuity, "unknown", "inconsistent_unit", evidence, **common)
        if not self._consistent(records, "source"):
            return self._segment(records, boundary, continuity, "unknown", "source_changed", evidence, **common)
        if evidence["availability_discontinuity"]:
            return self._segment(records, boundary, "observed_discontinuity", "insufficient_data",
                                 "availability_discontinuity", evidence, **common)
        if evidence["sample_count"] < 3:
            return self._segment(records, boundary, continuity, "insufficient_data", "insufficient_sample_count", evidence, **common)
        if evidence["coverage_seconds"] is None or evidence["coverage_seconds"] < 2 * self._history.sample_interval_seconds:
            return self._segment(records, boundary, continuity, "insufficient_data", "insufficient_coverage", evidence, **common)
        if evidence["largest_gap_seconds"] is None or evidence["largest_gap_seconds"] > 2 * self._history.sample_interval_seconds:
            return self._segment(records, boundary, continuity, "insufficient_data", "observed_gap", evidence, **common)
        return self._segment(records, boundary, continuity, "valid", "evidence_ready", evidence, **common)

    @staticmethod
    def _segment(records: list[dict], boundary: dict | None, continuity: str, outcome: str, reason: str,
                 evidence: dict | None = None, **common) -> dict:
        evidence = evidence or {"sample_count": len(records), "first_observed_at": None, "last_observed_at": None,
                                "coverage_seconds": None, "largest_gap_seconds": None}
        return {"start": evidence["first_observed_at"], "end": evidence["last_observed_at"],
                "recovery_boundary": None if boundary is None else {"observed_at": boundary["observed_at"]},
                "sample_count": evidence["sample_count"], "coverage_seconds": evidence["coverage_seconds"],
                "largest_gap_seconds": evidence["largest_gap_seconds"], "continuity": continuity,
                **common, "outcome": outcome, "reason": reason}

    @staticmethod
    def _consistent(records: list[dict], key: str) -> bool:
        return len({record.get(key) for record in records}) == 1
