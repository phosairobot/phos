"""Focused controlled-execution tests for interaction command routes."""
from robot.interaction import (CommandAuthorization, CommandExecutionStatus,
                               CommandExecutor, CommandRoute, InteractionSource)


class ApplicationServiceSpy:
    def __init__(self, *, fail=False):
        self.calls = []
        self.fail = fail

    def set_state(self, value):
        self.calls.append(("set_state", value))
        if self.fail:
            raise RuntimeError("application unavailable")
        return {"state": value}

    def set_expression(self, value):
        self.calls.append(("set_expression", value))
        if self.fail:
            raise RuntimeError("application unavailable")
        return {"expression": value}


def _authorization(*commands):
    return CommandAuthorization(InteractionSource.API, frozenset(commands))


def test_authorized_commands_execute_only_via_application_service():
    service = ApplicationServiceSpy()
    executor = CommandExecutor(service)
    cases = (
        (CommandRoute("sleep", "robot.sleep"), "robot.sleep", ("set_state", "sleeping")),
        (CommandRoute("wake", "robot.wake"), "robot.wake", ("set_state", "idle")),
        (CommandRoute("set_expression", "face.set_expression", {"expression": "happy"}),
         "face.set_expression", ("set_expression", "happy")),
        (CommandRoute("set_expression", "face.set_expression", {"expression": "neutral"}),
         "face.set_expression", ("set_expression", "neutral")),
    )
    for route, command, call in cases:
        result = executor.execute(route, _authorization(command))
        assert result.document() == {"status": "executed", "command": command,
                                     "parameters": route.parameters, "reason": None}
        assert service.calls[-1] == call


def test_valid_routes_default_to_denied_without_touching_application_service():
    service = ApplicationServiceSpy()
    executor = CommandExecutor(service)
    for route in (CommandRoute("sleep", "robot.sleep"),
                  CommandRoute("set_expression", "face.set_expression", {"expression": "happy"})):
        result = executor.execute(route)
        assert (result.status, result.reason) == (
            CommandExecutionStatus.DENIED, "execution_not_authorized")
    assert service.calls == []


def test_sensor_queries_remain_unsupported_without_application_service_reads():
    service = ApplicationServiceSpy()
    executor = CommandExecutor(service)
    for intent, command in (("get_temperature", "environment.get_temperature"),
                            ("get_air_quality", "environment.get_air_quality")):
        result = executor.execute(CommandRoute(intent, command), _authorization(command))
        assert result.document() == {"status": "unsupported", "command": command,
                                     "parameters": {}, "reason": "query_execution_not_implemented"}
    assert service.calls == []


def test_executor_refuses_malformed_routes_before_authorization_or_execution():
    service = ApplicationServiceSpy()
    malformed = CommandRoute("sleep", "robot.sleep", {"unexpected": True})
    result = CommandExecutor(service).execute(malformed, _authorization("robot.sleep"))
    assert (result.status, result.reason) == (CommandExecutionStatus.REJECTED, "invalid_route")
    assert service.calls == []


def test_application_failure_is_structured_and_does_not_escape():
    service = ApplicationServiceSpy(fail=True)
    result = CommandExecutor(service).execute(CommandRoute("sleep", "robot.sleep"),
                                               _authorization("robot.sleep"))
    assert result.document() == {"status": "failed", "command": "robot.sleep",
                                 "parameters": {}, "reason": "application_error"}
    assert service.calls == [("set_state", "sleeping")]
