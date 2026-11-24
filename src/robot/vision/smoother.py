# SPDX-License-Identifier: Apache-2.0
"""Conservative semantic decisions and bounded temporal visual evidence."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import math
import time
from typing import Optional

from .provider import ExpressionObservation, VisualExpression


@dataclass(frozen=True)
class SemanticDecision:
    label: str
    confidence: float
    reason: str


class ExpressionSmoother:
    """Reject unsupported evidence; confirm useful expressions over time.

    Neutral is disabled until a model/camera setup has been calibrated: the
    current recorded models give confident neutral scores on non-neutral poses.
    Legacy providers without a distribution remain compatible but abstain.
    """

    def __init__(
        self,
        *,
        minimum_confidence: float = 0.60,
        minimum_observations: int = 3,
        maximum_gap_seconds: float = 1.5,
        neutral_enabled: bool = False,
    ) -> None:
        if not 0.0 <= minimum_confidence <= 1.0:
            raise ValueError("minimum_confidence must be between 0.0 and 1.0.")
        if minimum_observations < 1:
            raise ValueError("minimum_observations must be at least one.")
        if not math.isfinite(maximum_gap_seconds) or maximum_gap_seconds <= 0:
            raise ValueError("maximum_gap_seconds must be positive and finite.")
        self.minimum_confidence = minimum_confidence
        self._minimum_observations = minimum_observations
        self._maximum_gap_seconds = maximum_gap_seconds
        self._neutral_enabled = neutral_enabled
        self.reset()

    def decide(self, observation: Optional[ExpressionObservation]) -> SemanticDecision:
        if observation is None:
            return SemanticDecision("unknown", 0.0, "missing_prediction")
        pairs = observation.probabilities
        if not pairs:
            return SemanticDecision("unknown", 0.0, "missing_distribution")
        scores = dict(pairs)
        if (len(scores) != len(pairs) or len(scores) < 2
                or any(not math.isfinite(p) or not 0 <= p <= 1 for p in scores.values())
                or abs(sum(scores.values()) - 1.0) > 0.01):
            return SemanticDecision("unknown", 0.0, "invalid_distribution")
        ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
        raw_label, confidence = ranked[0]
        label = {"happiness": "happy", "surprise": "surprised"}.get(raw_label, raw_label)
        if label not in {"happy", "surprised", "neutral"}:
            return SemanticDecision("unknown", 0.0, "unsupported_class")
        if label == "neutral" and not self._neutral_enabled:
            return SemanticDecision("unknown", 0.0, "neutral_not_calibrated")
        threshold = {"happy": 0.80, "surprised": 0.85, "neutral": 0.95}[label]
        margin = {"happy": 0.30, "surprised": 0.35, "neutral": 0.50}[label]
        if confidence < max(threshold, self.minimum_confidence):
            return SemanticDecision("unknown", 0.0, "below_class_threshold")
        if confidence - ranked[1][1] < margin:
            return SemanticDecision("unknown", 0.0, "ambiguous_margin")
        return SemanticDecision(label, confidence, "accepted_frame")

    def observe(
        self, observation: Optional[ExpressionObservation], *, timestamp: Optional[float] = None
    ) -> Optional[VisualExpression]:
        if observation is not None and observation.sampled_at is not None:
            if observation.sampled_at == self._cached_sample:
                return self._cached_result
            result = self._observe(observation, timestamp=observation.sampled_at)
            self._cached_sample = observation.sampled_at
            self._cached_result = result
            return result
        return self._observe(observation, timestamp=timestamp)

    def _observe(
        self, observation: Optional[ExpressionObservation], *, timestamp: Optional[float] = None
    ) -> Optional[VisualExpression]:
        now = time.monotonic() if timestamp is None else timestamp
        decision = self.decide(observation)
        if not math.isfinite(now):
            decision = SemanticDecision("unknown", 0.0, "invalid_timestamp")
        if decision.label == "unknown":
            self.reset()
            self.decision = decision
            return None
        if (self._label != decision.label or self._last_at is None
                or now <= self._last_at or now - self._last_at > self._maximum_gap_seconds):
            self.reset()
            self._label = decision.label
            self._started_at = now
        self._last_at = now
        self._confidences.append(decision.confidence)
        self.observed_for_ms = int(round((now - self._started_at) * 1000))
        duration = {"happy": 800, "surprised": 600, "neutral": 1200}[decision.label]
        if len(self._confidences) < self._minimum_observations or self.observed_for_ms < duration:
            self.decision = SemanticDecision("unknown", 0.0, "temporal_pending")
            return None
        self.decision = SemanticDecision(decision.label, decision.confidence, "confirmed")
        return VisualExpression(
            label=decision.label,
            confidence=round(sum(self._confidences) / len(self._confidences), 6),
            observed_for_ms=self.observed_for_ms,
        )

    @property
    def temporal_state(self) -> dict:
        return {"candidate": self._label, "count": len(self._confidences),
                "required_count": self._minimum_observations,
                "observed_for_ms": self.observed_for_ms,
                "confirmed": self.decision.reason == "confirmed"}

    def reset(self) -> None:
        self._cached_sample: Optional[float] = None
        self._cached_result: Optional[VisualExpression] = None
        self._label: Optional[str] = None
        self._started_at: Optional[float] = None
        self._last_at: Optional[float] = None
        self._confidences: deque[float] = deque(maxlen=max(3, self._minimum_observations))
        self.observed_for_ms = 0
        self.decision = SemanticDecision("unknown", 0.0, "reset")
