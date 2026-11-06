"""Application boundary for live MCP agent-directory administration."""
from __future__ import annotations

import re
from threading import RLock

from robot.config import ConfigRepository, ConfigurationError

from .agent_credentials import AgentCredentialError, AgentCredentialService
from .agent_identity import AgentIdentity, AgentRegistry, AgentRegistryProvider
from .agent_permissions import AgentProfile
from robot.ui.skins import SkinRepository


class AgentManagementError(ValueError):
    """Sanitized management error suitable for an administration adapter."""

    status = 400

    def document(self) -> dict:
        return {"error": {"code": str(self), "message": "Agent management request was rejected.", "details": {}}}


class AgentManagementService:
    """Persist and publish complete agent registries without exposing config.

    The provider is changed only after a replacement registry validates and is
    persisted.  Credential values remain exclusively behind
    ``AgentCredentialService``.
    """
    def __init__(self, repository: ConfigRepository, registry_provider: AgentRegistryProvider,
                 credentials: AgentCredentialService) -> None:
        self._repository = repository
        self._provider = registry_provider
        self._credentials = credentials
        self._lock = RLock()
        self._skins = None

    def _skin_repository(self):
        if self._skins is None:
            config = self._repository.load()
            self._skins = SkinRepository(self._repository.active_path,
                                         catalog_file=config.resolve_path(config.appearance_catalog_file))
        return self._skins

    def _validate_skin(self, skin) -> None:
        if skin is not None and skin["enabled"]:
            try: self._skin_repository().get(skin["id"])
            except ConfigurationError as error: raise AgentManagementError("unknown_skin") from error

    @staticmethod
    def profiles() -> tuple[str, ...]:
        return tuple(profile.value for profile in AgentProfile)

    @staticmethod
    def credential_ref_for(agent_id: str) -> str:
        # Keep the stable id untouched; this only encodes it into the separate
        # secret-reference namespace accepted by SecretsService.
        segment = re.sub(r"[^a-z0-9_]", "_", agent_id.lower())
        if not segment or not segment[0].isalpha():
            segment = f"agent_{segment}"
        return f"mcp.agents.{segment}.token"

    def _save_and_publish(self, registry: AgentRegistry) -> None:
        document = self._repository.document()
        document["agents"] = {"default_agent": registry.default_agent, "entries": [
            {"id": item.id, "display_name": item.display_name,
             "permission_profile": item.permission_profile.value,
             **({"skin": item.skin} if item.skin is not None else {}),
             **({"credential": {"type": "bearer", "secret_ref": registry.credential_refs[item.id]}}
                if item.id in registry.credential_refs else {})}
            for item in registry.entries
        ]}
        candidate = self._repository.load(document)
        self._repository.save(candidate)
        self._provider.replace(candidate.agent_registry)

    def _replace(self, *, entries=None, refs=None, default_agent=None) -> AgentRegistry:
        current = self._provider.get()
        registry = AgentRegistry(default_agent or current.default_agent,
                                 tuple(entries if entries is not None else current.entries),
                                 refs if refs is not None else current.credential_refs)
        self._save_and_publish(registry)
        return registry

    @staticmethod
    def _view(registry: AgentRegistry, identity: AgentIdentity, status=None) -> dict:
        configured = identity.id in registry.credential_refs
        return {"agent_id": identity.id, "display_name": identity.display_name,
                "permission_profile": identity.permission_profile.value,
                "skin": identity.skin,
                "default": identity.id == registry.default_agent,
                "credential_configured": configured if status is None else status.configured,
                "credential_active": None if not configured or status is None else status.active}

    def _skin_view(self, identity):
        if identity.skin is None: return {"state": "global", "id": None}
        if not identity.skin["enabled"]: return {"state": "disabled", "id": identity.skin["id"]}
        try: skin = self._skin_repository().get(identity.skin["id"])
        except ConfigurationError: return {"state": "missing", "id": identity.skin["id"]}
        return {"state": "assigned", "id": skin.id, "display_name": skin.name}

    def list_agents(self) -> list[dict]:
        with self._lock:
            registry = self._provider.get()
            result = []
            for identity in registry.entries:
                status = None
                if identity.id in registry.credential_refs:
                    try:
                        status = self._credentials.status(identity.id)
                    except AgentCredentialError as error:
                        if str(error) != "credential_status_failed":
                            raise
                item = self._view(registry, identity, status); item["skin_assignment"] = self._skin_view(identity); result.append(item)
            return result

    def get_agent(self, agent_id: str) -> dict:
        with self._lock:
            registry = self._provider.get()
            identity = registry.get(agent_id)
            if identity is None:
                raise AgentManagementError("unknown_agent")
            status = None
            if agent_id in registry.credential_refs:
                try: status = self._credentials.status(agent_id)
                except AgentCredentialError as error:
                    if str(error) != "credential_status_failed": raise
            item = self._view(registry, identity, status); item["skin_assignment"] = self._skin_view(identity); return item

    def create_agent(self, agent_id: str, display_name: str, permission_profile: str, skin=None) -> dict:
        with self._lock:
            current = self._provider.get()
            if current.get(agent_id) is not None:
                raise AgentManagementError("duplicate_agent")
            try:
                identity = AgentIdentity(agent_id, permission_profile, display_name, skin)
                self._validate_skin(identity.skin)
            except ValueError as error:
                raise AgentManagementError("invalid_agent") from error
            registry = self._replace(entries=(*current.entries, identity))
            return self._view(registry, identity)

    def update_agent(self, agent_id: str, display_name: str, permission_profile: str, skin=None) -> dict:
        with self._lock:
            current = self._provider.get()
            existing = current.get(agent_id)
            if existing is None:
                raise AgentManagementError("unknown_agent")
            try:
                replacement = AgentIdentity(agent_id, permission_profile, display_name,
                                            existing.skin if skin is None else skin)
                self._validate_skin(replacement.skin)
            except ValueError as error:
                raise AgentManagementError("invalid_agent") from error
            registry = self._replace(entries=tuple(replacement if item.id == agent_id else item for item in current.entries))
            return self._view(registry, replacement)

    def set_default_agent(self, agent_id: str) -> dict:
        """Persist and publish a new default identity without changing credentials."""
        with self._lock:
            current = self._provider.get()
            identity = current.get(agent_id)
            if identity is None:
                raise AgentManagementError("unknown_agent")
            registry = self._replace(default_agent=agent_id)
            return self._view(registry, identity)

    def create_credential(self, agent_id: str):
        with self._lock:
            current = self._provider.get()
            if current.get(agent_id) is None:
                raise AgentManagementError("unknown_agent")
            if agent_id not in current.credential_refs:
                ref = self.credential_ref_for(agent_id)
                if ref in current.credential_refs.values():
                    raise AgentManagementError("credential_reference_conflict")
                self._replace(refs={**current.credential_refs, agent_id: ref})
            try:
                return self._credentials.create(agent_id)
            except AgentCredentialError as error:
                raise AgentManagementError(str(error)) from error

    def rotate_credential(self, agent_id: str):
        try: return self._credentials.rotate(agent_id)
        except AgentCredentialError as error: raise AgentManagementError(str(error)) from error

    def revoke_credential(self, agent_id: str):
        try: return self._credentials.revoke(agent_id)
        except AgentCredentialError as error: raise AgentManagementError(str(error)) from error

    def delete_agent(self, agent_id: str) -> None:
        with self._lock:
            current = self._provider.get()
            if current.get(agent_id) is None:
                raise AgentManagementError("unknown_agent")
            if agent_id == current.default_agent:
                raise AgentManagementError("default_agent")
            if agent_id in current.credential_refs:
                # Revoke before publishing removal: any failure leaves both the
                # configured identity and its bearer intact; success fails closed.
                self.revoke_credential(agent_id)
            self._replace(entries=tuple(item for item in current.entries if item.id != agent_id),
                          refs={key: value for key, value in current.credential_refs.items() if key != agent_id})

    def skin_references(self, skin_id: str) -> tuple[str, ...]:
        return tuple(item.id for item in self._provider.get().entries
                     if item.skin is not None and item.skin.get("id") == skin_id)
