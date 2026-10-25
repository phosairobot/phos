"""Static, backend-generated appearance preview using runtime visual primitives."""
from __future__ import annotations

import base64
from types import SimpleNamespace

from robot.core.state import RobotState
from .backgrounds import BackgroundResolver
from .eye_safe import EyeSafeLayer, EyeSafeParams
from .eyes import EyeRenderer, upper_lid_points, valid_polygon_points
from .state import FaceExpression, FaceState, expression_reaction_accent
from .state_effect_layer import StateEffectLayer
from .state_effects import StateEffectResolver


def render_preview(config, options: dict) -> str:
    state = options.get("state", "idle")
    expression = options.get("expression", "neutral")
    if state not in {item.value for item in RobotState} or expression not in {item.value for item in FaceExpression}:
        raise ValueError("Unsupported preview state or expression.")
    enabled = options.get("eye_safe_enabled", config.eye_safe_layer_enabled)
    mode = options.get("eye_safe_mode", config.eye_safe_layer_mode)
    if type(enabled) is not bool or mode not in {"subtle", "balanced", "expressive"}:
        raise ValueError("Unsupported eye safe layer preview options.")
    effects_enabled = options.get("state_effects_enabled", config.state_effects_enabled)
    if type(effects_enabled) is not bool:
        raise ValueError("Unsupported state-effects preview option.")
    preview_expression = FaceExpression.SLEEPY if state == "sleeping" else FaceExpression(expression)
    # BehaviorEngine normally supplies reaction strength with an expression.
    # Preview is deliberately visual-only, so supply its representative stable
    # frame rather than mutating live behavior to obtain that value.
    frame = EyeRenderer(width=800, height=600, iris_color=config.iris_color).render(
        FaceState(expression=preview_expression,
                  accent=expression_reaction_accent(preview_expression),
                  reaction_strength=0 if preview_expression is FaceExpression.NEUTRAL else 1), timestamp=0)
    asset = BackgroundResolver(config).resolve(state)
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="800" height="600" viewBox="0 0 800 600"><rect width="800" height="600" fill="{frame.background}"/>']
    if asset.available and asset.path is not None:
        mime = "image/png" if asset.path.suffix.lower() == ".png" else "image/jpeg"
        parts.append(f'<image width="800" height="600" preserveAspectRatio="xMidYMid slice" href="data:{mime};base64,{base64.b64encode(asset.path.read_bytes()).decode()}"/>')
    parts.append('<defs><radialGradient id="safe"><stop offset="0" stop-color="#020814" stop-opacity=".28"/><stop offset="1" stop-color="#020814" stop-opacity="0"/></radialGradient></defs>')
    for item in EyeSafeLayer().primitives(EyeSafeParams(enabled, mode), frame, asset):
        x1, y1, x2, y2 = item.bounds
        parts.append(f'<ellipse cx="{(x1+x2)/2:.1f}" cy="{(y1+y2)/2:.1f}" rx="{(x2-x1)/2:.1f}" ry="{(y2-y1)/2:.1f}" fill="url(#safe)" opacity="{item.opacity:.3f}"/>')
    effect_config = SimpleNamespace(state_effects_enabled=effects_enabled,
                                    state_effects_preset=config.state_effects_preset,
                                    state_effects_palette=config.state_effects_palette,
                                    state_effects_reduce_motion=config.state_effects_reduce_motion)
    effect = StateEffectResolver(effect_config).resolve(state)
    if effects_enabled:
        for item in StateEffectLayer().primitives(effect, 800, 600, 0):
            x1, y1, x2, y2 = item.bounds
            if item.kind == "glow":
                parts.append(f'<ellipse class="state-effect" cx="{(x1+x2)/2:.1f}" cy="{(y1+y2)/2:.1f}" rx="{(x2-x1)/2:.1f}" ry="{(y2-y1)/2:.1f}" fill="{item.color}" opacity="{item.opacity * .62:.3f}"/>')
            elif item.kind == "oval":
                parts.append(f'<ellipse class="state-effect" cx="{(x1+x2)/2:.1f}" cy="{(y1+y2)/2:.1f}" rx="{(x2-x1)/2:.1f}" ry="{(y2-y1)/2:.1f}" fill="none" stroke="{item.color}" stroke-width="{item.width}"/>')
            else:
                parts.append(f'<path class="state-effect" d="M {x1:.1f} {(y1+y2)/2:.1f} A {(x2-x1)/2:.1f} {(y2-y1)/2:.1f} 0 0 1 {x2:.1f} {(y1+y2)/2:.1f}" fill="none" stroke="{item.color}" stroke-width="{item.width}" opacity="{item.opacity or 1:.3f}"/>')
    for eye in frame.eyes:
        if eye.closed:
            parts.append(f'<path class="eye" d="M {eye.center_x-eye.radius_x:.1f} {eye.center_y+8:.1f} Q {eye.center_x:.1f} {eye.center_y+35:.1f} {eye.center_x+eye.radius_x:.1f} {eye.center_y+8:.1f}" fill="none" stroke="{frame.eye_color}" stroke-width="12"/>')
            continue
        inset, iris_inset = 5, max(1.5, eye.iris_radius * .10)
        highlight, glint = max(2.0, eye.pupil_radius * .24), max(1.2, eye.pupil_radius * .24 * .42)
        parts.append(
            f'<ellipse class="eye-shadow" cx="{eye.center_x:.1f}" cy="{eye.center_y+7:.1f}" rx="{eye.radius_x:.1f}" ry="{eye.radius_y:.1f}" fill="#071522"/>'
            f'<ellipse class="eye-shell" cx="{eye.center_x:.1f}" cy="{eye.center_y:.1f}" rx="{eye.radius_x:.1f}" ry="{eye.radius_y:.1f}" fill="#8FA8B8" stroke="#526C80" stroke-width="3"/>'
            f'<ellipse class="eye" cx="{eye.center_x:.1f}" cy="{eye.center_y:.1f}" rx="{eye.radius_x-inset:.1f}" ry="{eye.radius_y-inset:.1f}" fill="{frame.eye_color}"/>'
            f'<circle cx="{eye.pupil_x:.1f}" cy="{eye.pupil_y:.1f}" r="{eye.iris_radius:.1f}" fill="#123246"/>'
            f'<circle cx="{eye.pupil_x:.1f}" cy="{eye.pupil_y:.1f}" r="{eye.iris_radius-iris_inset:.1f}" fill="{frame.iris_color}"/>'
            f'<circle cx="{eye.pupil_x:.1f}" cy="{eye.pupil_y:.1f}" r="{eye.pupil_radius:.1f}" fill="{frame.pupil_color}" stroke="#071522" stroke-width="2"/>'
            f'<circle cx="{eye.pupil_x-eye.iris_radius*.33:.1f}" cy="{eye.pupil_y-eye.iris_radius*.33:.1f}" r="{highlight:.1f}" fill="#FFFFFF"/>'
            f'<circle cx="{eye.pupil_x+eye.iris_radius*.30:.1f}" cy="{eye.pupil_y+eye.iris_radius*.28:.1f}" r="{glint:.1f}" fill="#D9FBFF"/>')
        if eye.upper_lid_depth:
            points = upper_lid_points(eye, inset=inset)
            if valid_polygon_points(points):
                path = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
                parts.append(f'<path class="upper-lid" d="M {path} Z" fill="#8FA8B8"/>')
    return "".join(parts) + "</svg>"
