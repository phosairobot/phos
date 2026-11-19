import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
import pytest
from robot.vision.object_awareness import (ObjectAwarenessCoordinator, ObjectAwarenessBusy,
    ObjectAwarenessTimeout, ObjectAwarenessUnavailable)

class Pipe:
    def __init__(self, freshness="fresh"): self.value=SimpleNamespace(frame=object(),captured_at_utc=datetime(2025,1,1,tzinfo=timezone.utc),age_ms=20,freshness=freshness)
    def latest_frame(self): return self.value
class Detector:
    def __init__(self, gate=None): self.gate=gate; self.calls=0; self.closed=False
    def detect(self, _):
        self.calls+=1
        if self.gate: self.gate.wait()
        return [SimpleNamespace(label="car",confidence=.9)]
    def close(self): self.closed=True

def test_observation_metadata_and_single_factory():
    async def run():
        made=[]; c=ObjectAwarenessCoordinator(Pipe(),lambda: made.append(Detector()) or made[-1]); value=await c.observe(); await c.close(); return value,made
    value,made=asyncio.run(run()); assert value.objects[0].label=="car" and value.age_ms==20 and len(made)==1 and made[0].closed

def test_stale_and_missing_frame_are_unavailable():
    async def run():
        c=ObjectAwarenessCoordinator(Pipe("stale"),Detector)
        with pytest.raises(ObjectAwarenessUnavailable): await c.observe()
        await c.close()
    asyncio.run(run())

def test_timeout_keeps_slot_until_native_finishes():
    async def run():
        import threading
        gate=threading.Event(); d=Detector(gate); c=ObjectAwarenessCoordinator(Pipe(),lambda:d,timeout_seconds=.01)
        with pytest.raises(ObjectAwarenessTimeout): await c.observe()
        with pytest.raises(ObjectAwarenessBusy): await c.observe()
        gate.set(); await asyncio.sleep(0)
        await asyncio.sleep(.01); assert (await c.observe()).objects
        await c.close()
    asyncio.run(run())
