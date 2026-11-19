"""OpenCV-backed, provider-neutral face detection."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Optional, Sequence, Tuple

from .provider import FaceDetector, FaceRegion

_CASCADE_FILENAME = "haarcascade_frontalface_default.xml"
_RASPBERRY_PI_CASCADE_PATH = Path("/usr/share/opencv4/haarcascades") / _CASCADE_FILENAME


class OpenCVFaceDetector(FaceDetector):
    """Low-cost Haar Cascade face detector suitable for Raspberry Pi 3."""

    def __init__(
        self,
        cascade_path: Optional[Path] = None,
        *,
        scale_factor: float = 1.1,
        min_neighbors: int = 5,
        min_size: Tuple[int, int] = (48, 48),
    ) -> None:
        if scale_factor <= 1.0:
            raise ValueError("scale_factor must be greater than 1.0.")
        self._cascade_path = cascade_path
        self._scale_factor = scale_factor
        self._min_neighbors = min_neighbors
        self._min_size = min_size
        self._cv2: Any = None
        self._cascade: Any = None

    async def detect(self, frame: Any) -> Sequence[FaceRegion]:
        return await asyncio.to_thread(self._detect_sync, frame)

    def _detect_sync(self, frame: Any) -> Sequence[FaceRegion]:
        cv2, cascade = self._load_cascade()
        grayscale = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        detected = cascade.detectMultiScale(
            grayscale,
            scaleFactor=self._scale_factor,
            minNeighbors=self._min_neighbors,
            minSize=self._min_size,
        )
        regions = tuple(FaceRegion(int(x), int(y), int(width), int(height)) for x, y, width, height in detected)
        return tuple(sorted(regions, key=lambda region: region.area, reverse=True))

    def _load_cascade(self) -> Tuple[Any, Any]:
        if self._cascade is not None:
            return self._cv2, self._cascade
        try:
            import cv2
        except ImportError as error:
            raise RuntimeError("OpenCV is required for face detection.") from error

        path = self._cascade_path or _default_cascade_path(cv2)
        cascade = cv2.CascadeClassifier(str(path))
        if cascade.empty():
            raise RuntimeError("Could not load the configured OpenCV Haar Cascade.")
        self._cv2 = cv2
        self._cascade = cascade
        return cv2, cascade


def _default_cascade_path(cv2: Any) -> Path:
    """Use OpenCV's bundled data when available, else Raspberry Pi OS data."""
    data = getattr(cv2, "data", None)
    haarcascades = getattr(data, "haarcascades", None)
    if haarcascades:
        return Path(haarcascades) / _CASCADE_FILENAME
    return _RASPBERRY_PI_CASCADE_PATH
