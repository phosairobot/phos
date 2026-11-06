import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from robot.secrets import SecretsError, SecretsService
from robot.config import ConfigRepository, ConfigurationError, RuntimeConfig, load_document
from robot.services import (AgentCredentialError, AgentCredentialService, AgentIdentity, AgentRegistry,
                            AgentManagementError, AgentManagementService, AgentRegistryProvider, BearerAgentAuthenticator)


def _registry():
    return AgentRegistry("standard", (AgentIdentity("standard", "standard"), AgentIdentity("observer", "observer")), {
        "standard": "mcp.standard.token", "observer": "mcp.observer.token",
    })


def _context(token):
    return SimpleNamespace(request=SimpleNamespace(headers={"Authorization": f"Bearer {token}"}))


def _skin_management(tmp_path, skin=None):
    tmp_path.mkdir(parents=True, exist_ok=True)
    document = load_document(); document.pop("appearance", None); document["logging"]["file"] = None
    if skin is not None: document["agents"]["entries"][0]["skin"] = skin
    path = tmp_path / "phos.json"; path.write_text(json.dumps(document))
    repository = ConfigRepository(path); config = repository.load()
    provider = AgentRegistryProvider(config.agent_registry)
    return AgentManagementService(repository, provider, AgentCredentialService(provider, SecretsService(tmp_path / ".secrets"))), path


def test_agent_skin_assignment_read_model_distinguishes_global_disabled_assigned_and_missing(tmp_path):
    global_management, _ = _skin_management(tmp_path / "global")
    assert global_management.get_agent("local-agent")["skin_assignment"] == {"state": "global", "id": None}

    disabled_management, _ = _skin_management(tmp_path / "disabled", {"enabled": False, "id": "ambient"})
    assert disabled_management.get_agent("local-agent")["skin_assignment"] == {"state": "disabled", "id": "ambient"}

    assigned_management, _ = _skin_management(tmp_path / "assigned", {"enabled": True, "id": "ambient"})
    assert assigned_management.get_agent("local-agent")["skin_assignment"] == {"state": "assigned", "id": "ambient", "display_name": "Ambient"}

    missing_management, missing_path = _skin_management(tmp_path / "missing", {"enabled": True, "id": "removed-skin"})
    before = missing_path.read_bytes()
    assert missing_management.get_agent("local-agent")["skin_assignment"] == {"state": "missing", "id": "removed-skin"}
    assert missing_management.get_agent("local-agent")["skin"] == {"enabled": True, "id": "removed-skin"}
    assert missing_path.read_bytes() == before


def test_credential_lifecycle_preserves_persisted_agent_skin_assignment(tmp_path):
    management, path = _skin_management(tmp_path, {"enabled": True, "id": "ambient"})
    expected = {"enabled": True, "id": "ambient"}
    for operation in (management.create_credential, management.rotate_credential, management.revoke_credential):
        operation("local-agent")
        persisted = RuntimeConfig.from_file(path).agent_registry.default_identity
        assert persisted.skin == expected and persisted.permission_profile.value == "standard"
        assert management.get_agent("local-agent")["skin"] == expected


def test_presentation_owner_configuration_is_backward_compatible_and_validates_registered_agents(tmp_path):
    document = load_document()
    document.pop("appearance", None)
    config = RuntimeConfig.from_dict(document, base_dir=tmp_path, check_paths=False)
    assert config.presentation_owner_agent_id is None
    assert config.to_dict()["appearance"]["presentation_owner"] == {"agent_id": None}

    document["appearance"] = {"config_file": None, "presentation_owner": {"agent_id": "missing"}}
    with pytest.raises(ConfigurationError, match="presentation_owner.agent_id"):
        RuntimeConfig.from_dict(document, base_dir=tmp_path, check_paths=False)


def test_presentation_owner_persists_and_effective_skin_uses_assignment_projection(tmp_path):
    management, path = _skin_management(tmp_path, {"enabled": True, "id": "ambient"})
    before_display = RuntimeConfig.from_file(path).to_dict()["display"]

    assert management.presentation_owner() == {"agent_id": None, "display_name": None}
    assert management.effective_skin()["fallback_reason"] == "no_owner"
    assert management.set_presentation_owner("local-agent") == {
        "agent_id": "local-agent", "display_name": "Local MCP agent"}
    assert RuntimeConfig.from_file(path).presentation_owner_agent_id == "local-agent"
    assert management.effective_skin() == {
        "configured_owner_id": "local-agent", "owner_display_name": "Local MCP agent",
        "assigned_skin_id": "ambient", "state": "assigned", "effective_skin_id": "ambient",
        "skin_display_name": "Ambient", "fallback_reason": None,
    }
    assert RuntimeConfig.from_file(path).to_dict()["display"] == before_display
    assert management.clear_presentation_owner() == {"agent_id": None, "display_name": None}
    assert RuntimeConfig.from_file(path).presentation_owner_agent_id is None


