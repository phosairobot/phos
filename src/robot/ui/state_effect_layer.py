"""Small peripheral canvas layer for already-resolved state-effect semantics."""
from __future__ import annotations

from dataclasses import dataclass
import math

from .state_effects import StateEffectParams


@dataclass(frozen=True)
class EffectPrimitive:
    kind: str
    bounds: tuple[float, float, float, float]
    color: str
    width: int = 1
    start: float = 25
    extent: float = 130
    opacity: float = 0.0


class StateEffectLayer:
    """Turn semantic parameters into a few cached, peripheral Canvas primitives.

    Geometry deliberately stays clear of the central eye region.  The layer has
    no knowledge of configuration or RobotState and performs no image I/O.
    """
    def __init__(self) -> None:
        self._geometry: dict[tuple[int, int], tuple[float, float, float]] = {}

    @property
    def cache_size(self) -> int:
        return len(self._geometry)

    def primitives(self, effect: StateEffectParams, width: int, height: int, timestamp: float) -> tuple[EffectPrimitive, ...]:
        if effect.intensity <= 0 or effect.mode == "none":
            return ()
        margin, cx, cy = self._geometry.setdefault((width, height), (min(width, height) * .075, width / 2, height / 2))
        strength = max(0.0, min(1.0, effect.intensity))
        color = effect.palette.accent if effect.mode == "circulate" else effect.palette.primary
        # Idle's .10 semantic speed is already a ten-second cycle. Faster
        # semantic states are slowed to similarly ambient multi-second travel.
        phase_scale = 1.35 if effect.mode == "ambient" else (.35 if effect.mode in {"inward", "outward"} else .22)
        phase = timestamp * effect.speed * math.tau * phase_scale if effect.motion_scale else 0.0
        drift = math.sin(phase) * margin * effect.motion_scale
        ring = min(width, height) * (.34 + effect.pulse_strength * .08 * math.sin(phase))
        edge = margin + drift
        if effect.mode == "ambient":
            # Transparent radial fields, mostly off screen: visual light, not
            # an outlined object.  The movement cycle is 8–15 seconds at the
            # resolver's idle speed.
            drift = min(width, height) * .060
            first, second, third = (math.sin(phase) * drift,
                                    math.sin(phase + math.tau/3) * drift,
                                    math.sin(phase + math.tau*2/3) * drift)
            opacity = min(.82, (.15 + strength * 1.05) * effect.palette.glow_gain)
            return (EffectPrimitive("glow", (-width*.22 + first, -height*.20, width*.42 + first, height*.60), effect.palette.primary, opacity=opacity),
                    EffectPrimitive("glow", (width*.56 + second, height*.28, width*1.20 + second, height*1.12), effect.palette.secondary, opacity=opacity*.88),
                    EffectPrimitive("glow", (width*.16 + third, height*.72, width*.84 + third, height*1.38), effect.palette.accent, opacity=opacity*.74))
        if effect.mode == "inward":
            return self._listening_fields(width, height, strength, phase, effect.palette.primary, effect.palette.glow_gain)
        if effect.mode == "circulate":
            return (EffectPrimitive("arc", (margin, margin, cx + ring*.5, cy + ring*.5), color, 3),
                    EffectPrimitive("arc", (cx-ring*.5, cy-ring*.5, width-margin, height-margin), color, 3))
        if effect.mode == "outward":
            return self._speaking_fields(width, height, strength, phase, effect.palette.secondary, effect.palette.glow_gain)
        if effect.mode == "breathe":
            radius = margin * (1.2 + effect.pulse_strength * math.sin(phase))
            return (EffectPrimitive("oval", (margin, height-margin-radius, margin+radius, height-margin), color, 2),)
        # Error intentionally has no timestamp-based flashing; it is restrained
        # after any state transition handled by the render loop.
        return (EffectPrimitive("arc", (margin, margin, margin*4, margin*3), color, 3),
                EffectPrimitive("arc", (width-margin*4, margin, width-margin, margin*3), color, 3))

    def render(self, canvas, effect: StateEffectParams, width: int, height: int, timestamp: float, *, arc_style,
               glow_factory=None) -> None:
        for primitive in self.primitives(effect, width, height, timestamp):
            if primitive.kind == "glow" and glow_factory is not None:
                photo = glow_factory(primitive)
                if photo is not None:
                    x1, y1, x2, y2 = primitive.bounds
                    canvas.create_image((x1+x2)/2, (y1+y2)/2, image=photo, anchor="center")
            elif primitive.kind == "oval":
                canvas.create_oval(*primitive.bounds, outline=primitive.color, width=primitive.width)
            else:
                canvas.create_arc(*primitive.bounds, start=primitive.start, extent=primitive.extent, style=arc_style,
                                  outline=primitive.color, width=primitive.width)

    def _listening_fields(self, width: int, height: int, strength: float, phase: float, color: str, gain: float) -> tuple[EffectPrimitive, ...]:
        """Focused cool fields travel from outside toward PHOS."""
        fields = []
        for index in range(2):
            # Cosine travel gives a soft turnaround instead of a visible reset.
            travel = (1 - math.cos(phase + index * math.pi)) / 2
            center_x = width * (-.03 + .21 * travel)
            field_width, field_height = width * .30, height * .58
            left = (center_x - field_width/2, height*.21, center_x + field_width/2, height*.79)
            right_center = width - center_x
            right = (right_center - field_width/2, height*.21, right_center + field_width/2, height*.79)
            opacity = min(.82, (.16 + strength * .68) * gain * (.78 if index else 1.0))
            fields.extend((EffectPrimitive("glow", left, color, opacity=opacity),
                           EffectPrimitive("glow", right, color, opacity=opacity)))
        return tuple(fields)

    def _speaking_fields(self, width: int, height: int, strength: float, phase: float, color: str, gain: float) -> tuple[EffectPrimitive, ...]:
        """Broad aqua fields project from PHOS toward the display edges."""
        fields = []
        for index in range(2):
            travel = (1 - math.cos(phase + index * math.pi)) / 2
            center_x = width * (.29 - .22 * travel)
            field_width, field_height = width * .54, height * (.86 + .07 * index)
            left = (center_x - field_width/2, height*(.07 - .02*index), center_x + field_width/2, height*(.93 + .02*index))
            right_center = width - center_x
            right = (right_center - field_width/2, height*(.07 - .02*index), right_center + field_width/2, height*(.93 + .02*index))
            opacity = min(.84, (.20 + strength * .72) * gain * (.78 if index else 1.0))
            fields.extend((EffectPrimitive("glow", left, color, opacity=opacity),
                           EffectPrimitive("glow", right, color, opacity=opacity)))
        return tuple(fields)

    @staticmethod
    def _color(mode: str, intensity: float) -> str:
        base = {"inward": (86, 188, 226), "circulate": (125, 147, 235), "outward": (106, 211, 194),
                "breathe": (103, 145, 188), "restrained_error": (201, 103, 92)}.get(mode, (91, 163, 205))
        # Blend toward the existing dark display rather than relying on Canvas alpha.
        return "#%02x%02x%02x" % tuple(round(12 + component * intensity * .75) for component in base)
