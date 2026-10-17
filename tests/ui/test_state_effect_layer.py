from types import SimpleNamespace

import pytest

from robot.core import RobotState
from robot.ui.eyes import EyeRenderer
from robot.ui.runtime import EyeRenderLoop
from robot.ui.state import FaceState
from robot.ui.state_effect_layer import StateEffectLayer
from robot.ui.state_effects import StateEffectParams, StateEffectResolver
from robot.ui.display import TkEyeDisplay


def _resolver(**overrides):
    values = {"state_effects_enabled": True, "state_effects_preset": "balanced",
              "state_effects_smooth_transitions": False, "state_effects_reduce_motion": False}
    values.update(overrides)
    return StateEffectResolver(SimpleNamespace(**values))


class Display:
    def __init__(self): self.calls = []
    def draw(self, *args, **kwargs): self.calls.append((args, kwargs))


def _loop(display, resolver, state):
    return EyeRenderLoop(EyeRenderer(), display, FaceState, state_effect_resolver=resolver,
                         robot_state_supplier=lambda: state[0])


def test_effect_layer_is_cached_per_display_size_and_preserves_state_languages():
    layer = StateEffectLayer()
    resolver = _resolver()
    primitives = {state: layer.primitives(resolver.resolve(state), 800, 600, 1.0) for state in RobotState}
    assert all(primitives.values())
    assert primitives[RobotState.LISTENING] != primitives[RobotState.THINKING]
    assert primitives[RobotState.SPEAKING] != primitives[RobotState.SLEEPING]
    assert layer.cache_size == 1
    layer.primitives(resolver.resolve(RobotState.IDLE), 800, 600, 2.0)
    assert layer.cache_size == 1


def test_error_effect_is_restrained_and_not_a_continuous_flash():
    layer, error = StateEffectLayer(), _resolver().resolve(RobotState.ERROR)
    assert layer.primitives(error, 800, 600, 1.0) == layer.primitives(error, 800, 600, 100.0)


def test_idle_uses_multiple_oversized_peripheral_glows_with_slow_motion():
    layer, idle = StateEffectLayer(), _resolver().resolve(RobotState.IDLE)
    at_start, later = layer.primitives(idle, 800, 600, 0), layer.primitives(idle, 800, 600, 2.5)
    assert len(at_start) == 3
    assert all(item.kind == "glow" and (item.bounds[2] - item.bounds[0]) > 500 for item in at_start)
    assert all(item.bounds[0] < 0 or item.bounds[2] > 800 or item.bounds[1] < 0 or item.bounds[3] > 600
               for item in at_start)
    # Idle is alive over seconds, not an isolated or frame-rate-speed symbol.
    assert at_start != later
    centers = [((item.bounds[0] + item.bounds[2]) / 2, (item.bounds[1] + item.bounds[3]) / 2)
               for item in at_start]
    assert len({round(x / 800, 1) for x, _ in centers}) > 1


def test_listening_is_narrow_mirrored_and_moves_inward_from_the_sides():
    layer = StateEffectLayer()
    primitives = layer.primitives(_resolver().resolve(RobotState.LISTENING), 800, 600, 1.0)
    assert len(primitives) == 4
    for left, right in zip(primitives[::2], primitives[1::2]):
        assert left.kind == right.kind == "glow"
        # The strongest part remains peripheral; its transparent edge fades
        # before the central eye region.
        assert left.bounds[2] <= 800 * .30 and right.bounds[0] >= 800 * .70
        assert left.bounds[0] == pytest.approx(800 - right.bounds[2])
        assert left.bounds[2] == pytest.approx(800 - right.bounds[0])
    earlier = layer.primitives(_resolver().resolve(RobotState.LISTENING), 800, 600, 0)[0]
    assert primitives[0].bounds[2] > earlier.bounds[2]


def test_speaking_is_broad_center_origin_and_expands_outward():
    layer = StateEffectLayer()
    first = layer.primitives(_resolver().resolve(RobotState.SPEAKING), 800, 600, 0)[0]
    later = layer.primitives(_resolver().resolve(RobotState.SPEAKING), 800, 600, 1)[0]
    listening = layer.primitives(_resolver().resolve(RobotState.LISTENING), 800, 600, 1)[0]
    assert first.kind == later.kind == "glow"
    assert first.bounds[2] - first.bounds[0] > listening.bounds[2] - listening.bounds[0]
    assert later.bounds[2] < first.bounds[2]  # left-origin pulse has travelled outward
    assert first.opacity > listening.opacity


def test_reduce_motion_makes_idle_listening_and_speaking_static():
    layer = StateEffectLayer()
    resolver = _resolver(state_effects_reduce_motion=True)
    for state in (RobotState.IDLE, RobotState.LISTENING, RobotState.SPEAKING):
        effect = resolver.resolve(state)
        assert layer.primitives(effect, 800, 600, 1) == layer.primitives(effect, 800, 600, 30)


def test_presets_increase_visible_band_intensity_without_changing_geometry_policy():
    layer = StateEffectLayer()
    colors = []
    for preset in ("subtle", "balanced", "expressive"):
        field = layer.primitives(_resolver(state_effects_preset=preset).resolve(RobotState.SPEAKING), 800, 600, 1)[0]
        colors.append(field.opacity)
    assert colors[0] < colors[1] < colors[2]
    assert colors[1] > .50  # Balanced is intentionally Pi-visible on dark PHOS backgrounds.


