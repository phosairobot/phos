"""Provider-neutral agent identity and transitional local authentication."""
from __future__ import annotations

from dataclasses import dataclass
import secrets
from types import MappingProxyType
from typing import Mapping, Protocol

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


class AgentRegistry:
    """Immutable configured identities, independent of transports and credentials."""
    def __init__(self, default_agent: str, entries: tuple[AgentIdentity, ...], credential_refs: Mapping[str, str] | None = None) -> None:
        identities = {identity.id: identity for identity in entries}
        if len(identities) != len(entries) or default_agent not in identities:
            raise ValueError("Configured agents require unique ids and a known default_agent.")
        self.default_agent = default_agent
        self._identities: Mapping[str, AgentIdentity] = MappingProxyType(identities)
        refs = dict(credential_refs or {})
        if set(refs) - set(identities) or len(set(refs.values())) != len(refs):
            raise ValueError("Configured credential references must be unique and belong to known agents.")
        self._credential_refs = MappingProxyType(refs)

    def get(self, agent_id: str) -> AgentIdentity | None:
        return self._identities.get(agent_id)

    @property
    def default_identity(self) -> AgentIdentity:
        return self._identities[self.default_agent]

    @property
    def entries(self) -> tuple[AgentIdentity, ...]:
        return tuple(self._identities.values())

    @property
    def credential_refs(self) -> Mapping[str, str]: return self._credential_refs

    def __eq__(self, other: object) -> bool:
        return (isinstance(other, AgentRegistry) and self.default_agent == other.default_agent
                and self.entries == other.entries and self.credential_refs == other.credential_refs)


COMPATIBILITY_AGENT_REGISTRY = AgentRegistry("local-agent", (LOCAL_AGENT_IDENTITY,))


class ConfiguredAgentAuthenticator(LocalAgentAuthenticator):
    """Resolves the configured default identity; credential selection is deferred."""
    def __init__(self, registry: AgentRegistry) -> None:
        self._registry = registry

    async def authenticate(self, request_context: object | None = None) -> AgentIdentity:
        return self._registry.default_identity


class BearerAgentAuthenticator(ConfiguredAgentAuthenticator):
    """Bearer verifier using only secret references held by the registry."""
    def __init__(self, registry: AgentRegistry, secrets_service) -> None:
        super().__init__(registry); self._secrets = secrets_service

    async def authenticate(self, request_context: object | None = None) -> AgentIdentity:
        if not self._registry.credential_refs:
            return await super().authenticate(request_context)
        headers = getattr(getattr(request_context, "request", None), "headers", None) or {}
        value = headers.get("authorization") or headers.get("Authorization")
        if not isinstance(value, str) or not value.startswith("Bearer ") or not value[7:]:
            raise PermissionError("authentication_failed")
        token = value[7:]
        for agent_id, secret_ref in self._registry.credential_refs.items():
            expected = self._secrets.get_secret(secret_ref)
            if expected and secrets.compare_digest(token, expected):
                return self._registry.get(agent_id)
        raise PermissionError("authentication_failed")
