import asyncio
import inspect

import pytest

pytest.importorskip("mcp")

from robot.mcp.server import MCP_TOOL_NAMES, McpSemanticAdapter, McpToolError, create_mcp_server
from robot.core.behavior_engine import BehaviorEngine
from robot.core.runtime import RobotCore
from robot.services import AgentAccess, AgentApplicationService, ApplicationError, AgentProfile
from robot.services.application import PhosApplicationService


class ApplicationSpy:
    def __init__(self):
        self.calls = []

    def _call(self, name, *arguments):
        self.calls.append((name, *arguments))
        return {"operation": name, "arguments": list(arguments)}

    def status(self):
        self._call("status")
        return {"robot": {"running": True, "state": "idle"},
                "environment": {"status": "available", "available": True},
                "motion": {"status": "available", "available": True},
                "sensors": {"ccs811": {"status": "available", "available": True}},
                "observed_expression": {"available": True},
                "voice": {"enabled": True, "listening": False, "stt_available": False, "last_error": None},
                "speech": {"tts": {"available": True}}, "semantic_led": {"available": True}}
    def get_physical_context(self): return self._call("get_physical_context")
    def presence(self): return self._call("presence")
    def attention(self): return self._call("attention")
    def observed_expression(self): return self._call("observed_expression")
    def set_expression(self, expression): return self._call("set_expression", expression)
    def speak(self, text): return self._call("speak", text)
    def set_state(self, state): return self._call("set_state", state)
    def signal_user(self, signal): return self._call("signal_user", signal)
    async def speak_async(self, text): return self.speak(text)
    async def set_state_async(self, state): return self.set_state(state)
    async def signal_user_async(self, signal): return self.signal_user(signal)


def _adapter(application=None, profile=AgentProfile.STANDARD):
    application = application or ApplicationSpy()
    return McpSemanticAdapter(AgentApplicationService(application, AgentAccess.for_profile(profile))), application


def test_server_registers_exactly_the_stage_one_semantic_tools_without_runtime_creation():
    adapter, _ = _adapter()
    server = create_mcp_server(adapter._agent_service)
    tools = asyncio.run(server.list_tools())

    assert {tool.name for tool in tools} == MCP_TOOL_NAMES
    assert len(MCP_TOOL_NAMES) == 10
    source = inspect.getsource(__import__("robot.mcp.server", fromlist=["server"]))
    assert all(forbidden not in source for forbidden in (
        "robot.hardware", "serial_controller", "LEDRingController", "robot.ui.renderer", "robot.vision.provider",
    ))
    expression = next(tool for tool in tools if tool.name == "phos_set_expression")
    assert expression.inputSchema["properties"]["expression"]["enum"] == [
        "neutral", "happy", "curious", "surprised", "sad", "angry", "focused", "tired", "confused",
        "scared", "skeptical",
    ]


@pytest.mark.parametrize(("method", "expected"), [
    ("get_status", ("status",)),
    ("get_physical_context", ("get_physical_context",)),
    ("get_presence", ("presence",)),
    ("get_attention", ("attention",)),
    ("get_observed_expression", ("observed_expression",)),
])
def test_read_tools_preserve_agent_results(method, expected):
    adapter, application = _adapter()
    result = getattr(adapter, method)()
    if method in {"get_status", "get_attention"}:
        assert result["state"] == "idle"
    elif method == "get_observed_expression":
        assert result["available"] is None
    elif method == "get_presence":
        assert result["state"] is None
    else:
        assert result["operation"] == expected[0]
    assert application.calls == [expected]


@pytest.mark.parametrize(("method", "arguments", "expected"), [
    ("set_expression", ("happy",), ("set_expression", "happy")),
    ("speak", ("Hello",), ("speak", "Hello")),
    ("sleep", (), ("set_state", "sleeping")),
    ("wake", (), ("set_state", "idle")),
    ("signal_user", ("warning",), ("signal_user", "warning")),
])
def test_action_tools_delegate_only_through_agent_service(method, arguments, expected):
    adapter, application = _adapter()
    result = getattr(adapter, method)(*arguments)
    assert result["applied"] is True if method in {"set_expression", "sleep", "wake", "signal_user"} else result["accepted"] is True
    assert application.calls == [expected]


