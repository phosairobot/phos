"""Preview image transport and error handling without camera/display hardware."""

from concurrent.futures import Future
import logging
from types import SimpleNamespace
from unittest.mock import Mock

from robot.ui.display import (
    CameraPreviewSettings, CameraPreviewView, TkEyeDisplay, _encode_preview_ppm,
)


def test_preview_passes_binary_ppm_to_tk_and_draws_image(monkeypatch):
    pixels = b"\x00\x80\xff" * 2
    small = SimpleNamespace(tobytes=lambda: pixels)
    resize = Mock(return_value=small)
    monkeypatch.setitem(__import__("sys").modules, "cv2",
                        SimpleNamespace(resize=resize, INTER_AREA=3))
    frame = SimpleNamespace(shape=(2, 4, 3))
    encoded = _encode_preview_ppm(frame, 2)
    assert encoded == (2, 1, b"P6 2 1 255\n" + pixels)
    resize.assert_called_once_with(frame, (2, 1), interpolation=3)

    photo = SimpleNamespace(width=lambda: 2, height=lambda: 1)
    display = TkEyeDisplay()
    display._tk = SimpleNamespace(PhotoImage=Mock(return_value=photo))
    display._canvas = Mock()
    display._preview_next_at = float("inf")
    display._preview_future = Future()
    display._preview_future.set_result(encoded)
    display._draw_preview(CameraPreviewView(frame), CameraPreviewSettings(enabled=True), 800, 600)

    display._tk.PhotoImage.assert_called_once_with(data=encoded[2], format="PPM")
    display._canvas.create_image.assert_called_once_with(782, 583, image=photo, anchor="nw")


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
    caplog.set_level(logging.INFO)
    splash = tmp_path / "phos-startup-800x600.png"
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
