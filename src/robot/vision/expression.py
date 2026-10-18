"""OpenCV DNN adapter for configurable ONNX expression models."""

from __future__ import annotations

import asyncio
import logging
import math
import time
from pathlib import Path
from typing import Any, Optional, Sequence, Tuple

from .provider import ExpressionObservation, ExpressionProvider

logger = logging.getLogger(__name__)

# Settings for the current Picamera2 RGB888 capture (BGR byte order).
# FERPlus deliberately preserves the existing baseline, including RGB2GRAY.
EXPRESSION_PRESETS = {
    "ferplus": dict(
        labels=("neutral", "happiness", "surprise", "sadness", "anger", "disgust", "fear", "contempt"),
        input_size=(64, 64), scale=1.0, grayscale=True, swap_rb=False,
    ),
    "mobilefacenet": dict(
        labels=("angry", "disgust", "fearful", "happy", "neutral", "sad", "surprised"),
        input_size=(112, 112), scale=1.0 / 127.5,
        mean=(127.5, 127.5, 127.5), swap_rb=True,
    ),
}


class OpenCVExpressionProvider(ExpressionProvider):
    """Run a compact ONNX expression model through OpenCV DNN.

    The selected ONNX model is intentionally external to this repository. Its
    labels and input preprocessing remain constructor configuration because
    they are model-specific.
    """

    def __init__(
        self,
        model_path: Path,
        labels: Sequence[str],
        *,
        input_size: Tuple[int, int] = (64, 64),
        scale: float = 1.0 / 255.0,
        mean: Tuple[float, float, float] = (0.0, 0.0, 0.0),
        swap_rb: bool = True,
        grayscale: bool = False,
        diagnostics: bool = False,
    ) -> None:
        if not labels:
            raise ValueError("At least one expression label is required.")
        if input_size[0] <= 0 or input_size[1] <= 0:
            raise ValueError("Expression model input size must be positive.")
        self._model_path = Path(model_path)
        self._labels = tuple(labels)
        self._input_size = input_size
        self._scale = scale
        self._mean = mean
        self._swap_rb = swap_rb
        self._grayscale = grayscale
        self._diagnostics = diagnostics
        self._cv2: Any = None
        self._network: Any = None
        self.last_inference_ms: Optional[float] = None

    @classmethod
    def from_preset(cls, model_path: Path, preset: str) -> OpenCVExpressionProvider:
        """Construct a known model for the current Pi camera byte order."""
        return cls(model_path, **EXPRESSION_PRESETS[preset])

    async def classify(self, face_crop: Any) -> Optional[ExpressionObservation]:
        if not _has_pixels(face_crop):
            return None
        return await asyncio.to_thread(self._classify_sync, face_crop)

    def _classify_sync(self, face_crop: Any) -> ExpressionObservation:
        cv2, network = self._load_network()
        if self._grayscale:
            face_crop = cv2.cvtColor(face_crop, cv2.COLOR_RGB2GRAY)
        blob = cv2.dnn.blobFromImage(
            face_crop,
            scalefactor=self._scale,
            size=self._input_size,
            mean=self._mean,
            swapRB=False if self._grayscale else self._swap_rb,
            crop=False,
        )
        network.setInput(blob)
        started = time.perf_counter()
        values = [float(value) for value in network.forward().flatten()]
        self.last_inference_ms = (time.perf_counter() - started) * 1000.0
        if len(values) != len(self._labels):
            raise RuntimeError("Expression model output count does not match configured labels.")
        probabilities = _probabilities(values)
        index = max(range(len(probabilities)), key=probabilities.__getitem__)
        if self._diagnostics:
            logger.debug(
                "Expression diagnostics: input_shape=%s grayscale=%s blob_shape=%s raw_scores=%s "
                "probabilities=%s top=%s confidence=%.4f inference_ms=%.2f",
                getattr(face_crop, "shape", None),
                self._grayscale,
                getattr(blob, "shape", None),
                [round(value, 4) for value in values],
                [round(value, 4) for value in probabilities],
                self._labels[index],
                probabilities[index],
                self.last_inference_ms,
            )
        return ExpressionObservation(
            label=self._labels[index], confidence=probabilities[index],
            probabilities=tuple(zip(self._labels, probabilities)),
        )

    def _load_network(self) -> Tuple[Any, Any]:
        if self._network is not None:
            return self._cv2, self._network
        if not self._model_path.is_file():
            raise FileNotFoundError(f"Expression ONNX model was not found: {self._model_path}")
        try:
            import cv2
        except ImportError as error:
            raise RuntimeError("OpenCV is required for expression classification.") from error
        self._cv2 = cv2
        self._network = cv2.dnn.readNetFromONNX(str(self._model_path))
        return self._cv2, self._network


def _has_pixels(image: Any) -> bool:
    shape = getattr(image, "shape", None)
    return bool(shape and len(shape) >= 2 and shape[0] > 0 and shape[1] > 0)


def _probabilities(values: Sequence[float]) -> Tuple[float, ...]:
    total = sum(values)
    if all(value >= 0.0 for value in values) and total > 0.0 and abs(total - 1.0) < 0.01:
        return tuple(value / total for value in values)
    maximum = max(values)
    exponentials = [math.exp(value - maximum) for value in values]
    denominator = sum(exponentials)
    return tuple(value / denominator for value in exponentials)
