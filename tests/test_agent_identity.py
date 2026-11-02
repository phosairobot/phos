import asyncio

import pytest

from robot.services import (AgentApplicationService, AgentIdentity, AgentPermissionDenied,
                            AgentProfile, LOCAL_AGENT_IDENTITY, LocalAgentAuthenticator)


def test_identity_is_immutable_and_references_an_existing_permission_profile():
    identity = AgentIdentity("observer-agent", AgentProfile.OBSERVER, "Observer")

    assert identity.id == "observer-agent"
    assert identity.permission_profile is AgentProfile.OBSERVER
    with pytest.raises((AttributeError, TypeError)):
        identity.id = "other-agent"
    with pytest.raises(ValueError, match="permission profile"):
        AgentIdentity("bad-agent", "unknown")


def test_local_authenticator_is_explicit_transitional_default_without_credentials():
    authenticator = LocalAgentAuthenticator()

    assert asyncio.run(authenticator.authenticate(object())) == LOCAL_AGENT_IDENTITY


def test_identity_profile_drives_existing_permission_evaluation():
    class Application:
        def speak(self, text):
            raise AssertionError("permission denial must precede delegation")

    agent = AgentApplicationService(Application(), AgentIdentity("observer-agent", "observer"))

    with pytest.raises(AgentPermissionDenied):
        agent.speak("Hello")
