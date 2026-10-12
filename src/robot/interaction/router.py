"""Pure canonical-intent validation and dry-run command routing.

This module deliberately has no dependency on runtime, application, behavior, or
hardware code.  A routable result describes a future semantic application command;
it never invokes one.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol

from .models import ResolvedIntent


class CommandRouteStatus(str, Enum):
    ROUTABLE = "routable"
    REJECTED = "rejected"
    FAILED = "failed"


class CommandRouterError(Exception):
    """Base error for the pure command-routing boundary."""


class CommandValidationError(CommandRouterError):
    """A known intent did not meet its canonical parameter contract."""


class CommandPolicyError(CommandRouterError):
    """A valid route is not permitted by the supplied policy."""


@dataclass(frozen=True)
class CommandRoute:
    """A language-independent semantic command selected by the catalog."""

    intent: str
    command: str
    parameters: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "parameters", dict(self.parameters))


class CommandPolicy(Protocol):
    """Optional policy seam, intentionally separate from execution permission."""

    def permits(self, route: CommandRoute) -> bool:
        """Return whether this semantic route may be considered routable."""


@dataclass(frozen=True)
class AllowListedCommandPolicy:
    """Small deterministic policy useful to callers and tests.

    ``None`` allows every catalog command.  It does not enable execution.
    """

    allowed_commands: frozenset[str] | None = None

    def permits(self, route: CommandRoute) -> bool:
        return self.allowed_commands is None or route.command in self.allowed_commands


@dataclass(frozen=True)
class CommandRouteResult:
    """Serializable outcome of a dry-run routing decision."""

    status: CommandRouteStatus
    intent: str
    command: str | None = None
    parameters: dict[str, Any] = field(default_factory=dict)
    execution_allowed: bool = False
    reason: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "parameters", dict(self.parameters))
        # Step 4A is intentionally non-executing, even when a policy permits it.
        object.__setattr__(self, "execution_allowed", False)

    def document(self) -> dict[str, Any]:
        return {"status": self.status.value, "intent": self.intent,
                "command": self.command, "parameters": dict(self.parameters),
                "execution_allowed": self.execution_allowed, "reason": self.reason}


_COMMAND_CATALOG = {
    "sleep": "robot.sleep",
    "wake": "robot.wake",
    "get_temperature": "environment.get_temperature",
    "get_air_quality": "environment.get_air_quality",
    "set_expression": "face.set_expression",
}
_NO_PARAMETER_INTENTS = frozenset(_COMMAND_CATALOG) - {"set_expression"}
_ALLOWED_EXPRESSIONS = frozenset({"neutral", "happy", "curious", "surprised"})


class CommandRouter:
    """Validate resolved intents and describe their future semantic route only."""

    def __init__(self, policy: CommandPolicy | None = None) -> None:
        self._policy = policy or AllowListedCommandPolicy()

    def route(self, intent: ResolvedIntent) -> CommandRouteResult:
        """Return a dry-run routing result without accessing PHOS runtime state."""
        if not isinstance(intent, ResolvedIntent):
            return CommandRouteResult(CommandRouteStatus.FAILED, "unknown", reason="internal_error")
        try:
            command = _COMMAND_CATALOG.get(intent.name)
            if command is None:
                return self._rejected(intent, "unknown_intent")
            parameters = self._validated_parameters(intent)
            route = CommandRoute(intent.name, command, parameters)
            if not self._policy.permits(route):
                return self._rejected(intent, "policy_denied")
            return CommandRouteResult(CommandRouteStatus.ROUTABLE, intent.name, command,
                                      parameters=parameters)
        except CommandValidationError:
            return self._rejected(intent, "invalid_parameter")
        except CommandPolicyError:
            return self._rejected(intent, "policy_denied")
        except Exception:
            return CommandRouteResult(CommandRouteStatus.FAILED, intent.name,
                                      parameters=intent.parameters, reason="internal_error")

    @staticmethod
    def _rejected(intent: ResolvedIntent, reason: str) -> CommandRouteResult:
        return CommandRouteResult(CommandRouteStatus.REJECTED, intent.name,
                                  parameters=intent.parameters, reason=reason)

    @staticmethod
    def _validated_parameters(intent: ResolvedIntent) -> dict[str, Any]:
        parameters = dict(intent.parameters)
        if intent.name in _NO_PARAMETER_INTENTS:
            if parameters:
                raise CommandValidationError("Unexpected parameters")
            return {}
        if set(parameters) != {"expression"}:
            raise CommandValidationError("set_expression requires only expression")
        expression = parameters["expression"]
        if not isinstance(expression, str) or expression not in _ALLOWED_EXPRESSIONS:
            raise CommandValidationError("Unsupported expression")
        return {"expression": expression}
