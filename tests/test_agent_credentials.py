import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from robot.secrets import SecretsError, SecretsService
from robot.config import ConfigRepository, RuntimeConfig, load_document
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
