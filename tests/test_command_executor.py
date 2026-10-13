"""Focused controlled-execution tests for interaction command routes."""
from robot.interaction import (CommandAuthorization, CommandExecutionStatus,
                               CommandExecutor, CommandRoute, InteractionSource)


class ApplicationServiceSpy:
    def __init__(self, *, fail=False, environment=None, sensors=None):
        self.calls = []
        self.fail = fail
        self._environment = environment if environment is not None else {"available": False}
        self._sensors = sensors if sensors is not None else {}

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

    def environment(self):
        self.calls.append(("environment",))
        if self.fail:
            raise RuntimeError("application unavailable")
        return self._environment

    def sensors(self):
        self.calls.append(("sensors",))
        if self.fail:
            raise RuntimeError("application unavailable")
        return self._sensors


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


def test_temperature_query_returns_a_structured_semantic_outcome():
    service = ApplicationServiceSpy(environment={"available": True, "measurements": {"temperature_c": 26.8}})
    executor = CommandExecutor(service)
    result = executor.execute(CommandRoute("get_temperature", "environment.get_temperature"),
                              _authorization("environment.get_temperature"), source_language="it")
    assert result.document() == {"status": "completed", "intent": "get_temperature", "source": "api",
                                 "source_language": "it", "result": {"temperature_c": 26.8}, "reason": None}
    assert service.calls == [("environment",)]


def test_unavailable_temperature_has_no_invented_value():
    service = ApplicationServiceSpy(environment={"available": False, "measurements": None})
    result = CommandExecutor(service).execute(CommandRoute("get_temperature", "environment.get_temperature"),
                                              _authorization("environment.get_temperature"))
    assert result.document()["status"] == "unavailable"
    assert result.document()["result"] is None
    assert result.document()["reason"] == "temperature_unavailable"


def test_air_quality_query_preserves_its_atomic_ccs811_measurements():
    service = ApplicationServiceSpy(sensors={"ccs811": {"available": True,
                                  "measurements": {"eco2_ppm": 520, "tvoc_ppb": 12}}})
    result = CommandExecutor(service).execute(CommandRoute("get_air_quality", "environment.get_air_quality"),
                                              _authorization("environment.get_air_quality"))
    assert result.document()["result"] == {"eco2_ppm": 520, "tvoc_ppb": 12}
    assert service.calls == [("sensors",)]


def test_unavailable_or_partial_air_quality_is_not_fabricated():
    for snapshot in ({"available": False, "measurements": None},
                     {"available": True, "measurements": {"eco2_ppm": 520}}):
        service = ApplicationServiceSpy(sensors={"ccs811": snapshot})
        result = CommandExecutor(service).execute(CommandRoute("get_air_quality", "environment.get_air_quality"),
                                                  _authorization("environment.get_air_quality"))
        assert (result.document()["status"], result.document()["result"], result.document()["reason"]) == (
            "unavailable", None, "air_quality_unavailable")


def test_denied_query_does_not_call_the_application_read_model():
    service = ApplicationServiceSpy()
    result = CommandExecutor(service).execute(CommandRoute("get_temperature", "environment.get_temperature"))
    assert (result.status, result.reason) == (CommandExecutionStatus.DENIED, "execution_not_authorized")
    assert service.calls == []


def test_query_application_failure_is_structured():
    service = ApplicationServiceSpy(fail=True)
    result = CommandExecutor(service).execute(CommandRoute("get_temperature", "environment.get_temperature"),
                                              _authorization("environment.get_temperature"))
    assert result.document()["status"] == "failed"
    assert result.document()["reason"] == "application_error"


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
