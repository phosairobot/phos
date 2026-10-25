from pathlib import Path
from types import SimpleNamespace

from robot.ui.eye_safe import EyeSafeAnalysis, EyeSafeLayer, EyeSafeParams
from robot.ui.eyes import EyeRenderer
from robot.ui.backgrounds import BackgroundAsset
from robot.ui.state import FaceState
from robot.ui.display import TkEyeDisplay
from robot.ui.state_effects import StateEffectParams
from robot.ui.eye_safe import EyeSafePrimitive


def test_disabled_eye_safe_layer_has_no_primitives():
    frame = EyeRenderer().render(FaceState(), timestamp=0)
    layer = EyeSafeLayer()
    asset = BackgroundAsset("idle", Path("image.png"), True)
    assert layer.primitives(EyeSafeParams(False), frame, asset) == ()


def test_cached_analysis_is_reused_for_the_same_background_and_not_for_another(monkeypatch):
    frame = EyeRenderer().render(FaceState(), timestamp=0)
    layer, calls = EyeSafeLayer(), []
    first, second = BackgroundAsset("idle", Path("first.png"), True), BackgroundAsset("idle", Path("second.png"), True)

    def analyze(path, width, height):
        calls.append((path, width, height))
        return EyeSafeAnalysis(.8, .4)

    monkeypatch.setattr(layer, "_read_analysis", analyze)
    # The primitive interface receives cached analysis from the layer; this
    # verifies background identity remains part of the invalidation key.
    layer.primitives(EyeSafeParams(True), frame, first)
    layer.primitives(EyeSafeParams(True), frame, first)
    layer.primitives(EyeSafeParams(True), frame, second)
    assert [item[0] for item in calls] == [first.path, second.path]
    assert len(layer._analysis) == 2


def test_legacy_or_unavailable_background_falls_back_without_protection():
    frame = EyeRenderer().render(FaceState(), timestamp=0)
    layer = EyeSafeLayer()
    assert layer.primitives(EyeSafeParams(True), frame, BackgroundAsset("idle")) == ()


def test_masks_are_local_soft_ellipses_with_ordered_mode_strength(monkeypatch):
    frame, layer = EyeRenderer().render(FaceState(), timestamp=0), EyeSafeLayer()
    asset = BackgroundAsset("idle", Path("bright.png"), True)
    monkeypatch.setattr(layer, "_read_analysis", lambda *_: EyeSafeAnalysis(.95, .80))
    subtle = layer.primitives(EyeSafeParams(True, "subtle"), frame, asset)
    balanced = layer.primitives(EyeSafeParams(True, "balanced"), frame, asset)
    expressive = layer.primitives(EyeSafeParams(True, "expressive"), frame, asset)
    assert len(subtle) == len(frame.eyes) == 2
    assert subtle[0].opacity < balanced[0].opacity < expressive[0].opacity
    subtle_width = subtle[0].bounds[2] - subtle[0].bounds[0]
    assert subtle_width < balanced[0].bounds[2] - balanced[0].bounds[0] < expressive[0].bounds[2] - expressive[0].bounds[0]
    assert all((item.bounds[2] - item.bounds[0]) < frame.width * .5 for item in expressive)


def test_dark_clean_background_needs_no_mask(monkeypatch):
    frame, layer = EyeRenderer().render(FaceState(), timestamp=0), EyeSafeLayer()
    monkeypatch.setattr(layer, "_read_analysis", lambda *_: EyeSafeAnalysis(.08, .02))
    assert layer.primitives(EyeSafeParams(True), frame, BackgroundAsset("idle", Path("dark.png"), True)) == ()


def test_mask_texture_has_a_transparent_soft_edge():
    rgba = TkEyeDisplay._glow_rgba(21, 21, "#020814", .3)
    alpha = lambda x, y: rgba[(y * 21 + x) * 4 + 3]
    assert alpha(0, 0) == 0
    assert 0 < alpha(10, 10) <= round(.3 * 255)


def test_eye_safe_layer_is_composed_between_background_and_state_effect():
    display, calls = TkEyeDisplay(), []
    display._root = object()
    display._canvas = SimpleNamespace(delete=lambda *_: None, configure=lambda **_: None,
                                      create_image=lambda *_args, **_kwargs: calls.append("eye-safe"))
    display._tk = SimpleNamespace(ARC="arc")
    display._draw_background = lambda *_: calls.append("background")
    display._eye_safe_layer.primitives = lambda *_: (EyeSafePrimitive((1, 1, 2, 2), "#010203", .2),)
    display._eye_safe_photo = lambda *_: object()
    display._draw_state_effect = lambda *_: calls.append("state-effect")
    display._draw_eye = lambda *_: calls.append("eyes")
    display._draw_ambient_overlay = lambda *_: calls.append("overlays")
    frame = EyeRenderer().render(FaceState(), timestamp=0)
    display.draw(frame, background=BackgroundAsset("idle", Path("image.png"), True),
                 eye_safe=EyeSafeParams(True), state_effect=StateEffectParams("idle", intensity=.1))
    assert calls == ["background", "eye-safe", "state-effect", "eyes", "eyes", "overlays"]
