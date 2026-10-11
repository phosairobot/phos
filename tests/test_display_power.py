import json
from types import SimpleNamespace

import pytest

from robot import main


def test_x11_display_keep_awake_runs_expected_xset_commands(monkeypatch):
    calls = []
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    monkeypatch.setattr(main.shutil, "which", lambda name: "/usr/bin/xset")
    monkeypatch.setattr(
        main.subprocess,
        "run",
        lambda command, **kwargs: calls.append((command, kwargs)) or SimpleNamespace(returncode=0),
    )

    main._configure_x11_display_power()

    assert [command for command, _ in calls] == [
        ("/usr/bin/xset", "s", "off"),
        ("/usr/bin/xset", "-dpms"),
        ("/usr/bin/xset", "s", "noblank"),
    ]
    assert all(kwargs == {"check": False, "timeout": 5} for _, kwargs in calls)


def test_x11_display_keep_awake_missing_xset_warns_without_running(monkeypatch, caplog):
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    monkeypatch.setattr(main.shutil, "which", lambda name: None)
    monkeypatch.setattr(main.subprocess, "run", lambda *args, **kwargs: pytest.fail("xset invoked"))

    main._configure_x11_display_power()

    assert "xset is unavailable" in caplog.text


def test_x11_display_keep_awake_command_failure_warns_and_continues(monkeypatch, caplog):
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    monkeypatch.setattr(main.shutil, "which", lambda name: "/usr/bin/xset")
    monkeypatch.setattr(main.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=1))

    main._configure_x11_display_power()

    assert "status 1" in caplog.text
    assert "PHOS will continue" in caplog.text


def test_non_x11_session_skips_xset(monkeypatch):
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    monkeypatch.setattr(main.shutil, "which", lambda name: pytest.fail("xset lookup"))

    main._configure_x11_display_power()


def test_unknown_session_type_skips_xset(monkeypatch):
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.delenv("XDG_SESSION_TYPE", raising=False)
    monkeypatch.setattr(main.shutil, "which", lambda name: pytest.fail("xset lookup"))

    main._configure_x11_display_power()


def test_graphical_session_log_reports_only_local_seat0_source(monkeypatch, caplog):
    caplog.set_level("INFO")
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    monkeypatch.setenv("PHOS_DISPLAY_SOURCE", "local-seat0")

    main._log_graphical_session()

    assert "session_type=x11 display=:0 source=local-seat0" in caplog.text


def test_main_configures_display_power_once_per_startup(tmp_path, monkeypatch):
    document = json.loads(json.dumps(main.ConfigRepository().document()))
    document["logging"]["file"] = None
    path = tmp_path / "phos.json"
    path.write_text(json.dumps(document))
    calls = []

    class Worker:
        def __init__(self, repository, config):
            self.lifecycle = SimpleNamespace(restart_at=None)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    async def run(**kwargs):
        return None

    monkeypatch.setattr(main, "_configure_x11_display_power", lambda: calls.append("configured"))
    monkeypatch.setattr("robot.web.server.WebServer", Worker)
    monkeypatch.setattr(main, "async_main", run)
    monkeypatch.setattr(main.logging, "basicConfig", lambda **kwargs: None)
    monkeypatch.setattr("sys.argv", ["phos", "--config", str(path)])

    main.main()

    assert calls == ["configured"]
