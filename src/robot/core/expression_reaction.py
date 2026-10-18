"""Provider-neutral policy for cautious reactions to observed expressions."""
from __future__ import annotations

from dataclasses import dataclass
import logging
import time
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from robot.vision.provider import ObservedExpression

logger = logging.getLogger(__name__)

EXPRESSION_REACTION_REQUESTED = "expression_reaction_requested"
EXPRESSION_REACTION_STARTED = "expression_reaction_started"
EXPRESSION_REACTION_COMPLETED = "expression_reaction_completed"
EXPRESSION_REACTION_SUPPRESSED = "expression_reaction_suppressed"


@dataclass(frozen=True)
class ReactionIntent:
    observed_label: str
    reaction: str
    confidence: float
    duration_ms: int
    source: str = "observed_expression"
    priority: int = 30

    def document(self) -> dict:
        return {"observed_label": self.observed_label, "reaction": self.reaction,
                "confidence": self.confidence, "duration_ms": self.duration_ms,
                "source": self.source, "priority": self.priority}


class ExpressionReactionPolicy:
    """Turn confirmed uncertain observations into bounded semantic intents."""
    _MAPPINGS = {"happy": "happy", "surprised": "surprised",
                 "sad": "curious", "fearful": "curious", "angry": "curious"}

    def __init__(self, *, enabled: bool, min_confidence: float, confirmation_ms: int,
                 cooldown_ms: int, reaction_duration_ms: int, clock=time.monotonic) -> None:
        if not 0 <= min_confidence <= 1 or min(confirmation_ms, cooldown_ms, reaction_duration_ms) < 0:
            raise ValueError("Invalid expression reaction policy settings")
        self.enabled, self.min_confidence = enabled, min_confidence
        self.confirmation_seconds, self.cooldown_seconds = confirmation_ms / 1000, cooldown_ms / 1000
        self.reaction_duration_ms, self._clock = reaction_duration_ms, clock
        self._candidate: Optional[str] = None
        self._candidate_at: Optional[float] = None
        self._last_reaction: dict[str, float] = {}

    def observe(self, observation: "ObservedExpression", *, now: Optional[float] = None) -> Optional[ReactionIntent]:
        now = self._clock() if now is None else now
        if not self.enabled or not observation.available or observation.confidence is None:
            self._reset_candidate()
            return None
        reaction = self._MAPPINGS.get(observation.label or "")
        if reaction is None or observation.confidence < self.min_confidence:
            self._reset_candidate()
            return None
        if self._candidate != observation.label:
            self._candidate, self._candidate_at = observation.label, now
            logger.debug("EXPRESSION REACTION CANDIDATE: label=%s confidence=%.2f", observation.label, observation.confidence)
            return None
        if now - self._candidate_at < self.confirmation_seconds:
            return None
        if now - self._last_reaction.get(reaction, float("-inf")) < self.cooldown_seconds:
            logger.debug("EXPRESSION REACTION SUPPRESSED: reason=cooldown")
            self._reset_candidate()
            return None
        self._last_reaction[reaction] = now
        self._reset_candidate()
        logger.info("EXPRESSION REACTION CONFIRMED: label=%s", observation.label)
        return ReactionIntent(observation.label, reaction, observation.confidence, self.reaction_duration_ms)

    def _reset_candidate(self) -> None:
        self._candidate = self._candidate_at = None
