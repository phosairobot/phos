"""Rate-limited local vision orchestration, independent of LLMs and hardware SDKs."""

from __future__ import annotations

import asyncio
import logging
import math
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional

from robot.core import Behavior, Event, EventBus
from robot.core.presence import VISION_FACE_OBSERVATION
from robot.core.expression_reaction import EXPRESSION_REACTION_REQUESTED, ExpressionReactionPolicy

from .provider import (CameraProvider, ExpressionObservation, ExpressionProvider, FaceDetector, FacePosition,
                       FaceRegion, ObservedExpression, VisionObservation, VisualExpression)
from .smoother import ExpressionSmoother
from .selection import FaceSelector

VISION_EXPRESSION_STABLE = "vision.visual_expression_stable"
VISION_FACE_LOST = "vision.face_lost"
VISION_FACE_POSITION = "vision.face_position"
OBSERVED_EXPRESSION_CHANGED = "vision.observed_expression_changed"

logger = logging.getLogger(__name__)

_EXPRESSION_LOG_INTERVAL_SECONDS = 1.0
# Classifier inference is slower than camera capture. Keep a valid result
# through normal frame gaps, then explicitly mark it stale.
_OBSERVED_EXPRESSION_TTL_SECONDS = 1.5
_OBSERVED_EXPRESSION_LABELS = {
    "happiness": "happy", "happy": "happy", "sadness": "sad", "sad": "sad",
    "surprise": "surprised", "surprised": "surprised", "anger": "angry", "angry": "angry",
    "fear": "fearful", "fearful": "fearful", "neutral": "neutral", "disgust": "disgust",
}


class VisionStatus(str, Enum):
    NOT_DUE = "not_due"
    NO_FACE = "no_face"
    LOW_CONFIDENCE = "low_confidence"
    UNSTABLE = "unstable"
    STABLE = "stable"
    UNKNOWN = "unknown"
    FACE_DETECTED = "face_detected"


@dataclass(frozen=True)
class VisionResult:
    status: VisionStatus
    visual_expression: Optional[VisualExpression] = None


@dataclass(frozen=True)
class VisionPreviewSnapshot:
    """Latest in-memory frame and diagnostics for the local display only."""
    frame: Any
    face: Optional[FaceRegion] = None
    raw_expression: Optional[str] = None
    confidence: Optional[float] = None
    semantic_expression: Optional[str] = None


