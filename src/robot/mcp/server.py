"""Stage 1 stdio MCP adapter over the permission-checked semantic boundary."""
from __future__ import annotations

from typing import Annotated, Any, Callable

from pydantic import Field

from robot.services import AgentApplicationService, AgentPermissionDenied, ApplicationError, USER_SIGNAL_INTENTS
from robot.services.application import COMMANDS


MCP_TOOL_NAMES = frozenset({
    "phos_get_status", "phos_get_physical_context", "phos_get_presence", "phos_get_attention",
    "phos_get_observed_expression", "phos_set_expression", "phos_speak", "phos_sleep",
    "phos_wake", "phos_signal_user",
})

Expression = Annotated[str, Field(min_length=1, json_schema_extra={
    "enum": COMMANDS["set_expression"].allowed_values,
})]
SpeechText = Annotated[str, Field(min_length=1, max_length=500)]
SignalIntent = Annotated[str, Field(min_length=1, json_schema_extra={
    "enum": sorted(USER_SIGNAL_INTENTS),
})]


class McpToolError(Exception):
    """Sanitized semantic error returned as an MCP tool failure."""


class McpSemanticAdapter:
    """Maps Stage 1 tools to an existing ``AgentApplicationService`` only."""

    def __init__(self, agent_service: AgentApplicationService) -> None:
        self._agent_service = agent_service

    def _call(self, operation: str, handler: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        try:
            return handler()
        except AgentPermissionDenied as error:
            raise McpToolError(
                f"permission_denied: operation={error.operation.value} required_permission={error.permission.value}"
            ) from None
        except ApplicationError as error:
            raise McpToolError(_application_error_message(error)) from None
        except Exception:
            raise McpToolError("internal_error: PHOS could not complete the requested operation.") from None

    async def _call_async(self, handler) -> dict[str, Any]:
        try:
            return await handler()
        except AgentPermissionDenied as error:
            raise McpToolError(
                f"permission_denied: operation={error.operation.value} required_permission={error.permission.value}"
            ) from None
        except ApplicationError as error:
            raise McpToolError(_application_error_message(error)) from None
        except Exception:
            raise McpToolError("internal_error: PHOS could not complete the requested operation.") from None

    def get_status(self) -> dict[str, Any]:
        return self._call("phos_get_status", self._agent_service.status)

    def get_physical_context(self) -> dict[str, Any]:
        return self._call("phos_get_physical_context", self._agent_service.get_physical_context)

    def get_presence(self) -> dict[str, Any]:
        return self._call("phos_get_presence", self._agent_service.presence)

    def get_attention(self) -> dict[str, Any]:
        return self._call("phos_get_attention", self._agent_service.attention)

    def get_observed_expression(self) -> dict[str, Any]:
        return self._call("phos_get_observed_expression", self._agent_service.observed_expression)

    def set_expression(self, expression: str) -> dict[str, Any]:
        return self._call("phos_set_expression", lambda: self._agent_service.set_expression(expression))

    def speak(self, text: str) -> dict[str, Any]:
        return self._call("phos_speak", lambda: self._agent_service.speak(text))

    def sleep(self) -> dict[str, Any]:
        return self._call("phos_sleep", self._agent_service.sleep)

    def wake(self) -> dict[str, Any]:
        return self._call("phos_wake", self._agent_service.wake)

    def signal_user(self, signal: str) -> dict[str, Any]:
        return self._call("phos_signal_user", lambda: self._agent_service.signal_user(signal))

    async def speak_async(self, text: str) -> dict[str, Any]:
        return await self._call_async(lambda: self._agent_service.speak_async(text))

    async def sleep_async(self) -> dict[str, Any]:
        return await self._call_async(self._agent_service.sleep_async)

    async def wake_async(self) -> dict[str, Any]:
        return await self._call_async(self._agent_service.wake_async)

    async def signal_user_async(self, signal: str) -> dict[str, Any]:
        return await self._call_async(lambda: self._agent_service.signal_user_async(signal))


def _application_error_message(error: ApplicationError) -> str:
    category = "invalid_argument" if error.code.startswith("invalid_") else (
        "unsupported_capability" if error.code.startswith("unsupported_") else (
            "service_unavailable" if error.status == 503 else "runtime_failure" if error.status >= 500
            else "semantic_error"))
    return f"{category}: {error.message}"


def create_mcp_server(agent_service: AgentApplicationService):
    """Create a server without constructing or owning a PHOS runtime."""
    try:
        from mcp.server.fastmcp import FastMCP
        from mcp.server.fastmcp.exceptions import ToolError
    except ImportError as error:
        raise RuntimeError("MCP support requires the optional 'mcp' dependency on Python 3.10+.") from error

    adapter = McpSemanticAdapter(agent_service)
    server = FastMCP("PHOS", instructions="Trusted-local semantic PHOS adapter; no hardware controls.")

    def tool(name: str, description: str):
        return server.tool(name=name, description=description, structured_output=True)

    @tool("phos_get_status", "Return provider-independent PHOS semantic status.")
    def get_status() -> dict[str, Any]:
        return _tool_result(adapter.get_status, ToolError)

    @tool("phos_get_physical_context", "Return the compact semantic physical context.")
    def get_physical_context() -> dict[str, Any]:
        return _tool_result(adapter.get_physical_context, ToolError)

    @tool("phos_get_presence", "Return semantic presence state without identity.")
    def get_presence() -> dict[str, Any]:
        return _tool_result(adapter.get_presence, ToolError)

    @tool("phos_get_attention", "Return semantic attention state.")
    def get_attention() -> dict[str, Any]:
        return _tool_result(adapter.get_attention, ToolError)

    @tool("phos_get_observed_expression", "Return observed-expression semantics, not emotion claims.")
    def get_observed_expression() -> dict[str, Any]:
        return _tool_result(adapter.get_observed_expression, ToolError)

    @tool("phos_set_expression", "Set a supported semantic expression.")
    def set_expression(expression: Expression) -> dict[str, Any]:
        return _tool_result(lambda: adapter.set_expression(expression), ToolError)

    @tool("phos_speak", "Request bounded semantic speech text.")
    async def speak(text: SpeechText) -> dict[str, Any]:
        return await _async_tool_result(lambda: adapter.speak_async(text), ToolError)

    @tool("phos_sleep", "Request semantic sleep through normal state transitions.")
    async def sleep() -> dict[str, Any]:
        return await _async_tool_result(adapter.sleep_async, ToolError)

    @tool("phos_wake", "Request semantic wake through normal state transitions.")
    async def wake() -> dict[str, Any]:
        return await _async_tool_result(adapter.wake_async, ToolError)

    @tool("phos_signal_user", "Request a canonical transient semantic user signal.")
    async def signal_user(signal: SignalIntent) -> dict[str, Any]:
        return await _async_tool_result(lambda: adapter.signal_user_async(signal), ToolError)

    return server


def _tool_result(handler: Callable[[], dict[str, Any]], error_type) -> dict[str, Any]:
    try:
        return handler()
    except McpToolError as error:
        raise error_type(str(error)) from None


async def _async_tool_result(handler, error_type) -> dict[str, Any]:
    try:
        return await handler()
    except McpToolError as error:
        raise error_type(str(error)) from None


async def run_stdio_server(server) -> None:
    """Run an already-constructed server on its SDK-provided stdio transport."""
    await server.run_stdio_async()
