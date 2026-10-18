"""Stable, provider-neutral human-presence interpretation."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import time
from typing import Optional
import logging

from .behaviors import Behavior
from .events import Event, EventBus

VISION_FACE_OBSERVATION = "vision.face_observation"
PRESENCE_CHANGED = "presence.changed"
PERSON_ENTERED = "presence.person_entered"
PERSON_LEFT = "presence.person_left"
logger = logging.getLogger(__name__)


class PresenceKind(str, Enum):
    NO_ONE = "no_one"
    PERSON_PRESENT = "person_present"
    PERSON_ENGAGED = "person_engaged"


@dataclass(frozen=True)
class PresenceState:
    state: PresenceKind = PresenceKind.NO_ONE
    people_count: int = 0
    primary_candidate_id: Optional[str] = None
    visible_since: Optional[float] = None
    last_seen: Optional[float] = None
    confidence: Optional[float] = None


class PresenceInterpreter(Behavior):
    """Turn selected, runtime-local face observations into stable semantics."""
    name = "presence-interpreter"

    def __init__(self, events: EventBus, *, enter_confirmation_seconds: float = .3,
                 leave_confirmation_seconds: float = 1.5, clock=time.monotonic) -> None:
        if enter_confirmation_seconds <= 0 or leave_confirmation_seconds <= 0:
            raise ValueError("Presence confirmation durations must be positive.")
        self._events, self._enter, self._leave, self._clock = events, enter_confirmation_seconds, leave_confirmation_seconds, clock
        self._state = PresenceState()
        self._candidate_since: Optional[float] = None
        self._unsubscribe = None
        self._logged_first_input = False

    @property
    def state(self) -> PresenceState: return self._state

    async def start(self) -> None:
        self._unsubscribe = self._events.subscribe(VISION_FACE_OBSERVATION, self._on_observation)
        logger.info("PRESENCE: initialized")

    async def stop(self) -> None:
        if self._unsubscribe: self._unsubscribe()
        self._unsubscribe = None

    async def _on_observation(self, event: Event) -> None:
        observation = event.data.get("observation")
        candidate = None if observation is None else {"id": observation["target_id"], "type": observation["kind"],
            "x": observation["x"], "y": observation["y"], "confidence": observation["confidence"]}
        await self.observe(candidate, now=(observation or {}).get("timestamp", self._clock()))

    async def observe(self, candidate: Optional[dict], *, now: Optional[float] = None) -> None:
        now = self._clock() if now is None else now
        if candidate and not self._logged_first_input:
            self._logged_first_input = True
            logger.debug("PRESENCE INPUT: detections=1 type=%s confidence=%s", candidate.get("type", "face"),
                        candidate.get("confidence"))
        logger.debug("PRESENCE: update called")
        logger.debug("Presence input: candidate_count=%s candidate=%s state=%s enter_started=%s last_seen=%s",
                     1 if candidate else 0, candidate, self._state.state.value, self._candidate_since, self._state.last_seen)
        if candidate:
            candidate_id = str(candidate.get("id", "face-1"))
            confidence = candidate.get("confidence")
            if self._state.state is PresenceKind.NO_ONE:
                self._candidate_since = now if self._candidate_since is None else self._candidate_since
                if now - self._candidate_since < self._enter:
                    logger.debug("Presence rejected/pending: enter_elapsed=%.3f required=%.3f", now - self._candidate_since, self._enter)
                    return
                self._state = PresenceState(PresenceKind.PERSON_PRESENT, 1, candidate_id,
                                            self._candidate_since, now, confidence)
                await self._publish(PERSON_ENTERED)
            else:
                self._state = PresenceState(self._state.state, 1, candidate_id,
                                            self._state.visible_since, now, confidence)
            return
        self._candidate_since = None
        if self._state.state is PresenceKind.NO_ONE or self._state.last_seen is None:
            return
        if now - self._state.last_seen < self._leave:
            logger.debug("Presence retained: leave_elapsed=%.3f required=%.3f", now - self._state.last_seen, self._leave)
            return
        self._state = PresenceState(last_seen=self._state.last_seen)
        await self._publish(PERSON_LEFT)

    async def _publish(self, lifecycle_event: str) -> None:
        payload = _document(self._state)
        logger.info("Presence transition: event=%s state=%s people=%s", lifecycle_event, payload["state"], payload["people_count"])
        await self._events.publish(Event(PRESENCE_CHANGED, payload))
        await self._events.publish(Event(lifecycle_event, payload))


def _document(state: PresenceState) -> dict:
    return {"state": state.state.value, "people_count": state.people_count,
            "primary_candidate_id": state.primary_candidate_id, "visible_since": state.visible_since,
            "last_seen": state.last_seen, "confidence": state.confidence}
