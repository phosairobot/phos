import asyncio
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
