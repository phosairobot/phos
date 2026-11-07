import asyncio
import inspect
import json
from pathlib import Path

import pytest
import yaml

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
from robot.config import ConfigRepository, RuntimeConfig, load_document
from robot.secrets import SecretsService
from robot.services import (AgentAccess, AgentApplicationService, AgentCredentialService, AgentIdentity,
                            AgentManagementService, AgentRegistry, AgentRegistryProvider,
                            BearerAgentAuthenticator, ApplicationError, AgentProfile)
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


def test_mcp_middleware_authenticates_once_for_each_tool_or_resource_request():
    class Authenticator:
        def __init__(self):
            self.contexts = []

        async def authenticate(self, context):
            self.contexts.append(context)
            return AgentIdentity("observer-agent", AgentProfile.OBSERVER)

    adapter, _ = _adapter()
    authenticator = Authenticator()
    server = create_mcp_server(adapter._agent_service, authenticator=authenticator)
    middleware = server.middleware[-1]

    async def exercise():
        resource_context, tool_context = object(), object()
        resource = await middleware(resource_context, lambda _: server.read_resource("phos://status"))
        tool = await middleware(tool_context, lambda _: server.call_tool("phos_get_status", {}))
        return resource, tool, resource_context, tool_context

    resource, tool, resource_context, tool_context = asyncio.run(exercise())

    assert json.loads(resource[0].content)["running"] is True
    assert tool.structured_content["running"] is True
    assert authenticator.contexts == [resource_context, tool_context]


def test_bearer_middleware_isolates_profiles_and_sanitizes_failed_authentication():
    class Secrets:
        def get_secret(self, reference): return {"mcp.standard.token": "super-secret-a", "mcp.observer.token": "super-secret-b"}.get(reference)
    registry = AgentRegistry("standard", (AgentIdentity("standard", "standard"), AgentIdentity("observer", "observer")),
                             {"standard": "mcp.standard.token", "observer": "mcp.observer.token"})
    adapter, application = _adapter()
    server = create_mcp_server(adapter._agent_service, authenticator=BearerAgentAuthenticator(registry, Secrets()))
    middleware = server.middleware[-1]
    def context(token): return type("Context", (), {"request": type("Request", (), {"headers": {"Authorization": token}})()})()
    async def exercise():
        allowed = await middleware(context("Bearer super-secret-a"), lambda _: server.call_tool("phos_sleep", {}))
        with pytest.raises(Exception, match="permission_denied"):
            await middleware(context("Bearer super-secret-b"), lambda _: server.call_tool("phos_sleep", {}))
        with pytest.raises(PermissionError, match="^authentication_failed$") as error:
            await middleware(context("Bearer invalid"), lambda _: server.call_tool("phos_sleep", {}))
        return allowed, str(error.value)
    allowed, error = asyncio.run(exercise())
    assert allowed.is_error is False
    assert error == "authentication_failed" and "super-secret" not in error
    assert application.calls == [("set_state", "sleeping")]


def test_mcp_activity_is_recorded_after_authorization_not_authentication_or_keepalive():
    standard = AgentIdentity("standard", "standard")
    observer = AgentIdentity("observer", "observer")

    class Authenticator:
        async def authenticate(self, context):
            token = context.request.headers.get("Authorization")
            if token == "Bearer standard": return standard
            if token == "Bearer observer": return observer
            raise PermissionError("authentication_failed")

    class Tracker:
        def __init__(self): self.identities = []
        def record(self, identity): self.identities.append(identity); return True

    tracker = Tracker()
    adapter, _ = _adapter()
    server = create_mcp_server(adapter._agent_service, authenticator=Authenticator(), activity_tracker=tracker)
    middleware = server.middleware[-1]
    def context(token):
        return type("Context", (), {"request": type("Request", (), {"headers": {"Authorization": token}})()})()

    async def exercise():
        # Authentication alone (the transport's initialization path) has no
        # semantic adapter call and therefore does not renew a lease.
        await Authenticator().authenticate(context("Bearer standard"))
        await middleware(context("Bearer standard"), lambda _: server.call_tool("phos_get_status", {}))
        with pytest.raises(Exception, match="permission_denied"):
            await middleware(context("Bearer observer"), lambda _: server.call_tool("phos_sleep", {}))
        with pytest.raises(PermissionError, match="authentication_failed"):
            await middleware(context("Bearer invalid"), lambda _: server.call_tool("phos_get_status", {}))

    asyncio.run(exercise())
    assert tracker.identities == [standard]