def test_transparent_glow_texture_is_cached_across_frames():
    display = TkEyeDisplay()
    created = []
    display._tk = SimpleNamespace(PhotoImage=lambda **kwargs: created.append(kwargs) or object())
    primitive = StateEffectLayer().primitives(_resolver().resolve(RobotState.IDLE), 800, 600, 1)[0]
    first = display._state_effect_photo(primitive)
    second = display._state_effect_photo(primitive)
    assert first is second
    # On a Tk-capable runtime this is one cached PhotoImage.  The test remains
    # headless-safe when Tk rejects image creation without an interpreter.
    assert len(created) in {0, 1}


def test_disabled_effects_preserve_legacy_display_call():
    display, state = Display(), [RobotState.IDLE]
    loop = _loop(display, _resolver(state_effects_enabled=False), state)
    frame = EyeRenderer().render(FaceState(), timestamp=0)
    loop._draw(frame, timestamp=1)
    assert display.calls == [((frame,), {})]


@pytest.mark.parametrize("state", list(RobotState))
def test_all_state_effects_are_passed_to_the_display(state):
    display, current = Display(), [state]
    loop = _loop(display, _resolver(), current)
    frame = EyeRenderer().render(FaceState(), timestamp=0)
    loop._draw(frame, timestamp=1)
    effect = display.calls[-1][1]["state_effect"]
    assert effect.state == state.value and effect.intensity > 0


def test_preset_reduce_motion_and_live_replacement_change_the_next_frame():
    display, state = Display(), [RobotState.THINKING]
    loop = _loop(display, _resolver(state_effects_preset="subtle"), state)
    frame = EyeRenderer().render(FaceState(), timestamp=0)
    loop._draw(frame, timestamp=1)
    subtle = display.calls[-1][1]["state_effect"]
    loop._replace_state_effect_resolver(_resolver(state_effects_preset="expressive", state_effects_reduce_motion=True))
    loop._draw(frame, timestamp=2)
    updated = display.calls[-1][1]["state_effect"]
    assert updated.intensity > subtle.intensity and updated.motion_scale == 0


def test_live_palette_replacement_prewarm_requests_active_state_textures():
    class PrewarmDisplay(Display):
        def __init__(self): super().__init__(); self.prewarmed = []
        def prewarm_state_effect(self, effect, width, height, timestamp): self.prewarmed.append((effect, width, height))
    display, state = PrewarmDisplay(), [RobotState.LISTENING]
    loop = _loop(display, _resolver(state_effects_palette="phos"), state)
    loop._effect_dimensions = (800, 600)
    loop._replace_state_effect_resolver(_resolver(state_effects_palette="amber"))
    assert display.prewarmed[-1][0].palette.primary == "#FFE05B"
    assert display.prewarmed[-1][1:] == (800, 600)


def test_smooth_transition_interpolates_but_false_switches_immediately():
    display, state = Display(), [RobotState.IDLE]
    loop = _loop(display, _resolver(state_effects_smooth_transitions=True), state)
    frame = EyeRenderer().render(FaceState(), timestamp=0)
    loop._draw(frame, timestamp=.5)
    loop._draw(frame, timestamp=1)
    state[0] = RobotState.SPEAKING
    loop._draw(frame, timestamp=1.2)
    partial = display.calls[-1][1]["state_effect"]
    loop._draw(frame, timestamp=1.6)
    complete = display.calls[-1][1]["state_effect"]
    assert 0 < partial.intensity < complete.intensity
    loop._replace_state_effect_resolver(_resolver(state_effects_smooth_transitions=False))
    loop._draw(frame, timestamp=2)
    assert display.calls[-1][1]["state_effect"].intensity == _resolver().resolve(RobotState.SPEAKING).intensity


def test_tk_effect_layer_is_below_eyes_and_failures_are_isolated(caplog):
    calls = []
    display = TkEyeDisplay()
    display._root = object()
    display._canvas = SimpleNamespace(delete=lambda *_: calls.append("clear"), configure=lambda **_: None,
        create_image=lambda *_args, **_kwargs: calls.append("effect"),
        create_oval=lambda *_args, **_kwargs: calls.append("effect-or-eye"),
        create_arc=lambda *_args, **_kwargs: calls.append("effect-or-eye"),
        create_line=lambda *_args, **_kwargs: calls.append("overlay"))
    display._tk = SimpleNamespace(ARC="arc")
    display._draw_background = lambda *_: calls.append("background")
    display._draw_ambient_overlay = lambda *_: calls.append("overlay")
    display._state_effect_photo = lambda _primitive: object()
    frame = EyeRenderer().render(FaceState(), timestamp=0)
    display.draw(frame, state_effect=_resolver().resolve(RobotState.LISTENING), effect_timestamp=1)
    assert calls.index("background") < calls.index("effect") < calls.index("effect-or-eye") < calls.index("overlay")

    display._state_effect_layer.render = lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("broken"))
    display.draw(frame, state_effect=StateEffectParams("thinking", intensity=.2, mode="circulate"), effect_timestamp=2)
    assert "state effect unavailable mode=circulate" in caplog.text