@pytest.mark.parametrize(("skin", "reason"), [
    ({"enabled": False, "id": "ambient"}, "assignment_disabled"),
    (None, "no_skin_assignment"),
    ({"enabled": True, "id": "removed-skin"}, "missing_skin"),
])
def test_presentation_owner_global_fallbacks_preserve_assignment(tmp_path, skin, reason):
    management, path = _skin_management(tmp_path, skin)
    before = path.read_bytes()
    management.set_presentation_owner("local-agent")

    resolved = management.effective_skin()
    assert resolved["state"] == "global"
    assert resolved["effective_skin_id"] is None
    assert resolved["fallback_reason"] == reason
    if reason == "missing_skin":
        assert resolved["missing_skin_id"] == "removed-skin"
    # Selection changes only the owner field; it never normalizes or rewrites
    # the retained skin assignment while resolving a fallback.
    assert RuntimeConfig.from_file(path).agent_registry.default_identity.skin == skin
    assert path.read_bytes() != before


def test_owner_deletion_clears_owner_in_the_same_registry_persistence_update(tmp_path):
    management, path = _skin_management(tmp_path)
    management.create_agent("presentation", "Presentation", "observer", {"enabled": True, "id": "ambient"})
    management.set_presentation_owner("presentation")
    management.delete_agent("presentation")

    reloaded = RuntimeConfig.from_file(path)
    assert reloaded.presentation_owner_agent_id is None
    assert reloaded.agent_registry.get("presentation") is None
    assert management.presentation_owner() == {"agent_id": None, "display_name": None}
    assert management.effective_skin()["fallback_reason"] == "no_owner"


def test_deleting_an_unrelated_agent_preserves_presentation_owner(tmp_path):
    management, path = _skin_management(tmp_path)
    management.create_agent("presentation", "Presentation", "observer")
    management.create_agent("other", "Other", "observer")
    management.set_presentation_owner("presentation")
    management.delete_agent("other")

    assert RuntimeConfig.from_file(path).presentation_owner_agent_id == "presentation"
    assert management.presentation_owner() == {"agent_id": "presentation", "display_name": "Presentation"}


def test_failed_owner_update_and_failed_owner_deletion_preserve_persisted_owner(tmp_path, monkeypatch):
    management, path = _skin_management(tmp_path)
    management.create_agent("presentation", "Presentation", "observer")
    management.set_presentation_owner("presentation")
    original_save = management._repository.save

    def fail_save(_candidate):
        raise OSError("disk unavailable")

    monkeypatch.setattr(management._repository, "save", fail_save)
    with pytest.raises(OSError):
        management.clear_presentation_owner()
    with pytest.raises(OSError):
        management.delete_agent("presentation")
    monkeypatch.setattr(management._repository, "save", original_save)

    assert RuntimeConfig.from_file(path).presentation_owner_agent_id == "presentation"
    assert management._provider.get().get("presentation") is not None


def test_owner_is_independent_of_credentials_and_default_agent(tmp_path):
    management, path = _skin_management(tmp_path)
    management.create_agent("presentation", "Presentation", "observer")
    management.set_presentation_owner("presentation")
    management.create_credential("presentation")
    management.rotate_credential("presentation")
    management.revoke_credential("presentation")
    management.set_default_agent("presentation")

    reloaded = RuntimeConfig.from_file(path)
    assert reloaded.presentation_owner_agent_id == "presentation"
    assert reloaded.agent_registry.default_agent == "presentation"


def test_create_discloses_a_new_secret_once_and_status_never_does(tmp_path, caplog):
    service = AgentCredentialService(_registry(), SecretsService(tmp_path / ".phos-secrets"))
    issued = service.create("standard")

    assert issued.token and issued.token != repr(issued)
    assert issued.status.active is True
    assert service.status("standard") == issued.status
    assert issued.token not in repr(service.status("standard"))
    assert issued.token not in caplog.text
    with pytest.raises(AgentCredentialError, match="credential_already_exists"):
        service.create("standard")


def test_configured_credential_without_a_stored_secret_is_inactive(tmp_path):
    service = AgentCredentialService(_registry(), SecretsService(tmp_path / ".phos-secrets"))

    status = service.status("standard")

    assert status.configured is True
    assert status.active is False


def test_rotation_replaces_the_stable_secret_without_changing_identity_or_profile(tmp_path):
    registry = _registry()
    store = SecretsService(tmp_path / ".phos-secrets")
    service = AgentCredentialService(registry, store)
    first = service.create("standard")
    second = service.rotate("standard")
    auth = BearerAgentAuthenticator(registry, store)

    assert first.token != second.token
    assert first.status.secret_ref == second.status.secret_ref == "mcp.standard.token"
    with pytest.raises(PermissionError, match="authentication_failed"):
        asyncio.run(auth.authenticate(_context(first.token)))
    identity = asyncio.run(auth.authenticate(_context(second.token)))
    assert (identity.id, identity.permission_profile.value) == ("standard", "standard")


