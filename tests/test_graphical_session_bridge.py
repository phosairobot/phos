"""Deployment boundary tests for the local graphical-session bridge."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "deploy" / "phos-session-env.sh"


def _fake_commands(tmp_path: Path, *, remote: str = "no", seat: str = "seat0", session_type: str = "x11") -> Path:
    commands = tmp_path / "bin"
    commands.mkdir()
    (commands / "loginctl").write_text(
        "#!/bin/sh\n"
        "case \"$*\" in\n"
        f"  *User*) id -u ;; *Active*) echo yes ;; *Remote*) echo {remote} ;;\n"
        f"  *Seat*) echo {seat} ;; *Type*) echo {session_type} ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    (commands / "systemctl").write_text(
        "#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$PHOS_TEST_CALLS\"\n",
        encoding="utf-8",
    )
    for command in commands.iterdir():
        command.chmod(0o755)
    return commands


def _run_bridge(tmp_path: Path, **settings) -> tuple[subprocess.CompletedProcess[str], str]:
    display = settings.pop("display", ":0")
    commands = _fake_commands(tmp_path, **settings)
    calls = tmp_path / "calls"
    environment = os.environ | {
        "PATH": f"{commands}:{os.environ['PATH']}",
        "XDG_SESSION_ID": "42",
        "DISPLAY": display,
        "XDG_SESSION_TYPE": settings.get("session_type", "x11"),
        "PHOS_TEST_CALLS": str(calls),
    }
    result = subprocess.run(["sh", str(BRIDGE)], env=environment, text=True,
                            capture_output=True, check=False)
    return result, calls.read_text(encoding="utf-8") if calls.exists() else ""


def test_bridge_imports_only_an_active_local_seat0_graphical_session(tmp_path):
    result, calls = _run_bridge(tmp_path)

    assert result.returncode == 0
    assert "import-environment DISPLAY XAUTHORITY XDG_SESSION_TYPE" in calls
    assert "PHOS_LOCAL_GRAPHICAL_SESSION PHOS_DISPLAY_SOURCE" in calls
    assert "start phos.service" in calls


def test_bridge_rejects_remote_forwarded_x11_environment(tmp_path):
    result, calls = _run_bridge(tmp_path, remote="yes", display="localhost:10.0")

    assert result.returncode == 0
    assert not calls
    assert "session is remote" in result.stderr


def test_bridge_rejects_nonlocal_x11_display_even_for_a_local_session(tmp_path):
    result, calls = _run_bridge(tmp_path, display="localhost:10.0")

    assert result.returncode == 0
    assert not calls
    assert "no local graphical display" in result.stderr


def test_bridge_rejects_missing_graphical_session(tmp_path):
    result, calls = _run_bridge(tmp_path, session_type="tty")

    assert result.returncode == 0
    assert not calls
    assert "not graphical" in result.stderr
