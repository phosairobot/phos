"""Semantic, lightweight state effects independent from backgrounds and eyes."""
from __future__ import annotations

from dataclasses import dataclass

from robot.core.state import RobotState


@dataclass(frozen=True)
class StateEffectPalette:
    primary: str
    secondary: str
    accent: str
    glow_gain: float = 1.0


_PALETTES = {
    "phos": StateEffectPalette("#62D7FF", "#8E9BFF", "#46CBE8", 1.05),
    "cyan": StateEffectPalette("#4DF6FF", "#7BEFFF", "#1DDDEB", 1.08),
    "blue": StateEffectPalette("#1E5BFF", "#2C3FFF", "#1737C8", 1.12),
    "violet": StateEffectPalette("#B18CFF", "#9678F5", "#8A72D9", 1.15),
    "aqua": StateEffectPalette("#22F0A8", "#3BE6BF", "#12C887", 1.16),
    "amber": StateEffectPalette("#FFE05B", "#FFAC3D", "#F58A24", 1.32),
    "white": StateEffectPalette("#F4FAFF", "#DDEBFF", "#C8D8EE", 1.04),
    "green": StateEffectPalette("#62F56E", "#35D95A", "#22B847", 1.24),
}


@dataclass(frozen=True)
class StateEffectParams:
    state: str
    intensity: float = 0.0
    speed: float = 0.0
    glow: float = 0.0
    pulse_strength: float = 0.0
    motion_scale: float = 0.0
    mode: str = "none"
    palette: StateEffectPalette = StateEffectPalette("#62D7FF", "#8E9BFF", "#46CBE8")


_STATE = {
    "idle": (0.10, 0.10, 0.08, 0.03, 0.05, "ambient"),
    "listening": (0.32, 0.55, 0.22, 0.36, 0.24, "inward"),
    "thinking": (0.46, 0.62, 0.30, 0.22, 0.38, "circulate"),
    "speaking": (0.48, 0.58, 0.32, 0.42, 0.34, "outward"),
    "sleeping": (0.08, 0.07, 0.06, 0.13, 0.02, "breathe"),
    "error": (0.58, 0.24, 0.38, 0.20, 0.12, "restrained_error"),
}
_PRESETS = {"subtle": .55, "balanced": 1.0, "expressive": 1.45}


class StateEffectResolver:
    """Map semantic robot state to cheap renderer parameters only."""
    def __init__(self, config) -> None:
        self.configure(config)

    def configure(self, config) -> None:
        self.enabled = bool(config.state_effects_enabled)
        self.preset = config.state_effects_preset
        self.palette = _PALETTES[getattr(config, "state_effects_palette", "phos")]
        self.reduce_motion = bool(config.state_effects_reduce_motion)

    def resolve(self, state: RobotState | str) -> StateEffectParams:
        name = state.value if isinstance(state, RobotState) else str(state)
        if not self.enabled:
            return StateEffectParams(name, palette=self.palette)
        intensity, speed, glow, pulse, motion, mode = _STATE.get(name, _STATE["idle"])
        multiplier = _PRESETS[self.preset]
        if self.reduce_motion:
            speed, motion, pulse = min(speed * .08, .02), 0.0, min(pulse * .2, .03)
        return StateEffectParams(name, intensity * multiplier, speed * multiplier, glow * multiplier,
                                 pulse * multiplier, motion * multiplier, mode, self.palette)
