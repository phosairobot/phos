import asyncio
from types import SimpleNamespace

import pytest

from robot.core.agent_visual_state import AgentVisualStateLeaseManager
from robot.core.behavior_engine import BehaviorEngine
from robot.core.runtime import RobotCore
from robot.core.state import RobotState
from robot.services.agent_identity import AgentIdentity
from robot.services.agent_permissions import (AgentAccess, AgentApplicationService, AgentOperation,
                                              AgentPermission, AgentPermissionDenied, AgentProfile)
from robot.services.application import ApplicationError, PhosApplicationService


class FakeClock:
    def __init__(self): self.now = 0.0
    def __call__(self): return self.now


class FakeCall:
    def cancel(self): pass


def running_application():
    core = RobotCore()
    manager = AgentVisualStateLeaseManager(clock=FakeClock(), scheduler=lambda _delay, _callback: FakeCall())
    runtime = SimpleNamespace(core=core, _behavior_engine=BehaviorEngine(core.events), _agent_visual_state=manager)
    asyncio.run(core.start())
    return PhosApplicationService(runtime), runtime


def identity(name, profile=AgentProfile.STANDARD):
    return AgentIdentity(name, profile, name)


def cleanup(application, runtime):
    application.close()
    asyncio.run(runtime.core.stop())
    runtime._agent_visual_state.shutdown()


def test_authorized_agent_request_forwards_identity_and_returns_structured_result():
    application, runtime = running_application()
    try:
        result = AgentApplicationService(application, identity("agent-a")).request_visual_state("thinking", 1000)
        assert result == {"accepted": True, "requested_state": "thinking", "effective_state": "thinking",
                          "lease_active": True, "remaining_expiry_ms": 1000, "suppressed_by": None}
        assert runtime.core.state is RobotState.IDLE
    finally:
        cleanup(application, runtime)


def test_agent_facade_forwards_only_the_trusted_identity():
    class Application:
        def __init__(self): self.received = None
        def request_agent_visual_state(self, agent_id, state, duration_ms):
            self.received = (agent_id, state, duration_ms)
            return {"accepted": True}

    application = Application()
    result = AgentApplicationService(application, identity("agent-a")).request_visual_state("thinking", 1000)
    assert result == {"accepted": True}
    assert application.received == ("agent-a", "thinking", 1000)


def test_agent_release_and_replacement_are_owned_by_authenticated_identity():
    application, runtime = running_application()
    try:
        first = AgentApplicationService(application, identity("agent-a"))
        second = AgentApplicationService(application, identity("agent-b"))
        first.request_visual_state("thinking", 250)
        first.request_visual_state("thinking", 1000)
        second.request_visual_state("thinking", 1000)
        released = first.request_visual_state("idle")
        assert released["lease_active"] is False
        # Agent B's lease remains active but its identity is never disclosed.
        assert released["effective_state"] == "thinking"
        assert second.request_visual_state("idle") == {
            "accepted": True, "requested_state": "idle", "effective_state": "idle",
            "lease_active": False, "remaining_expiry_ms": None, "suppressed_by": None,
        }
    finally:
        cleanup(application, runtime)


def test_observer_and_explicit_permission_denials_precede_application_invocation():
    class Application:
        def __init__(self): self.called = False
        def request_agent_visual_state(self, *_args): self.called = True

    application = Application()
    observer = AgentApplicationService(application, identity("observer", AgentProfile.OBSERVER))
    explicit = AgentApplicationService(application, AgentAccess({AgentPermission.CONTROL_EXPRESSION}))
    for agent in (observer, explicit):
        with pytest.raises(AgentPermissionDenied) as error:
            agent.request_visual_state("thinking", 1000)
        assert error.value.operation is AgentOperation.SET_VISUAL_STATE
        assert error.value.permission is AgentPermission.CONTROL_VISUAL_STATE
    assert not application.called


@pytest.mark.parametrize(("state", "duration"), [
    ("thinking", None), ("thinking", 249), ("thinking", 60_001), ("thinking", True),
    ("speaking", 250), ("listening", 250), ("sleeping", 250), ("error", 250),
])
def test_application_rejects_invalid_visual_state_requests(state, duration):
    application, runtime = running_application()
    try:
        with pytest.raises(ApplicationError) as error:
            AgentApplicationService(application, identity("agent-a")).request_visual_state(state, duration)
        assert error.value.code == "invalid_visual_state_request" and error.value.status == 400
    finally:
        cleanup(application, runtime)


@pytest.mark.parametrize("duration", [None, 10_000, 0, 60_001])
def test_agent_idle_release_ignores_optional_duration(duration):
    application, runtime = running_application()
    try:
        agent = AgentApplicationService(application, identity("agent-a"))
        agent.request_visual_state("thinking", 1000)
        released = agent.request_visual_state("idle", duration)
        assert released == {
            "accepted": True, "requested_state": "idle", "effective_state": "idle",
            "lease_active": False, "remaining_expiry_ms": None, "suppressed_by": None,
        }
    finally:
        cleanup(application, runtime)


def test_unavailable_or_closed_runtime_maps_to_service_unavailable():
    core = RobotCore()
    manager = AgentVisualStateLeaseManager()
    runtime = SimpleNamespace(core=core, _behavior_engine=BehaviorEngine(core.events), _agent_visual_state=manager)
    application = PhosApplicationService(runtime)
    agent = AgentApplicationService(application, identity("agent-a"))
    try:
        with pytest.raises(ApplicationError) as unavailable:
            agent.request_visual_state("thinking", 1000)
        assert unavailable.value.code == "runtime_unavailable"
        asyncio.run(core.start())
        manager.shutdown()
        with pytest.raises(ApplicationError) as closed:
            agent.request_visual_state("thinking", 1000)
        assert closed.value.code == "runtime_unavailable"
    finally:
        application.close()
        asyncio.run(core.stop())