class VisionPipeline(Behavior):
    """Capture, detect, classify, smooth and publish local vision results."""

    name = "vision-pipeline"

    def __init__(
        self,
        camera: CameraProvider,
        face_detector: FaceDetector,
        expression_provider: Optional[ExpressionProvider],
        smoother: Optional[ExpressionSmoother],
        *,
        events: Optional[EventBus] = None,
        capture_interval_seconds: float = 1.0 / 15.0,
        detection_interval_seconds: float = 1.0 / 4.0,
        expression_interval_seconds: float = 1.0 / 3.0,
        diagnostics: bool = False,
        crop_margin: float = 0.10,
        preview_enabled: bool = False,
        publish_face_position: bool = True,
        observed_expression_provider: Optional[str] = None,
        observed_expression_model: Optional[str] = None,
        observed_expression_ttl_seconds: float = _OBSERVED_EXPRESSION_TTL_SECONDS,
        expression_reaction_policy: Optional[ExpressionReactionPolicy] = None,
        clock=time.monotonic,
    ) -> None:
        for name, value in (
            ("capture_interval_seconds", capture_interval_seconds),
            ("detection_interval_seconds", detection_interval_seconds),
            ("expression_interval_seconds", expression_interval_seconds),
        ):
            if value <= 0:
                raise ValueError(f"{name} must be positive.")
        if (expression_provider is None) != (smoother is None):
            raise ValueError("expression_provider and smoother must be configured together.")
        if not math.isfinite(crop_margin) or not 0.0 <= crop_margin <= 0.5:
            raise ValueError("crop_margin must be between zero and 0.5.")
        if not math.isfinite(observed_expression_ttl_seconds) or observed_expression_ttl_seconds <= 0:
            raise ValueError("observed_expression_ttl_seconds must be positive and finite.")
        self._crop_margin = crop_margin
        self._preview_enabled = preview_enabled
        self._publish_face_position = publish_face_position
        self._preview_snapshot: Optional[VisionPreviewSnapshot] = None
        self._face_selector = FaceSelector()
        self._camera = camera
        self._face_detector = face_detector
        self._expression_provider = expression_provider
        self._smoother = smoother
        self._events = events
        self._capture_interval = capture_interval_seconds
        self._detection_interval = detection_interval_seconds
        self._expression_interval = expression_interval_seconds
        self._diagnostics = diagnostics
        self._next_detection_at = 0.0
        self._next_expression_at = 0.0
        self._face_present = False
        self._last_logged_expression_label: Optional[str] = None
        self._last_expression_log_at: Optional[float] = None
        self._observed_expression_provider = observed_expression_provider
        self._observed_expression_model = observed_expression_model
        self._observed_expression_ttl_seconds = observed_expression_ttl_seconds
        self._clock = clock
        self._observed_expression = ObservedExpression(
            False, None, None, observed_expression_provider, observed_expression_model, None,
            "provider_disabled" if expression_provider is None else "no_observation",
        )
        # This is deliberately pipeline-local monotonic time. Provider sample
        # timestamps remain useful observation metadata but must never be
        # compared against a potentially different clock domain for TTL.
        self._observed_expression_fresh_at: Optional[float] = None
        self._expression_reaction_policy = expression_reaction_policy
        self._last_expression_inference_at: Optional[float] = None
        self._task: Optional[asyncio.Task[None]] = None

    async def start(self) -> None:
        if self._task is not None:
            raise RuntimeError("Vision pipeline is already running.")
        self._face_selector.reset()
        self._next_detection_at = self._next_expression_at = 0.0
        self._face_present = False
        if self._smoother is not None:
            self._smoother.reset()
        await self._camera.start()
        self._task = asyncio.create_task(self._run(), name="vision-pipeline")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            except Exception:
                # A runtime supervisor observes and reports task failures. Stop
                # must still release the camera after such a failure.
                pass
            self._task = None
        try:
            if self._expression_provider is not None:
                close = getattr(self._expression_provider, "close", None)
                if close is not None:
                    await close()
        finally:
            await self._camera.stop()

    async def wait(self) -> None:
        """Wait for the background pipeline task; used by the runtime supervisor."""
        if self._task is None:
            raise RuntimeError("Vision pipeline is not running.")
        await asyncio.shield(self._task)

    async def process_once(self, *, timestamp: Optional[float] = None) -> VisionResult:
        """Capture one frame and perform work only when its rate limit permits."""
        now = self._clock() if timestamp is None else timestamp
        frame = await self._camera.capture_frame()
        if self._preview_enabled:
            previous = self._preview_snapshot
            self._preview_snapshot = VisionPreviewSnapshot(
                frame, previous.face if previous else None,
                self._observed_expression.label, self._observed_expression.confidence,
                previous.semantic_expression if previous else None,
            )
        await self._expire_observed_expression(now)
        if now < self._next_detection_at:
            return VisionResult(VisionStatus.NOT_DUE)
        self._next_detection_at = now + self._detection_interval
        faces = await self._face_detector.detect(frame)
        height, width = frame.shape[:2]
        if self._diagnostics:
            # FaceDetector deliberately exposes provider-neutral geometry only.
            # A detector label/class or confidence must be normalized by its
            # adapter before this point; it is not silently inferred here.
            logger.debug("Vision raw detections: count=%s timestamp=%.3f boxes=%s", len(faces), now,
                        [(face.x, face.y, face.width, face.height) for face in faces])
        selection = self._face_selector.select(faces, width=width, height=height, timestamp=now)
        face = selection.face
        if self._preview_enabled:
            previous = self._preview_snapshot
            self._preview_snapshot = VisionPreviewSnapshot(
                frame, face, self._observed_expression.label, self._observed_expression.confidence,
                previous.semantic_expression if previous else None,
            )
        if self._diagnostics:
            logger.debug(
                "Face selection: detected=%s selected=%s reason=%s rejected=%s expression_ready=%s",
                faces, face, selection.reason, selection.rejected, selection.expression_ready,
            )
        if selection.new_track or not selection.expression_ready:
            invalidate = getattr(self._expression_provider, "invalidate", None)
            if invalidate is not None:
                invalidate()
        if selection.new_track and self._smoother is not None:
            self._smoother.reset()
        if face is None:
            if self._smoother is not None:
                self._smoother.reset()
            self._reset_expression_log()
            if self._publish_face_position and self._face_present and self._events is not None:
                await self._events.publish(Event(VISION_FACE_LOST))
            self._face_present = False
            if self._events is not None:
                await self._events.publish(Event(VISION_FACE_OBSERVATION, {"observation": None, "timestamp": now}))
            return VisionResult(VisionStatus.NO_FACE)

        self._face_present = True
        if self._publish_face_position and self._events is not None:
            position = face_position(frame, face)
            await self._events.publish(
                Event(VISION_FACE_POSITION, {"face_position": {"x": position.x, "y": position.y}})
            )
        if self._events is not None:
            observation = vision_observation(frame, face, timestamp=now)
            position = FacePosition(observation.x, observation.y)
            if self._diagnostics:
                logger.debug("Vision normalized observation: type=face id=face-1 confidence=%s "
                            "center=(%.3f,%.3f) bbox=(%s,%s,%s,%s)", observation.confidence, position.x, position.y,
                            face.x, face.y, face.width, face.height)
            await self._events.publish(Event(VISION_FACE_OBSERVATION, {"observation": observation.document()}))
        if self._expression_provider is None or self._smoother is None:
            return VisionResult(VisionStatus.FACE_DETECTED)
        if not selection.expression_ready:
            self._smoother.reset()
            return VisionResult(VisionStatus.UNSTABLE, VisualExpression("unknown", 0.0, 0))
        if now < self._next_expression_at:
            return VisionResult(VisionStatus.NOT_DUE)
        self._next_expression_at = now + self._expression_interval
        region = square_face_region(frame, face, margin=self._crop_margin, reference_size=selection.crop_size)
        face_crop = None if region is None else frame[
            region.y:region.y + region.height, region.x:region.x + region.width
        ]
        if self._diagnostics:
            logger.debug("Face crop: region=%s margin=%.3f smoothed_size=%s",
                        region, self._crop_margin, selection.crop_size)
        if face_crop is None:
            invalidate = getattr(self._expression_provider, "invalidate", None)
            if invalidate is not None:
                invalidate()
            self._smoother.reset()
            self._reset_expression_log()
            return VisionResult(VisionStatus.NO_FACE)
        if self._diagnostics:
            logger.debug(
                "Expression diagnostics: selected_face=(x=%s, y=%s, width=%s, height=%s) crop_shape=%s",
                face.x,
                face.y,
                face.width,
                face.height,
                getattr(face_crop, "shape", None),
            )
        try:
            observation = await self._expression_provider.classify(face_crop)
        except Exception:
            # Classification telemetry is independent of face presence.  A
            # model failure must not stop the shared selected-face path.
            logger.warning("Observed expression classification failed", exc_info=self._diagnostics)
            await self._set_observed_expression(None, now, "classification_failed")
            return VisionResult(VisionStatus.UNKNOWN, VisualExpression("unknown", 0.0, 0))
        # Freshness starts when the provider actually returns, not when this
        # frame started. ONNX inference on a Pi can take a meaningful part of
        # the freshness window.
        observed_now = self._clock() if timestamp is None else now
        if self._last_expression_inference_at is not None:
            logger.debug("EXPR INFERENCE: interval_ms=%.1f", (observed_now - self._last_expression_inference_at) * 1000)
        self._last_expression_inference_at = observed_now
        logger.debug("EXPR RAW: label=%s confidence=%s monotonic=%.3f",
                    None if observation is None else observation.label,
                    None if observation is None else observation.confidence, observed_now)
        await self._set_observed_expression(observation, observed_now,
                                            "no_observation" if observation is None else None)
        if self._expression_reaction_policy is not None:
            intent = self._expression_reaction_policy.observe(self._observed_expression, now=observed_now)
            if intent is not None and self._events is not None:
                await self._events.publish(Event(EXPRESSION_REACTION_REQUESTED, intent.document()))
        if observation is not None:
            self._log_expression_observation(observation.label, observation.confidence, now)
        stable = self._smoother.observe(observation, timestamp=now)
        decision = self._smoother.decision
        result = stable or VisualExpression("unknown", 0.0, 0)
        if self._preview_enabled and stable is not None:
            self._preview_snapshot = VisionPreviewSnapshot(frame, face,
                self._observed_expression.label, self._observed_expression.confidence, stable.label)
        if self._diagnostics:
            logger.debug(
                "Expression semantics: top=%s semantic=%s reason=%s temporal=%s",
                sorted(observation.probabilities, key=lambda item: item[1], reverse=True)[:3]
                if observation is not None else [],
                result.label, decision.reason, self._smoother.temporal_state,
            )
        if self._events is not None:
            await self._events.publish(
                Event(
                    VISION_EXPRESSION_STABLE,
                    {
                        "visual_expression": {
                            "label": result.label,
                            "confidence": result.confidence,
                            "observed_for_ms": result.observed_for_ms,
                        }
                    },
                )
            )
        status = VisionStatus.STABLE if stable else (
            VisionStatus.UNSTABLE if decision.reason == "temporal_pending" else VisionStatus.UNKNOWN
        )
        return VisionResult(status, result)

    @property
    def preview_snapshot(self) -> Optional[VisionPreviewSnapshot]:
        """Return one latest-frame reference; no queue, disk or remote transport."""
        return self._preview_snapshot if self._preview_enabled else None

    @property
    def observed_expression(self) -> ObservedExpression:
        return self._observed_expression

    async def _set_observed_expression(self, observation: Optional[ExpressionObservation], now: float,
                                       reason: Optional[str]) -> None:
        label = None if observation is None else _canonical_expression_label(observation.label)
        confidence = None if observation is None else observation.confidence
        if observation is not None and (not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0):
            label = None
            confidence = None
            reason = "invalid_confidence"
        if observation is not None:
            logger.debug("EXPR NORMALIZED: raw=%s canonical=%s accepted=%s reason=%s",
                        observation.label, label, label is not None, reason)
        value = ObservedExpression(
            label is not None,
            label,
            confidence,
            self._observed_expression_provider,
            self._observed_expression_model,
            None if label is None else (now if observation.sampled_at is None else observation.sampled_at),
            reason,
        )
        previous = self._observed_expression
        previous_fresh_at = self._observed_expression_fresh_at
        # Confidence jitter alone is not a new semantic event, but the read
        # model always retains the actual latest classifier confidence.
        changed = (previous.available, previous.label, previous.provider, previous.model) != (
            value.available, value.label, value.provider, value.model)
        self._observed_expression = value
        self._observed_expression_fresh_at = now if value.available else None
        # The local overlay is a projection of the same canonical observation
        # consumed by the application service and Web Admin.
        if self._preview_enabled and self._preview_snapshot is not None:
            snapshot = self._preview_snapshot
            self._preview_snapshot = VisionPreviewSnapshot(
                snapshot.frame, snapshot.face, value.label, value.confidence, snapshot.semantic_expression,
            )
        if value.available:
            logger.debug("EXPR CANONICAL SET: state_id=%s available=true label=%s confidence=%s observed_monotonic=%.3f",
                        id(self._observed_expression), value.label, value.confidence, self._observed_expression_fresh_at)
        elif previous.available:
            age_ms = 0.0 if previous_fresh_at is None else (now - previous_fresh_at) * 1000
            logger.debug("EXPR CANONICAL CLEAR: previous_label=%s reason=%s age_ms=%.1f",
                        previous.label, reason, age_ms)
        if not changed:
            return
        logger.debug("OBSERVED EXPRESSION: %s -> %s confidence=%s", previous.label or "unavailable",
                     value.label or "unavailable", value.confidence)
        if self._events is not None:
            logger.info("PHOS VISION: observed_expression_changed available=%s label=%s",
                        value.available, value.label)
            await self._events.publish(Event(OBSERVED_EXPRESSION_CHANGED, value.document()))

    async def _expire_observed_expression(self, now: float) -> None:
        current = self._observed_expression
        if (not current.available or self._observed_expression_fresh_at is None
                or now - self._observed_expression_fresh_at <= self._observed_expression_ttl_seconds):
            return
        await self._set_observed_expression(None, now, "expired")

    def configure_preview(self, enabled: bool) -> None:
        self._preview_enabled = enabled
        if not enabled:
            self._preview_snapshot = None

    async def _run(self) -> None:
        while True:
            await self.process_once()
            await asyncio.sleep(self._capture_interval)

    def _log_expression_observation(self, label: str, confidence: float, now: float) -> None:
        """Report classifier output without logging every inference frame."""
        if (
            label == self._last_logged_expression_label
            and self._last_expression_log_at is not None
            and now - self._last_expression_log_at < _EXPRESSION_LOG_INTERVAL_SECONDS
        ):
            return
        logger.debug("Visible expression observation: %s (confidence %.2f)", label, confidence)
        self._last_logged_expression_label = label
        self._last_expression_log_at = now

    def _reset_expression_log(self) -> None:
        self._last_logged_expression_label = None
        self._last_expression_log_at = None


