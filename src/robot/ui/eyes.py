"""Pure, interpolated eye geometry for the PHOS face."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Optional, Tuple

from robot.iris_colors import IrisColorResolver
from .state import AmbientOverlayState, BlinkPhase, FaceExpression, FaceState, VisualAccent

_IRIS_ACCENTS = {
    VisualAccent.NEUTRAL: None,
    VisualAccent.WARM: (48, 226, 178),
    VisualAccent.COOL: (79, 195, 248),
    VisualAccent.CURIOUS: (79, 195, 248),
    VisualAccent.ALERT: (255, 191, 72),
    VisualAccent.SLEEPY: (173, 145, 248),
    VisualAccent.ERROR: (255, 93, 111),
}


@dataclass(frozen=True)
class EyeGeometry:
    center_x: float
    center_y: float
    radius_x: float
    radius_y: float
    pupil_x: float
    pupil_y: float
    iris_radius: float
    pupil_radius: float
    squint: float
    # Positive values lower the lid toward the eye's inner edge.  The value is
    # mirrored between eyes so the renderer remains independent of semantics.
    upper_lid_slope: float
    # Fraction of the upper eye reserved for the internal shell-coloured lid.
    upper_lid_depth: float
    # Downward bow of the internal lid boundary as a fraction of eye height.
    upper_lid_curve: float
    closed: bool


@dataclass(frozen=True)
class EyeFrame:
    width: int
    height: int
    background: str
    eye_color: str
    pupil_color: str
    iris_color: str
    eyes: Tuple[EyeGeometry, EyeGeometry]
    ambient_overlay: AmbientOverlayState


def upper_lid_points(eye: EyeGeometry, *, inset: float = 5.0, segments: int = 12) -> tuple[tuple[float, float], ...]:
    """Return a lid cap bounded by the eye's own inner elliptical contour.

    The first points follow the upper arc from left to right; the remainder is
    the expression-specific inner lid curve returning to the start. Using the
    inset eye ellipse keeps every cap point inside the visible shell without a
    mask.
    """
    if eye.upper_lid_depth <= 0.0:
        return ()
    radius_x, radius_y = eye.radius_x - inset, eye.radius_y - inset
    if radius_x <= 0.0 or radius_y <= 0.0:
        return ()
    top = eye.center_y - radius_y
    depth = radius_y * eye.upper_lid_depth
    left_y = top + depth * (1.0 + max(0.0, -eye.upper_lid_slope))
    right_y = top + depth * (1.0 + max(0.0, eye.upper_lid_slope))
    clamp_y = lambda value: max(eye.center_y - radius_y * .98, min(value, eye.center_y + radius_y * .98))
    left_y, right_y = clamp_y(left_y), clamp_y(right_y)
    left_angle = math.pi - math.asin((left_y - eye.center_y) / radius_y)
    right_angle = 2.0 * math.pi + math.asin((right_y - eye.center_y) / radius_y)
    arc = tuple(
        (eye.center_x + radius_x * math.cos(angle), eye.center_y + radius_y * math.sin(angle))
        for angle in (left_angle + (right_angle - left_angle) * index / segments for index in range(segments + 1))
    )
    inner_curve = []
    for fraction in (index / segments for index in range(1, segments + 1)):
        x = arc[-1][0] + (arc[0][0] - arc[-1][0]) * fraction
        y = arc[-1][1] + (arc[0][1] - arc[-1][1]) * fraction + eye.upper_lid_curve * radius_y * 4.0 * fraction * (1.0 - fraction)
        edge = radius_y * math.sqrt(max(0.0, 1.0 - ((x - eye.center_x) / radius_x) ** 2))
        inner_curve.append((x, max(eye.center_y - edge, min(y, eye.center_y + edge))))
    return arc + tuple(inner_curve)


def valid_polygon_points(points: tuple[tuple[float, float], ...]) -> bool:
    """Return whether a point set is safe to submit to a Canvas/SVG polygon."""
    return len(points) >= 3 and all(math.isfinite(coordinate) for point in points for coordinate in point)


@dataclass
class _AnimatedValues:
    eye_open_left: float
    eye_open_right: float
    squint: float
    upper_lid_tilt: float
    upper_lid_tilt_right: float
    upper_lid_depth: float
    upper_lid_depth_right: float
    upper_lid_curve: float
    upper_lid_curve_right: float
    pupil_inward: float
    pupil_x: float
    pupil_x_right: float
    pupil_y: float
    pupil_y_right: float
    iris_scale: float
    pupil_scale: float
    background: Tuple[int, int, int]
    eye_color: Tuple[int, int, int]
    pupil_color: Tuple[int, int, int]
    iris_color: Tuple[int, int, int]


@dataclass(frozen=True)
class ExpressionProfile:
    """Small immutable deformation input for the shared eye renderer."""

    eye_open: float = 1.0
    asymmetry: float = 0.0
    squint: float = 0.05
    gaze_x: float = 0.0
    gaze_y: float = 0.0
    upper_lid_tilt: float = 0.0
    upper_lid_depth: float = 0.0
    upper_lid_curve: float = 0.0
    gaze_inward: float = 0.0
    # Defaults retain mirrored profiles; side values are only for deliberate
    # internal-lid asymmetry.
    left_lid_tilt: Optional[float] = None
    right_lid_tilt: Optional[float] = None
    left_lid_depth: Optional[float] = None
    right_lid_depth: Optional[float] = None
    left_lid_curve: Optional[float] = None
    right_lid_curve: Optional[float] = None
    left_gaze_x: Optional[float] = None
    right_gaze_x: Optional[float] = None
    iris_scale: float = 1.0
    pupil_scale: float = 1.0


_EXPRESSION_PROFILES = {
    # Preserve the established shapes exactly before adding Phase 1 profiles.
    FaceExpression.NEUTRAL: ExpressionProfile(),
    FaceExpression.HAPPY: ExpressionProfile(.82, 0.0, .45, 0.0, .10),
    FaceExpression.CURIOUS: ExpressionProfile(1.03, .12, .15, .14, -.10),
    FaceExpression.SURPRISED: ExpressionProfile(1.25, .02, 0.0, 0.0, -.06),
    FaceExpression.SLEEPY: ExpressionProfile(.38, 0.0, .36, 0.0, .10),
    FaceExpression.WORRIED: ExpressionProfile(.82, .04, .18, -.10, -.08),
    # Phase 1: downcast sadness, tense anger, controlled focus, and physical
    # fatigue are deliberately separate rather than state-specific variants.
    # Soft, mirrored emotional lids: each outer endpoint falls lower than its
    # inner endpoint, with a slight raised arch instead of an angry straight edge.
    FaceExpression.SAD: ExpressionProfile(.74, 0.0, .20, 0.0, .18, -.10,
                                           upper_lid_depth=.82, upper_lid_curve=-.025,
                                           gaze_inward=.08),
    # Strong, mirrored inward tension: the cap falls at the inner eye corner
    # and rises toward the outer edge.  It is deeper and steeper than focus.
    FaceExpression.ANGRY: ExpressionProfile(.70, 0.0, .28, 0.0, .0, .50,
                                             upper_lid_depth=.74, upper_lid_curve=.01),
    # A clean, mirrored diagonal cap: the outer edge stays high while the
    # inner edge lowers toward the gap between the eyes.  Its almost-flat
    # boundary keeps the concentration controlled rather than aggressive.
    FaceExpression.FOCUSED: ExpressionProfile(.76, 0.0, .22, 0.0, .0, .42,
                                               upper_lid_depth=.68, upper_lid_curve=.015),
    FaceExpression.TIRED: ExpressionProfile(.62, 0.0, .28, 0.0, .15, 0.0, .76, .018),
    FaceExpression.CONFUSED: ExpressionProfile(.99, .10, .10, .02, -.03,
                                                left_lid_tilt=-.42, right_lid_tilt=.08,
                                                left_lid_depth=.52, right_lid_depth=.30,
                                                left_lid_curve=.01, right_lid_curve=-.10,
                                                left_gaze_x=-.06, right_gaze_x=.04),
    FaceExpression.SCARED: ExpressionProfile(1.25, 0.0, 0.0, 0.0, -.04,
                                              upper_lid_depth=.22, upper_lid_curve=-.12,
                                              iris_scale=.68, pupil_scale=.76),
    FaceExpression.SKEPTICAL: ExpressionProfile(.88, .17, .14, .01, .02,
                                                 left_lid_tilt=.12, right_lid_tilt=-.05,
                                                 left_lid_depth=.72, right_lid_depth=.22,
                                                 left_lid_curve=.015, right_lid_curve=-.06,
                                                 left_gaze_x=.05, right_gaze_x=-.03),
}


class EyeRenderer:
    """Convert only ``FaceState`` into smoothly animated eye geometry."""

    def __init__(self, *, width: int = 800, height: int = 600, transition_seconds: float = 0.18,
                 iris_color: str = "cyan") -> None:
        if width <= 0 or height <= 0 or transition_seconds <= 0:
            raise ValueError("Eye renderer dimensions and transition duration must be positive.")
        self._width = width
        self._height = height
        self._transition_seconds = transition_seconds
        try:
            self._iris_color = IrisColorResolver.normalize(iris_color)
        except ValueError as error:
            raise ValueError(f"Unsupported iris color: {iris_color}") from error
        self._values: Optional[_AnimatedValues] = None
        self._last_timestamp: Optional[float] = None

    @property
    def iris_color(self) -> str:
        """Return the selected canonical base color, before semantic tinting."""
        return self._iris_color

    def render(self, face_state: FaceState, *, timestamp: float) -> EyeFrame:
        state = face_state.normalized()
        target = _target_values(state, self._iris_color)
        if self._values is None:
            self._values = target
        else:
            previous_timestamp = timestamp if self._last_timestamp is None else self._last_timestamp
            elapsed = max(0.0, timestamp - previous_timestamp)
            self._values = _interpolate(self._values, target, min(elapsed / self._transition_seconds, 1.0))
        self._last_timestamp = timestamp
        blink_amount = _blink_openness(state.blink_phase, state.blink_progress)
        return EyeFrame(
            width=self._width,
            height=self._height,
            background=_hex_color(self._values.background),
            eye_color=_hex_color(self._values.eye_color),
            pupil_color=_hex_color(self._values.pupil_color),
            iris_color=_hex_color(self._values.iris_color),
            eyes=(
                self._make_eye(True, blink_amount, self._values),
                self._make_eye(False, blink_amount, self._values),
            ),
            ambient_overlay=state.ambient_overlay,
        )

    def update_appearance(self, *, iris_color: str) -> None:
        """Change validated renderer style; color blends in subsequent frames."""
        try:
            self._iris_color = IrisColorResolver.normalize(iris_color)
        except ValueError as error:
            raise ValueError(f"Unsupported iris color: {iris_color}") from error

    def _make_eye(self, left: bool, blink_amount: float, values: _AnimatedValues) -> EyeGeometry:
        eye_width = self._width * 0.27
        base_height = self._height * 0.42
        separation = self._width * 0.16
        center_x = self._width / 2 - separation if left else self._width / 2 + separation
        center_y = self._height * 0.50
        radius_x = eye_width / 2
        openness = values.eye_open_left if left else values.eye_open_right
        visible_open = max(0.0, openness * (1.0 - values.squint * 0.40) * blink_amount)
        radius_y = max(2.0, base_height * visible_open / 2)
        iris_radius = max(1.0, min(radius_x * 0.35, radius_y * 0.62) * values.iris_scale)
        pupil_radius = iris_radius * 0.52 * values.pupil_scale
        max_pupil_x = max(0.0, radius_x * 0.52 - iris_radius)
        max_pupil_y = max(0.0, radius_y * 0.48 - iris_radius)
        return EyeGeometry(
            center_x=center_x,
            center_y=center_y,
            radius_x=radius_x,
            radius_y=radius_y,
            pupil_x=center_x + ((values.pupil_x if left else values.pupil_x_right) + values.pupil_inward * (1 if left else -1)) * max_pupil_x,
            pupil_y=center_y + (values.pupil_y if left else values.pupil_y_right) * max_pupil_y,
            iris_radius=iris_radius,
            pupil_radius=pupil_radius,
            squint=values.squint,
            upper_lid_slope=values.upper_lid_tilt if left else values.upper_lid_tilt_right,
            upper_lid_depth=values.upper_lid_depth if left else values.upper_lid_depth_right,
            upper_lid_curve=values.upper_lid_curve if left else values.upper_lid_curve_right,
            closed=blink_amount <= 0.02,
        )


def _target_values(state: FaceState, iris_color: str = "cyan") -> _AnimatedValues:
    profile = _expression_profile(state.expression)
    strength = state.reaction_strength
    base_open = _blend(state.eye_open, profile.eye_open, strength)
    asymmetry = profile.asymmetry * strength if state.eye_asymmetry is None else state.eye_asymmetry
    left_open, right_open = ((base_open - asymmetry, base_open + asymmetry)
                             if state.eye_asymmetry is None else (base_open + asymmetry, base_open - asymmetry))
    eye_color, pupil_color = _accent_colors(state)
    iris_color = _iris_target(IrisColorResolver.rgb(iris_color), state)
    return _AnimatedValues(
        eye_open_left=_clamp_open(left_open),
        eye_open_right=_clamp_open(right_open),
        squint=_blend(state.squint, profile.squint, strength),
        upper_lid_tilt=(profile.left_lid_tilt if profile.left_lid_tilt is not None else profile.upper_lid_tilt) * strength,
        upper_lid_tilt_right=(profile.right_lid_tilt if profile.right_lid_tilt is not None else -profile.upper_lid_tilt) * strength,
        upper_lid_depth=(profile.left_lid_depth if profile.left_lid_depth is not None else profile.upper_lid_depth) * strength,
        upper_lid_depth_right=(profile.right_lid_depth if profile.right_lid_depth is not None else profile.upper_lid_depth) * strength,
        upper_lid_curve=(profile.left_lid_curve if profile.left_lid_curve is not None else profile.upper_lid_curve) * strength,
        upper_lid_curve_right=(profile.right_lid_curve if profile.right_lid_curve is not None else profile.upper_lid_curve) * strength,
        pupil_inward=profile.gaze_inward * strength,
        pupil_x=_clamp_unit(state.pupil_x + (profile.left_gaze_x if profile.left_gaze_x is not None else profile.gaze_x) * strength),
        pupil_x_right=_clamp_unit(state.pupil_x + (profile.right_gaze_x if profile.right_gaze_x is not None else profile.gaze_x) * strength),
        pupil_y=_clamp_unit(state.pupil_y + profile.gaze_y * strength),
        pupil_y_right=_clamp_unit(state.pupil_y + profile.gaze_y * strength),
        iris_scale=1.0 + (profile.iris_scale - 1.0) * strength,
        pupil_scale=1.0 + (profile.pupil_scale - 1.0) * strength,
        background=_rgb_color(state.background),
        eye_color=eye_color,
        pupil_color=pupil_color,
        iris_color=iris_color,
    )


def _expression_profile(expression: FaceExpression) -> ExpressionProfile:
    return _EXPRESSION_PROFILES.get(expression, _EXPRESSION_PROFILES[FaceExpression.NEUTRAL])


def _interpolate(current: _AnimatedValues, target: _AnimatedValues, amount: float) -> _AnimatedValues:
    return _AnimatedValues(
        eye_open_left=_blend(current.eye_open_left, target.eye_open_left, amount),
        eye_open_right=_blend(current.eye_open_right, target.eye_open_right, amount),
        squint=_blend(current.squint, target.squint, amount),
        upper_lid_tilt=_blend(current.upper_lid_tilt, target.upper_lid_tilt, amount),
        upper_lid_tilt_right=_blend(current.upper_lid_tilt_right, target.upper_lid_tilt_right, amount),
        upper_lid_depth=_blend(current.upper_lid_depth, target.upper_lid_depth, amount),
        upper_lid_depth_right=_blend(current.upper_lid_depth_right, target.upper_lid_depth_right, amount),
        upper_lid_curve=_blend(current.upper_lid_curve, target.upper_lid_curve, amount),
        upper_lid_curve_right=_blend(current.upper_lid_curve_right, target.upper_lid_curve_right, amount),
        pupil_inward=_blend(current.pupil_inward, target.pupil_inward, amount),
        pupil_x=_blend(current.pupil_x, target.pupil_x, amount),
        pupil_x_right=_blend(current.pupil_x_right, target.pupil_x_right, amount),
        pupil_y=_blend(current.pupil_y, target.pupil_y, amount),
        pupil_y_right=_blend(current.pupil_y_right, target.pupil_y_right, amount),
        iris_scale=_blend(current.iris_scale, target.iris_scale, amount),
        pupil_scale=_blend(current.pupil_scale, target.pupil_scale, amount),
        background=_blend_color(current.background, target.background, amount),
        eye_color=_blend_color(current.eye_color, target.eye_color, amount),
        pupil_color=_blend_color(current.pupil_color, target.pupil_color, amount),
        iris_color=_blend_color(current.iris_color, target.iris_color, amount),
    )


def _blink_openness(phase: BlinkPhase, progress: float) -> float:
    if phase is BlinkPhase.CLOSING:
        return 1.0 - progress
    if phase is BlinkPhase.CLOSED:
        return 0.0
    if phase is BlinkPhase.OPENING:
        return progress
    return 1.0


def _blend(start: float, end: float, amount: float) -> float:
    return start + (end - start) * amount


def _clamp_unit(value: float) -> float:
    return max(-1.0, min(value, 1.0))


def _clamp_open(value: float) -> float:
    return max(0.0, min(value, 1.25))


def _accent_colors(state: FaceState) -> Tuple[Tuple[int, int, int], Tuple[int, int, int]]:
    neutral_eye = (234, 251, 255)
    neutral_pupil = (20, 32, 45)
    accent_eye, accent_pupil = {
        VisualAccent.NEUTRAL: (neutral_eye, neutral_pupil),
        VisualAccent.WARM: ((40, 224, 176), (6, 59, 61)),
        VisualAccent.COOL: ((53, 189, 242), (8, 43, 66)),
        VisualAccent.CURIOUS: ((53, 189, 242), (8, 43, 66)),
        VisualAccent.ALERT: ((255, 200, 87), (68, 44, 8)),
        VisualAccent.SLEEPY: ((167, 139, 250), (35, 25, 73)),
        VisualAccent.ERROR: ((255, 92, 108), (78, 15, 25)),
    }[state.accent]
    return (
        _blend_color(neutral_eye, accent_eye, state.reaction_strength),
        _blend_color(neutral_pupil, accent_pupil, state.reaction_strength),
    )


def _iris_target(base: Tuple[int, int, int], state: FaceState) -> Tuple[int, int, int]:
    accent = _IRIS_ACCENTS[state.accent]
    # Preserve the selected theme while allowing semantic reactions to gently
    # tint the iris; all intent still arrives through FaceState.
    return base if accent is None else _blend_color(base, accent, state.reaction_strength * 0.45)


def _rgb_color(value: str) -> Tuple[int, int, int]:
    return tuple(int(value[index : index + 2], 16) for index in (1, 3, 5))


def _hex_color(value: Tuple[int, int, int]) -> str:
    return "#" + "".join(f"{channel:02X}" for channel in value)


def _blend_color(
    start: Tuple[int, int, int], end: Tuple[int, int, int], amount: float
) -> Tuple[int, int, int]:
    return tuple(round(_blend(first, second, amount)) for first, second in zip(start, end))
