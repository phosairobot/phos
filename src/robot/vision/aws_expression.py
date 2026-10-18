"""Optional Rekognition adapter: one bounded background request, memory-only cache."""
from __future__ import annotations

import asyncio
import logging
import math
import os
import time
from typing import Any, Callable, Optional

from robot.config import CloudExpressionConfig

from .provider import ExpressionObservation, ExpressionProvider

logger = logging.getLogger(__name__)


def map_response(response: dict, minimum_face_confidence: float) -> Optional[ExpressionObservation]:
    """Conservatively translate independent confidences, never amplify weak scores."""
    faces = response.get("FaceDetails", [])
    # Ambiguous crops are not an opportunity to select a different person remotely.
    if len(faces) != 1:
        return None
    face = faces[0]
    confidence = float(face.get("Confidence", 0)) / 100
    if not math.isfinite(confidence) or not minimum_face_confidence <= confidence <= 1:
        return None
    scores = {"happy": 0.0, "surprised": 0.0, "neutral": 0.0, "unknown": 0.0}
    seen = set()
    for item in face.get("Emotions", []):
        raw = item["Type"]
        value = float(item["Confidence"]) / 100
        if raw in seen or not math.isfinite(value) or not 0 <= value <= 1:
            return None
        seen.add(raw)
        label = {"HAPPY": "happy", "SURPRISED": "surprised", "CALM": "neutral"}.get(raw, "unknown")
        scores[label] += value
    total = sum(scores.values())
    if not total:
        return None
    if total < 1:
        scores["unknown"] += 1 - total
    else:
        scores = {label: value / total for label, value in scores.items()}
    label = max(scores, key=scores.get)
    return ExpressionObservation(label, scores[label], tuple(scores.items()))


class AWSExpressionProvider(ExpressionProvider):
    """classify polls a single worker and returns immediately with unexpired evidence.

    Callers must supply only locally selected stable crops and invalidate on loss.
    Cached observations retain their sample timestamp; they are not new evidence.
    """

    def __init__(self, config: CloudExpressionConfig, *, diagnostics: bool = False, client: Any = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.config = config
        self._client = client
        self._clock = clock
        self._diagnostics = diagnostics
        self._task: Optional[asyncio.Task[None]] = None
        self._generation = 0
        self._stable_since = None
        self._cache = None
        self._signature = None
        self._expires = 0.0
        self._refresh_at = 0.0
        self._next_request = 0.0
        self._failures = 0
        self._last_diagnostic = -math.inf
        self.requests = 0

    def invalidate(self) -> None:
        self._generation += 1
        self._stable_since = None
        self._cache = self._signature = None
        self._expires = 0.0
        self._refresh_at = 0.0
        # Do not cancel an active SDK thread: keep single-flight and discard its result.

    async def close(self) -> None:
        self.invalidate()
        if self._task is not None:
            await self._task
            self._task = None

    async def classify(self, face_crop: Any) -> Optional[ExpressionObservation]:
        now = self._clock()
        if face_crop is None or not getattr(face_crop, "size", 0):
            self.invalidate()
            return None
        if self._stable_since is None:
            self._stable_since = now
        if now - self._stable_since < self.config.stable_seconds:
            self._diagnostic("stable_face_pending", now)
            return None
        cached = self._cache if now < self._expires else None
        if self._task is not None:
            if not self._task.done():
                self._diagnostic("in_flight", now)
                return cached
            self._task.result()
            self._task = None
        if now < self._next_request:
            self._diagnostic("cache/cooldown_or_backoff" if cached else "cooldown_or_backoff", now)
            return cached
        if self.config.max_requests_per_session and self.requests >= self.config.max_requests_per_session:
            self._diagnostic("session_limit", now)
            return cached
        # A small area-averaged luminance thumbnail is sufficient for a change gate.
        signature = self._fingerprint(face_crop)
        if now < self._refresh_at and self._signature is not None:
            difference = sum(abs(a - b) for a, b in zip(signature, self._signature)) / len(signature)
            if difference <= self.config.change_threshold:
                self._diagnostic("cache/unchanged", now)
                return cached
        self.requests += 1
        self._next_request = now + max(self.config.cooldown_seconds, 60 / self.config.max_requests_per_minute)
        self._signature = signature
        self._refresh_at = now + self.config.refresh_seconds
        self._task = asyncio.create_task(self._request(face_crop.copy(), now, self._generation))
        self._diagnostic("requested", now, force=True)
        return cached

    @staticmethod
    def _fingerprint(crop: Any) -> tuple[float, ...]:
        import cv2
        thumbnail = cv2.resize(crop, (16, 16), interpolation=cv2.INTER_AREA)
        return tuple(float(value) / 255 for value in cv2.cvtColor(thumbnail, cv2.COLOR_BGR2GRAY).flat)

    async def _request(self, crop: Any, sampled_at: float, generation: int) -> None:
        started = self._clock()
        try:
            observation = await asyncio.to_thread(self._request_sync, crop)
        except Exception as error:
            self._failures += 1
            delay = min(self.config.retry_max_seconds,
                        self.config.retry_initial_seconds * 2 ** min(self._failures - 1, 20))
            self._next_request = max(self._next_request, self._clock() + delay)
            self._cache = None
            # Exception text/SDK responses may include sensitive data. Log only the type.
            logger.warning("AWS expression unavailable (%s); retry in %.0fs; requests=%d",
                           type(error).__name__, delay, self.requests)
            return
        self._failures = 0
        if generation == self._generation:
            self._expires = sampled_at + self.config.cache_ttl_seconds
            self._cache = None if observation is None else ExpressionObservation(
                observation.label, observation.confidence, observation.probabilities, sampled_at)
        if self._diagnostics:
            logger.debug("Cloud expression latency_ms=%.1f expression=%s confidence=%.3f requests=%d discarded=%s",
                        (self._clock() - started) * 1000,
                        observation.label if observation else "unknown",
                        observation.confidence if observation else 0, self.requests,
                        generation != self._generation)

    def _request_sync(self, crop: Any) -> Optional[ExpressionObservation]:
        import cv2
        if self._client is None:
            import boto3
            from botocore.config import Config
            self._client = boto3.client(
                "rekognition", region_name=self.config.region or os.environ.get("AWS_REGION") or None,
                config=Config(connect_timeout=self.config.connect_timeout_seconds,
                              read_timeout=self.config.read_timeout_seconds,
                              retries={"total_max_attempts": 1, "mode": "standard"}))
        # Picamera2 RGB888 currently produces BGR bytes, matching OpenCV JPEG encoding.
        if max(crop.shape[:2]) > 512:
            scale = 512 / max(crop.shape[:2])
            crop = cv2.resize(crop, (round(crop.shape[1] * scale), round(crop.shape[0] * scale)))
        ok, encoded = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 85])
        if not ok:
            raise ValueError("Could not encode selected crop")
        response = self._client.detect_faces(Image={"Bytes": encoded.tobytes()}, Attributes=["EMOTIONS"])
        return map_response(response, self.config.minimum_face_confidence)

    def _diagnostic(self, reason: str, now: float, *, force: bool = False) -> None:
        if self._diagnostics and (force or now - self._last_diagnostic >= 5):
            logger.debug("Cloud expression policy=%s requests=%d", reason, self.requests)
            self._last_diagnostic = now
