"""Local, cached eye-readability protection independent of semantic effects."""
from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_MODES = {
    # radius, maximum opacity: strength expands and softens the local halo,
    # rather than turning the same shape into an opaque black blot.
    "subtle": (1.22, .16), "balanced": (1.38, .27), "expressive": (1.58, .36),
}


@dataclass(frozen=True)
class EyeSafeParams:
    enabled: bool = False
    mode: str = "balanced"


@dataclass(frozen=True)
class EyeSafeAnalysis:
    luminance: float = .0
    variation: float = .0


@dataclass(frozen=True)
class EyeSafePrimitive:
    bounds: tuple[float, float, float, float]
    color: str
    opacity: float


class EyeSafeResolver:
    """Expose the small canonical appearance setting to the render loop."""
    def __init__(self, config) -> None:
        self.enabled = bool(config.eye_safe_layer_enabled)
        self.mode = config.eye_safe_layer_mode

    def resolve(self) -> EyeSafeParams:
        return EyeSafeParams(self.enabled, self.mode)


class EyeSafeLayer:
    """Draw soft local protection; image analysis is cached per prepared asset."""
    def __init__(self) -> None:
        self._analysis: dict[tuple[Path, int, int], EyeSafeAnalysis] = {}
        self._failed: set[tuple[Path, int, int]] = set()

    def primitives(self, params: EyeSafeParams, frame, asset) -> tuple[EyeSafePrimitive, ...]:
        if not params.enabled:
            return ()
        analysis = self._analyze(asset, frame.width, frame.height)
        if analysis is None:
            return ()  # Legacy colour/no usable image: preserve its rendering unchanged.
        # Dark, low-variation backgrounds need effectively no treatment.
        need = min(1.0, max(0.0, (analysis.luminance - .22) * .85 + analysis.variation * .50))
        if need < .04:
            return ()
        base_radius, max_opacity = _MODES[params.mode]
        scale = base_radius + need * (.12 if params.mode == "subtle" else .20)
        opacity = max_opacity * need
        # One transparent, elliptical radial mask per actual eye keeps the
        # background recognisable between and around the eyes.
        primitives = []
        for eye in frame.eyes:
            radius_x, radius_y = eye.radius_x * scale, eye.radius_y * scale
            primitives.append(EyeSafePrimitive(
                (eye.center_x - radius_x, eye.center_y - radius_y,
                 eye.center_x + radius_x, eye.center_y + radius_y), "#020814", opacity))
        return tuple(primitives)

    def _analyze(self, asset, width: int, height: int) -> EyeSafeAnalysis | None:
        if asset is None or not asset.available or asset.path is None:
            return None
        key = (asset.path, width, height)
        if key in self._analysis:
            return self._analysis[key]
        if key in self._failed:
            return None
        try:
            result = self._read_analysis(asset.path, width, height)
            self._analysis[key] = result
            return result
        except Exception as error:
            self._failed.add(key)
            logger.debug("PHOS VISUAL: eye safe analysis unavailable reason=%s", type(error).__name__)
            return None

    @staticmethod
    def _read_analysis(path: Path, width: int, height: int) -> EyeSafeAnalysis:
        """Decode only on a cache miss, using the already-supported OpenCV path."""
        import cv2
        image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        if image is None:
            raise ValueError("invalid image")
        source_height, source_width = image.shape[:2]
        scale = max(width / source_width, height / source_height)
        image = cv2.resize(image, (round(source_width * scale), round(source_height * scale)), interpolation=cv2.INTER_AREA)
        left, top = (image.shape[1] - width) // 2, (image.shape[0] - height) // 2
        crop = image[top:top + height, left:left + width]
        # The two fixed regions bound the normal eye positions. Downsample
        # once on background change, not in the frame loop.
        regions = (crop[height * 22 // 100:height * 70 // 100, width * 10 // 100:width * 46 // 100],
                   crop[height * 22 // 100:height * 70 // 100, width * 54 // 100:width * 90 // 100])
        values = [region[::8, ::8] for region in regions if region.size]
        if not values:
            raise ValueError("empty image")
        luminance = sum(float(region.mean()) for region in values) / len(values) / 255
        variation = sum(float(region.std()) for region in values) / len(values) / 128
        return EyeSafeAnalysis(luminance, min(1.0, variation))
