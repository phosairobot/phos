from types import SimpleNamespace

import pytest

from robot.core import RobotState
from robot.ui.state_effects import StateEffectResolver
from robot.ui.display import TkEyeDisplay
import struct
import zlib


def resolver(**values):
    defaults = dict(state_effects_enabled=False, state_effects_preset="balanced",
                    state_effects_palette="phos", state_effects_smooth_transitions=True, state_effects_reduce_motion=False)
    defaults.update(values)
    return StateEffectResolver(SimpleNamespace(**defaults))


def test_disabled_effects_have_no_semantic_output():
    effect = resolver().resolve(RobotState.LISTENING)
    assert effect.intensity == effect.speed == effect.glow == 0


@pytest.mark.parametrize("preset", ["subtle", "balanced", "expressive"])
def test_every_robot_state_resolves_for_each_supported_preset(preset):
    effects = resolver(state_effects_enabled=True, state_effects_preset=preset)
    assert {effects.resolve(state).state for state in RobotState} == {state.value for state in RobotState}


def test_presets_scale_predictably_and_reduce_motion_suppresses_motion():
    subtle = resolver(state_effects_enabled=True, state_effects_preset="subtle").resolve(RobotState.THINKING)
    balanced = resolver(state_effects_enabled=True, state_effects_preset="balanced").resolve(RobotState.THINKING)
    expressive = resolver(state_effects_enabled=True, state_effects_preset="expressive").resolve(RobotState.THINKING)
    reduced = resolver(state_effects_enabled=True, state_effects_reduce_motion=True).resolve(RobotState.THINKING)
    assert subtle.intensity < balanced.intensity < expressive.intensity
    assert reduced.motion_scale == 0 and reduced.speed <= .02


def test_each_configured_palette_resolves_to_distinct_semantic_colors():
    colors = {resolver(state_effects_enabled=True, state_effects_palette=name).resolve(RobotState.IDLE).palette
              for name in ("phos", "cyan", "blue", "violet", "aqua", "amber", "white", "green")}
    assert len(colors) == 8


def test_palette_channels_have_deliberately_distinct_real_display_identities():
    palettes = {name: resolver(state_effects_enabled=True, state_effects_palette=name).resolve(RobotState.IDLE).palette
                for name in ("cyan", "blue", "violet", "aqua", "amber")}
    rgb = lambda value: tuple(bytes.fromhex(value[1:]))
    cyan, blue, violet, aqua, amber = (rgb(palettes[name].primary) for name in ("cyan", "blue", "violet", "aqua", "amber"))
    assert cyan[1] > cyan[0] and cyan[2] > cyan[0]
    assert blue[2] > blue[1] and blue[2] > blue[0]
    assert aqua[1] > aqua[2] and aqua[1] > blue[1]
    assert violet[0] > violet[1] and violet[2] > violet[1]
    assert amber[0] > amber[1] > amber[2]


def test_white_is_luminous_and_green_is_distinct_from_aqua():
    palette = lambda name: resolver(state_effects_enabled=True, state_effects_palette=name).resolve(RobotState.IDLE).palette
    white, green, aqua = palette("white"), palette("green"), palette("aqua")
    rgb = lambda value: tuple(bytes.fromhex(value[1:]))
    assert min(rgb(white.primary)) >= 244
    assert rgb(green.primary)[1] > rgb(green.primary)[2]
    assert rgb(green.primary) != rgb(aqua.primary)


def test_cached_glow_cores_preserve_palette_hue_with_soft_edges():
    palette = lambda name: resolver(state_effects_enabled=True, state_effects_palette=name).resolve(RobotState.IDLE).palette
    pixel = lambda data, x, y: data[(y * 40 + x) * 4:(y * 40 + x + 1) * 4]
    core = lambda name: pixel(TkEyeDisplay._glow_rgba(40, 40, palette(name).primary, .7), 20, 20)
    amber, green, violet, blue = (core(name) for name in ("amber", "green", "violet", "blue"))
    # BGRA texture channels: colour stays source-saturated at the opaque core.
    assert amber[2] > amber[1] > amber[0]
    assert green[1] > green[2] and green[1] > green[0]
    assert violet[0] > violet[1] and violet[2] > violet[1]
    assert blue[0] > blue[1] and blue[0] > blue[2]
    image = TkEyeDisplay._glow_rgba(40, 40, palette("amber").primary, .7)
    assert pixel(image, 20, 20)[3] > pixel(image, 8, 8)[3] > pixel(image, 0, 0)[3] == 0
