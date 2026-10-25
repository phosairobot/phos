import pytest

from robot.core.behavior_engine import BehaviorEngine
from robot.core.runtime import RobotCore
from robot.services import (AGENT_ACCESS_PROFILES, DEFAULT_AGENT_PROFILE, OPERATION_PERMISSIONS,
                            AgentAccess, AgentApplicationService, AgentOperation, AgentPermission,
                            AgentPermissionDenied, AgentPermissionService, AgentProfile,
                            ApplicationError, PhosApplicationService)


class ApplicationSpy:
    def __init__(self):
        self.calls = []

    def _call(self, name, *args):
        self.calls.append((name, *args))
        return {"operation": name}

    def status(self): return self._call("status")
    def get_physical_context(self): return self._call("get_physical_context")
    def presence(self): return self._call("presence")
    def attention(self): return self._call("attention")
    def observed_expression(self): return self._call("observed_expression")
    def set_expression(self, value): return self._call("set_expression", value)
    def speak(self, text): return self._call("speak", text)
    def set_state(self, state): return self._call("set_state", state)
    def signal_user(self, signal): return self._call("signal_user", signal)


def test_permission_evaluator_is_explicit_and_access_is_immutable():
    original = {AgentPermission.READ_STATUS}
    access = AgentAccess(original)
    original.add(AgentPermission.CONTROL_VOICE)

    assert AgentPermission.CONTROL_VOICE not in access.permissions
    assert AgentPermission.READ_STATUS in access.permissions
    assert AgentPermissionService.is_allowed(access, AgentPermission.READ_STATUS)
    assert not AgentPermissionService.is_allowed(access, AgentPermission.READ_PHYSICAL_CONTEXT)
    with pytest.raises(AttributeError):
        access.permissions.add(AgentPermission.CONTROL_VOICE)


def test_missing_or_unrelated_permission_is_a_distinct_denial_without_invocation():
    application = ApplicationSpy()
    agent = AgentApplicationService(application, AgentAccess({AgentPermission.CONTROL_VOICE}))

    with pytest.raises(AgentPermissionDenied) as error:
        agent.set_expression("happy")

    assert error.value.operation is AgentOperation.SET_EXPRESSION
    assert error.value.permission is AgentPermission.CONTROL_EXPRESSION
    assert application.calls == []


def test_observer_can_read_the_minimal_semantic_surface_but_cannot_act():
    application = ApplicationSpy()
    agent = AgentApplicationService(application, AgentAccess.for_profile(AgentProfile.OBSERVER))

    assert agent.status() == {"operation": "status"}
    assert agent.get_physical_context() == {"operation": "get_physical_context"}
    assert agent.presence() == {"operation": "presence"}
    assert agent.attention() == {"operation": "attention"}
    assert agent.observed_expression() == {"operation": "observed_expression"}
    with pytest.raises(AgentPermissionDenied):
        agent.speak("Hello")

    assert [call[0] for call in application.calls] == [
        "status", "get_physical_context", "presence", "attention", "observed_expression",
    ]


def test_standard_profile_has_the_explicit_documented_permission_set():
    assert DEFAULT_AGENT_PROFILE is AgentProfile.STANDARD
    assert AGENT_ACCESS_PROFILES[AgentProfile.STANDARD] == frozenset(AgentPermission)
    assert AGENT_ACCESS_PROFILES[AgentProfile.OBSERVER] == {
        AgentPermission.READ_STATUS, AgentPermission.READ_PHYSICAL_CONTEXT, AgentPermission.READ_PERCEPTION,
    }


@pytest.mark.parametrize(("method", "arguments", "expected"), [
    ("set_expression", ("happy",), ("set_expression", "happy")),
    ("speak", ("Hello",), ("speak", "Hello")),
    ("sleep", (), ("set_state", "sleeping")),
    ("wake", (), ("set_state", "idle")),
    ("signal_user", ("warning",), ("signal_user", "warning")),
])
def test_standard_profile_routes_semantic_actions_through_application(method, arguments, expected):
    application = ApplicationSpy()
    result = getattr(AgentApplicationService(application, AgentAccess.for_profile()), method)(*arguments)

    assert result["operation"] == expected[0]
    assert application.calls == [expected]


def test_permission_denial_precedes_existing_application_capability_failure():
    class UnavailableApplication(ApplicationSpy):
        def speak(self, text):
            self._call("speak", text)
            raise ApplicationError("tts_unavailable", "Text-to-speech is currently unavailable.", status=503)

    application = UnavailableApplication()
    denied = AgentApplicationService(application, AgentAccess())
    with pytest.raises(AgentPermissionDenied):
        denied.speak("Hello")
    assert application.calls == []

    permitted = AgentApplicationService(application, AgentAccess({AgentPermission.CONTROL_VOICE}))
    with pytest.raises(ApplicationError) as error:
        permitted.speak("Hello")
    assert error.value.code == "tts_unavailable"
    assert application.calls == [("speak", "Hello")]


def test_denied_actions_do_not_change_real_expression_or_robot_state():
    class Runtime:
        def __init__(self):
            self.core = RobotCore()
            self._behavior_engine = BehaviorEngine(self.core.events)

    runtime = Runtime()
    application = PhosApplicationService(runtime)
    agent = AgentApplicationService(application, AgentAccess())

    with pytest.raises(AgentPermissionDenied):
        agent.set_expression("happy")
    with pytest.raises(AgentPermissionDenied):
        agent.sleep()
    with pytest.raises(AgentPermissionDenied):
        agent.signal_user("warning")

    assert runtime.core.state.value == "idle"
    assert runtime._behavior_engine.manual_expression_state() is None


def test_agent_surface_has_no_raw_hardware_renderer_or_admin_operations():
    public_methods = {name for name in dir(AgentApplicationService) if not name.startswith("_")}

    assert public_methods == {
        "attention", "get_physical_context", "observed_expression", "presence", "set_expression",
        "signal_user", "sleep", "speak", "status", "wake",
    }
    assert set(OPERATION_PERMISSIONS) == set(AgentOperation)
