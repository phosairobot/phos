"""Pure, interpolated eye geometry for the PHOS face."""

from __future__ import annotations

from dataclasses import dataclass
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


@dataclass
class _AnimatedValues:
    eye_open_left: float
    eye_open_right: float
    squint: float
    pupil_x: float
    pupil_y: float
    background: Tuple[int, int, int]
    eye_color: Tuple[int, int, int]
    pupil_color: Tuple[int, int, int]
    iris_color: Tuple[int, int, int]


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
        iris_radius = max(1.0, min(radius_x * 0.35, radius_y * 0.62))
        pupil_radius = iris_radius * 0.52
        max_pupil_x = max(0.0, radius_x * 0.52 - iris_radius)
        max_pupil_y = max(0.0, radius_y * 0.48 - iris_radius)
        return EyeGeometry(
            center_x=center_x,
            center_y=center_y,
            radius_x=radius_x,
            radius_y=radius_y,
            pupil_x=center_x + values.pupil_x * max_pupil_x,
            pupil_y=center_y + values.pupil_y * max_pupil_y,
            iris_radius=iris_radius,
            pupil_radius=pupil_radius,
            squint=values.squint,
            closed=blink_amount <= 0.02,
        )


def _target_values(state: FaceState, iris_color: str = "cyan") -> _AnimatedValues:
    profile = _expression_profile(state.expression)
    strength = state.reaction_strength
    base_open = _blend(state.eye_open, profile[0], strength)
    asymmetry = profile[1] * strength if state.eye_asymmetry is None else state.eye_asymmetry
    left_open, right_open = ((base_open - asymmetry, base_open + asymmetry)
                             if state.eye_asymmetry is None else (base_open + asymmetry, base_open - asymmetry))
    eye_color, pupil_color = _accent_colors(state)
    iris_color = _iris_target(IrisColorResolver.rgb(iris_color), state)
    return _AnimatedValues(
        eye_open_left=_clamp_open(left_open),
        eye_open_right=_clamp_open(right_open),
        squint=_blend(state.squint, profile[2], strength),
        pupil_x=_clamp_unit(state.pupil_x + profile[3] * strength),
        pupil_y=_clamp_unit(state.pupil_y + profile[4] * strength),
        background=_rgb_color(state.background),
        eye_color=eye_color,
        pupil_color=pupil_color,
        iris_color=iris_color,
    )


def _expression_profile(expression: FaceExpression) -> Tuple[float, float, float, float, float]:
    if expression is FaceExpression.HAPPY:
        return (0.82, 0.0, 0.45, 0.0, 0.10)
    if expression is FaceExpression.CURIOUS:
        return (1.03, 0.12, 0.15, 0.14, -0.10)
    if expression is FaceExpression.SURPRISED:
        return (1.25, 0.02, 0.0, 0.0, -0.06)
    if expression is FaceExpression.SLEEPY:
        return (0.38, 0.0, 0.36, 0.0, 0.10)
    if expression is FaceExpression.WORRIED:
        return (0.82, 0.04, 0.18, -0.10, -0.08)
    return (1.0, 0.0, 0.05, 0.0, 0.0)


def _interpolate(current: _AnimatedValues, target: _AnimatedValues, amount: float) -> _AnimatedValues:
    return _AnimatedValues(
        eye_open_left=_blend(current.eye_open_left, target.eye_open_left, amount),
        eye_open_right=_blend(current.eye_open_right, target.eye_open_right, amount),
        squint=_blend(current.squint, target.squint, amount),
        pupil_x=_blend(current.pupil_x, target.pupil_x, amount),
        pupil_y=_blend(current.pupil_y, target.pupil_y, amount),
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
