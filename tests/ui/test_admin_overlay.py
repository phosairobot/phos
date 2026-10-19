from types import SimpleNamespace
import time

from robot.admin_access import AdminOverlayState, RecoveryState
from robot.runtime import PhosRuntime
from robot.ui.display import (MemoryEyeDisplay, TkEyeDisplay, admin_access_overlay_layout,
                              recovery_overlay_layout)
from robot.ui.eyes import EyeRenderer
from robot.ui.runtime import EyeRenderLoop
from robot.ui.state import FaceState


def test_runtime_recovery_overlay_is_local_and_clear_is_idempotent():
    class Loop:
        admin_overlay_available = True

        def __init__(self):
            self.overlays = []

        def request_admin_overlay(self, overlay):
            self.overlays.append(overlay)

        def clear_admin_overlay(self):
            self.overlays.append(AdminOverlayState())

    runtime = object.__new__(PhosRuntime)
    runtime._eye_render_loop = Loop()
    face_before = object()
    robot_before = object()
    runtime.core = SimpleNamespace(state=robot_before)
    runtime._behavior_engine = SimpleNamespace(face_state=face_before)

    runtime.show_recovery_code("483921", 300.0)
    runtime.clear_admin_overlay()
    runtime.clear_admin_overlay()

    assert runtime._eye_render_loop.overlays[0].mode == "recovery"
    assert runtime._eye_render_loop.overlays[0].recovery_code == "483921"
    assert [overlay.mode for overlay in runtime._eye_render_loop.overlays[1:]] == ["hidden", "hidden"]
    assert runtime.core.state is robot_before
    assert runtime._behavior_engine.face_state is face_before


def test_overlay_is_removed_at_the_recovery_deadline_without_a_second_ttl():
    display = MemoryEyeDisplay()
    loop = EyeRenderLoop(EyeRenderer(), display, FaceState)
    frame = EyeRenderer().render(FaceState(), timestamp=0.0)

    loop._set_admin_overlay(AdminOverlayState.recovery("483921", 10.0))
    loop._draw(frame, timestamp=10.0)

    assert loop.admin_overlay.mode == "hidden"
    assert loop.admin_overlay.recovery_code is None
    assert display.admin_overlays[-1].mode == "hidden"


def test_overlay_changes_are_handed_to_the_display_loop_not_called_cross_thread():
    class Queue:
        def __init__(self):
            self.callback = None

        def call_soon_threadsafe(self, callback, *args):
            self.callback = (callback, args)

    display = MemoryEyeDisplay()
    loop = EyeRenderLoop(EyeRenderer(), display, FaceState)
    queue = Queue()
    loop._appearance_ready = True
    loop._loop = queue

    loop.request_admin_overlay(AdminOverlayState.recovery("483921", 10.0))

    assert display.admin_overlays == []
    callback, args = queue.callback
    callback(*args)
    assert display.admin_overlays[-1].recovery_code == "483921"


def test_recovery_overlay_is_the_final_tk_layer_and_hidden_state_draws_nothing():
    display = TkEyeDisplay()
    calls = []
    display._root = object()
    display._canvas = SimpleNamespace(
        delete=lambda *_: calls.append(("delete",)),
        configure=lambda **_: None,
        create_oval=lambda *_args, **_kwargs: calls.append(("eye",)),
        create_arc=lambda *_args, **_kwargs: calls.append(("eye",)),
        create_line=lambda *_args, **_kwargs: calls.append(("eye",)),
        create_rectangle=lambda *_args, **kwargs: calls.append(("rectangle", kwargs.get("fill"))),
        create_text=lambda *_args, **kwargs: calls.append(("text", kwargs.get("text"))),
    )
    display._tk = SimpleNamespace(ARC="arc")
    frame = EyeRenderer().render(FaceState(), timestamp=0.0)

    display.draw(frame)
    assert ("text", "PHOS ADMIN") not in calls

    display.set_admin_overlay(AdminOverlayState.recovery("483921", time.monotonic() + 60.0))
    display.draw(frame)

    assert ("rectangle", "#020B1C") in calls
    assert ("rectangle", "#071B35") in calls
    assert ("text", "PHOS ADMIN") in calls
    assert ("text", "Password recovery code") in calls
    assert ("text", "483921") in calls
    assert any(entry[0] == "text" and entry[1].startswith("Expires in ") for entry in calls if len(entry) == 2)


