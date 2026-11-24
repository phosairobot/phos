# SPDX-License-Identifier: Apache-2.0
"""Short-lived geometric face continuity; no identity or image persistence."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Optional, Sequence

from .provider import FaceRegion


@dataclass(frozen=True)
class FaceSelection:
    face: Optional[FaceRegion]
    reason: str
    rejected: tuple[tuple[FaceRegion, str], ...] = ()
    expression_ready: bool = False
    new_track: bool = False
    crop_size: Optional[float] = None


class FaceSelector:
    """Prefer a plausible continuation over the largest competing detection.

    A track is remembered for one second after its last match, but an old box
    is never returned as a current detection. Gaze may use the first detection;
    expression inference requires two consecutive matched detections.
    """

    def __init__(self, *, maximum_gap_seconds: float = 1.0) -> None:
        if not math.isfinite(maximum_gap_seconds) or maximum_gap_seconds <= 0:
            raise ValueError("maximum_gap_seconds must be positive and finite.")
        self._maximum_gap = maximum_gap_seconds
        self.reset()

    def reset(self) -> None:
        self._face: Optional[FaceRegion] = None
        self._last_at: Optional[float] = None
        self._frame_size: Optional[tuple[int, int]] = None
        self._consecutive = 0
        self._crop_size: Optional[float] = None

    def select(
        self, faces: Sequence[FaceRegion], *, width: int, height: int, timestamp: float
    ) -> FaceSelection:
        if (self._frame_size != (width, height) or self._last_at is None
                or timestamp <= self._last_at or timestamp - self._last_at > self._maximum_gap):
            self.reset()
        self._frame_size = (width, height)
        rejected = []
        valid = []
        for face in faces:
            if (face.width <= 0 or face.height <= 0 or face.x < 0 or face.y < 0
                    or face.x + face.width > width or face.y + face.height > height):
                rejected.append((face, "invalid_bounds"))
            elif not 0.5 <= face.width / face.height <= 2.0:
                rejected.append((face, "implausible_aspect"))
            else:
                valid.append(face)
        new_track = self._face is None
        if new_track:
            selected = max(valid, key=lambda face: (face.area, -face.x, -face.y), default=None)
            reason = "acquired_largest"
        else:
            ranked = []
            for face in valid:
                previous = self._face
                scale = max(previous.width, previous.height)
                distance = math.hypot(
                    face.x + face.width / 2 - previous.x - previous.width / 2,
                    face.y + face.height / 2 - previous.y - previous.height / 2,
                ) / scale
                ratios = (face.width / previous.width, face.height / previous.height)
                overlap = _iou(previous, face)
                if any(ratio < 2 / 3 or ratio > 1.5 for ratio in ratios):
                    rejected.append((face, "size_jump"))
                elif distance > 0.60 or (overlap < 0.15 and distance > 0.25):
                    rejected.append((face, "position_jump"))
                else:
                    size_change = sum(abs(math.log(ratio)) for ratio in ratios)
                    ranked.append((2 * overlap - distance - size_change, face))
            selected = max(ranked, key=lambda item: (item[0], -item[1].x, -item[1].y),
                           default=(0, None))[1]
            reason = "continuity_match"
        for face in valid:
            if face != selected and not any(face == item[0] for item in rejected):
                rejected.append((face, "competing_detection"))
        if selected is None:
            self._consecutive = 0
            return FaceSelection(None, "no_detections" if not faces else "no_plausible_match",
                                 tuple(rejected))
        self._face = selected
        self._last_at = timestamp
        self._consecutive = min(2, self._consecutive + 1)
        size = float(max(selected.width, selected.height))
        self._crop_size = size if self._crop_size is None else self._crop_size + 0.5 * (size - self._crop_size)
        return FaceSelection(selected, reason, tuple(rejected), self._consecutive >= 2,
                             new_track, self._crop_size)


def _iou(a: FaceRegion, b: FaceRegion) -> float:
    width = max(0, min(a.x + a.width, b.x + b.width) - max(a.x, b.x))
    height = max(0, min(a.y + a.height, b.y + b.height) - max(a.y, b.y))
    intersection = width * height
    return intersection / (a.area + b.area - intersection)
