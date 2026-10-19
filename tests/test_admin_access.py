import sys
from types import SimpleNamespace

import pytest

from robot.admin_access import (AdminOverlayState, NetworkInfoService, QRMatrixCache,
                                RecoveryState, select_lan_ipv4)
from robot.runtime import PhosRuntime
from robot.ui.touch import TouchKind


def test_lan_selection_rejects_loopback_and_link_local_addresses():
    interface, address = select_lan_ipv4([
        ("lo", "127.0.0.1"), ("wlan0", "169.254.1.2"), ("eth0", "192.168.10.20"),
    ])

    assert (interface, address) == ("eth0", "192.168.10.20")
    assert select_lan_ipv4([("lo", "127.0.0.1"), ("wlan0", "169.254.1.2")]) == (None, None)


def test_network_info_is_cached_and_uses_the_configured_web_port():
    now = [0.0]
    calls = []
    service = NetworkInfoService(8123, hostname=lambda: "phos", addresses=lambda: calls.append(1) or [
        ("wlan0", "192.168.1.42")], clock=lambda: now[0])

    assert service.info().document() == {"hostname": "phos", "ip_address": "192.168.1.42", "interface": "wlan0",
                                         "port": 8123, "admin_url": "http://192.168.1.42:8123/", "status": "connected"}
    assert service.info().admin_url == "http://192.168.1.42:8123/"
    assert len(calls) == 1
    now[0] = 31
    service.info()
    assert len(calls) == 2


def test_network_info_reports_a_bounded_disconnected_state():
    info = NetworkInfoService(8080, hostname=lambda: "phos", addresses=lambda: []).info().document()
    assert info == {"hostname": "phos", "ip_address": None, "interface": None, "port": 8080,
                    "admin_url": None, "status": "unavailable"}


def test_qr_matrix_cache_encodes_only_the_effective_admin_url(monkeypatch):
    payloads = []

    class Code:
        def __init__(self, border): self.border = border
        def add_data(self, value): payloads.append(value)
        def make(self, fit): assert fit is True
        def get_matrix(self): return [[False, True], [True, False]]

    monkeypatch.setitem(sys.modules, "qrcode", SimpleNamespace(QRCode=Code))
    cache = QRMatrixCache()
    url = "http://192.168.1.42:8080/"
    first = cache.matrix(url)
    second = cache.matrix(url)
    changed = cache.matrix("http://192.168.1.43:8080/")

    assert first == ((False, True), (True, False)) and second is first
    assert changed == first
    assert payloads == [url, "http://192.168.1.43:8080/"]
    assert all("password" not in payload and "token" not in payload for payload in payloads)


def test_admin_overlay_long_press_and_tap_do_not_mutate_semantic_state(monkeypatch):
    class Loop:
        admin_overlay_available = True
        def __init__(self): self.admin_overlay = AdminOverlayState(); self.overlays = []
        def request_admin_overlay(self, overlay): self.admin_overlay = overlay; self.overlays.append(overlay)
        def clear_admin_overlay(self): self.admin_overlay = AdminOverlayState(); self.overlays.append(self.admin_overlay)

    class Network:
        def info(self):
            return SimpleNamespace(connected=True, admin_url="http://192.168.1.42:8080/")

    runtime = object.__new__(PhosRuntime)
    runtime._eye_render_loop, runtime._admin_network = Loop(), Network()
    runtime._admin_qr = SimpleNamespace(matrix=lambda url: ((False, True), (True, False)))
    runtime.core = SimpleNamespace(state="idle")
    runtime._behavior_engine = SimpleNamespace(face_state=object())
    before = runtime._behavior_engine.face_state
    short = SimpleNamespace(kind=TouchKind.LONG_PRESS, duration_ms=2499, normalized_x=.9, normalized_y=.1)
    outside = SimpleNamespace(kind=TouchKind.LONG_PRESS, duration_ms=2500, normalized_x=.7, normalized_y=.1)
    inside = SimpleNamespace(kind=TouchKind.LONG_PRESS, duration_ms=2500, normalized_x=.8, normalized_y=.2)

    assert not runtime.handle_admin_touch(short)
    assert not runtime.handle_admin_touch(outside)
    assert runtime.handle_admin_touch(inside)
    assert runtime._eye_render_loop.admin_overlay.mode == "admin_access"
    assert runtime.handle_admin_touch(SimpleNamespace(kind=TouchKind.TAP, duration_ms=20, normalized_x=.5, normalized_y=.5))
    assert runtime._eye_render_loop.admin_overlay.mode == "hidden"
    assert runtime.core.state == "idle" and runtime._behavior_engine.face_state is before


def test_recovery_overlay_stays_distinct_from_admin_access():
    recovery = AdminOverlayState.recovery("483921", 120.0)
    access = AdminOverlayState.admin_access("http://192.168.1.42:8080/", ((False, True),))
    assert recovery.mode == "recovery" and recovery.admin_url is None
    assert access.mode == "admin_access" and access.recovery_code is None
