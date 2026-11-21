"""Pure, interpolated eye geometry for the PHOS face."""

from __future__ import annotations

from dataclasses import dataclass, replace
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
    # Optional absolute, closed-outline vertices for future non-elliptical
    # silhouettes.  ``None`` preserves the established ellipse contract.
    contour: Optional[Tuple[Tuple[float, float], ...]] = None

    def __post_init__(self) -> None:
        if self.contour is None:
            return
        if (not isinstance(self.contour, tuple) or len(self.contour) < 3
                or any(not isinstance(point, tuple) or len(point) != 2
                       or not all(isinstance(value, (int, float)) and math.isfinite(value) for value in point)
                       for point in self.contour)):
            raise ValueError("Eye contour must be an immutable sequence of at least three finite points.")


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


def custom_eye_interior_contour(eye: EyeGeometry, *, inset: float = 5.0) -> tuple[tuple[float, float], ...]:
    """Return the same conservative inset contour used for the colored body."""
    if eye.contour is None or eye.radius_x <= 0.0 or eye.radius_y <= 0.0:
        return ()
    safe_inset = min(inset, max(0.0, min(eye.radius_x, eye.radius_y) - 1.0))
    scale_x = (eye.radius_x - safe_inset) / eye.radius_x
    scale_y = (eye.radius_y - safe_inset) / eye.radius_y
    return tuple(
        (eye.center_x + (x - eye.center_x) * scale_x,
         eye.center_y + (y - eye.center_y) * scale_y)
        for x, y in eye.contour
    )