def test_revocation_removes_only_that_bearer_and_never_falls_back(tmp_path):
    registry = _registry()
    store = SecretsService(tmp_path / ".phos-secrets")
    service = AgentCredentialService(registry, store)
    standard = service.create("standard")
    observer = service.create("observer")
    auth = BearerAgentAuthenticator(registry, store)

    revoked = service.revoke("observer")
    assert revoked.active is False
    assert registry.get("observer").permission_profile.value == "observer"
    with pytest.raises(PermissionError, match="authentication_failed"):
        asyncio.run(auth.authenticate(_context(observer.token)))
    assert asyncio.run(auth.authenticate(_context(standard.token))).id == "standard"


def test_lifecycle_rejects_unknown_or_unconfigured_agents(tmp_path):
    registry = AgentRegistry("standard", (AgentIdentity("standard", "standard"),))
    service = AgentCredentialService(registry, SecretsService(tmp_path / ".phos-secrets"))
    for operation in (service.create, service.rotate, service.revoke, service.status):
        with pytest.raises(AgentCredentialError, match="credential_not_configured"):
            operation("standard")
        with pytest.raises(AgentCredentialError, match="unknown_agent"):
            operation("missing")


def test_failed_rotation_preserves_the_previous_secret(tmp_path, monkeypatch):
    registry = _registry()
    store = SecretsService(tmp_path / ".phos-secrets")
    service = AgentCredentialService(registry, store)
    issued = service.create("standard")
    original = store._write

    def fail_write(values):
        raise SecretsError("store failure")

    monkeypatch.setattr(store, "_write", fail_write)
    with pytest.raises(AgentCredentialError, match="credential_rotation_failed"):
        service.rotate("standard")
    monkeypatch.setattr(store, "_write", original)
    assert asyncio.run(BearerAgentAuthenticator(registry, store).authenticate(_context(issued.token))).id == "standard"


def test_concurrent_rotations_remain_isolated_by_agent(tmp_path):
    registry = _registry()
    store = SecretsService(tmp_path / ".phos-secrets")
    service = AgentCredentialService(registry, store)
    standard, observer = service.create("standard"), service.create("observer")
    with ThreadPoolExecutor(max_workers=2) as pool:
        new_standard, new_observer = tuple(pool.map(service.rotate, ("standard", "observer")))
    auth = BearerAgentAuthenticator(registry, store)
    assert new_standard.token != standard.token and new_observer.token != observer.token
    assert asyncio.run(auth.authenticate(_context(new_standard.token))).id == "standard"
    assert asyncio.run(auth.authenticate(_context(new_observer.token))).id == "observer"


def test_bearer_authenticator_resolves_the_current_registry_for_each_request(tmp_path):
    original = AgentRegistry("standard", (AgentIdentity("standard", "standard"),),
                             {"standard": "mcp.agents.standard.token"})
    provider = AgentRegistryProvider(original)
    store = SecretsService(tmp_path / ".phos-secrets")
    auth = BearerAgentAuthenticator(provider, store)
    with pytest.raises(PermissionError, match="authentication_failed"):
        asyncio.run(auth.authenticate(_context("ignored")))
    updated = AgentRegistry("standard", (AgentIdentity("standard", "standard"),
                                         AgentIdentity("live", "observer")),
                            {"standard": "mcp.agents.standard.token", "live": "mcp.agents.live.token"})
    provider.replace(updated)
    service = AgentCredentialService(provider, store)
    issued = service.create("live")
    assert asyncio.run(auth.authenticate(_context(issued.token))).id == "live"
    provider.replace(original)
    with pytest.raises(PermissionError, match="authentication_failed"):
        asyncio.run(auth.authenticate(_context(issued.token)))


def test_default_agent_change_persists_publishes_and_releases_previous_default(tmp_path):
    document = load_document()
    document["agents"] = {"default_agent": "standard", "entries": [
        {"id": "standard", "display_name": "Standard", "permission_profile": "standard"},
        {"id": "observer", "display_name": "Observer", "permission_profile": "observer"},
    ]}
    path = tmp_path / "phos.json"; path.write_text(json.dumps(document))
    repository = ConfigRepository(path)
    provider = AgentRegistryProvider(repository.load().agent_registry)
    management = AgentManagementService(repository, provider,
                                        AgentCredentialService(provider, SecretsService(tmp_path / ".phos-secrets")))
    changed = management.set_default_agent("observer")
    assert changed["default"] is True
    assert provider.get().default_agent == "observer"
    assert repository.load().agent_registry.default_agent == "observer"
    management.delete_agent("standard")
    with pytest.raises(AgentManagementError, match="default_agent"):
        management.delete_agent("observer")
    with pytest.raises(AgentManagementError, match="unknown_agent"):
        management.set_default_agent("missing")
