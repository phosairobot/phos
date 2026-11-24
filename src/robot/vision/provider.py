# SPDX-License-Identifier: Apache-2.0
"""Provider-neutral contracts and data structures for visual perception."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Optional, Sequence, Tuple
import math


@dataclass(frozen=True)
class FaceRegion:
    """A rectangular face location in pixels within a captured frame."""

    x: int
    y: int
    width: int
    height: int

    @property
    def area(self) -> int:
        return self.width * self.height


@dataclass(frozen=True)
class FacePosition:
    """The center of a detected face, normalized to the camera frame."""

    x: float
    y: float


@dataclass(frozen=True)
class VisionObservation:
    """Canonical selected-detection semantics; IDs are runtime-local only."""
    kind: str
    target_id: str
    x: float
    y: float
    width_normalized: float
    height_normalized: float
    confidence: Optional[float]
    timestamp: float

    def __post_init__(self) -> None:
        if self.kind != "face" or not self.target_id:
            raise ValueError("Vision observation requires a face kind and runtime-local target ID.")
        if not all(math.isfinite(value) and -1 <= value <= 1 for value in (self.x, self.y)):
            raise ValueError("Vision observation center must be normalized to [-1, 1].")
        if not all(math.isfinite(value) and 0 < value <= 1 for value in (self.width_normalized, self.height_normalized)):
            raise ValueError("Vision observation dimensions must be normalized to (0, 1].")
        if self.confidence is not None and (not math.isfinite(self.confidence) or not 0 <= self.confidence <= 1):
            raise ValueError("Vision observation confidence must be null or normalized to [0, 1].")

    def document(self) -> dict:
        return {"kind": self.kind, "target_id": self.target_id, "x": self.x, "y": self.y,
                "width_normalized": self.width_normalized, "height_normalized": self.height_normalized,
                "confidence": self.confidence, "timestamp": self.timestamp}


@dataclass(frozen=True)
class ExpressionObservation:
    """Raw model output; labelled probabilities enable semantic abstention."""

    label: str
    confidence: float
    probabilities: Tuple[Tuple[str, float], ...] = ()
    sampled_at: Optional[float] = None  # cached providers retain the original capture time


@dataclass(frozen=True)
class ObservedExpression:
    """Latest uncertain classifier output for the selected face, not PHOS state."""

    available: bool
    label: Optional[str]
    confidence: Optional[float]
    provider: Optional[str]
    model: Optional[str]
    observed_at: Optional[float]
    unavailable_reason: Optional[str] = None

    def document(self) -> dict:
        return {"available": self.available, "label": self.label, "confidence": self.confidence,
                "provider": self.provider, "model": self.model, "observed_at": self.observed_at,
                "unavailable_reason": self.unavailable_reason}


@dataclass(frozen=True)
class VisualExpression:
    """A confirmed semantic observation, or explicit UNKNOWN abstention."""

    label: str
    confidence: float
    observed_for_ms: int


class CameraProvider(ABC):
    """Camera lifecycle and frame capture boundary."""

    @abstractmethod
    async def start(self) -> None:
        raise NotImplementedError

    @abstractmethod
    async def capture_frame(self) -> Any:
        raise NotImplementedError

    @abstractmethod
    async def stop(self) -> None:
        raise NotImplementedError


class FaceDetector(ABC):
    """Detect visible faces without exposing a computer-vision backend."""

    @abstractmethod
    async def detect(self, frame: Any) -> Sequence[FaceRegion]:
        raise NotImplementedError


class ExpressionProvider(ABC):
    """Classify visible features in a cropped face image."""

    def invalidate(self) -> None:
        """Discard evidence when local selection loses continuity."""

    async def close(self) -> None:
        """Drain background work during shutdown, if any."""

    @abstractmethod
    async def classify(self, face_crop: Any) -> Optional[ExpressionObservation]:
        raise NotImplementedError
