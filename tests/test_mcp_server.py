import asyncio
import inspect
import json

import pytest

pytest.importorskip("mcp")

from robot.mcp.server import (
    MCP_RESOURCE_URIS,
    MCP_TOOL_NAMES,
    McpSemanticAdapter,
    McpSemanticEventBridge,
    McpToolError,
    create_mcp_server,
)
from robot.core.behavior_engine import BehaviorEngine
from robot.core.runtime import RobotCore
from robot.services import AgentAccess, AgentApplicationService, ApplicationError, AgentProfile
from robot.services.application import PhosApplicationService
from robot.services.semantic_events import SemanticEventPublisher


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
    def get_physical_context(self):
        result = self._call("get_physical_context")
        return {**result, "environment": {"operation": "get_physical_context"}}
    def presence(self): return self._call("presence")
    def attention(self): return self._call("attention")
    def observed_expression(self): return self._call("observed_expression")
    def get_camera_snapshot(self):
        self._call("get_camera_snapshot")
        return b"\xff\xd8fake-jpeg\xff\xd9"
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
    assert expression.input_schema["properties"]["expression"]["enum"] == [
        "neutral", "happy", "curious", "surprised", "sad", "angry", "focused", "tired", "confused",
        "scared", "skeptical",
    ]


def test_server_lists_semantic_resources_and_reads_a_binary_camera_snapshot():
    adapter, _ = _adapter()
    server = create_mcp_server(adapter._agent_service)

    async def read():
        resources = await server.list_resources()
        status = await server.read_resource("phos://status")
        physical_context = await server.read_resource("phos://physical-context")
        environment = await server.read_resource("phos://environment")
        snapshot = await server.read_resource("phos://vision/snapshot")
        return resources, status, physical_context, environment, snapshot

    resources, status, physical_context, environment, snapshot = asyncio.run(read())
    assert {resource.uri for resource in resources} == MCP_RESOURCE_URIS
    assert next(resource for resource in resources if resource.uri == "phos://vision/snapshot").mime_type == "image/jpeg"
    assert all(resource.mime_type == "application/json" for resource in resources if resource.uri != "phos://vision/snapshot")
    assert json.loads(status[0].content)["running"] is True
    assert json.loads(physical_context[0].content)["operation"] == "get_physical_context"
    assert json.loads(environment[0].content)["operation"] == "get_physical_context"
    assert snapshot[0].content == b"\xff\xd8fake-jpeg\xff\xd9"
    assert snapshot[0].mime_type == "image/jpeg"


def test_camera_snapshot_uses_dedicated_permission_and_observer_is_denied():
    adapter, application = _adapter(profile=AgentProfile.OBSERVER)
    with pytest.raises(McpToolError, match="permission_denied: operation=camera_snapshot required_permission=read_camera_snapshot"):
        adapter.camera_snapshot()
    assert application.calls == []

    standard, application = _adapter()
    assert standard.camera_snapshot().startswith(b"\xff\xd8")
    assert application.calls == [("get_camera_snapshot",)]


def test_camera_snapshot_unavailable_is_sanitized():
    class Unavailable(ApplicationSpy):
        def get_camera_snapshot(self):
            raise ApplicationError("camera_frame_unavailable", "No camera frame is available yet.", status=503)
    adapter, _ = _adapter(Unavailable())
    with pytest.raises(McpToolError, match="service_unavailable: No camera frame is available yet"):
        adapter.camera_snapshot()


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
        result = await server.call_tool("phos_get_physical_context", {})
        with pytest.raises(Exception):
            await server.call_tool("phos_speak", {"text": ""})
        return result

    assert asyncio.run(call()).structured_content["operation"] == "get_physical_context"


def test_streamable_http_server_configuration_is_kept_outside_the_semantic_surface():
    adapter, _ = _adapter()
    server = create_mcp_server(adapter._agent_service)
    app = server.streamable_http_app(host="127.0.0.1", streamable_http_path="/robot-mcp")

    assert [route.path for route in app.routes] == ["/robot-mcp"]


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


def test_semantic_events_publish_only_mapped_resource_update_cues_and_close_cleanly():
    class SemanticApplication(ApplicationSpy):
        def __init__(self):
            super().__init__()
            self.events = SemanticEventPublisher(deduplication_window_seconds=0)

        def subscribe_semantic_events(self, listener, *, event_types=None):
            return self.events.subscribe(listener, event_types=event_types)

    async def exercise():
        application = SemanticApplication()
        agent = AgentApplicationService(application, AgentAccess.for_profile())
        bridge = McpSemanticEventBridge(agent)
        received = []
        bridge.bus.subscribe(received.append)
        bridge.start(asyncio.get_running_loop())

        application.events.publish("robot_state_changed", "robot", {"current": "sleeping"})
        application.events.publish("environment_condition_changed", "environment", {"current": "warm"})
        application.events.publish("air_quality_condition_changed", "air_quality", {"current": "poor"})
        application.events.publish("motion_state_changed", "motion", {"current": "moving"})
        application.events.publish("presence_changed", "presence", {"current": "present"})
        application.events.publish("attention_changed", "attention", {"current": "engaged"})
        application.events.publish("observed_expression_changed", "vision", {"current": "happy"})
        application.events.publish("capability_changed", "system", {"current": True})
        application.events.publish("system_health_changed", "system", {"current": "healthy"})
        application.events.publish("environment_availability_changed", "environment", {"current": "stale"})
        application.events.publish("air_quality_availability_changed", "air_quality", {"current": "unavailable"})
        application.events.publish("motion_availability_changed", "motion", {"current": "available"})
        await asyncio.sleep(.05)

        assert {event.uri for event in received} == {
            "phos://status", "phos://physical-context", "phos://environment",
        }
        assert len(received) == 3
        assert "phos://vision/snapshot" not in {event.uri for event in received}
        bridge.close()
        assert application.events.active_subscription_count == 0

    asyncio.run(exercise())


def test_semantic_event_bridge_coalesces_repeated_uri_updates_without_blocking_publishers():
    class SemanticApplication(ApplicationSpy):
        def __init__(self):
            super().__init__()
            self.events = SemanticEventPublisher(deduplication_window_seconds=0)

        def subscribe_semantic_events(self, listener, *, event_types=None):
            return self.events.subscribe(listener, event_types=event_types)

    async def exercise():
        application = SemanticApplication()
        bridge = McpSemanticEventBridge(AgentApplicationService(application, AgentAccess.for_profile()))
        received = []
        bridge.bus.subscribe(received.append)
        bridge.start(asyncio.get_running_loop())
        for state in ("sleeping", "idle", "thinking"):
            application.events.publish("robot_state_changed", "robot", {"current": state})
        await asyncio.sleep(.05)
        assert {event.uri for event in received} == {"phos://status", "phos://physical-context"}
        assert len(received) == 2
        bridge.close()

    asyncio.run(exercise())
