from pathlib import Path

import pytest

from robot.config import RuntimeConfig, load_document
from robot.ui.appearance_preview import render_preview
from robot.ui.eyes import EyeRenderer
from robot.ui.state import FaceExpression, FaceState, expression_reaction_accent


def _config(tmp_path):
    return RuntimeConfig.from_dict(load_document(), base_dir=tmp_path, check_paths=False)


@pytest.mark.parametrize("state", ["idle", "listening", "sleeping"])
@pytest.mark.parametrize("expression", ["neutral", "happy", "surprised"])
def test_preview_uses_visual_inputs_without_mutating_runtime(tmp_path, state, expression):
    config = _config(tmp_path)
    svg = render_preview(config, {"state": state, "expression": expression,
                                  "eye_safe_enabled": True, "eye_safe_mode": "balanced",
                                  "state_effects_enabled": True})
    assert svg.startswith("<svg") and "ellipse" in svg
    assert config.to_dict()["display"]["eye_safe_layer"]["mode"] == "balanced"


def test_preview_rejects_invalid_asset_selection_and_falls_back_when_unassigned(tmp_path):
    config = _config(tmp_path)
    assert "<svg" in render_preview(config, {"state": "idle", "expression": "neutral",
                                               "eye_safe_enabled": False, "eye_safe_mode": "subtle",
                                               "state_effects_enabled": False})
    with pytest.raises(ValueError):
        render_preview(config, {"state": "../../etc/passwd", "expression": "neutral"})


def test_preview_uses_live_eye_body_color_and_expression_geometry(tmp_path):
    config = _config(tmp_path)
    common = {"state": "idle", "eye_safe_enabled": False, "eye_safe_mode": "balanced", "state_effects_enabled": False}
    neutral = render_preview(config, {**common, "expression": "neutral"})
    happy = render_preview(config, {**common, "expression": "happy"})
    curious = render_preview(config, {**common, "expression": "curious"})
    surprised = render_preview(config, {**common, "expression": "surprised"})
    assert 'class="eye"' in neutral and f'fill="{EyeRenderer().render(FaceState(), timestamp=0).eye_color}"' in neutral
    assert len({neutral, happy, curious, surprised}) == 4


def test_preview_uses_the_behavior_expression_accent_for_eye_color(tmp_path):
    config = _config(tmp_path)
    common = {"state": "idle", "eye_safe_enabled": False, "eye_safe_mode": "balanced", "state_effects_enabled": False}

    previews = {
        expression: render_preview(config, {**common, "expression": expression.value})
        for expression in (FaceExpression.NEUTRAL, FaceExpression.HAPPY,
                           FaceExpression.CURIOUS, FaceExpression.SURPRISED)
    }
    live_colors = {
        expression: EyeRenderer(iris_color=config.iris_color).render(
            FaceState(expression=expression,
                      accent=expression_reaction_accent(expression),
                      reaction_strength=0 if expression is FaceExpression.NEUTRAL else 1),
            timestamp=0,
        ).eye_color
        for expression in previews
    }

    for expression, svg in previews.items():
        assert f'fill="{live_colors[expression]}"' in svg
    assert live_colors[FaceExpression.SURPRISED] != live_colors[FaceExpression.NEUTRAL]
    # Calls are stateless, so a new expression cannot serve stale SVG output.
    assert previews[FaceExpression.SURPRISED] != previews[FaceExpression.NEUTRAL]
    assert render_preview(config, {**common, "expression": "surprised"}) == previews[FaceExpression.SURPRISED]


def test_preview_uses_the_same_custom_iris_color_as_the_renderer(tmp_path):
    document = load_document()
    document["display"]["iris_color"] = "#7a5cff"
    config = RuntimeConfig.from_dict(document, base_dir=tmp_path, check_paths=False)
    frame = EyeRenderer(iris_color=config.iris_color).render(FaceState(), timestamp=0)

    assert config.iris_color == "#7A5CFF"
    assert f'fill="{frame.iris_color}"' in render_preview(config, {"state": "idle", "expression": "neutral"})


def test_preview_effects_are_visible_and_ordered_before_eyes(tmp_path):
    config = _config(tmp_path)
    enabled = render_preview(config, {"state": "listening", "expression": "neutral",
                                      "eye_safe_enabled": True, "eye_safe_mode": "balanced",
                                      "state_effects_enabled": True})
    disabled = render_preview(config, {"state": "listening", "expression": "neutral",
                                       "eye_safe_enabled": True, "eye_safe_mode": "balanced",
                                       "state_effects_enabled": False})
    assert 'class="state-effect"' in enabled and 'class="state-effect"' not in disabled
    assert enabled.index('class="state-effect"') < enabled.index('class="eye"')