def test_revoked_bearer_cannot_renew_mcp_activity(tmp_path):
    registry = AgentRegistry("standard", (AgentIdentity("standard", "standard"),),
                             {"standard": "mcp.standard.token"})
    secrets = SecretsService(tmp_path / ".secrets")
    issued = AgentCredentialService(registry, secrets).create("standard")

    class Tracker:
        def __init__(self): self.identities = []
        def record(self, identity): self.identities.append(identity); return True

    tracker = Tracker()
    adapter, _ = _adapter()
    server = create_mcp_server(adapter._agent_service,
                               authenticator=BearerAgentAuthenticator(registry, secrets),
                               activity_tracker=tracker)
    middleware = server.middleware[-1]
    context = lambda: type("Context", (), {"request": type("Request", (), {
        "headers": {"Authorization": f"Bearer {issued.token}"}})()})()

    async def exercise():
        await middleware(context(), lambda _: server.call_tool("phos_get_status", {}))
        AgentCredentialService(registry, secrets).revoke("standard")
        with pytest.raises(PermissionError, match="authentication_failed"):
            await middleware(context(), lambda _: server.call_tool("phos_get_status", {}))

    asyncio.run(exercise())
    assert [identity.id for identity in tracker.identities] == ["standard"]


def test_streamable_http_bearer_authentication_uses_real_asgi_request_path(tmp_path, caplog):
    from mcp.client import ClientSession
    from mcp.client.streamable_http import httpx2 as httpx, streamable_http_client
    from mcp.shared.exceptions import MCPError

    document = load_document()
    document["agents"] = {"default_agent": "local-agent", "entries": [
        {"id": "local-agent", "display_name": "Local MCP agent", "permission_profile": "standard"},
        {"id": "stage3-standard", "display_name": "Stage 3 Standard Agent", "permission_profile": "standard",
         "credential": {"type": "bearer", "secret_ref": "mcp.stage3_standard.token"}},
        {"id": "stage3-observer", "display_name": "Stage 3 Observer Agent", "permission_profile": "observer",
         "credential": {"type": "bearer", "secret_ref": "mcp.stage3_observer.token"}},
    ]}
    config_path = tmp_path / "phos.yaml"
    config_path.write_text(yaml.safe_dump(document, default_flow_style=False, sort_keys=False))
    config = RuntimeConfig.from_file(config_path)
    registry = config.agent_registry
    store = SecretsService(config.resolve_path(Path(".")) / ".phos-secrets")
    standard_token = "STAGE3_TOKEN_MUST_NOT_LEAK_91A7"
    observer_token = "HTTP_OBSERVER_TOKEN_MUST_NOT_LEAK_4C2B"
    store.set_secret("mcp.stage3_standard.token", standard_token)
    store.set_secret("mcp.stage3_observer.token", observer_token)
    adapter, application = _adapter()
    authenticator = BearerAgentAuthenticator(registry, store)
    seen_request = {}
    original_authenticate = authenticator.authenticate

    async def authenticate(context):
        request = context.request
        headers = request.headers
        seen_request.update(request_type=type(request).__name__, headers_type=type(headers).__name__,
                            authorization_present=isinstance(headers.get("authorization"), str))
        return await original_authenticate(context)

    authenticator.authenticate = authenticate
    server = create_mcp_server(adapter._agent_service, authenticator=authenticator)
    app = server.streamable_http_app(host="127.0.0.1", streamable_http_path="/mcp")
    async def session_for(authorization):
        headers = {} if authorization is None else {"Authorization": authorization}
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8001", headers=headers)
        async with streamable_http_client("http://127.0.0.1:8001/mcp", http_client=client) as streams:
            async with ClientSession(*streams) as session:
                await session.initialize()
                yield session

    async def rejected(authorization):
        try:
            async for _ in session_for(authorization):
                pass
        except BaseExceptionGroup as error:
            return repr(error)
        raise AssertionError("unauthenticated MCP initialization unexpectedly succeeded")

    async def exercise():
        async with server.session_manager.run():
            async for standard in session_for(f"Bearer {standard_token}"):
                allowed_first = await standard.call_tool("phos_sleep")
                snapshot = await standard.read_resource("phos://vision/snapshot")
                async for observer in session_for(f"Bearer {observer_token}"):
                    denied = await observer.call_tool("phos_sleep")
                    with pytest.raises(MCPError) as denied_camera:
                        await observer.read_resource("phos://vision/snapshot")
                allowed_second = await standard.call_tool("phos_sleep")
            failures = [await rejected(value) for value in (None, "Bearer invalid", "Bearer", "Basic invalid")]
            return allowed_first, allowed_second, denied, snapshot, repr(denied_camera.value), failures
    allowed_first, allowed_second, denied, snapshot, denied_camera, failures = asyncio.run(exercise())
    assert registry.default_agent == "local-agent"
    assert registry.credential_refs == {"stage3-standard": "mcp.stage3_standard.token",
                                        "stage3-observer": "mcp.stage3_observer.token"}
    assert allowed_first.is_error is False and allowed_second.is_error is False
    assert denied.is_error is True and "permission_denied" in denied.content[0].text
    assert snapshot.contents[0].mime_type == "image/jpeg" and snapshot.contents[0].blob
    assert "permission_denied" in denied_camera
    assert all("authentication_failed" in failure and standard_token not in failure and observer_token not in failure
               for failure in failures)
    assert seen_request == {"request_type": "Request", "headers_type": "Headers", "authorization_present": True}
    assert standard_token not in caplog.text and observer_token not in caplog.text
    assert application.calls == [("set_state", "sleeping"), ("get_camera_snapshot",), ("set_state", "sleeping")]