def _canonical_expression_label(raw_label: str) -> Optional[str]:
    """Normalize known provider aliases without substituting a neutral fallback."""
    label = raw_label.strip().lower()
    return _OBSERVED_EXPRESSION_LABELS.get(label, label or None)


def _select_largest_face(faces: Any) -> Optional[FaceRegion]:
    return max(faces, key=lambda face: face.area, default=None)


def crop_face(
    frame: Any, face: FaceRegion, *, margin: float = 0.10, reference_size: Optional[float] = None
) -> Optional[Any]:
    """Return a square, bounded image view with margin; no copying or stretching."""
    region = square_face_region(frame, face, margin=margin, reference_size=reference_size)
    if region is None:
        return None
    return frame[region.y:region.y + region.height, region.x:region.x + region.width]


def square_face_region(
    frame: Any, face: FaceRegion, *, margin: float = 0.10,
    reference_size: Optional[float] = None,
) -> Optional[FaceRegion]:
    """Shift a square inside the frame, reducing margin before clipping a face."""
    if not math.isfinite(margin) or not 0 <= margin <= 0.5:
        raise ValueError("margin must be between zero and 0.5.")
    shape = getattr(frame, "shape", None)
    if not shape or len(shape) < 2:
        raise ValueError("Camera frames must expose image-like shape information.")
    height, width = int(shape[0]), int(shape[1])
    if width <= 0 or height <= 0:
        raise ValueError("Camera frame dimensions must be positive.")
    if face.width <= 0 or face.height <= 0:
        return None
    left, top = max(0, face.x), max(0, face.y)
    right, bottom = min(width, face.x + face.width), min(height, face.y + face.height)
    if left >= right or top >= bottom:
        return None
    minimum_side = max(right - left, bottom - top)
    if minimum_side > min(width, height):
        return None  # A square cannot contain this face without trimming it.
    size = max(face.width, face.height) if reference_size is None else reference_size
    side = min(width, height, max(minimum_side, math.ceil(size * (1 + 2 * margin))))
    x = max(0, min(width - side, round((left + right - side) / 2)))
    y = max(0, min(height - side, round((top + bottom - side) / 2)))
    return FaceRegion(x, y, side, side)


