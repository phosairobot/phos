"""Streamable HTTP MCP adapter over the permission-checked semantic boundary."""
from __future__ import annotations

import asyncio
import base64
from contextvars import ContextVar
import ipaddress
import logging
from threading import RLock
import time
from typing import Annotated, Any, Callable

from pydantic import ConfigDict, Field, StrictInt

from robot.services import (AgentApplicationService, AgentPermissionDenied, ApplicationError, SemanticEvent,
                            USER_SIGNAL_INTENTS, AgentIdentity, AgentOperation, LocalAgentAuthenticator)
from robot.services.application import COMMANDS

logger = logging.getLogger(__name__)
_REQUEST_IDENTITY: ContextVar[AgentIdentity | None] = ContextVar("mcp_request_identity", default=None)
_REQUEST_WITHOUT_AUTHORIZATION: ContextVar[bool] = ContextVar("mcp_request_without_authorization", default=False)


MCP_TOOL_NAMES = frozenset({
    "phos_get_status", "phos_get_physical_context", "phos_get_presence", "phos_get_attention",
    "phos_get_observed_expression", "phos_get_camera_snapshot", "phos_capture_camera_snapshot", "phos_set_expression", "phos_speak", "phos_sleep",
    "phos_wake", "phos_signal_user", "phos_query_measurements", "phos_list_physical_transitions", "phos_get_visible_objects", "phos_set_state", "phos_get_physical_assessment",
})
MCP_RESOURCE_URIS = frozenset({"phos://status", "phos://physical-context", "phos://physical-context/assessment", "phos://environment", "phos://vision/snapshot"})
PHYSICAL_CONTEXT_TOOL_DESCRIPTION = "Get PHOS's current semantic physical context, including available environmental, air-quality, motion, presence, and attention information. Use this tool to answer questions about what PHOS currently perceives. This tool does not establish historical continuity, measurement trends, or sufficient temporal evidence for longitudinal analysis. For evidence quality, use phos_get_physical_assessment."
PHYSICAL_ASSESSMENT_TOOL_DESCRIPTION = "Assess the temporal quality of PHOS's retained physical measurements, including source availability, freshness, sample coverage, observed discontinuities, and post-recovery evidence. Use this tool before interpreting changes over time or determining whether measurements collected after a sensor interruption are suitable for analysis. This tool does not return a current environmental reading as its primary purpose, calculate rising/falling/stable trends, or guarantee physical sensor accuracy. For current perceived conditions, use phos_get_physical_context."
_SNAPSHOT_TOOL_MIN_INTERVAL_SECONDS = 1.0
_SNAPSHOT_TOOL_MAX_IDENTITIES = 64

_EVENT_RESOURCE_URIS = {
    "robot_state_changed": frozenset({"phos://status", "phos://physical-context"}),
    "environment_condition_changed": frozenset({"phos://environment", "phos://physical-context", "phos://physical-context/assessment"}),
    "air_quality_condition_changed": frozenset({"phos://physical-context", "phos://physical-context/assessment"}),
    "motion_state_changed": frozenset({"phos://physical-context"}),
    "presence_changed": frozenset({"phos://physical-context"}),
    "attention_changed": frozenset({"phos://physical-context"}),
    "observed_expression_changed": frozenset({"phos://physical-context"}),
    "capability_changed": frozenset({"phos://status"}),
    "system_health_changed": frozenset({"phos://status"}),
    "environment_availability_changed": frozenset({"phos://environment", "phos://physical-context", "phos://physical-context/assessment"}),
    "air_quality_availability_changed": frozenset({"phos://physical-context", "phos://physical-context/assessment"}),
    "motion_availability_changed": frozenset({"phos://physical-context"}),
}

Expression = Annotated[str, Field(min_length=1, description="Supported semantic expression to display; use phos_set_state for a temporary agent thinking visual.", json_schema_extra={
    "enum": COMMANDS["set_expression"].allowed_values,
})]
SpeechText = Annotated[str, Field(min_length=1, max_length=500,
                                  description="Text for one PHOS speech request (1–500 characters).")]
SignalIntent = Annotated[str, Field(min_length=1,
                                    description="Transient user signal; it does not change robot lifecycle state.", json_schema_extra={
    "enum": sorted(USER_SIGNAL_INTENTS),
})]
VisualState = Annotated[str, Field(description="thinking creates a temporary caller-owned visual lease; idle releases that caller's lease.",
                                   json_schema_extra={"enum": ["thinking", "idle"]})]
