import pytest

from robot.iris_colors import IrisColorResolver


@pytest.mark.parametrize("preset,canonical", IrisColorResolver.PRESETS.items())
def test_every_legacy_preset_resolves_to_its_canonical_color(preset, canonical):
    assert IrisColorResolver.normalize(preset) == canonical


@pytest.mark.parametrize("value,canonical", [
    ("#FFFFFF", "#FFFFFF"), ("#ffffff", "#FFFFFF"), ("#7a5cff", "#7A5CFF"),
])
def test_custom_hex_is_normalized(value, canonical):
    assert IrisColorResolver.normalize(value) == canonical


@pytest.mark.parametrize("value", ["#FFF", "FFFFFF", "rgb(1, 2, 3)", "rgba(1, 2, 3, .5)", "red", "#00FFFF00"])
def test_noncanonical_color_forms_are_rejected(value):
    with pytest.raises(ValueError):
        IrisColorResolver.normalize(value)
