"""Bounded, on-demand object inference; intentionally not runtime-composed yet."""
from __future__ import annotations
import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Callable

@dataclass(frozen=True)
class VisibleObject:
    label: str
    confidence: float

@dataclass(frozen=True)
class ObjectObservation:
    captured_at_utc: Any
    age_ms: int
    freshness: str
    objects: tuple[VisibleObject, ...]
    truncated: bool

class ObjectAwarenessError(RuntimeError): pass
class ObjectAwarenessBusy(ObjectAwarenessError): pass
class ObjectAwarenessUnavailable(ObjectAwarenessError): pass
class ObjectAwarenessTimeout(ObjectAwarenessError): pass
class ObjectAwarenessFailure(ObjectAwarenessError): pass

class ObjectAwarenessCoordinator:
    def __init__(self, pipeline, detector_factory: Callable[[], Any], *, timeout_seconds: float = 2.0) -> None:
        self._pipeline, self._factory, self._timeout = pipeline, detector_factory, timeout_seconds
        self._detector = None; self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="phos-object")
        self._busy = asyncio.Lock(); self._closed = False; self._native = None
    async def observe(self) -> ObjectObservation:
        if self._closed: raise ObjectAwarenessUnavailable("object awareness is closed")
        if self._busy.locked(): raise ObjectAwarenessBusy("object inference is busy")
        await self._busy.acquire()
        try:
            try: frame = self._pipeline.latest_frame()
            except (AttributeError, RuntimeError) as error: raise ObjectAwarenessUnavailable(str(error)) from error
            if frame.freshness != "fresh": raise ObjectAwarenessUnavailable("latest frame is stale")
            loop = asyncio.get_running_loop()
            def infer():
                if self._detector is None: self._detector = self._factory()
                return self._detector.detect(frame.frame)
            native = loop.run_in_executor(self._executor, infer); self._native = native
            def done(_):
                self._native = None
                if self._busy.locked(): self._busy.release()
            native.add_done_callback(done)
            try: detections = await asyncio.wait_for(asyncio.shield(native), self._timeout)
            except asyncio.TimeoutError: raise ObjectAwarenessTimeout("object inference timed out") from None
            except asyncio.CancelledError: raise
            except Exception as error: raise ObjectAwarenessFailure("object inference failed") from error
            values = tuple(VisibleObject(item.label, item.confidence) for item in detections[:5])
            return ObjectObservation(frame.captured_at_utc, frame.age_ms, frame.freshness, values, len(detections) > 5)
        except Exception:
            if self._native is None and self._busy.locked(): self._busy.release()
            raise
    async def close(self) -> None:
        if self._closed: return
        self._closed = True
        if self._native is not None:
            try: await asyncio.shield(self._native)
            except Exception: pass
        if self._detector is not None: self._detector.close()
        self._executor.shutdown(wait=True, cancel_futures=False)
