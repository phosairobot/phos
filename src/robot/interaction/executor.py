"""Explicitly-authorized application-command execution.

The executor accepts only a validated semantic ``CommandRoute``.  It delegates
the actual operation to the application-service boundary and never subscribes to
voice or interaction events.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol

from .models import InteractionOutcome, InteractionOutcomeStatus, InteractionSource
from .router import CommandRoute


class CommandExecutionStatus(str, Enum):
    EXECUTED = "executed"
    DENIED = "denied"
    UNSUPPORTED = "unsupported"
    REJECTED = "rejected"
    FAILED = "failed"


class CommandExecutionError(Exception):
    """Base error for the controlled application-command execution boundary."""


class CommandApplicationService(Protocol):
    """Narrow application boundary used by the executor."""

    def set_state(self, state: str) -> dict:
        """Apply a validated semantic robot state through the application service."""

    def set_expression(self, expression: str) -> dict:
        """Apply a validated semantic expression through the application service."""

    def environment(self) -> dict:
        """Return the semantic environmental snapshot."""

    def sensors(self) -> dict:
        """Return the semantic sensor snapshot collection."""


@dataclass(frozen=True)
class CommandAuthorization:
    """An explicit, source-carrying authorization capability.

    Empty ``allowed_commands`` is the default-deny policy.  This object is
    deliberately supplied by the caller rather than inferred from a route, so a
    valid route alone can never execute.
    """

    source: InteractionSource
    allowed_commands: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        if not isinstance(self.source, InteractionSource):
            raise TypeError("CommandAuthorization.source must be an InteractionSource")
        object.__setattr__(self, "allowed_commands", frozenset(self.allowed_commands))

    def permits(self, route: CommandRoute) -> bool:
        return route.command in self.allowed_commands


@dataclass(frozen=True)
class CommandExecutionResult:
    """Serializable result without application exception details."""

    status: CommandExecutionStatus
    command: str | None
    parameters: dict[str, Any] = field(default_factory=dict)
    reason: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "parameters", dict(self.parameters))

    def document(self) -> dict[str, Any]:
        return {"status": self.status.value, "command": self.command,
                "parameters": dict(self.parameters), "reason": self.reason}


_EXECUTABLE_COMMANDS = frozenset({"robot.sleep", "robot.wake", "face.set_expression"})
_QUERY_COMMANDS = frozenset({"environment.get_temperature", "environment.get_air_quality"})
_ROUTE_INTENTS = {
    "robot.sleep": "sleep",
    "robot.wake": "wake",
    "face.set_expression": "set_expression",
    "environment.get_temperature": "get_temperature",
    "environment.get_air_quality": "get_air_quality",
}


class CommandExecutor:
    """Execute a small authorized command subset through an application service."""

    def __init__(self, application_service: CommandApplicationService) -> None:
        self._application_service = application_service

    def execute(self, route: CommandRoute, authorization: CommandAuthorization | None = None,
                *, source_language: str | None = None) -> CommandExecutionResult | InteractionOutcome:
        """Execute only explicitly authorized, well-formed semantic routes."""
        if not self._valid_route(route):
            command = route.command if isinstance(route, CommandRoute) else None
            parameters = route.parameters if isinstance(route, CommandRoute) else {}
            return CommandExecutionResult(CommandExecutionStatus.REJECTED, command, parameters,
                                          "invalid_route")
        if authorization is None or not authorization.permits(route):
            return CommandExecutionResult(CommandExecutionStatus.DENIED, route.command,
                                          route.parameters, "execution_not_authorized")
        if route.command in _QUERY_COMMANDS:
            return self._query(route, authorization.source, source_language)
        if route.command not in _EXECUTABLE_COMMANDS:
            return CommandExecutionResult(CommandExecutionStatus.UNSUPPORTED, route.command,
                                          route.parameters, "command_execution_not_implemented")
        try:
            if route.command == "robot.sleep":
                self._application_service.set_state("sleeping")
            elif route.command == "robot.wake":
                self._application_service.set_state("idle")
            else:
                self._application_service.set_expression(route.parameters["expression"])
        except Exception:
            return CommandExecutionResult(CommandExecutionStatus.FAILED, route.command,
                                          route.parameters, "application_error")
        return CommandExecutionResult(CommandExecutionStatus.EXECUTED, route.command,
                                      route.parameters)

    def _query(self, route: CommandRoute, source: InteractionSource,
               source_language: str | None) -> InteractionOutcome:
        try:
            if route.command == "environment.get_temperature":
                snapshot = self._application_service.environment()
                value = self._measurement(snapshot, "temperature_c")
                if value is None:
                    return InteractionOutcome(InteractionOutcomeStatus.UNAVAILABLE, route.intent, source,
                                              source_language, reason="temperature_unavailable")
                return InteractionOutcome(InteractionOutcomeStatus.COMPLETED, route.intent, source,
                                          source_language, {"temperature_c": value})
            snapshot = self._application_service.sensors().get("ccs811")
            eco2 = self._measurement(snapshot, "eco2_ppm")
            tvoc = self._measurement(snapshot, "tvoc_ppb")
            # AirQualityReading is atomic, so a missing member means the current
            # semantic snapshot is unavailable rather than partially invented.
            if eco2 is None or tvoc is None:
                return InteractionOutcome(InteractionOutcomeStatus.UNAVAILABLE, route.intent, source,
                                          source_language, reason="air_quality_unavailable")
            return InteractionOutcome(InteractionOutcomeStatus.COMPLETED, route.intent, source,
                                      source_language, {"eco2_ppm": eco2, "tvoc_ppb": tvoc})
        except Exception:
            return InteractionOutcome(InteractionOutcomeStatus.FAILED, route.intent, source,
                                      source_language, reason="application_error")

    @staticmethod
    def _measurement(snapshot: object, name: str) -> int | float | None:
        if not isinstance(snapshot, dict) or not snapshot.get("available"):
            return None
        measurements = snapshot.get("measurements")
        value = measurements.get(name) if isinstance(measurements, dict) else None
        return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None

    @staticmethod
    def _valid_route(route: object) -> bool:
        if not isinstance(route, CommandRoute) or _ROUTE_INTENTS.get(route.command) != route.intent:
            return False
        if route.command in {"robot.sleep", "robot.wake", "environment.get_temperature", "environment.get_air_quality"}:
            return not route.parameters
        return (route.command == "face.set_expression" and set(route.parameters) == {"expression"}
                and isinstance(route.parameters["expression"], str))
