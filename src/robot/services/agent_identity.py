"""Provider-neutral agent identity and transitional local authentication."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .agent_permissions import AgentProfile


@dataclass(frozen=True)
class AgentIdentity:
    """Stable agent identity, deliberately separate from credentials and transport."""

    id: str
    permission_profile: AgentProfile | str
    display_name: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id.strip():
            raise ValueError("Agent identity id is required.")
        if self.display_name is not None and (not isinstance(self.display_name, str) or not self.display_name.strip()):
            raise ValueError("Agent display name must be a non-empty string when provided.")
        try:
            profile = AgentProfile(self.permission_profile)
        except (TypeError, ValueError) as error:
            raise ValueError("Unknown agent permission profile.") from error
        object.__setattr__(self, "id", self.id.strip())
        object.__setattr__(self, "display_name", self.display_name.strip() if self.display_name else None)
        object.__setattr__(self, "permission_profile", profile)


class AgentAuthenticator(Protocol):
    """Resolves one authenticated identity for an adapter request context."""

    async def authenticate(self, request_context: object | None = None) -> AgentIdentity:
        """Return the identity for this request without exposing credentials."""


LOCAL_AGENT_IDENTITY = AgentIdentity(
    id="local-agent", display_name="Local MCP agent", permission_profile=AgentProfile.STANDARD,
)


class LocalAgentAuthenticator:
    """Transitional local identity resolver; it does not verify credentials.

    This preserves the trusted-local behavior that existed before Stage 3.1 while
    establishing the pluggable authentication boundary for a later credential
    backed authenticator.
    """

    def __init__(self, identity: AgentIdentity = LOCAL_AGENT_IDENTITY) -> None:
        self._identity = identity

    async def authenticate(self, request_context: object | None = None) -> AgentIdentity:
        return self._identity
