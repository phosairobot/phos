"""Provider-neutral local vision pipeline for PHOS."""

from .camera import Picamera2CameraProvider
from .detector import OpenCVFaceDetector
from .expression import OpenCVExpressionProvider
from .pipeline import (
    VISION_EXPRESSION_STABLE,
    VISION_FACE_LOST,
    VISION_FACE_POSITION,
    CameraSnapshot,
    CapturedFrame,
    VisionPipeline,
    VisionResult,
    VisionStatus,
    crop_face,
    face_position,
    vision_observation,
)
from robot.core.presence import VISION_FACE_OBSERVATION
from .provider import (
    CameraProvider,
    ExpressionObservation,
    ExpressionProvider,
    FaceDetector,
    FacePosition,
    FaceRegion,
    ObservedExpression,
    VisionObservation,
    VisualExpression,
)
from .smoother import ExpressionSmoother

__all__ = [
    "CameraProvider",
    "CameraSnapshot",
    "CapturedFrame",
    "ExpressionObservation",
    "ObservedExpression",
    "ExpressionProvider",
    "ExpressionSmoother",
    "FaceDetector",
    "FacePosition",
    "FaceRegion",
    "VisionObservation",
    "OpenCVExpressionProvider",
    "OpenCVFaceDetector",
    "Picamera2CameraProvider",
    "VISION_EXPRESSION_STABLE",
    "VISION_FACE_LOST",
    "VISION_FACE_POSITION",
    "VISION_FACE_OBSERVATION",
    "VisionPipeline",
    "VisionResult",
    "VisionStatus",
    "VisualExpression",
    "crop_face",
    "face_position",
    "vision_observation",
]