@pytest.mark.parametrize(("method", "arguments", "operation", "permission"), [
    ("set_expression", ("happy",), "set_expression", "control_expression"),
    ("speak", ("Hello",), "speak", "control_voice"),
    ("sleep", (), "sleep", "control_sleep"),
    ("wake", (), "wake", "control_sleep"),
    ("signal_user", ("warning",), "signal_user", "signal_user"),
])
def test_observer_reads_but_denied_actions_never_reach_the_application(method, arguments, operation, permission):
    adapter, application = _adapter(profile=AgentProfile.OBSERVER)
    assert adapter.get_physical_context()["operation"] == "get_physical_context"

    with pytest.raises(McpToolError, match=f"permission_denied: operation={operation} required_permission={permission}"):
        getattr(adapter, method)(*arguments)
    assert application.calls == [("get_physical_context",)]


def test_adapter_maps_semantic_errors_without_tracebacks_or_provider_details():
    class FailingApplication(ApplicationSpy):
        def set_expression(self, expression):
            raise ApplicationError("invalid_expression", "Unsupported semantic expression.")

        def speak(self, text):
            raise ApplicationError("tts_unavailable", "Text-to-speech is currently unavailable.", status=503)

        def signal_user(self, signal):
            raise RuntimeError("provider secret traceback")

    adapter, _ = _adapter(FailingApplication())
    with pytest.raises(McpToolError, match="invalid_argument: Unsupported semantic expression"):
        adapter.set_expression("not-real")
    with pytest.raises(McpToolError, match="service_unavailable: Text-to-speech"):
        adapter.speak("Hello")
    with pytest.raises(McpToolError) as error:
        adapter.signal_user("warning")
    assert str(error.value) == "internal_error: PHOS could not complete the requested operation."

    class UnsupportedApplication(ApplicationSpy):
        def set_expression(self, expression):
            raise ApplicationError("unsupported_expression_command", "Expression is not writable through this command.")

    unsupported, _ = _adapter(UnsupportedApplication())
    with pytest.raises(McpToolError, match="unsupported_capability: Expression is not writable"):
        unsupported.set_expression("happy")


def test_mcp_server_returns_structured_results_and_schema_rejects_bad_speech_input():
    adapter, _ = _adapter()
    server = create_mcp_server(adapter._agent_service)

    async def call():
        _, result = await server.call_tool("phos_get_physical_context", {})
        with pytest.raises(Exception):
            await server.call_tool("phos_speak", {"text": ""})
        return result

    assert asyncio.run(call())["operation"] == "get_physical_context"


def test_streamable_http_server_configuration_is_kept_outside_the_semantic_surface():
    adapter, _ = _adapter()
    server = create_mcp_server(adapter._agent_service, host="127.0.0.1", port=8123, path="/robot-mcp")

    assert server.settings.host == "127.0.0.1"
    assert server.settings.port == 8123
    assert server.settings.streamable_http_path == "/robot-mcp"


def test_mcp_status_uses_the_sanitized_agent_status_contract():
    adapter, _ = _adapter()

    status = adapter.get_status()

    assert set(status) == {"running", "state", "availability", "degraded", "degraded_reasons"}
    assert status["availability"]["physical_context"] is True


def test_mcp_sleep_and_wake_use_the_runtime_loop_without_self_blocking():
    class Runtime:
        def __init__(self):
            self.core = RobotCore()
            self._behavior_engine = BehaviorEngine(self.core.events)

    async def exercise():
        runtime = Runtime()
        runtime._loop = asyncio.get_running_loop()
        agent = AgentApplicationService(PhosApplicationService(runtime), AgentAccess.for_profile())
        adapter = McpSemanticAdapter(agent)
        assert await adapter.sleep_async() == {"state": "sleeping", "applied": True}
        assert await adapter.wake_async() == {"state": "idle", "applied": True}

    asyncio.run(exercise())