def face_position(frame: Any, face: FaceRegion) -> FacePosition:
    """Map a detected face center to bounded, UI-neutral camera coordinates."""
    shape = getattr(frame, "shape", None)
    if not shape or len(shape) < 2:
        raise ValueError("Camera frames must expose image-like shape information.")
    frame_height, frame_width = int(shape[0]), int(shape[1])
    if frame_width <= 0 or frame_height <= 0:
        raise ValueError("Camera frame dimensions must be positive.")
    center_x = face.x + face.width / 2
    center_y = face.y + face.height / 2
    return FacePosition(
        x=_clamp_unit(center_x / (frame_width / 2) - 1.0),
        y=_clamp_unit(center_y / (frame_height / 2) - 1.0),
    )


def vision_observation(frame: Any, face: FaceRegion, *, timestamp: float,
                       target_id: str = "face-1", confidence: Optional[float] = None) -> VisionObservation:
    """Normalize selected geometry; Haar has no confidence, so preserve null."""
    shape = getattr(frame, "shape", None)
    if not shape or len(shape) < 2:
        raise ValueError("Camera frames must expose image-like shape information.")
    height, width = int(shape[0]), int(shape[1])
    position = face_position(frame, face)
    return VisionObservation("face", target_id, position.x, position.y,
                             min(1.0, face.width / width), min(1.0, face.height / height), confidence, timestamp)


def _clamp_unit(value: float) -> float:
    return max(-1.0, min(value, 1.0))
