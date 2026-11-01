import pytest

from robot.ui.display import (CAMERA_PREVIEW_SAFE_MARGIN, camera_preview_layout)


@pytest.mark.parametrize("position", ("top_left", "top_right", "bottom_left", "bottom_right"))
def test_camera_preview_visual_box_stays_inside_800x600_safe_margin(position):
    layout = camera_preview_layout(800, 600, 200, 150, position)

    assert layout.x >= CAMERA_PREVIEW_SAFE_MARGIN
    assert layout.y >= CAMERA_PREVIEW_SAFE_MARGIN
    assert layout.x + layout.width <= 800 - CAMERA_PREVIEW_SAFE_MARGIN
    assert layout.y + layout.height <= 600 - CAMERA_PREVIEW_SAFE_MARGIN


def test_camera_preview_layout_accounts_for_border_in_its_bounds():
    layout = camera_preview_layout(800, 600, 200, 150, "bottom_right")

    assert layout.width == 206
    assert layout.height == 156
    assert layout.image_x == 581
    assert layout.image_y == 431
    assert layout.x + layout.width == 784
    assert layout.y + layout.height == 584
