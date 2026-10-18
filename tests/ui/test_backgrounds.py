from pathlib import Path
from types import SimpleNamespace

import pytest

from robot.core import RobotState
from robot.ui.backgrounds import BackgroundAsset, BackgroundResolver
from robot.ui.display import CameraPreviewSettings, TkEyeDisplay
from robot.ui.eyes import EyeRenderer
from robot.ui.runtime import EyeRenderLoop
from robot.ui.state import AmbientOverlayState, FaceState


class RecordingDisplay:
    def __init__(self):
        self.calls = []

    def draw(self, *args, **kwargs):
        self.calls.append((args, kwargs))


def _config(tmp_path, *, enabled=True, **paths):
    values = {f"background_{state.value}": None for state in RobotState}
    values.update(paths)
    return SimpleNamespace(backgrounds_enabled=enabled, resolve_path=lambda value: value, **values)


def _loop(display, resolver, state):
    return EyeRenderLoop(EyeRenderer(), display, FaceState, background_resolver=resolver,
                         robot_state_supplier=lambda: state[0])


def test_disabled_backgrounds_preserve_the_legacy_display_call(tmp_path):
    display = RecordingDisplay()
    loop = _loop(display, BackgroundResolver(_config(tmp_path, enabled=False)), [RobotState.IDLE])
    frame = EyeRenderer().render(FaceState(), timestamp=0)

    loop._draw(frame)

    assert display.calls == [((frame,), {})]


@pytest.mark.parametrize("state", [RobotState.IDLE, RobotState.LISTENING, RobotState.SPEAKING,
                                    RobotState.SLEEPING, RobotState.ERROR])
def test_supported_state_passes_its_resolved_background_to_display(tmp_path, state):
    path = tmp_path / f"{state.value}.png"
    path.touch()
    display, current = RecordingDisplay(), [state]
    loop = _loop(display, BackgroundResolver(_config(tmp_path, **{f"background_{state.value}": path})), current)
    frame = EyeRenderer().render(FaceState(), timestamp=0)

    loop._draw(frame)

    assert display.calls[0][1]["background"].path == path


def test_state_change_switches_background_without_resolving_each_frame(tmp_path):
    idle, listening = tmp_path / "idle.png", tmp_path / "listening.png"
    idle.touch()
    listening.touch()
    resolver = BackgroundResolver(_config(tmp_path, background_idle=idle, background_listening=listening))
    calls = []
    original = resolver.resolve
    resolver.resolve = lambda state: calls.append(state) or original(state)
    display, current = RecordingDisplay(), [RobotState.IDLE]
    loop, frame = _loop(display, resolver, current), EyeRenderer().render(FaceState(), timestamp=0)

    loop._draw(frame)
    loop._draw(frame)
    current[0] = RobotState.LISTENING
    loop._draw(frame)

    assert calls == [RobotState.IDLE, RobotState.LISTENING]
    assert display.calls[0][1]["background"].path == idle
    assert display.calls[-1][1]["background"].path == listening


def test_unassigned_and_missing_assets_fall_back_to_legacy_background(tmp_path):
    frame = EyeRenderer().render(FaceState(), timestamp=0)
    for config in (_config(tmp_path), _config(tmp_path, background_speaking=tmp_path / "missing.png")):
        display = RecordingDisplay()
        loop = _loop(display, BackgroundResolver(config), [RobotState.SPEAKING])
        loop._draw(frame)
        assert display.calls == [((frame,), {})]


def test_status_is_filename_only_and_reports_unavailable_assignment(tmp_path):
    missing = tmp_path / "missing.png"
    resolver = BackgroundResolver(_config(tmp_path, background_speaking=missing))

    status = resolver.status(RobotState.SPEAKING)

    assert status["current_state"] == "speaking"
    assert status["current_asset"] == "missing.png"
    assert status["available"] is False and status["reason"] == "asset_missing"
    assert str(tmp_path) not in repr(status)


def _tk_display():
    display = TkEyeDisplay()
    calls = []
    display._root = object()
    display._canvas = SimpleNamespace(
        delete=lambda *_: calls.append("clear"), configure=lambda **_: calls.append("color"),
        create_image=lambda *_args, **_kwargs: calls.append("background"),
        create_oval=lambda *_args, **_kwargs: calls.append("eye-or-overlay"),
        create_arc=lambda *_args, **_kwargs: calls.append("overlay"),
        create_line=lambda *_args, **_kwargs: calls.append("overlay"),
    )
    display._tk = SimpleNamespace(ARC="arc")
    display._prepare_background = lambda *_: object()
    return display, calls


def test_invalid_image_falls_back_once_without_reloading(tmp_path, caplog):
    display, _ = _tk_display()
    display._prepare_background = TkEyeDisplay._prepare_background.__get__(display, TkEyeDisplay)
    invalid = tmp_path / "invalid.png"
    invalid.write_text("not an image")
    asset = BackgroundAsset("idle", invalid, True)
    display._draw_background(asset, 800, 600)
    display._draw_background(asset, 800, 600)

    assert (invalid, 800, 600) in display._background_failed
    assert "background unavailable state=idle reason=invalid_image" in caplog.text


def test_prepared_background_is_cached_and_composed_below_eyes_and_overlays(tmp_path):
    display, calls = _tk_display()
    asset = BackgroundAsset("idle", Path("idle.png"), True)
    frame = EyeRenderer().render(
        FaceState(ambient_overlay=AmbientOverlayState(temperature="warm")), timestamp=0
    )
    prepared = []
    display._prepare_background = lambda *_: prepared.append(1) or object()
    display._draw_preview = lambda *_: calls.append("transient")
    display.draw(frame, preview=object(), preview_settings=CameraPreviewSettings(enabled=True), background=asset)
    display.draw(frame, preview=object(), preview_settings=CameraPreviewSettings(enabled=True), background=asset)

    assert prepared == [1]
    assert calls.count("background") == 2
    first_background = calls.index("background")
    assert first_background < calls.index("eye-or-overlay") < calls.index("transient")
