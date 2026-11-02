"""Safe lifecycle operations for configured MCP bearer credentials."""
from __future__ import annotations

from dataclasses import dataclass, field
import logging
import secrets
from threading import RLock

from robot.secrets import SecretsError, SecretsService

from .agent_identity import AgentRegistry, AgentRegistryProvider

logger = logging.getLogger(__name__)


class AgentCredentialError(ValueError):
    """A sanitized credential-management error."""


@dataclass(frozen=True)
class AgentCredentialStatus:
    """Non-secret credential metadata safe for a future administration UI."""

    agent_id: str
    credential_type: str
    secret_ref: str
    configured: bool
    active: bool


@dataclass(frozen=True)
class IssuedAgentCredential:
    """One-time bearer disclosure; its repr intentionally omits the token."""

    agent_id: str
    status: AgentCredentialStatus
    token: str = field(repr=False)


class AgentCredentialService:
    """Creates, rotates, revokes and reports configured agent credentials.

    The registry owns only non-secret references.  This service is the sole
    lifecycle boundary over ``SecretsService`` and deliberately has no method
    to retrieve an already stored bearer value.
    """

    def __init__(self, registry: AgentRegistry | AgentRegistryProvider, secrets_service: SecretsService) -> None:
        self._registry = registry
        self._secrets = secrets_service
        self._lock = RLock()

    def _status(self, agent_id: str) -> AgentCredentialStatus:
        registry = self._registry.get() if isinstance(self._registry, AgentRegistryProvider) else self._registry
        if registry.get(agent_id) is None:
            raise AgentCredentialError("unknown_agent")
        secret_ref = registry.credential_refs.get(agent_id)
        if secret_ref is None:
            raise AgentCredentialError("credential_not_configured")
        try:
            active = self._secrets.has_secret(secret_ref)
        except (SecretsError, OSError) as error:
            raise AgentCredentialError("credential_status_failed") from error
        return AgentCredentialStatus(agent_id, "bearer", secret_ref, configured=True, active=active)

    @staticmethod
    def _new_token() -> str:
        """Return 256 bits of URL-safe bearer entropy from the OS CSPRNG."""
        return secrets.token_urlsafe(32)

    def create(self, agent_id: str) -> IssuedAgentCredential:
        """Create a bearer once; callers must use rotate for an active entry."""
        with self._lock:
            status = self._status(agent_id)
            if status.active:
                raise AgentCredentialError("credential_already_exists")
            token = self._new_token()
            try:
                self._secrets.set_secret(status.secret_ref, token)
            except (SecretsError, OSError) as error:
                raise AgentCredentialError("credential_creation_failed") from error
            issued = IssuedAgentCredential(agent_id, self._status(agent_id), token)
        logger.info("PHOS MCP credential created agent=%s", agent_id)
        return issued

    def rotate(self, agent_id: str) -> IssuedAgentCredential:
        """Atomically replace an active bearer at its stable secret reference."""
        with self._lock:
            status = self._status(agent_id)
            if not status.active:
                raise AgentCredentialError("credential_not_active")
            token = self._new_token()
            try:
                # SecretsService writes a complete encrypted replacement before
                # replacing the store path, so an old value remains on failure.
                self._secrets.set_secret(status.secret_ref, token)
            except (SecretsError, OSError) as error:
                raise AgentCredentialError("credential_rotation_failed") from error
            issued = IssuedAgentCredential(agent_id, self._status(agent_id), token)
        logger.info("PHOS MCP credential rotated agent=%s", agent_id)
        return issued

    def revoke(self, agent_id: str) -> AgentCredentialStatus:
        """Remove the bearer while retaining its configured agent identity."""
        with self._lock:
            status = self._status(agent_id)
            try:
                self._secrets.remove_secret(status.secret_ref)
            except (SecretsError, OSError) as error:
                raise AgentCredentialError("credential_revocation_failed") from error
            revoked = self._status(agent_id)
        logger.info("PHOS MCP credential revoked agent=%s", agent_id)
        return revoked

    def status(self, agent_id: str) -> AgentCredentialStatus:
        """Return non-secret configured and active state; never a bearer value."""
        with self._lock:
            return self._status(agent_id)
