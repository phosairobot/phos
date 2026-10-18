"""Provider-neutral state consumed by the PHOS eye renderer."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from typing import Optional


class FaceExpression(str, Enum):
    NEUTRAL = "neutral"
    HAPPY = "happy"
    CURIOUS = "curious"
    SURPRISED = "surprised"
    SLEEPY = "sleepy"
    WORRIED = "worried"


class VisualAccent(str, Enum):
    """Provider-neutral color intent for PHOS's visual reaction."""

    NEUTRAL = "neutral"
    WARM = "warm"
    COOL = "cool"
    CURIOUS = "curious"
    ALERT = "alert"
    SLEEPY = "sleepy"
    ERROR = "error"


def expression_reaction_accent(expression: FaceExpression) -> VisualAccent:
    """Return the visual accent used for an explicit expression reaction.

    This is semantic visual intent, shared by BehaviorEngine's expression
    reactions and non-mutating visual representations such as the appearance
    preview. Robot states may still select a different accent for the same
    expression when they have a distinct meaning.
    """
    return {
        FaceExpression.HAPPY: VisualAccent.CURIOUS,
        FaceExpression.CURIOUS: VisualAccent.CURIOUS,
        FaceExpression.SURPRISED: VisualAccent.ALERT,
    }.get(expression, VisualAccent.NEUTRAL)


class EnvironmentalLEDIntent(str, Enum):
    """Persistent environmental LED meaning, independent of eye appearance."""

    COLD = "cold"
    WARM = "warm"
    AIR_QUALITY_WARNING = "air_quality_warning"
    AIR_QUALITY_BAD = "air_quality_bad"


class TransientVisualEffect(str, Enum):
    PRESENCE_ENTERED = "presence_entered"
    PRESENCE_LEFT = "presence_left"


@dataclass(frozen=True)
class AmbientOverlayState:
    temperature: str = "none"
    air_quality: str = "none"


class BlinkPhase(str, Enum):
    OPEN = "open"
    CLOSING = "closing"
    CLOSED = "closed"
    OPENING = "opening"


_HEX_COLOR = re.compile(r"#[0-9A-Fa-f]{6}")


@dataclass(frozen=True)
class FaceState:
    """The complete visual intent for one renderer frame."""

    background: str = "#10243A"
    eye_open: float = 1.0
    # Signed extra openness for the left eye; right receives the opposite value.
    # None keeps the expression profile's normal asymmetry, while 0 requests symmetry.
    eye_asymmetry: Optional[float] = None
    squint: float = 0.0
    pupil_x: float = 0.0
    pupil_y: float = 0.0
    reaction_strength: float = 0.0
    expression: FaceExpression = FaceExpression.NEUTRAL
    accent: VisualAccent = VisualAccent.NEUTRAL
    environmental_led_intent: Optional[EnvironmentalLEDIntent] = None
    ambient_overlay: AmbientOverlayState = AmbientOverlayState()
    blink_phase: BlinkPhase = BlinkPhase.OPEN
    blink_progress: float = 0.0
    # Semantic motion intent is produced by BehaviorEngine; renderers may ignore it.
    motion_state: Optional[str] = None
    motion_event_at: Optional[float] = None
    motion_started_at: Optional[float] = None
    transient_effect: Optional[TransientVisualEffect] = None
    transient_effect_started_at: Optional[float] = None
    transient_effect_duration_seconds: Optional[float] = None
    transient_effect_direction: Optional[str] = None

    def normalized(self) -> "FaceState":
        expression = self.expression if isinstance(self.expression, FaceExpression) else FaceExpression.NEUTRAL
        accent = self.accent if isinstance(self.accent, VisualAccent) else VisualAccent.NEUTRAL
        blink_phase = self.blink_phase if isinstance(self.blink_phase, BlinkPhase) else BlinkPhase.OPEN
        background = self.background if isinstance(self.background, str) else "#10243A"
        return FaceState(
            background=background if _HEX_COLOR.fullmatch(background) else "#10243A",
            eye_open=_clamp(self.eye_open, 0.0, 1.25),
            eye_asymmetry=None if self.eye_asymmetry is None else _clamp(self.eye_asymmetry, -0.5, 0.5),
            squint=_clamp(self.squint, 0.0, 1.0),
            pupil_x=_clamp(self.pupil_x, -1.0, 1.0),
            pupil_y=_clamp(self.pupil_y, -1.0, 1.0),
            reaction_strength=_clamp(self.reaction_strength, 0.0, 1.0),
            expression=expression,
            accent=accent,
            environmental_led_intent=(self.environmental_led_intent
                                      if isinstance(self.environmental_led_intent, EnvironmentalLEDIntent) else None),
            ambient_overlay=self.ambient_overlay if isinstance(self.ambient_overlay, AmbientOverlayState) else AmbientOverlayState(),
            blink_phase=blink_phase,
            blink_progress=_clamp(self.blink_progress, 0.0, 1.0),
            motion_state=self.motion_state if isinstance(self.motion_state, str) else None,
            motion_event_at=self.motion_event_at if isinstance(self.motion_event_at, (int, float)) else None,
            motion_started_at=self.motion_started_at if isinstance(self.motion_started_at, (int, float)) else None,
            transient_effect=self.transient_effect if isinstance(self.transient_effect, TransientVisualEffect) else None,
            transient_effect_started_at=self.transient_effect_started_at if isinstance(self.transient_effect_started_at, (int, float)) else None,
            transient_effect_duration_seconds=self.transient_effect_duration_seconds if isinstance(self.transient_effect_duration_seconds, (int, float)) and self.transient_effect_duration_seconds > 0 else None,
            transient_effect_direction=self.transient_effect_direction if self.transient_effect_direction in {"clockwise", "counter_clockwise"} else None,
        )


def _clamp(value: float, lower: float, upper: float) -> float:
    try:
        return max(lower, min(float(value), upper))
    except (TypeError, ValueError):
        return lower
