"""Provider-neutral agent identity and transitional local authentication."""
from __future__ import annotations

from dataclasses import dataclass
import logging
from threading import RLock
import secrets
from types import MappingProxyType
from typing import Mapping, Protocol

from robot.secrets import SecretsError

from .agent_permissions import AgentProfile

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AgentIdentity:
    """Stable agent identity, deliberately separate from credentials and transport."""

    id: str
    permission_profile: AgentProfile | str
    display_name: str | None = None
    skin: dict | None = None

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
        if self.skin is not None:
            if (not isinstance(self.skin, dict) or set(self.skin) != {"enabled", "id"}
                    or type(self.skin["enabled"]) is not bool
                    or self.skin["id"] is not None and (not isinstance(self.skin["id"], str) or not self.skin["id"].strip())
                    or self.skin["enabled"] and self.skin["id"] is None):
                raise ValueError("Invalid agent skin assignment.")
            object.__setattr__(self, "skin", {"enabled": self.skin["enabled"],
                                                "id": self.skin["id"].strip() if self.skin["id"] else None})


class AgentAuthenticator(Protocol):
    """Resolves one authenticated identity for an adapter request context."""

    async def authenticate(self, request_context: object | None = None) -> AgentIdentity:
        """Return the identity for this request without exposing credentials."""


LOCAL_AGENT_IDENTITY = AgentIdentity(
    id="local-agent", display_name="Local MCP agent", permission_profile=AgentProfile.STANDARD,
)


def anonymous_mcp_identity(profile: AgentProfile | str) -> AgentIdentity:
    """Create the non-persistent principal used only for anonymous MCP access."""
    return AgentIdentity("anonymous-mcp", profile, "Anonymous MCP client")


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


class AgentRegistryProvider:
    """Thread-safe owner for the registry consulted by new MCP requests."""
    def __init__(self, registry: AgentRegistry) -> None:
        self._registry = registry
        self._lock = RLock()

    def get(self) -> AgentRegistry:
        with self._lock:
            return self._registry

    def replace(self, registry: AgentRegistry) -> None:
        if not isinstance(registry, AgentRegistry):
            raise TypeError("registry must be an AgentRegistry")
        with self._lock:
            self._registry = registry


COMPATIBILITY_AGENT_REGISTRY = AgentRegistry("local-agent", (LOCAL_AGENT_IDENTITY,))


class ConfiguredAgentAuthenticator(LocalAgentAuthenticator):
    """Resolves the configured default identity; credential selection is deferred."""
    def __init__(self, registry: AgentRegistry | AgentRegistryProvider) -> None:
        self._registry = registry

    def _current_registry(self) -> AgentRegistry:
        return self._registry.get() if isinstance(self._registry, AgentRegistryProvider) else self._registry

    async def authenticate(self, request_context: object | None = None) -> AgentIdentity:
        return self._current_registry().default_identity


class BearerAgentAuthenticator(ConfiguredAgentAuthenticator):
    """Bearer verifier using only secret references held by the registry."""
    def __init__(self, registry: AgentRegistry | AgentRegistryProvider, secrets_service) -> None:
        super().__init__(registry); self._secrets = secrets_service

    async def authenticate(self, request_context: object | None = None) -> AgentIdentity:
        registry = self._current_registry()
        if not registry.credential_refs:
            logger.info("PHOS MCP auth compatibility_no_runtime_credential_refs=true")
            return await super().authenticate(request_context)
        headers = getattr(getattr(request_context, "request", None), "headers", None) or {}
        value = headers.get("authorization") or headers.get("Authorization")
        header_present = value is not None
        bearer_format_valid = isinstance(value, str) and value.startswith("Bearer ")
        logger.info("PHOS MCP auth request header_present=%s bearer_format_valid=%s credential_refs=%s",
                    header_present, bearer_format_valid, len(registry.credential_refs))
        if not header_present:
            logger.info("PHOS MCP auth failed reason=missing_header")
            raise PermissionError("authentication_failed")
        if not bearer_format_valid:
            logger.info("PHOS MCP auth failed reason=invalid_scheme")
            raise PermissionError("authentication_failed")
        token = value[7:]
        if not token:
            logger.info("PHOS MCP auth failed reason=empty_bearer")
            raise PermissionError("authentication_failed")
        secret_present = False
        for agent_id, secret_ref in registry.credential_refs.items():
            try:
                expected = self._secrets.get_secret(secret_ref)
            except (SecretsError, OSError):
                logger.info("PHOS MCP auth credential agent=%s secret_ref=%s secret_present=unknown match=false",
                            agent_id, secret_ref)
                logger.info("PHOS MCP auth failed reason=secret_store_failure")
                raise PermissionError("authentication_failed") from None
            present = bool(expected)
            secret_present = secret_present or present
            matched = present and secrets.compare_digest(token, expected)
            logger.info("PHOS MCP auth credential agent=%s secret_ref=%s secret_present=%s match=%s",
                        agent_id, secret_ref, present, matched)
            if matched:
                identity = registry.get(agent_id)
                if identity is None:
                    logger.info("PHOS MCP auth failed reason=registry_agent_missing")
                    raise PermissionError("authentication_failed")
                return identity
        logger.info("PHOS MCP auth failed reason=%s", "no_matching_credential" if secret_present else "secret_missing")
        raise PermissionError("authentication_failed")


class McpAuthenticationModeAuthenticator:
    """Resolve MCP principals once per request for bearer, none, or mixed mode.

    An explicit malformed or invalid credential is always rejected; only an
    absent header may select the anonymous principal in mixed mode.
    """
    def __init__(self, bearer: BearerAgentAuthenticator, *, mode: str = "bearer",
                 anonymous_permission_profile: AgentProfile | str = AgentProfile.STANDARD) -> None:
        if mode not in {"bearer", "none", "mixed"}:
            raise ValueError("unsupported MCP authentication mode")
        self._bearer, self._mode = bearer, mode
        self._anonymous = anonymous_mcp_identity(anonymous_permission_profile)
        self._lock = RLock()

    def configure(self, *, mode: str, anonymous_permission_profile: AgentProfile | str) -> None:
        """Atomically publish validated MCP authentication settings for new requests."""
        if mode not in {"bearer", "none", "mixed"}:
            raise ValueError("unsupported MCP authentication mode")
        anonymous = anonymous_mcp_identity(anonymous_permission_profile)
        with self._lock:
            self._mode, self._anonymous = mode, anonymous

    def settings(self) -> dict[str, str]:
        with self._lock:
            return {"mode": self._mode, "anonymous_permission_profile": self._anonymous.permission_profile.value}

    @staticmethod
    def _authorization(request_context: object | None):
        headers = getattr(getattr(request_context, "request", None), "headers", None) or {}
        return headers.get("authorization") or headers.get("Authorization")

    async def authenticate(self, request_context: object | None = None) -> AgentIdentity:
        # Snapshot the pair together: a request never observes a mode and
        # anonymous profile from different configuration generations.
        with self._lock:
            mode, anonymous = self._mode, self._anonymous
        value = self._authorization(request_context)
        if mode == "bearer":
            return await self._bearer.authenticate(request_context)
        if mode == "none":
            if value is not None and (not isinstance(value, str) or not value.startswith("Bearer ") or not value[7:]):
                raise PermissionError("authentication_failed")
            return anonymous
        if value is None:
            return anonymous
        # Do not downgrade an explicitly supplied invalid/revoked bearer.
        return await self._bearer.authenticate(request_context)