# Bounds are state-dependent: MCP validates an integer here, while the shared
# manager applies 250..60000 only when the request is ``thinking``.  For
# ``idle``, any supplied integer is compatibility input and is discarded.
VisualStateDuration = Annotated[StrictInt, Field(description="Required for thinking: 250–60000 ms. Optional and ignored for idle releases.")]
HistoryMetric = Annotated[str, Field(description=(
    "Open metric identifier. PHOS currently produces temperature_c, pressure_hpa, humidity_percent, "
    "eco2_ppm, tvoc_ppb, and aqi_uba; unknown identifiers return no samples."))]
HistoryTimestamp = Annotated[str, Field(description=(
    "Optional timezone-aware RFC 3339 timestamp. The start/end window must not exceed six hours; "
    "history is RAM-only and uses PHOS receipt-time observations."))]
HistoryLimit = Annotated[int, Field(description="Maximum returned records, from 1 through 360 (default 360).",
                                    json_schema_extra={"minimum": 1, "maximum": 360})]
TransitionEventType = Annotated[str, Field(description=(
    "Optional open-string exact event-type filter, for example presence_changed, attention_changed, "
    "robot_state_changed, environment_availability_changed, or air_quality_availability_changed."))]
TransitionLimit = Annotated[int, Field(description="Maximum returned transitions, from 1 through 360 (default 100).",
                                       json_schema_extra={"minimum": 1, "maximum": 360})]


class McpToolError(Exception):
    """Sanitized semantic error returned as an MCP tool failure."""


class _SnapshotToolRateLimiter:
    """Bound snapshot transfer work per authenticated identity without workers."""

    def __init__(self, *, clock=time.monotonic) -> None:
        self._clock = clock
        self._last_request: dict[str, float] = {}
        self._lock = RLock()

    def require_allowed(self, identity: str) -> None:
        now = self._clock()
        with self._lock:
            previous = self._last_request.get(identity)
            if previous is not None and now - previous < _SNAPSHOT_TOOL_MIN_INTERVAL_SECONDS:
                raise McpToolError("rate_limited: Camera snapshots are limited to one request per second.")
            if identity not in self._last_request and len(self._last_request) >= _SNAPSHOT_TOOL_MAX_IDENTITIES:
                oldest = min(self._last_request, key=self._last_request.__getitem__)
                del self._last_request[oldest]
            self._last_request[identity] = now


def _snapshot_request_identity(agent_service: AgentApplicationService) -> str:
    """Use the authenticated principal when available, never a credential."""
    identity = _REQUEST_IDENTITY.get() or getattr(agent_service, "_identity", None)
    return getattr(identity, "id", None) or "trusted-local"


class McpSemanticEventBridge:
    """Bounded thread-safe bridge from PHOS events to MCP change cues."""

    def __init__(self, agent_service: AgentApplicationService) -> None:
        from mcp.server.subscriptions import InMemorySubscriptionBus

        self.bus = InMemorySubscriptionBus()
        self._agent_service = agent_service
        self._loop = self._subscription = None
        self._pending: set[str] = set()
        self._draining = self._closed = False
        self._lock = RLock()

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        """Attach once after the server loop exists; safe to call repeatedly."""
        with self._lock:
            if self._closed or self._subscription is not None:
                return
            self._loop = loop
            self._subscription = self._agent_service._subscribe_semantic_events(self._on_semantic_event)

    def _on_semantic_event(self, event: SemanticEvent) -> None:
        uris = _EVENT_RESOURCE_URIS.get(event.type)
        if not uris:
            return
        with self._lock:
            if self._closed or self._loop is None:
                return
            self._pending.update(uris)
            if self._draining:
                return
            self._draining = True
            self._loop.call_soon_threadsafe(self._start_drain)

    def _start_drain(self) -> None:
        asyncio.create_task(self._drain(), name="mcp-semantic-resource-updates")

    async def _drain(self) -> None:
        from mcp.shared.subscriptions import ResourceUpdated

        while True:
            with self._lock:
                pending = tuple(sorted(self._pending))
                self._pending.clear()
                closed = self._closed
            if closed or not pending:
                with self._lock:
                    if self._pending and not self._closed:
                        continue
                    self._draining = False
                return
            for uri in pending:
                await self.bus.publish(ResourceUpdated(uri=uri))

    def close(self) -> None:
        """Release the PHOS observer; SDK owns client-stream teardown."""
        with self._lock:
            self._closed = True
            self._pending.clear()
            subscription, self._subscription = self._subscription, None
        if subscription is not None:
            subscription.close()


