import asyncio
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from robot.secrets import SecretsError, SecretsService
from robot.services import (AgentCredentialError, AgentCredentialService, AgentIdentity, AgentRegistry,
                            AgentRegistryProvider, BearerAgentAuthenticator)


def _registry():
    return AgentRegistry("standard", (AgentIdentity("standard", "standard"), AgentIdentity("observer", "observer")), {
        "standard": "mcp.standard.token", "observer": "mcp.observer.token",
    })


def _context(token):
    return SimpleNamespace(request=SimpleNamespace(headers={"Authorization": f"Bearer {token}"}))


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