def test_admin_access_overlay_draws_a_matrix_url_and_close_instruction():
    display = TkEyeDisplay()
    calls = []
    display._root = object()
    display._canvas = SimpleNamespace(
        delete=lambda *_: calls.append(("delete",)), configure=lambda **_: None,
        create_oval=lambda *_args, **_kwargs: None, create_arc=lambda *_args, **_kwargs: None,
        create_line=lambda *_args, **_kwargs: None,
        create_rectangle=lambda *_args, **kwargs: calls.append(("rectangle", kwargs.get("fill"))),
        create_text=lambda *_args, **kwargs: calls.append(("text", kwargs.get("text"))),
    )
    display._tk = SimpleNamespace(ARC="arc")
    display.set_admin_overlay(AdminOverlayState.admin_access("http://192.168.1.42:8080/", (
        (False, True, False), (True, False, True), (False, True, False))))

    display.draw(EyeRenderer().render(FaceState(), timestamp=0.0))

    assert ("text", "PHOS ADMIN") in calls
    assert ("text", "http://192.168.1.42:8080/") in calls
    assert ("text", "IP: 192.168.1.42") in calls
    assert ("text", "Touch to close") in calls
    assert len([entry for entry in calls if entry == ("rectangle", "#02050D")]) == 4


def test_admin_access_card_and_content_are_safely_bounded_on_800x600():
    layout = admin_access_overlay_layout(800, 600, 33)

    assert layout.card_x >= 0 and layout.card_y >= 0
    assert layout.card_right <= 800 and layout.card_bottom <= 600
    assert (round(layout.card_x), round(layout.card_y), round(layout.card_right), round(layout.card_bottom)) == (96, 72, 704, 528)
    assert abs((layout.card_y + layout.card_bottom) / 2 - 300) <= 1
    assert layout.card_x <= layout.qr_x and layout.qr_x + layout.qr_size <= layout.card_right
    assert layout.card_y <= layout.qr_y and layout.qr_y + layout.qr_size <= layout.card_bottom
    assert all(layout.card_y < y < layout.card_bottom for y in (layout.url_y, layout.ip_y, layout.close_y))


def test_admin_access_layout_scales_to_the_actual_usable_canvas():
    layout = admin_access_overlay_layout(800, 480, 33)

    assert layout.card_x >= 64 and layout.card_right <= 736
    assert layout.card_y >= 57 and layout.card_bottom <= 423
    assert layout.qr_y >= layout.card_y and layout.qr_y + layout.qr_size <= layout.card_bottom


def test_overlay_state_repr_does_not_contain_the_recovery_code():
    assert "483921" not in repr(AdminOverlayState.recovery("483921", 100.0))


def test_recovery_default_ttl_is_two_minutes_and_expiry_still_clears_the_code():
    now = [10.0]
    recovery = RecoveryState(clock=lambda: now[0], token=lambda: "483921")

    recovery.request()
    assert recovery.ttl == 120
    assert recovery.expires == 130.0
    now[0] = 130.0

    try:
        recovery.verify("483921")
    except ValueError as error:
        assert str(error) == "recovery_code_expired"
    else:
        raise AssertionError("expired recovery code was accepted")
    assert recovery.code is None


def test_recovery_card_is_centered_and_fully_visible_on_the_physical_canvas():
    layout = recovery_overlay_layout(800, 600)

    assert tuple(round(value, 3) for value in
                 (layout.card_x, layout.card_y, layout.card_right, layout.card_bottom)) == (144.0, 132.0, 656.0, 468.0)
    assert layout.card_x >= 0 and layout.card_y >= 0
    assert layout.card_right <= 800 and layout.card_bottom <= 600
    assert abs((layout.card_y + layout.card_bottom) / 2 - 300) <= 1
    assert all(layout.card_y < y < layout.card_bottom
               for y in (layout.title_y, layout.message_y, layout.code_y, layout.countdown_y))