class McpSemanticAdapter:
    """Maps Stage 1 tools to an existing ``AgentApplicationService`` only."""

    def __init__(self, agent_service: AgentApplicationService, *, authenticator=None, activity_tracker=None) -> None:
        self._agent_service = agent_service
        self._authenticator = authenticator or LocalAgentAuthenticator()
        self._activity_tracker = activity_tracker

    def _record_accepted_activity(self) -> None:
        """Refresh only a trusted identity after authorization accepted work."""
        identity = _REQUEST_IDENTITY.get()
        if identity is not None and self._activity_tracker is not None:
            is_anonymous = getattr(self._authenticator, "is_anonymous_identity", lambda _: False)
            # A supplied bearer must never become an appearance-eligible
            # anonymous request, even under an authentication mode that treats
            # it as an anonymous principal for compatibility.
            if is_anonymous(identity) and _REQUEST_WITHOUT_AUTHORIZATION.get():
                self._activity_tracker.record_anonymous_mcp()
            else:
                self._activity_tracker.record(identity)

    def _request_agent_service(self) -> AgentApplicationService:
        """Bind the facade to the identity resolved once by MCP middleware."""
        identity = _REQUEST_IDENTITY.get()
        if identity is None:
            return self._agent_service
        return AgentApplicationService(self._agent_service._application, identity)

    def _call(self, operation: str, handler: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        try:
            result = handler()
        except AgentPermissionDenied as error:
            raise McpToolError(
                f"permission_denied: operation={error.operation.value} required_permission={error.permission.value}"
            ) from None
        except ApplicationError as error:
            # Authorization already passed; a domain rejection still represents
            # an accepted MCP interaction, not a transport keepalive.
            self._record_accepted_activity()
            raise McpToolError(_application_error_message(error)) from None
        except Exception:
            raise McpToolError("internal_error: PHOS could not complete the requested operation.") from None
        self._record_accepted_activity()
        return result

    async def _call_async(self, handler) -> dict[str, Any]:
        try:
            result = await handler()
        except AgentPermissionDenied as error:
            raise McpToolError(
                f"permission_denied: operation={error.operation.value} required_permission={error.permission.value}"
            ) from None
        except ApplicationError as error:
            self._record_accepted_activity()
            raise McpToolError(_application_error_message(error)) from None
        except Exception:
            raise McpToolError("internal_error: PHOS could not complete the requested operation.") from None
        self._record_accepted_activity()
        return result

    def get_status(self) -> dict[str, Any]:
        return self._call("phos_get_status", self._request_agent_service().status)

    def get_physical_context(self) -> dict[str, Any]:
        return self._call("phos_get_physical_context", self._request_agent_service().get_physical_context)

    def get_physical_assessment(self) -> dict[str, Any]:
        return self._call("phos_get_physical_assessment", self._request_agent_service().get_physical_assessment)

    def physical_assessment(self) -> dict[str, Any]:
        """Compatibility path for the assessment Resource."""
        return self.get_physical_assessment()

    def get_presence(self) -> dict[str, Any]:
        return self._call("phos_get_presence", self._request_agent_service().presence)

    def get_attention(self) -> dict[str, Any]:
        return self._call("phos_get_attention", self._request_agent_service().attention)

    def get_observed_expression(self) -> dict[str, Any]:
        return self._call("phos_get_observed_expression", self._request_agent_service().observed_expression)

    async def visible_objects(self) -> dict[str, Any]:
        return await self._call_async(self._request_agent_service().visible_objects)

    def environment(self) -> dict[str, Any]:
        """Return the canonical environmental semantic-context section."""
        return self._call("phos://environment", lambda: self._request_agent_service().get_physical_context()["environment"])

    def camera_snapshot(self) -> bytes:
        return self._call("phos://vision/snapshot", self._request_agent_service().camera_snapshot)

    def camera_snapshot_with_metadata(self):
        return self._call("phos_get_camera_snapshot", self._request_agent_service().camera_snapshot_with_metadata)

    async def capture_camera_snapshot_with_metadata(self):
        return await self._call_async(self._request_agent_service().capture_camera_snapshot_with_metadata)

    def set_expression(self, expression: str) -> dict[str, Any]:
        return self._call("phos_set_expression", lambda: self._request_agent_service().set_expression(expression))

    def set_visual_state(self, state: str, duration_ms: int | None = None) -> dict[str, Any]:
        # An accepted state request is normal agent activity.  In follow-agent
        # mode this is how an authenticated agent's assigned appearance becomes
        # active before subsequent visual frames consume the thinking lease.
        # Anonymous identities remain constrained by the existing low-priority
        # fallback policy in AgentAppearanceActivityTracker.
        return self._call("phos_set_state",
                          lambda: self._request_agent_service().request_visual_state(state, duration_ms))

    async def set_visual_state_async(self, state: str, duration_ms: int | None = None) -> dict[str, Any]:
        service = self._request_agent_service()
        # Validate authorization before activity arbitration, then let existing
        # follow-agent reconciliation queue its appearance before the lease.
        service._require(AgentOperation.SET_VISUAL_STATE)
        self._record_accepted_activity()
        try:
            await service._wait_for_visual_appearance()
            return service.request_visual_state(state, duration_ms)
        except AgentPermissionDenied as error:
            raise McpToolError(
                f"permission_denied: operation={error.operation.value} required_permission={error.permission.value}"
            ) from None
        except ApplicationError as error:
            raise McpToolError(_application_error_message(error)) from None

    def speak(self, text: str) -> dict[str, Any]:
        return self._call("phos_speak", lambda: self._request_agent_service().speak(text))

    def sleep(self) -> dict[str, Any]:
        return self._call("phos_sleep", self._request_agent_service().sleep)

    def wake(self) -> dict[str, Any]:
        return self._call("phos_wake", self._request_agent_service().wake)

    def signal_user(self, signal: str) -> dict[str, Any]:
        return self._call("phos_signal_user", lambda: self._request_agent_service().signal_user(signal))

    def query_measurements(self, metric: str, start: str | None = None, end: str | None = None, limit: int = 360) -> dict[str, Any]:
        return self._call("phos_query_measurements", lambda: self._request_agent_service().query_measurements(metric, start, end, limit))

    def list_physical_transitions(self, event_type: str | None = None, limit: int = 100) -> dict[str, Any]:
        return self._call("phos_list_physical_transitions", lambda: self._request_agent_service().list_physical_transitions(event_type, limit))

    async def speak_async(self, text: str) -> dict[str, Any]:
        return await self._call_async(lambda: self._request_agent_service().speak_async(text))

    async def sleep_async(self) -> dict[str, Any]:
        return await self._call_async(self._request_agent_service().sleep_async)

    async def wake_async(self) -> dict[str, Any]:
        return await self._call_async(self._request_agent_service().wake_async)

    async def signal_user_async(self, signal: str) -> dict[str, Any]:
        return await self._call_async(lambda: self._request_agent_service().signal_user_async(signal))


def _application_error_message(error: ApplicationError) -> str:
    category = "invalid_argument" if error.code.startswith("invalid_") else (
        "unsupported_capability" if error.code.startswith("unsupported_") else (
            "service_unavailable" if error.status == 503 else "runtime_failure" if error.status >= 500
            else "semantic_error"))
    return f"{category}: {error.message}"


def create_mcp_server(agent_service: AgentApplicationService, *, subscriptions=None, authenticator=None,
                      activity_tracker=None):
    """Create a server without constructing or owning a PHOS runtime."""
    try:
        from mcp.server.mcpserver import MCPServer
        from mcp.server.mcpserver.exceptions import ToolError
        from mcp.types import ToolAnnotations
    except ImportError as error:
        raise RuntimeError("MCP support requires the optional 'mcp' dependency on Python 3.11+.") from error

    adapter = McpSemanticAdapter(agent_service, authenticator=authenticator,
                                 activity_tracker=activity_tracker)
    snapshot_limiter = _SnapshotToolRateLimiter()

    async def authenticate_request(request_context, call_next):
        identity = await adapter._authenticator.authenticate(request_context)
        token = _REQUEST_IDENTITY.set(identity)
        headers = getattr(getattr(request_context, "request", None), "headers", None) or {}
        authorization_token = _REQUEST_WITHOUT_AUTHORIZATION.set(
            (headers.get("authorization") or headers.get("Authorization")) is None)
        try:
            return await call_next(request_context)
        finally:
            _REQUEST_WITHOUT_AUTHORIZATION.reset(authorization_token)
            _REQUEST_IDENTITY.reset(token)

    server = MCPServer("PHOS", instructions="Trusted-local semantic PHOS adapter; no hardware controls.",
                       subscriptions=subscriptions, middleware=[authenticate_request])

    passive_read = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True,
                                   openWorldHint=False)
    on_demand_read = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=False,
                                     openWorldHint=False)
    idempotent_control = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True,
                                         openWorldHint=False)
    transient_control = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False,
                                        openWorldHint=False)

    def tool(name: str, description: str, *, annotations=None):
        return server.tool(name=name, description=description, annotations=annotations, structured_output=True)

    def resource(uri: str, name: str, description: str, mime_type="application/json"):
        return server.resource(uri, name=name, description=description, mime_type=mime_type)

    @resource("phos://status", "PHOS semantic status", "Current compact PHOS semantic status.")
    def status_resource() -> dict[str, Any]:
        return _resource_result(adapter.get_status)

    @resource("phos://physical-context", "PHOS semantic physical context",
              "Current provider-independent semantic physical context.")
    def physical_context_resource() -> dict[str, Any]:
        return _resource_result(adapter.get_physical_context)

    @resource("phos://physical-context/assessment", "PHOS physical assessment evidence",
              "Read-only bounded temporal evidence and source availability; no trend classification.")
    def physical_assessment_resource() -> dict[str, Any]:
        return _resource_result(adapter.physical_assessment)

    @resource("phos://environment", "PHOS semantic environment",
              "Current environmental section of PHOS semantic physical context.")
    def environment_resource() -> dict[str, Any]:
        return _resource_result(adapter.environment)

    @resource("phos://vision/snapshot", "PHOS vision snapshot",
              "Current privacy-bounded camera snapshot from PHOS vision.", "image/jpeg")
    def vision_snapshot_resource() -> bytes:
        return _resource_bytes_result(adapter.camera_snapshot)

    @tool("phos_get_status", "Get compact PHOS runtime status and capabilities. Use phos_get_physical_context for current sensed conditions; fields can be unavailable when a subsystem is disabled or unhealthy.",
          annotations=passive_read)
    def get_status() -> dict[str, Any]:
        return _tool_result(adapter.get_status, ToolError)

    @tool("phos_get_physical_context", PHYSICAL_CONTEXT_TOOL_DESCRIPTION, annotations=passive_read)
    def get_physical_context() -> dict[str, Any]:
        return _tool_result(adapter.get_physical_context, ToolError)

    @tool("phos_get_physical_assessment", PHYSICAL_ASSESSMENT_TOOL_DESCRIPTION,
          annotations=passive_read)
    def get_physical_assessment() -> dict[str, Any]:
        return _tool_result(adapter.get_physical_assessment, ToolError)

    @tool("phos_get_presence", "Get PHOS's current semantic presence state without identity. Use phos_get_attention for attention direction or phos_list_physical_transitions for retained presence changes; the result may be unavailable.",
          annotations=passive_read)
    def get_presence() -> dict[str, Any]:
        return _tool_result(adapter.get_presence, ToolError)

    @tool("phos_get_attention", "Get PHOS's current semantic attention state. Use phos_get_presence for occupancy or phos_list_physical_transitions for retained changes; it can be unavailable when perception is inactive.",
          annotations=passive_read)
    def get_attention() -> dict[str, Any]:
        return _tool_result(adapter.get_attention, ToolError)

    @tool("phos_get_observed_expression", "Get the latest observed facial-expression label and confidence, not an emotion claim. Use phos_get_visible_objects for object labels or phos_get_camera_snapshot for an image; vision data may be unavailable or stale.",
          annotations=passive_read)
    def get_observed_expression() -> dict[str, Any]:
        return _tool_result(adapter.get_observed_expression, ToolError)

    @tool("phos_get_visible_objects", "Run one bounded on-demand object observation from PHOS's existing Vision frame and return labels only. Use phos_get_camera_snapshot for an image; this can be busy, time out, or be unavailable and does not track objects.",
          annotations=on_demand_read)
    async def get_visible_objects() -> dict[str, Any]:
        return await _async_tool_result(adapter.visible_objects, ToolError)

    @server.tool(name="phos_get_camera_snapshot",
                 description="Return PHOS's latest cached JPEG camera frame without interpretation. Use phos_capture_camera_snapshot when a post-request frame is needed; cached frames can be stale or unavailable and requests are limited to one per second per identity.",
                 annotations=passive_read,
                 structured_output=False)
    def get_camera_snapshot():
        """Return the existing JPEG as an MCP image block, never as JSON text."""
        snapshot_limiter.require_allowed(_snapshot_request_identity(adapter._agent_service))
        return _snapshot_tool_result(adapter.camera_snapshot_with_metadata, ToolError, source="cached")

    @server.tool(name="phos_capture_camera_snapshot",
                 description="Wait for PHOS's next camera frame and return it as JPEG without interpretation. Use phos_get_camera_snapshot for the latest cached frame; capture can be busy, time out, or be unavailable and requests are limited to one per second per identity.",
                 annotations=on_demand_read,
                 structured_output=False)
    async def capture_camera_snapshot():
        snapshot_limiter.require_allowed(_snapshot_request_identity(adapter._agent_service))
        return await _async_snapshot_tool_result(adapter.capture_camera_snapshot_with_metadata, ToolError,
                                                 source="on_demand")

    @tool("phos_set_expression", "Set PHOS's supported semantic expression. Use phos_set_state for temporary agent thinking visuals; this changes displayed expression but not robot lifecycle state.",
          annotations=idempotent_control)
    def set_expression(expression: Expression) -> dict[str, Any]:
        return _tool_result(lambda: adapter.set_expression(expression), ToolError)

    @tool("phos_set_state", "Request a temporary caller-owned thinking visual lease, or release it with idle. Use phos_set_expression for expression control; thinking requires duration_ms and may be visually suppressed by safety, sleep, speech, or listening states.",
          annotations=transient_control)
    async def set_state(state: VisualState, duration_ms: VisualStateDuration | None = None) -> dict[str, Any]:
        if state not in {"thinking", "idle"}:
            raise ToolError("invalid_argument: state must be thinking or idle.")
        if state == "thinking" and duration_ms is None:
            raise ToolError("invalid_argument: thinking requires duration_ms.")
        if state == "thinking" and not 250 <= duration_ms <= 60_000:
            raise ToolError("invalid_argument: thinking duration_ms must be from 250 to 60000.")
        return await _async_tool_result(lambda: adapter.set_visual_state_async(
            state, duration_ms if state == "thinking" else None), ToolError)

    # MCP's generated function models otherwise ignore unknown object keys.
    # This tool intentionally has a closed public vocabulary: callers cannot
    # smuggle an identity or any future Core state parameter into its request.
    visual_state_tool = server._tool_manager.get_tool("phos_set_state")
    assert visual_state_tool is not None
    argument_model = visual_state_tool.fn_metadata.arg_model
    argument_model.model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")
    argument_model.model_rebuild(force=True)
    visual_state_tool.parameters = argument_model.model_json_schema(by_alias=True)

    @tool("phos_speak", "Request PHOS to speak one bounded text message. Use phos_signal_user for a transient non-speech cue; speech may be unavailable or interrupted by normal runtime policy.",
          annotations=transient_control)
    async def speak(text: SpeechText) -> dict[str, Any]:
        return await _async_tool_result(lambda: adapter.speak_async(text), ToolError)

    @tool("phos_sleep", "Request PHOS to enter sleep through normal lifecycle transitions. Use phos_wake to request wake; the request follows runtime transition and availability rules.",
          annotations=idempotent_control)
    async def sleep() -> dict[str, Any]:
        return await _async_tool_result(adapter.sleep_async, ToolError)

    @tool("phos_wake", "Request PHOS to wake through normal lifecycle transitions. Use phos_sleep to request sleep; the request follows runtime transition and availability rules.",
          annotations=idempotent_control)
    async def wake() -> dict[str, Any]:
        return await _async_tool_result(adapter.wake_async, ToolError)

    @tool("phos_signal_user", "Request one transient canonical user signal. Use phos_speak for spoken output; this may trigger configured visual or hardware feedback and does not change robot lifecycle state.",
          annotations=transient_control)
    async def signal_user(signal: SignalIntent) -> dict[str, Any]:
        return await _async_tool_result(lambda: adapter.signal_user_async(signal), ToolError)

    @tool("phos_query_measurements", "Get one bounded RAM-only measurement series for a selected metric and time window. Use phos_get_physical_context for current conditions and phos_get_physical_assessment before longitudinal interpretation; unknown metrics return no samples and no trend conclusion is made.",
          annotations=passive_read)
    def query_measurements(metric: HistoryMetric, start: HistoryTimestamp | None = None,
                           end: HistoryTimestamp | None = None, limit: HistoryLimit = 360) -> dict[str, Any]:
        return _tool_result(lambda: adapter.query_measurements(metric, start, end, limit), ToolError)

    @tool("phos_list_physical_transitions", "Get bounded retained semantic physical transitions, optionally filtered by an exact open-string event type. Use phos_query_measurements for numeric samples and phos_get_physical_assessment for evidence quality; absence of a retained transition does not prove it never occurred.",
          annotations=passive_read)
    def list_physical_transitions(event_type: TransitionEventType | None = None,
                                  limit: TransitionLimit = 100) -> dict[str, Any]:
        return _tool_result(lambda: adapter.list_physical_transitions(event_type, limit), ToolError)

    return server