def contain_custom_eye_graphics(eye: EyeGeometry) -> EyeGeometry:
    """Conservatively fit custom-shape iris graphics into their colored interior.

    Generated contours are convex.  We shrink their inset interior by the
    graphics' enclosing radius (iris plus a two-pixel reflection allowance),
    then clamp the requested pupil point along its center-to-target segment.
    Degenerate or unexpectedly non-convex input safely suppresses the iris
    stack instead of letting a Canvas primitive leak through the silhouette.
    """
    if eye.contour is None or eye.closed:
        return eye
    contour = custom_eye_interior_contour(eye)
    if len(contour) < 3 or not valid_polygon_points(contour):
        return replace(eye, iris_radius=0.0, pupil_radius=0.0, pupil_x=eye.center_x, pupil_y=eye.center_y)

    area = sum(x1 * y2 - y1 * x2 for (x1, y1), (x2, y2) in zip(contour, contour[1:] + contour[:1]))
    if not math.isfinite(area) or abs(area) <= 1e-9:
        return replace(eye, iris_radius=0.0, pupil_radius=0.0, pupil_x=eye.center_x, pupil_y=eye.center_y)
    orientation = 1.0 if area > 0.0 else -1.0
    turns = tuple(
        orientation * ((x2 - x1) * (y3 - y2) - (y2 - y1) * (x3 - x2))
        for (x1, y1), (x2, y2), (x3, y3) in zip(contour, contour[1:] + contour[:1], contour[2:] + contour[:2])
    )
    if any(turn < -1e-9 for turn in turns):
        return replace(eye, iris_radius=0.0, pupil_radius=0.0, pupil_x=eye.center_x, pupil_y=eye.center_y)

    def clearances(point: tuple[float, float]) -> tuple[float, ...]:
        margins = []
        for (x1, y1), (x2, y2) in zip(contour, contour[1:] + contour[:1]):
            length = math.hypot(x2 - x1, y2 - y1)
            if length <= 1e-9:
                return ()
            margins.append(orientation * ((x2 - x1) * (point[1] - y1) - (y2 - y1) * (point[0] - x1)) / length)
        return tuple(margins)

    base = (eye.center_x, eye.center_y)
    base_clearances = clearances(base)
    if not base_clearances or min(base_clearances) <= 2.0:
        return replace(eye, iris_radius=0.0, pupil_radius=0.0, pupil_x=eye.center_x, pupil_y=eye.center_y)

    # A two-pixel allowance contains the minimum-sized white highlight; for
    # normal irises it is also outside the already-larger iris backing.
    iris_radius = min(eye.iris_radius, min(base_clearances) - 2.0)
    if iris_radius < 2.0:
        return replace(eye, iris_radius=0.0, pupil_radius=0.0, pupil_x=eye.center_x, pupil_y=eye.center_y)
    required_clearance = iris_radius + 2.0
    target = (eye.pupil_x, eye.pupil_y)
    target_clearances = clearances(target)
    if not target_clearances:
        return replace(eye, iris_radius=0.0, pupil_radius=0.0, pupil_x=eye.center_x, pupil_y=eye.center_y)
    travel = 1.0
    for base_margin, target_margin in zip(base_clearances, target_clearances):
        delta = target_margin - base_margin
        if delta < 0.0:
            travel = min(travel, max(0.0, (base_margin - required_clearance) / -delta))
    pupil_ratio = eye.pupil_radius / eye.iris_radius if eye.iris_radius > 0.0 else 0.0
    return replace(
        eye,
        pupil_x=base[0] + (target[0] - base[0]) * travel,
        pupil_y=base[1] + (target[1] - base[1]) * travel,
        iris_radius=iris_radius,
        pupil_radius=iris_radius * pupil_ratio,
    )


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
                 iris_color: str = "cyan", shape: str = "classic_oval") -> None:
        if width <= 0 or height <= 0 or transition_seconds <= 0:
            raise ValueError("Eye renderer dimensions and transition duration must be positive.")
        self._width = width
        self._height = height
        self._transition_seconds = transition_seconds
        if shape not in {"classic_oval", "round", "expressive", "angular", "compact"}: raise ValueError("Unsupported eye shape")
        self._shape = shape
        try:
            self._iris_color = IrisColorResolver.normalize(iris_color)
        except ValueError as error:
            raise ValueError(f"Unsupported iris color: {iris_color}") from error
        self._values: Optional[_AnimatedValues] = None
        self._last_timestamp: Optional[float] = None
        self._visual_state = None
        self._last_transition_progress = 1.0

    @property
    def iris_color(self) -> str:
        """Return the selected canonical base color, before semantic tinting."""
        return self._iris_color

    @property
    def shape(self) -> str:
        return self._shape

    @property
    def last_transition_progress(self) -> float:
        """Most recent geometry interpolation fraction for transition diagnostics."""
        return self._last_transition_progress

    def update_appearance(self, *, iris_color: str | None = None, shape: str | None = None) -> None:
        """Update render-loop-owned appearance without resetting animation state."""
        if iris_color is not None:
            try:
                iris_color = IrisColorResolver.normalize(iris_color)
            except ValueError as error:
                raise ValueError(f"Unsupported iris color: {iris_color}") from error
        if shape is not None and shape not in {"classic_oval", "round", "expressive", "angular", "compact"}:
            raise ValueError("Unsupported eye shape")
        if iris_color is not None and iris_color != self._iris_color:
            self._iris_color = iris_color
        if shape is not None and shape != self._shape:
            self._shape = shape

    def render(self, face_state: FaceState, *, timestamp: float, visual_state=None) -> EyeFrame:
        state = face_state.normalized()
        target = _target_values(state, self._iris_color)
        state_key = getattr(visual_state, "value", visual_state)
        semantic_transition = (state_key is not None and self._visual_state is not None and
                               state_key != self._visual_state)
        if self._values is None:
            self._values = target
            self._last_transition_progress = 1.0
        else:
            previous_timestamp = timestamp if self._last_timestamp is None else self._last_timestamp
            elapsed = max(0.0, timestamp - previous_timestamp)
            self._last_transition_progress = min(elapsed / self._transition_seconds, 1.0)
            self._values = _interpolate(self._values, target, self._last_transition_progress)
            if semantic_transition:
                # State-selected backgrounds and effects change immediately.
                # Keep eye geometry interpolated, but snap colors so an Idle
                # palette cannot appear beneath a Thinking surface (or reverse).
                self._values = replace(self._values, background=target.background, eye_color=target.eye_color,
                                       pupil_color=target.pupil_color, iris_color=target.iris_color)
        self._last_timestamp = timestamp
        if state_key is not None:
            self._visual_state = state_key
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
        if self._shape == "round": radius_y = radius_x
        elif self._shape == "compact": radius_y *= .62
        iris_radius = max(1.0, min(radius_x * 0.35, radius_y * 0.62) * values.iris_scale)
        pupil_radius = iris_radius * 0.52 * values.pupil_scale
        max_pupil_x = max(0.0, radius_x * 0.52 - iris_radius)
        max_pupil_y = max(0.0, radius_y * 0.48 - iris_radius)
        eye = EyeGeometry(
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
            contour=None if self._shape == "classic_oval" or blink_amount <= .02 else _eye_contour(self._shape, center_x, center_y, radius_x, radius_y, left),
        )
        return contain_custom_eye_graphics(eye)


def _eye_contour(shape, cx, cy, rx, ry, left):
    # Clockwise local outline; expressive/angular lift the outer corner.
    if shape in {"round", "compact"}:
        return tuple((cx + rx * math.cos(i * math.tau / 16), cy + ry * math.sin(i * math.tau / 16)) for i in range(16))
    outer = -1 if left else 1
    if shape == "expressive":
        points = ((-1, .05), (-.65, -.72), (.2, -.92), (1, -.30), (.88, .48), (.2, .82), (-.62, .58))
    else:  # winged angular: tapered inner point, elevated outer wing, curved lower return
        points = ((-1, .10), (-.45, -.48), (.45, -.90), (1, -.28), (.78, .48), (.05, .78), (-.62, .52))
    return tuple((cx + x * rx, cy + (y + outer * x * -.12) * ry) for x, y in points)


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
