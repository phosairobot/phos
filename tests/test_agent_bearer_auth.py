import asyncio
import logging
from types import SimpleNamespace

import pytest

from robot.services import AgentIdentity, AgentRegistry, BearerAgentAuthenticator


class Secrets:
    def __init__(self, values): self.values = values
    def get_secret(self, name): return self.values.get(name)


def _context(value): return SimpleNamespace(request=SimpleNamespace(headers={"Authorization": value}))


def test_bearer_auth_resolves_distinct_configured_agents_without_exposing_token():
    registry = AgentRegistry("standard", (AgentIdentity("standard", "standard"), AgentIdentity("observer", "observer")),
                             {"standard": "mcp.standard.token", "observer": "mcp.observer.token"})
    auth = BearerAgentAuthenticator(registry, Secrets({"mcp.standard.token": "token-a", "mcp.observer.token": "token-b"}))
    assert asyncio.run(auth.authenticate(_context("Bearer token-a"))).id == "standard"
    assert asyncio.run(auth.authenticate(_context("Bearer token-b"))).id == "observer"
    with pytest.raises(PermissionError, match="authentication_failed"):
        asyncio.run(auth.authenticate(_context("Bearer invalid")))
    with pytest.raises(PermissionError, match="authentication_failed"):
        asyncio.run(auth.authenticate(SimpleNamespace(request=SimpleNamespace(headers={}))))


def test_bearer_auth_logs_safe_reason_codes_without_bearer_values(caplog):
    bearer = "bearer-value-must-not-appear-in-logs"
    registry = AgentRegistry("standard", (AgentIdentity("standard", "standard"),),
                             {"standard": "mcp.standard.token"})
    auth = BearerAgentAuthenticator(registry, Secrets({"mcp.standard.token": "stored-secret-must-not-appear"}))
    caplog.set_level(logging.INFO, logger="robot.services.agent_identity")

    assert asyncio.run(auth.authenticate(_context(f"Bearer stored-secret-must-not-appear"))).id == "standard"
    with pytest.raises(PermissionError, match="authentication_failed"):
        asyncio.run(auth.authenticate(_context(f"Bearer {bearer}")))
    with pytest.raises(PermissionError, match="authentication_failed"):
        asyncio.run(auth.authenticate(SimpleNamespace(request=SimpleNamespace(headers={}))))

    text = caplog.text
    assert "match=True" in text and "match=False" in text
    assert "reason=no_matching_credential" in text and "reason=missing_header" in text
    assert bearer not in text and "stored-secret-must-not-appear" not in text


def test_bearer_auth_logs_missing_secret_without_treating_it_as_a_match(caplog):
    registry = AgentRegistry("standard", (AgentIdentity("standard", "standard"),),
                             {"standard": "mcp.standard.token"})
    caplog.set_level(logging.INFO, logger="robot.services.agent_identity")

    with pytest.raises(PermissionError, match="authentication_failed"):
        asyncio.run(BearerAgentAuthenticator(registry, Secrets({})).authenticate(_context("Bearer nonmatching")))

    assert "secret_present=False match=False" in caplog.text
    assert "reason=secret_missing" in caplog.text
