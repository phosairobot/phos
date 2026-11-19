import asyncio

import pytest

from robot.services import (AgentIdentity, AgentProfile, AgentRegistry,
                            BearerAgentAuthenticator, McpAuthenticationModeAuthenticator)


class Secrets:
    def get_secret(self, name):
        return {"mcp.standard": "valid-token"}.get(name)


class Context:
    def __init__(self, authorization=None):
        self.request = type("Request", (), {"headers": {} if authorization is None else {"authorization": authorization}})()


def authenticator(mode, profile="standard"):
    identity = AgentIdentity("token-agent", AgentProfile.STANDARD, "Token agent")
    registry = AgentRegistry("token-agent", (identity,), {"token-agent": "mcp.standard"})
    return McpAuthenticationModeAuthenticator(BearerAgentAuthenticator(registry, Secrets()), mode=mode,
                                               anonymous_permission_profile=profile)


def test_none_mode_uses_anonymous_principal_and_rejects_malformed_authorization():
    resolver = authenticator("none", "observer")
    assert asyncio.run(resolver.authenticate(Context())).id == "anonymous-mcp"
    assert asyncio.run(resolver.authenticate(Context("Bearer ignored"))).permission_profile is AgentProfile.OBSERVER
    with pytest.raises(PermissionError, match="authentication_failed"):
        asyncio.run(resolver.authenticate(Context("Basic ignored")))


def test_mixed_mode_never_downgrades_explicit_invalid_or_revoked_bearer_to_anonymous():
    resolver = authenticator("mixed")
    assert asyncio.run(resolver.authenticate(Context())).id == "anonymous-mcp"
    assert asyncio.run(resolver.authenticate(Context("Bearer valid-token"))).id == "token-agent"
    with pytest.raises(PermissionError, match="authentication_failed"):
        asyncio.run(resolver.authenticate(Context("Bearer revoked-token")))
    with pytest.raises(PermissionError, match="authentication_failed"):
        asyncio.run(resolver.authenticate(Context("Basic token")))


def test_bearer_mode_rejects_missing_authorization():
    with pytest.raises(PermissionError, match="authentication_failed"):
        asyncio.run(authenticator("bearer").authenticate(Context()))


def test_authentication_policy_hot_reload_applies_atomically_to_subsequent_requests():
    resolver = authenticator("bearer")
    with pytest.raises(PermissionError, match="authentication_failed"):
        asyncio.run(resolver.authenticate(Context()))

    resolver.configure(mode="mixed", anonymous_permission_profile="standard")
    assert asyncio.run(resolver.authenticate(Context())).permission_profile is AgentProfile.STANDARD
    assert asyncio.run(resolver.authenticate(Context("Bearer valid-token"))).id == "token-agent"

    resolver.configure(mode="none", anonymous_permission_profile="observer")
    assert asyncio.run(resolver.authenticate(Context("Bearer valid-token"))).id == "anonymous-mcp"
    assert asyncio.run(resolver.authenticate(Context())).permission_profile is AgentProfile.OBSERVER

    resolver.configure(mode="bearer", anonymous_permission_profile="observer")
    with pytest.raises(PermissionError, match="authentication_failed"):
        asyncio.run(resolver.authenticate(Context()))
    assert asyncio.run(resolver.authenticate(Context("Bearer valid-token"))).id == "token-agent"