def _tool_result(handler: Callable[[], dict[str, Any]], error_type) -> dict[str, Any]:
    try:
        return handler()
    except McpToolError as error:
        raise error_type(str(error)) from None


def _snapshot_tool_result(handler, error_type, *, source: str):
    """Build SDK-native image content from the authorized Vision JPEG."""
    try:
        return _snapshot_content(handler(), source)
    except McpToolError as error:
        raise error_type(str(error)) from None


async def _async_snapshot_tool_result(handler, error_type, *, source: str):
    try:
        return _snapshot_content(await handler(), source)
    except McpToolError as error:
        raise error_type(str(error)) from None


def _snapshot_content(snapshot, source: str):
    from mcp.types import ImageContent, TextContent
    return [
        ImageContent(data=base64.b64encode(snapshot.jpeg).decode("ascii"), mimeType="image/jpeg"),
        TextContent(text=(f"source={source}; captured_at="
                          f"{snapshot.captured_at_utc.isoformat().replace('+00:00', 'Z')}; "
                          f"age_ms={snapshot.age_ms}; freshness={snapshot.freshness}.")),
    ]


async def _async_tool_result(handler, error_type) -> dict[str, Any]:
    try:
        return await handler()
    except McpToolError as error:
        raise error_type(str(error)) from None


