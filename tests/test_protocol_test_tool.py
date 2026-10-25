"""Focused usability coverage for the standalone phos-hw serial tester."""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
from types import ModuleType


def _tool(monkeypatch):
    serial = ModuleType("serial")
    serial.Serial = object
    tools = ModuleType("serial.tools")
    ports = ModuleType("serial.tools.list_ports")
    ports.comports = lambda: []
    tools.list_ports = ports
    monkeypatch.setitem(sys.modules, "serial", serial)
    monkeypatch.setitem(sys.modules, "serial.tools", tools)
    monkeypatch.setitem(sys.modules, "serial.tools.list_ports", ports)
    path = Path(__file__).parents[1] / "phos-protocol-test" / "phos-protocol-test.py"
    spec = spec_from_file_location("phos_protocol_test_tool", path)
    module = module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_noisy_frames_are_consumed_but_hidden_by_default(capsys, monkeypatch):
    tool = _tool(monkeypatch)
    session = tool.SerialSession()
    session._handle_line(b'{"type":"orientation_update","payload":{}}')
    session._handle_line(b'{"type":"heartbeat","payload":{}}')
    assert [session._messages.get_nowait().document["type"] for _ in range(2)] == ["orientation_update", "heartbeat"]
    assert "RX type=" not in capsys.readouterr().out


def test_ack_remains_visible_and_interactive_filters_change_rendering(capsys, monkeypatch):
    tool = _tool(monkeypatch)
    session = tool.SerialSession()
    tester = tool.InteractiveTester(session)
    session._handle_line(b'{"type":"ack","payload":{}}')
    assert "RX type=ack" in capsys.readouterr().out
    tester.handle("show orientation_update")
    session._handle_line(b'{"type":"orientation_update","payload":{}}')
    assert "RX type=orientation_update" in capsys.readouterr().out
    tester.handle("hide orientation_update")
    session._handle_line(b'{"type":"orientation_update","payload":{}}')
    assert "] RX type=orientation_update" not in capsys.readouterr().out
    tester.handle("quiet on")
    session._handle_line(b'{"type":"error","payload":{}}')
    assert "RX type=error" in capsys.readouterr().out
