"""Preview image transport and error handling without camera/display hardware."""

from concurrent.futures import Future
import logging
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from robot.ui.display import (
    CameraPreviewSettings, CameraPreviewView, TkEyeDisplay, _encode_preview_ppm,
)


def test_missing_graphical_environment_fails_clearly_at_display_boundary(monkeypatch):
    class TclError(Exception):
        pass

    monkeypatch.setitem(sys.modules, "tkinter", SimpleNamespace(
        TclError=TclError,
        Tk=Mock(side_effect=TclError("no display name and no $DISPLAY environment variable")),
    ))

    with pytest.raises(RuntimeError, match="Could not open the PHOS HDMI display from this session"):
        TkEyeDisplay().open(800, 600, fullscreen=True)


def test_preview_converts_bgr_to_rgb_before_passing_binary_ppm_to_tk(monkeypatch):
    # Pure red, blue and green expressed in the VisionPipeline BGR contract.
    bgr_pixels = b"\x00\x00\xff\xff\x00\x00\x00\xff\x00"
    rgb_pixels = b"\xff\x00\x00\x00\x00\xff\x00\xff\x00"
    small = SimpleNamespace(tobytes=lambda: bgr_pixels)
    rgb = SimpleNamespace(tobytes=lambda: rgb_pixels)
    resize = Mock(return_value=small)
    cvt_color = Mock(return_value=rgb)
    monkeypatch.setitem(__import__("sys").modules, "cv2",
                        SimpleNamespace(resize=resize, cvtColor=cvt_color,
                                        INTER_AREA=3, COLOR_BGR2RGB=4))
    frame = SimpleNamespace(shape=(1, 3, 3))
    encoded = _encode_preview_ppm(frame, 3)
    assert encoded == (3, 1, b"P6 3 1 255\n" + rgb_pixels)
    resize.assert_called_once_with(frame, (3, 1), interpolation=3)
    cvt_color.assert_called_once_with(small, 4)

    photo = SimpleNamespace(width=lambda: 3, height=lambda: 1)
    display = TkEyeDisplay()
    display._tk = SimpleNamespace(PhotoImage=Mock(return_value=photo))
    display._canvas = Mock()
    display._preview_next_at = float("inf")
    display._preview_future = Future()
    display._preview_future.set_result(encoded)
    display._draw_preview(CameraPreviewView(frame), CameraPreviewSettings(enabled=True), 800, 600)

    display._tk.PhotoImage.assert_called_once_with(data=encoded[2], format="PPM")
    # Bottom-right layout keeps the bordered preview within its 16px safe margin.
    display._canvas.create_image.assert_called_once_with(778, 580, image=photo, anchor="nw")


def test_preview_failure_is_logged_once_until_recovery(caplog):
    display = TkEyeDisplay()
    display._preview_next_at = float("inf")
    preview = CameraPreviewView(SimpleNamespace(shape=(2, 4, 3)))
    settings = CameraPreviewSettings(enabled=True)
    for _ in range(2):
        display._preview_future = Future()
        display._preview_future.set_exception(ValueError("invalid preview data"))
        display._draw_preview(preview, settings, 800, 600)
    assert caplog.text.count("Could not render camera preview") == 1
    assert "invalid preview data" in caplog.text
    assert display._preview_photo is None
    assert display._preview_future is None

    display._tk = SimpleNamespace(PhotoImage=Mock(return_value=SimpleNamespace(width=lambda: 2, height=lambda: 1)))
    display._canvas = Mock()
    display._preview_future = Future()
    display._preview_future.set_result((2, 1, b"P6 2 1 255\n" + b"\x00" * 6))
    display._draw_preview(preview, settings, 800, 600)
    assert not display._preview_failed


def test_invalid_startup_image_logs_loader_failure_without_crashing(caplog):
    display = TkEyeDisplay()
    display._root = SimpleNamespace(winfo_width=lambda: 800, winfo_height=lambda: 600)
    display._canvas = Mock()
    display._tk = SimpleNamespace(PhotoImage=Mock(side_effect=RuntimeError("unsupported image")))
    display.draw_startup(state="starting", message="Starting...", image_path="/missing/image.webp")
    assert "STARTUP SPLASH LOAD FAILED" in caplog.text
    assert "unsupported image" in caplog.text


def test_startup_splash_is_one_centered_full_screen_item(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    splash = tmp_path / "neon_phos_logo-startup-800x600.png"
    splash.touch()
    photo = SimpleNamespace(width=lambda: 800, height=lambda: 600)
    display = TkEyeDisplay()
    display._root = SimpleNamespace(winfo_geometry=lambda: "800x600+0+0")
    display._canvas = Mock()
    display._canvas.winfo_width.return_value = 800
    display._canvas.winfo_height.return_value = 600
    display._canvas.create_image.return_value = 17
    display._tk = SimpleNamespace(PhotoImage=Mock(return_value=photo))

    display.draw_startup(state="starting", message="Starting...", image_path=str(splash))
    display.draw_startup(state="starting", message="Starting...", image_path=str(splash))

    display._tk.PhotoImage.assert_called_once_with(file=str(splash))
    display._canvas.create_image.assert_called_once_with(400, 300, image=photo, anchor="center")
    assert display._startup_photo is photo
    assert "root_geometry=800x600+0+0" in caplog.text
    assert "canvas_size=800x600" in caplog.text
    assert "source_size=800x600" in caplog.text
    assert "resized_size=800x600" in caplog.text
