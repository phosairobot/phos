"""Streamable HTTP MCP adapter over the permission-checked semantic boundary."""
from __future__ import annotations

import asyncio
import ipaddress
import logging
from typing import Annotated, Any, Callable

from pydantic import Field

from robot.services import AgentApplicationService, AgentPermissionDenied, ApplicationError, USER_SIGNAL_INTENTS
from robot.services.application import COMMANDS

logger = logging.getLogger(__name__)


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


def create_mcp_server(agent_service: AgentApplicationService, *, host: str = "127.0.0.1",
                      port: int = 8001, path: str = "/mcp"):
    """Create a server without constructing or owning a PHOS runtime."""
    try:
        from mcp.server.fastmcp import FastMCP
        from mcp.server.fastmcp.exceptions import ToolError
    except ImportError as error:
        raise RuntimeError("MCP support requires the optional 'mcp' dependency on Python 3.10+.") from error

    adapter = McpSemanticAdapter(agent_service)
    server = FastMCP("PHOS", instructions="Trusted-local semantic PHOS adapter; no hardware controls.",
                     host=host, port=port, streamable_http_path=path)

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


class McpHttpServer:
    """One Streamable HTTP listener owned by the normal PHOS process."""

    def __init__(self, agent_service: AgentApplicationService, *, host: str, port: int, path: str) -> None:
        self.host, self.port, self.path = host, port, path
        self._mcp_server = create_mcp_server(agent_service, host=host, port=port, path=path)
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

        if not ipaddress.ip_address(self.host).is_loopback:
            logger.warning("PHOS MCP: non-loopback bind host=%s; network authentication, agent identity, and network authorization are not implemented", self.host)
        logger.info("PHOS MCP: starting host=%s port=%s path=%s", self.host, self.port, self.path)
        config = uvicorn.Config(self._mcp_server.streamable_http_app(), host=self.host, port=self.port,
                                log_level="warning", access_log=False)
        server = self._uvicorn_server = uvicorn.Server(config)
        try:
            await server.serve()
        except (OSError, SystemExit) as error:
            logger.error("PHOS MCP: bind failure host=%s port=%s path=%s", self.host, self.port, self.path)
            raise RuntimeError(f"MCP Streamable HTTP could not bind {self.url}") from error
        if not server.started:
            logger.error("PHOS MCP: bind failure host=%s port=%s path=%s", self.host, self.port, self.path)
            raise RuntimeError(f"MCP Streamable HTTP could not bind {self.url}")

    @property
    def started(self) -> bool:
        return bool(self._uvicorn_server is not None and self._uvicorn_server.started)

    async def stop(self) -> None:
        """Request graceful listener shutdown; its ASGI lifespan closes sessions."""
        if self._uvicorn_server is not None:
            self._uvicorn_server.should_exit = True
            await asyncio.sleep(0)
        logger.info("PHOS MCP: stopped host=%s port=%s path=%s", self.host, self.port, self.path)


class McpServerManager:
    """Own only the MCP listener, never the PHOS runtime or service graph."""

    def __init__(self, agent_service: AgentApplicationService, *, host: str, path: str,
                 enabled: bool, port: int) -> None:
        self._agent_service, self._host, self._path = agent_service, host, path
        self._enabled, self._configured_port = enabled, port
        self._server = self._task = self._loop = None
        self._status, self._error = ("disabled" if not enabled else "starting"), None
        self._lock = asyncio.Lock()

    def status(self) -> dict[str, Any]:
        server = self._server
        bound = server.port if self._status == "running" and server is not None else None
        endpoint = server.url if bound is not None else None
        return {"enabled": self._enabled, "port": self._configured_port, "status": self._status,
                "bound_port": bound, "endpoint": endpoint, "error": self._error}

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
        server = Listener(self._agent_service, host=self._host, port=port, path=self._path)
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