def _resource_result(handler: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    """Map the existing sanitized adapter error into an MCP resource failure."""
    try:
        return handler()
    except McpToolError as error:
        from mcp.server.mcpserver.exceptions import ResourceError
        raise ResourceError(str(error)) from None


def _resource_bytes_result(handler: Callable[[], bytes]) -> bytes:
    """Preserve SDK binary resource semantics while sanitizing failures."""
    try:
        return handler()
    except McpToolError as error:
        from mcp.server.mcpserver.exceptions import ResourceError
        raise ResourceError(str(error)) from None


class McpHttpServer:
    """One Streamable HTTP listener owned by the normal PHOS process."""

    def __init__(self, agent_service: AgentApplicationService, *, host: str, port: int, path: str,
                 authenticator=None, activity_tracker=None) -> None:
        self.host, self.port, self.path = host, port, path
        self._semantic_events = McpSemanticEventBridge(agent_service)
        self._mcp_server = create_mcp_server(agent_service, subscriptions=self._semantic_events.bus,
                                             authenticator=authenticator, activity_tracker=activity_tracker)
        self._uvicorn_server = None

    @property
    def url(self) -> str:
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"http://{host}:{self.port}{self.path}"

    async def serve(self) -> None:
        """Serve on PHOS's event loop and fail PHOS startup if binding fails."""
        try:
            import uvicorn
        except ImportError as error:
            raise RuntimeError("MCP Streamable HTTP requires the optional 'mcp' dependency.") from error

        self._semantic_events.start(asyncio.get_running_loop())
        if not ipaddress.ip_address(self.host).is_loopback:
            logger.warning("PHOS MCP: non-loopback bind host=%s; require TLS and appropriate network controls", self.host)
        logger.info("PHOS MCP: starting host=%s port=%s path=%s", self.host, self.port, self.path)
        app = self._mcp_server.streamable_http_app(host=self.host, streamable_http_path=self.path)
        config = uvicorn.Config(app, host=self.host, port=self.port,
                                log_level="warning", access_log=False)
        server = self._uvicorn_server = uvicorn.Server(config)
        try:
            try:
                await server.serve()
            except (OSError, SystemExit) as error:
                logger.error("PHOS MCP: bind failure host=%s port=%s path=%s", self.host, self.port, self.path)
                raise RuntimeError(f"MCP Streamable HTTP could not bind {self.url}") from error
            if not server.started:
                logger.error("PHOS MCP: bind failure host=%s port=%s path=%s", self.host, self.port, self.path)
                raise RuntimeError(f"MCP Streamable HTTP could not bind {self.url}")
        finally:
            self._semantic_events.close()

    @property
    def started(self) -> bool:
        return bool(self._uvicorn_server is not None and self._uvicorn_server.started)

    async def stop(self) -> None:
        """Request graceful listener shutdown; its ASGI lifespan closes sessions."""
        self._semantic_events.close()
        if self._uvicorn_server is not None:
            self._uvicorn_server.should_exit = True
            await asyncio.sleep(0)
        logger.info("PHOS MCP: stopped host=%s port=%s path=%s", self.host, self.port, self.path)


class McpServerManager:
    """Own only the MCP listener, never the PHOS runtime or service graph."""

    def __init__(self, agent_service: AgentApplicationService, *, host: str, path: str,
                 enabled: bool, port: int, authenticator=None, activity_tracker=None) -> None:
        self._agent_service, self._host, self._path = agent_service, host, path
        self._enabled, self._configured_port, self._authenticator = enabled, port, authenticator
        self._activity_tracker = activity_tracker
        self._server = self._task = self._loop = None
        self._status, self._error = ("disabled" if not enabled else "starting"), None
        self._lock = asyncio.Lock()

    def status(self) -> dict[str, Any]:
        server = self._server
        bound = server.port if self._status == "running" and server is not None else None
        endpoint = server.url if bound is not None else None
        return {"enabled": self._enabled, "port": self._configured_port, "status": self._status,
                "bound_port": bound, "endpoint": endpoint, "error": self._error}

    def configure_authentication(self, *, mode: str, anonymous_permission_profile: str) -> dict[str, Any]:
        """Apply request-bound authentication policy without rebinding MCP."""
        configure = getattr(self._authenticator, "configure", None)
        if not callable(configure):
            raise RuntimeError("MCP authentication runtime is unavailable")
        configure(mode=mode, anonymous_permission_profile=anonymous_permission_profile)
        return self.status()

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        if self._enabled:
            await self._start(self._configured_port)

    def attach_loop(self) -> None:
        """Publish the already-running PHOS loop before Admin becomes ready."""
        self._loop = asyncio.get_running_loop()

    def apply(self, *, enabled: bool, port: int) -> dict[str, Any]:
        """Thread-safe bridge for the lifecycle worker; it reuses this loop."""
        if self._loop is None:
            raise RuntimeError("MCP runtime is not ready")
        future = asyncio.run_coroutine_threadsafe(self._apply(enabled, port), self._loop)
        return future.result(timeout=10)

    async def _start(self, port: int) -> None:
        self._status, self._error = "starting", None
        # Resolve through the package at start time so deployments/tests can
        # provide the listener adapter without changing lifecycle semantics.
        from robot.mcp import McpHttpServer as Listener
        server = Listener(self._agent_service, host=self._host, port=port, path=self._path,
                          authenticator=self._authenticator, activity_tracker=self._activity_tracker)
        task = asyncio.create_task(server.serve(), name="mcp-streamable-http")
        for _ in range(200):
            if server.started:
                self._server, self._task, self._status = server, task, "running"
                return
            if task.done():
                try: task.result()
                except Exception: pass
                self._status, self._error = "error", "MCP could not start on the requested port."
                raise RuntimeError(self._error)
            await asyncio.sleep(.01)
        await server.stop(); await asyncio.gather(task, return_exceptions=True)
        self._status, self._error = "error", "MCP startup timed out."
        raise RuntimeError(self._error)

    async def _stop(self) -> None:
        if self._server is None: return
        self._status = "stopping"
        await self._server.stop()
        await asyncio.gather(self._task, return_exceptions=True)
        self._server = self._task = None

    async def _apply(self, enabled: bool, port: int) -> dict[str, Any]:
        async with self._lock:
            previous_enabled, previous_port = self._enabled, self._configured_port
            if not enabled:
                await self._stop(); self._enabled, self._configured_port, self._status, self._error = False, port, "disabled", None
                return self.status()
            if self._status == "running" and previous_enabled and previous_port == port:
                return self.status()
            await self._stop()
            try:
                await self._start(port)
            except Exception:
                # A failed rebind must restore the working listener before reporting failure.
                self._enabled, self._configured_port = previous_enabled, previous_port
                if previous_enabled:
                    try: await self._start(previous_port)
                    except Exception: pass
                raise
            self._enabled, self._configured_port = True, port
            return self.status()

    async def close(self) -> None:
        async with self._lock:
            await self._stop()
