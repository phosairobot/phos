"""Application boundary for live MCP agent-directory administration."""
from __future__ import annotations

import re
from threading import RLock

from robot.config import ConfigRepository, ConfigurationError, RuntimeConfig

from .agent_credentials import AgentCredentialError, AgentCredentialService
from .agent_identity import AgentIdentity, AgentRegistry, AgentRegistryProvider
from .agent_permissions import AgentProfile
from robot.ui.skins import SkinRepository, apply_appearance_snapshot, available_appearance_snapshot


_UNSET = object()
DEFAULT_APPEARANCE_POLICY = {"participate": True, "priority": 50, "activity_timeout_seconds": 30}


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
        self._effective_appearance_applier = None
        self._appearance_activity_tracker = None

    def register_effective_appearance_applier(self, applier) -> None:
        """Install the lifecycle-owned transient display update callback."""
        self._effective_appearance_applier = applier

    def _refresh_effective_appearance(self) -> None:
        if self._effective_appearance_applier is not None:
            self._effective_appearance_applier()

    def register_appearance_activity_tracker(self, tracker) -> None:
        """Attach the lifecycle-owned, non-persistent activity tracker."""
        self._appearance_activity_tracker = tracker
        tracker.recompute()

    def _refresh_appearance_arbitration(self) -> None:
        if self._appearance_activity_tracker is not None:
            self._appearance_activity_tracker.recompute()

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

    def _save_and_publish(self, registry: AgentRegistry, *, presentation_owner_agent_id=_UNSET) -> None:
        document = self._repository.document()
        document["agents"] = {"default_agent": registry.default_agent, "entries": [
            {"id": item.id, "display_name": item.display_name,
             "permission_profile": item.permission_profile.value,
             **({"skin": item.skin} if item.skin is not None else {}),
             **({"appearance": item.appearance} if item.appearance is not None else {}),
             **({"credential": {"type": "bearer", "secret_ref": registry.credential_refs[item.id]}}
                if item.id in registry.credential_refs else {})}
            for item in registry.entries
        ]}
        if presentation_owner_agent_id is not _UNSET:
            appearance = document.setdefault("appearance", {"config_file": None})
            appearance.setdefault("config_file", None)
            appearance["presentation_owner"] = {
                "agent_id": presentation_owner_agent_id,
            }
        candidate = self._repository.load(document)
        self._repository.save(candidate)
        self._provider.replace(candidate.agent_registry)

    def _replace(self, *, entries=None, refs=None, default_agent=None, presentation_owner_agent_id=_UNSET) -> AgentRegistry:
        current = self._provider.get()
        registry = AgentRegistry(default_agent or current.default_agent,
                                 tuple(entries if entries is not None else current.entries),
                                 refs if refs is not None else current.credential_refs)
        self._save_and_publish(registry, presentation_owner_agent_id=presentation_owner_agent_id)
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

    def _appearance_policy_view(self, identity):
        policy = dict(DEFAULT_APPEARANCE_POLICY if identity.appearance is None else identity.appearance)
        assignment = self._skin_view(identity)
        if not policy["participate"]: reason = "participation_disabled"
        elif assignment["state"] == "disabled": reason = "skin_disabled"
        elif assignment["state"] == "global": reason = "skin_not_assigned"
        elif assignment["state"] == "missing": reason = "skin_missing"
        else: reason = "eligible"
        return {**policy, "assigned_skin_id": assignment["id"], "skin_assignment": assignment["state"],
                "static_eligible": reason == "eligible", "reason": reason}

    def appearance_arbitration_candidates(self) -> dict:
        """Read-only configuration projection consumed by activity arbitration."""
        with self._lock:
            config, registry = self._repository.load(), self._provider.get()
            agents = [{"agent_id": item.id, "display_name": item.display_name,
                       "appearance_policy": self._appearance_policy_view(item)}
                      for item in registry.entries]
            default = registry.default_identity
            default_policy = self._appearance_policy_view(default)
            anonymous_reason = ("anonymous_mcp_disabled" if not config.anonymous_mcp_use_default_agent_skin
                                else default_policy["reason"])
            # Fixed priority -1 keeps this fallback below every eligible
            # authenticated agent (whose validated priority range begins at 0;
            # a tie is resolved in favour of a real agent by the tracker).
            agents.append({"agent_id": "anonymous-mcp", "display_name": "Anonymous MCP",
                           "appearance_policy": {**default_policy, "priority": -1,
                                                 "activity_timeout_seconds": 30,
                                                 "static_eligible": anonymous_reason == "eligible",
                                                 "reason": anonymous_reason,
                                                 "default_agent_id": default.id,
                                                 "default_agent_display_name": default.display_name}})
            return {"mode": config.appearance_mode, "agents": agents}

    def appearance_arbitration(self) -> dict:
        if self._appearance_activity_tracker is None:
            return {"mode": self._repository.load().appearance_mode, "selected_agent_id": None,
                    "selected_display_name": None, "selected_skin_id": None,
                    "selection_reason": "activity_tracker_unavailable", "effective_priority": None,
                    "remaining_lease_seconds": None, "next_expiry_monotonic": None, "agents": []}
        return self._appearance_activity_tracker.status()

    def appearance_mode(self) -> dict:
        with self._lock:
            return {"mode": self._repository.load().appearance_mode}

    def set_appearance_mode(self, mode: str) -> dict:
        """Persist mode through the canonical repository and reconcile live."""
        if mode not in {"global", "follow_agent"}:
            raise AgentManagementError("invalid_appearance_mode")
        with self._lock:
            document = self._repository.document()
            appearance = document.setdefault("appearance", {"config_file": None})
            appearance.setdefault("config_file", None)
            appearance["mode"] = mode
            candidate = self._repository.load(document)
            self._repository.save(candidate)
            self._refresh_appearance_arbitration()
            self._refresh_effective_appearance()
            return {"mode": candidate.appearance_mode}

    def anonymous_mcp_appearance(self) -> dict:
        with self._lock:
            config = self._repository.load()
            default = self._provider.get().default_identity
            assignment = self._skin_view(default)
            return {"enabled": config.anonymous_mcp_use_default_agent_skin,
                    "default_agent_id": default.id, "default_agent_display_name": default.display_name,
                    "skin": assignment}

    def set_anonymous_mcp_appearance(self, enabled: bool) -> dict:
        if type(enabled) is not bool:
            raise AgentManagementError("invalid_anonymous_mcp_appearance")
        with self._lock:
            document = self._repository.document()
            appearance = document.setdefault("appearance", {"config_file": None})
            appearance.setdefault("config_file", None)
            appearance["anonymous_mcp_use_default_agent_skin"] = enabled
            candidate = self._repository.load(document)
            self._repository.save(candidate)
            self._refresh_appearance_arbitration()
            self._refresh_effective_appearance()
            return self.anonymous_mcp_appearance()

    def available_skins(self) -> list[dict]:
        """Project the canonical catalog for administration selectors."""
        with self._lock:
            return [{"id": skin.id, "display_name": skin.name}
                    for skin in self._skin_repository().list()]

    def presentation_owner(self) -> dict:
        """Return the explicit owner preference; it is unrelated to MCP sessions."""
        with self._lock:
            config = self._repository.load()
            owner_id = config.presentation_owner_agent_id
            owner = self._provider.get().get(owner_id) if owner_id is not None else None
            return {"agent_id": owner_id,
                    "display_name": None if owner is None else owner.display_name}

    def effective_skin(self) -> dict:
        """Resolve intended presentation without changing global display configuration."""
        with self._lock:
            config = self._repository.load()
            owner_id = config.presentation_owner_agent_id
            if owner_id is None:
                return {"configured_owner_id": None, "owner_display_name": None,
                        "assigned_skin_id": None, "state": "global",
                        "effective_skin_id": None, "fallback_reason": "no_owner"}
            owner = self._provider.get().get(owner_id)
            # RuntimeConfig rejects a dangling persisted owner.  Retain a safe
            # degraded result here if a concurrent external replacement occurs.
            if owner is None:
                return {"configured_owner_id": owner_id, "owner_display_name": None,
                        "assigned_skin_id": None, "state": "global",
                        "effective_skin_id": None, "fallback_reason": "owner_unavailable"}
            assignment = self._skin_view(owner)
            result = {"configured_owner_id": owner.id, "owner_display_name": owner.display_name,
                      "assigned_skin_id": assignment["id"]}
            if assignment["state"] == "assigned":
                return {**result, "state": "assigned", "effective_skin_id": assignment["id"],
                        "skin_display_name": assignment["display_name"], "fallback_reason": None}
            reason = {"global": "no_skin_assignment", "disabled": "assignment_disabled",
                      "missing": "missing_skin"}[assignment["state"]]
            return {**result, "state": "global", "effective_skin_id": None,
                    "fallback_reason": reason,
                    **({"missing_skin_id": assignment["id"]} if assignment["state"] == "missing" else {})}

    def effective_appearance_config(self) -> tuple[RuntimeConfig, dict]:
        """Build a transient runtime config from the current global base and owner.

        This never persists the resolved skin; callers apply it only through the
        lifecycle render-loop boundary.
        """
        with self._lock:
            global_config = self._repository.load()
            resolved = self.effective_skin()
            if resolved["state"] != "assigned":
                return global_config, resolved
            skin = self._skin_repository().get(resolved["effective_skin_id"])
            snapshot, missing = available_appearance_snapshot(skin.appearance, self._repository.active_path.parent)
            if missing:
                return global_config, {**resolved, "state": "global", "effective_skin_id": None,
                                       "fallback_reason": "missing_skin_assets", "missing_assets": missing}
            return RuntimeConfig.from_dict(apply_appearance_snapshot(global_config.to_dict(), snapshot),
                                           base_dir=self._repository.active_path.parent), resolved

    def automatic_appearance_config(self, arbitration: dict) -> tuple[RuntimeConfig, dict]:
        """Resolve the mode-authoritative transient appearance.

        The legacy Presentation Owner remains persisted/readable, but never
        participates in automatic runtime selection.  This method does not
        mutate either the global display configuration or that legacy value.
        """
        with self._lock:
            global_config = self._repository.load()
            mode = global_config.appearance_mode
            selected_id = arbitration.get("selected_agent_id") if mode == "follow_agent" else None
            base = {"authority": "automatic", "mode": mode,
                    "legacy_presentation_owner_id": global_config.presentation_owner_agent_id,
                    "selected_agent_id": selected_id,
                    "selected_display_name": arbitration.get("selected_display_name") if selected_id else None,
                    "selection_reason": arbitration.get("selection_reason")}
            if selected_id is None:
                reason = "global_mode" if mode == "global" else arbitration.get("selection_reason", "no_active_eligible_agents")
                return global_config, {**base, "state": "global", "effective_skin_id": None,
                                       "fallback_reason": reason}
            identity = (self._provider.get().default_identity if selected_id == "anonymous-mcp"
                        else self._provider.get().get(selected_id))
            assignment = self._skin_view(identity) if identity is not None else {"state": "missing", "id": None}
            if assignment["state"] != "assigned":
                return global_config, {**base, "state": "global", "effective_skin_id": None,
                                       "fallback_reason": "selection_skin_unavailable"}
            skin = self._skin_repository().get(assignment["id"])
            snapshot, missing = available_appearance_snapshot(skin.appearance, self._repository.active_path.parent)
            if missing:
                return global_config, {**base, "state": "global", "effective_skin_id": None,
                                       "fallback_reason": "missing_skin_assets", "missing_assets": missing}
            return RuntimeConfig.from_dict(apply_appearance_snapshot(global_config.to_dict(), snapshot),
                                           base_dir=self._repository.active_path.parent), {
                **base, "state": "assigned", "effective_skin_id": skin.id,
                "skin_display_name": skin.name, "fallback_reason": None,
            }

    def effective_led_palette(self, arbitration: dict | None = None, *, resolved: dict | None = None) -> dict:
        """Read the skin palette selected by appearance arbitration only.

        This is intentionally a metadata projection for SKIN-03a.  It does
        not configure, command, or otherwise reach the LED arbiter.
        """
        with self._lock:
            config = self._repository.load()
            arbitration = arbitration or self.appearance_arbitration()
            selected = arbitration.get("selected_agent_id") if config.appearance_mode == "follow_agent" else None
            # The LED theme is part of the transient appearance, not a second
            # arbitration result.  If resolving the selected skin fell back to
            # global appearance (for example because an image asset is gone),
            # keep the semantic LEDs global too.
            if resolved is not None and resolved.get("state") != "assigned":
                return {"state": "global", "skin_id": None, "colors": {},
                        "fallback_reason": resolved.get("fallback_reason", "appearance_not_applied")}
            if selected is None:
                return {"state": "global", "skin_id": None, "colors": {},
                        "fallback_reason": "global_mode" if config.appearance_mode == "global" else arbitration.get("selection_reason")}
            identity = self._provider.get().default_identity if selected == "anonymous-mcp" else self._provider.get().get(selected)
            assignment = self._skin_view(identity) if identity is not None else {"state": "missing"}
            if assignment["state"] != "assigned":
                return {"state": "global", "skin_id": None, "colors": {}, "fallback_reason": "selection_skin_unavailable"}
            result = self._skin_repository().resolve_led_palette(assignment["id"])
            return {**result, "selected_agent_id": selected,
                    "fallback_reason": None if result["state"] == "configured" else result["state"]}

    def set_presentation_owner(self, agent_id: str) -> dict:
        with self._lock:
            current = self._provider.get()
            if not isinstance(agent_id, str) or current.get(agent_id) is None:
                raise AgentManagementError("unknown_agent")
            self._save_and_publish(current, presentation_owner_agent_id=agent_id)
            self._refresh_effective_appearance()
            return self.presentation_owner()

    def clear_presentation_owner(self) -> dict:
        with self._lock:
            self._save_and_publish(self._provider.get(), presentation_owner_agent_id=None)
            self._refresh_effective_appearance()
            return self.presentation_owner()

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
                item = self._view(registry, identity, status); item["skin_assignment"] = self._skin_view(identity); item["appearance_policy"] = self._appearance_policy_view(identity); result.append(item)
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
            item = self._view(registry, identity, status); item["skin_assignment"] = self._skin_view(identity); item["appearance_policy"] = self._appearance_policy_view(identity); return item

    def create_agent(self, agent_id: str, display_name: str, permission_profile: str, skin=None, appearance=None) -> dict:
        with self._lock:
            current = self._provider.get()
            if current.get(agent_id) is not None:
                raise AgentManagementError("duplicate_agent")
            try:
                identity = AgentIdentity(agent_id, permission_profile, display_name, skin, appearance)
                self._validate_skin(identity.skin)
            except AgentManagementError:
                raise
            except ValueError as error:
                raise AgentManagementError("invalid_agent") from error
            registry = self._replace(entries=(*current.entries, identity))
            self._refresh_appearance_arbitration()
            return self._view(registry, identity)

    def update_agent(self, agent_id: str, display_name: str, permission_profile: str, skin=None, appearance=None) -> dict:
        with self._lock:
            current = self._provider.get()
            existing = current.get(agent_id)
            if existing is None:
                raise AgentManagementError("unknown_agent")
            try:
                replacement = AgentIdentity(agent_id, permission_profile, display_name,
                                            existing.skin if skin is None else skin,
                                            existing.appearance if appearance is None else appearance)
                self._validate_skin(replacement.skin)
            except AgentManagementError:
                raise
            except ValueError as error:
                raise AgentManagementError("invalid_agent") from error
            registry = self._replace(entries=tuple(replacement if item.id == agent_id else item for item in current.entries))
            self._refresh_appearance_arbitration()
            if self._repository.load().presentation_owner_agent_id == agent_id:
                self._refresh_effective_appearance()
            item = self._view(registry, replacement); item["appearance_policy"] = self._appearance_policy_view(replacement); return item

    def set_default_agent(self, agent_id: str) -> dict:
        """Persist and publish a new default identity without changing credentials."""
        with self._lock:
            current = self._provider.get()
            identity = current.get(agent_id)
            if identity is None:
                raise AgentManagementError("unknown_agent")
            registry = self._replace(default_agent=agent_id)
            self._refresh_appearance_arbitration()
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
                          refs={key: value for key, value in current.credential_refs.items() if key != agent_id},
                          presentation_owner_agent_id=(None if self._repository.load().presentation_owner_agent_id == agent_id
                                                       else _UNSET))
            if self._appearance_activity_tracker is not None:
                self._appearance_activity_tracker.forget(agent_id)
            if self._repository.load().presentation_owner_agent_id is None:
                self._refresh_effective_appearance()

    def skin_references(self, skin_id: str) -> tuple[str, ...]:
        return tuple(item.id for item in self._provider.get().entries
                     if item.skin is not None and item.skin.get("id") == skin_id)