def test_streamable_http_dynamic_agent_crud_updates_authentication_without_restart(tmp_path):
    from mcp.client import ClientSession
    from mcp.client.streamable_http import httpx2 as httpx, streamable_http_client

    document = load_document()
    document["agents"] = {"default_agent": "local-agent", "entries": [
        {"id": "local-agent", "display_name": "Local MCP agent", "permission_profile": "standard",
         "credential": {"type": "bearer", "secret_ref": "mcp.agents.local_agent.token"}},
    ]}
    path = tmp_path / "phos.yaml"
    path.write_text(yaml.safe_dump(document, default_flow_style=False, sort_keys=False))
    repository = ConfigRepository(path)
    config = repository.load()
    before_fps = config.display_fps
    provider = AgentRegistryProvider(config.agent_registry)
    store = SecretsService(tmp_path / ".phos-secrets")
    credentials = AgentCredentialService(provider, store)
    management = AgentManagementService(repository, provider, credentials)
    application = ApplicationSpy()
    authenticator = BearerAgentAuthenticator(provider, store)
    authenticated = []
    original_authenticate = authenticator.authenticate

    async def authenticate(context):
        identity = await original_authenticate(context)
        authenticated.append((identity.id, identity.permission_profile.value))
        return identity

    authenticator.authenticate = authenticate
    server = create_mcp_server(AgentApplicationService(application, config.agent_registry.default_identity),
                               authenticator=authenticator)
    app = server.streamable_http_app(host="127.0.0.1", streamable_http_path="/mcp")

    async def session_for(token):
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8001",
                                 headers={"Authorization": f"Bearer {token}"})
        async with streamable_http_client("http://127.0.0.1:8001/mcp", http_client=client) as streams:
            async with ClientSession(*streams) as session:
                await session.initialize()
                yield session

    async def rejected(token):
        try:
            async for _ in session_for(token):
                pass
        except BaseExceptionGroup as error:
            return repr(error)
        raise AssertionError("deleted bearer unexpectedly initialized an MCP session")

    async def exercise(token):
        async with server.session_manager.run():
            async for observer in session_for(token):
                allowed_read = await observer.call_tool("phos_get_status")
                denied_sleep = await observer.call_tool("phos_sleep")
            management.update_agent("live-observer", "Live Observer", "standard")
            async for standard in session_for(token):
                allowed_sleep = await standard.call_tool("phos_sleep")
                await standard.call_tool("phos_wake")
            management.delete_agent("live-observer")
            failure = await rejected(token)
            return allowed_read, denied_sleep, allowed_sleep, failure

    management.create_agent("live-observer", "Live Observer", "observer")
    created = repository.load()
    assert created.agent_registry.get("live-observer").permission_profile.value == "observer"
    assert created.display_fps == before_fps
    issued = management.create_credential("live-observer")
    reference = issued.status.secret_ref
    allowed_read, denied_sleep, allowed_sleep, failure = asyncio.run(exercise(issued.token))

    assert allowed_read.is_error is False
    assert denied_sleep.is_error is True and "permission_denied" in denied_sleep.content[0].text
    assert allowed_sleep.is_error is False
    assert ("live-observer", "observer") in authenticated
    assert ("live-observer", "standard") in authenticated
    assert "authentication_failed" in failure
    assert store.has_secret(reference) is False
    reloaded = repository.load()
    assert reloaded.agent_registry.get("live-observer") is None
    assert reloaded.display_fps == before_fps
    assert application.calls == [("status",), ("set_state", "sleeping"), ("set_state", "idle")]


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
