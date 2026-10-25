"""A small semantic permission boundary for future trusted agent adapters.

This module deliberately has no identity, transport, persistence, or runtime
knowledge.  It decides only whether a named semantic operation may enter the
existing application-service boundary.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import logging
from types import MappingProxyType
from typing import Any, Mapping


logger = logging.getLogger(__name__)


class AgentPermission(str, Enum):
    """Semantic permissions, intentionally independent of providers or hardware."""

    READ_STATUS = "read_status"
    READ_PHYSICAL_CONTEXT = "read_physical_context"
    READ_PERCEPTION = "read_perception"
    CONTROL_EXPRESSION = "control_expression"
    CONTROL_VOICE = "control_voice"
    CONTROL_SLEEP = "control_sleep"
    SIGNAL_USER = "signal_user"


class AgentOperation(str, Enum):
    """The deliberately small public operation vocabulary for agent adapters."""

    STATUS = "status"
    GET_PHYSICAL_CONTEXT = "get_physical_context"
    PRESENCE = "presence"
    ATTENTION = "attention"
    OBSERVED_EXPRESSION = "observed_expression"
    SET_EXPRESSION = "set_expression"
    SPEAK = "speak"
    SLEEP = "sleep"
    WAKE = "wake"
    SIGNAL_USER = "signal_user"


OPERATION_PERMISSIONS: Mapping[AgentOperation, AgentPermission] = MappingProxyType({
    AgentOperation.STATUS: AgentPermission.READ_STATUS,
    AgentOperation.GET_PHYSICAL_CONTEXT: AgentPermission.READ_PHYSICAL_CONTEXT,
    AgentOperation.PRESENCE: AgentPermission.READ_PERCEPTION,
    AgentOperation.ATTENTION: AgentPermission.READ_PERCEPTION,
    AgentOperation.OBSERVED_EXPRESSION: AgentPermission.READ_PERCEPTION,
    AgentOperation.SET_EXPRESSION: AgentPermission.CONTROL_EXPRESSION,
    AgentOperation.SPEAK: AgentPermission.CONTROL_VOICE,
    AgentOperation.SLEEP: AgentPermission.CONTROL_SLEEP,
    AgentOperation.WAKE: AgentPermission.CONTROL_SLEEP,
    AgentOperation.SIGNAL_USER: AgentPermission.SIGNAL_USER,
})


class AgentProfile(str, Enum):
    """The two in-code profiles available before adapter/authentication design."""

    STANDARD = "standard"
    OBSERVER = "observer"


_READ_PERMISSIONS = frozenset({
    AgentPermission.READ_STATUS,
    AgentPermission.READ_PHYSICAL_CONTEXT,
    AgentPermission.READ_PERCEPTION,
})

AGENT_ACCESS_PROFILES: Mapping[AgentProfile, frozenset[AgentPermission]] = MappingProxyType({
    AgentProfile.STANDARD: frozenset(AgentPermission),
    AgentProfile.OBSERVER: _READ_PERMISSIONS,
})
DEFAULT_AGENT_PROFILE = AgentProfile.STANDARD


@dataclass(frozen=True)
class AgentAccess:
    """An immutable set of permissions assigned by a future trusted adapter."""

    permissions: frozenset[AgentPermission] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        try:
            normalized = frozenset(AgentPermission(permission) for permission in self.permissions)
        except (TypeError, ValueError) as error:
            raise ValueError("AgentAccess permissions must be AgentPermission values.") from error
        object.__setattr__(self, "permissions", normalized)

    @classmethod
    def for_profile(cls, profile: AgentProfile | str = DEFAULT_AGENT_PROFILE) -> "AgentAccess":
        try:
            selected = AgentProfile(profile)
        except (TypeError, ValueError) as error:
            raise ValueError("Unknown agent access profile.") from error
        return cls(AGENT_ACCESS_PROFILES[selected])


class AgentPermissionDenied(PermissionError):
    """A denied semantic operation, distinct from availability or validation errors."""

    def __init__(self, operation: AgentOperation, permission: AgentPermission) -> None:
        self.operation, self.permission = operation, permission
        super().__init__(f"Agent operation '{operation.value}' requires '{permission.value}'.")


class AgentPermissionService:
    """Pure deterministic permission evaluation with no runtime side effects."""

    @staticmethod
    def is_allowed(access: AgentAccess, permission: AgentPermission) -> bool:
        return permission in access.permissions

    def require(self, access: AgentAccess, operation: AgentOperation) -> None:
        required = OPERATION_PERMISSIONS[operation]
        if self.is_allowed(access, required):
            return
        logger.warning("PHOS AGENT: operation denied operation=%s required_permission=%s",
                       operation.value, required.value)
        raise AgentPermissionDenied(operation, required)


class AgentApplicationService:
    """Permission-checked semantic facade over ``PhosApplicationService``.

    It intentionally contains only stable semantic reads and actions.  Granted
    permission does not alter the validation, capability, arbitration, or
    lifecycle behavior of the delegated application method.
    """

    __slots__ = ("_application", "_access", "_permissions")

    def __init__(self, application, access: AgentAccess,
                 permissions: AgentPermissionService | None = None) -> None:
        self._application = application
        self._access = access
        self._permissions = permissions or AgentPermissionService()

    def _require(self, operation: AgentOperation) -> None:
        self._permissions.require(self._access, operation)

    def status(self) -> dict[str, Any]:
        self._require(AgentOperation.STATUS)
        return self._application.status()

    def get_physical_context(self) -> dict[str, Any]:
        self._require(AgentOperation.GET_PHYSICAL_CONTEXT)
        return self._application.get_physical_context()

    def presence(self) -> dict[str, Any]:
        self._require(AgentOperation.PRESENCE)
        return self._application.presence()

    def attention(self) -> dict[str, Any]:
        self._require(AgentOperation.ATTENTION)
        return self._application.attention()

    def observed_expression(self) -> dict[str, Any]:
        self._require(AgentOperation.OBSERVED_EXPRESSION)
        return self._application.observed_expression()

    def set_expression(self, expression: str) -> dict[str, Any]:
        self._require(AgentOperation.SET_EXPRESSION)
        return self._application.set_expression(expression)

    def speak(self, text: str) -> dict[str, Any]:
        self._require(AgentOperation.SPEAK)
        return self._application.speak(text)

    def sleep(self) -> dict[str, Any]:
        self._require(AgentOperation.SLEEP)
        return self._application.set_state("sleeping")

    def wake(self) -> dict[str, Any]:
        self._require(AgentOperation.WAKE)
        return self._application.set_state("idle")

    def signal_user(self, signal: str) -> dict[str, Any]:
        self._require(AgentOperation.SIGNAL_USER)
        return self._application.signal_user(signal)
