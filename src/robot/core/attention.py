"""Runtime-local target continuity, separate from human presence semantics."""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
import time
from typing import Optional
import logging
from .behaviors import Behavior
from .events import Event, EventBus
from .presence import PRESENCE_CHANGED, PresenceKind, VISION_FACE_OBSERVATION

ATTENTION_CHANGED = "attention.changed"
ATTENTION_TARGET_ACQUIRED = "attention.target_acquired"
ATTENTION_TARGET_LOST = "attention.target_lost"
ATTENTION_TARGET_CHANGED = "attention.target_changed"
logger = logging.getLogger(__name__)

class AttentionKind(str, Enum): IDLE="idle"; ACQUIRING="acquiring"; TRACKING="tracking"; LOST="lost"

@dataclass(frozen=True)
class AttentionState:
    state: AttentionKind = AttentionKind.IDLE
    target_id: Optional[str] = None
    target_x: Optional[float] = None
    target_y: Optional[float] = None
    confidence: Optional[float] = None
    acquired_at: Optional[float] = None
    last_seen: Optional[float] = None

class AttentionManager(Behavior):
    name="attention-manager"
    def __init__(self, events: EventBus, *, lost_hold_seconds: float=0.0, clock=time.monotonic) -> None:
        if lost_hold_seconds < 0: raise ValueError("lost_hold_seconds must be nonnegative")
        self._events, self._hold, self._clock = events, lost_hold_seconds, clock
        self._state=AttentionState(); self._presence=PresenceKind.NO_ONE; self._unsubscribers=[]
        self._logged_first_input = False
    @property
    def state(self): return self._state
    async def start(self):
        self._unsubscribers=[self._events.subscribe(PRESENCE_CHANGED, self._on_presence), self._events.subscribe(VISION_FACE_OBSERVATION, self._on_observation)]
        logger.info("ATTENTION: initialized")
    async def stop(self):
        for unsubscribe in self._unsubscribers: unsubscribe()
        self._unsubscribers=[]
    async def _on_presence(self,event):
        self._presence=PresenceKind(event.data["state"])
        if self._presence is PresenceKind.NO_ONE and self._state.state is AttentionKind.TRACKING:
            self._state=AttentionState(AttentionKind.LOST,self._state.target_id,self._state.target_x,self._state.target_y,self._state.confidence,self._state.acquired_at,self._state.last_seen); logger.info("ATTENTION TARGET LOST: %s", self._state.target_id); logger.info("ATTENTION TRANSITION: tracking -> lost"); await self._publish(ATTENTION_TARGET_LOST)
    async def _on_observation(self,event):
        observation=event.data.get("observation")
        candidate=None if observation is None else {"id":observation["target_id"],"x":observation["x"],"y":observation["y"],"confidence":observation["confidence"]}
        await self.observe(candidate, now=(observation or {}).get("timestamp",self._clock()))
    async def observe(self,candidate,*,now=None):
        now=self._clock() if now is None else now
        if candidate and not self._logged_first_input:
            self._logged_first_input = True
            logger.debug("ATTENTION: update called")
        logger.debug("Attention input: candidate=%s presence=%s current=%s", candidate, self._presence.value, self._state.state.value)
        if candidate and self._presence is not PresenceKind.NO_ONE:
            if self._state.state is AttentionKind.IDLE:
                self._state=AttentionState(AttentionKind.ACQUIRING, str(candidate.get("id", "face-1")), float(candidate["x"]), float(candidate["y"]), candidate.get("confidence"), None, now)
                logger.info("ATTENTION TRANSITION: idle -> acquiring")
                return
            confidence = candidate.get("confidence")
            state=AttentionState(AttentionKind.TRACKING,str(candidate.get("id","face-1")),float(candidate["x"]),float(candidate["y"]),None if confidence is None else float(confidence),self._state.acquired_at or now,now)
            previous = self._state
            acquired=previous.state is not AttentionKind.TRACKING; self._state=state
            if acquired:
                logger.info("ATTENTION TRANSITION: acquiring -> tracking"); logger.info("ATTENTION TARGET ACQUIRED: %s", state.target_id); await self._publish(ATTENTION_TARGET_ACQUIRED)
            elif previous.target_id != state.target_id:
                payload={"old_target_id":previous.target_id,"new_target_id":state.target_id,"target":{"id":state.target_id,"x":state.target_x,"y":state.target_y,"confidence":state.confidence}}
                logger.info("ATTENTION TARGET CHANGED: %s -> %s", previous.target_id, state.target_id)
                await self._events.publish(Event(ATTENTION_TARGET_CHANGED,payload))
        elif self._state.state is AttentionKind.ACQUIRING:
            self._state=AttentionState(); logger.info("ATTENTION TRANSITION: acquiring -> idle")
        elif self._state.state is AttentionKind.LOST and self._state.last_seen is not None and now-self._state.last_seen >= self._hold:
            self._state=AttentionState(); logger.info("ATTENTION TRANSITION: lost -> idle"); await self._publish(ATTENTION_CHANGED)
    async def _publish(self,event_name):
        payload={"state":self._state.state.value,"target":{"id":self._state.target_id,"x":self._state.target_x,"y":self._state.target_y,"confidence":self._state.confidence},"acquired_at":self._state.acquired_at,"last_seen":self._state.last_seen}
        await self._events.publish(Event(ATTENTION_CHANGED,payload))
        logger.debug("Attention transition: event=%s state=%s target=%s", event_name, payload["state"], payload["target"])
        logger.debug("ATTENTION OUTPUT: state=%s target_id=%s target_x=%s target_y=%s confidence=%s",
                    payload["state"], payload["target"]["id"], payload["target"]["x"], payload["target"]["y"], payload["target"]["confidence"])
        if event_name != ATTENTION_CHANGED: await self._events.publish(Event(event_name,payload))
