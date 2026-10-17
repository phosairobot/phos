import base64
import struct
import zlib

from robot.core import RobotState
from robot.ui.display import TkEyeDisplay
from robot.ui.state_effect_layer import StateEffectLayer
from robot.ui.state_effects import StateEffectResolver
from types import SimpleNamespace


def _effect(state, preset="balanced"):
    return StateEffectResolver(SimpleNamespace(state_effects_enabled=True, state_effects_preset=preset,
        state_effects_palette="amber", state_effects_reduce_motion=False)).resolve(state)


def _pixel(data, width, x, y):
    return data[(y * width + x) * 4:(y * width + x + 1) * 4]


def test_balanced_glow_textures_have_visible_alpha_for_all_states():
    layer = StateEffectLayer()
    for state in RobotState:
        primitive = layer.primitives(_effect(state), 800, 600, 1)[0]
        width, height = round(primitive.bounds[2]-primitive.bounds[0]), round(primitive.bounds[3]-primitive.bounds[1])
        data = TkEyeDisplay._glow_rgba(width, height, primitive.color, primitive.opacity)
        alpha = data[3::4]
        assert max(alpha) > 40 and sum(value > 0 for value in alpha) > width * height // 4


def test_new_glow_modes_reuse_cached_textures_across_frames():
    display = TkEyeDisplay()
    created = []
    display._tk = SimpleNamespace(PhotoImage=lambda **kwargs: created.append(kwargs) or object())
    layer = StateEffectLayer()
    for state in (RobotState.THINKING, RobotState.SLEEPING, RobotState.ERROR):
        primitives = layer.primitives(_effect(state), 800, 600, 1)
        first = [display._state_effect_photo(primitive) for primitive in primitives]
        count = len(created)
        second = [display._state_effect_photo(primitive) for primitive in primitives]
        assert first == second
        assert len(created) == count


def test_opacity_is_nonzero_and_scales_by_preset():
    layer = StateEffectLayer()
    opacity = [layer.primitives(_effect(RobotState.IDLE, preset), 800, 600, 1)[0].opacity
               for preset in ("subtle", "balanced", "expressive")]
    assert 0 < opacity[0] < opacity[1] < opacity[2]


def test_cached_png_round_trip_preserves_rgba_and_photo_reference():
    display = TkEyeDisplay()
    created = []
    display._tk = SimpleNamespace(PhotoImage=lambda **kwargs: created.append(kwargs) or object())
    primitive = StateEffectLayer().primitives(_effect(RobotState.IDLE), 800, 600, 1)[0]
    width, height = round(primitive.bounds[2]-primitive.bounds[0]), round(primitive.bounds[3]-primitive.bounds[1])
    raw = TkEyeDisplay._glow_rgba(width, height, primitive.color, primitive.opacity)
    # Encoder-compatible filter-0 round trip verifies the actual channel layout.
    rows = b"".join(b"\0" + raw[row*width*4:(row+1)*width*4] for row in range(height))
    decoded = zlib.decompress(zlib.compress(rows, 6))
    center = decoded[(height//2) * (width*4+1) + 1 + (width//2)*4:(height//2) * (width*4+1) + 5 + (width//2)*4]
    assert center[3] > 0 and center[:3] == _pixel(raw, width, width//2, height//2)[:3]
    assert display._state_effect_photo(primitive) is display._state_effect_photo(primitive)
    assert len(created) == 1


def test_prewarm_creates_once_then_reuses_required_active_palette_textures():
    display = TkEyeDisplay()
    created = []
    display._tk = SimpleNamespace(PhotoImage=lambda **kwargs: created.append(kwargs) or object())
    effect = _effect(RobotState.LISTENING)
    display.prewarm_state_effect(effect, 800, 600, 1)
    first = len(created)
    display.prewarm_state_effect(effect, 800, 600, 1)
    assert first > 0 and len(created) == first
