"""Voice-to-command bridge tests with no physical runtime dependencies."""
import asyncio

from robot.core import Event, EventBus
from robot.interaction import (CommandExecutionResult, CommandExecutionStatus, InteractionOutcome,
                               InteractionOutcomeStatus,
                               InteractionCoordinator, InteractionSource, ResolvedIntent,
                               INTERACTION_COMMAND_EXECUTED, INTERACTION_COMMAND_FAILED,
                               INTERACTION_COMMAND_UNSUPPORTED, INTERACTION_INTENT_RESOLVED,
                               INTERACTION_OUTCOME_COMPLETED, INTERACTION_OUTCOME_FAILED,
                               INTERACTION_RESPONSE_READY)
from robot.interaction.deterministic import DeterministicIntentResolver
from robot.voice.session import VOICE_TRANSCRIPTION_COMPLETED


class RecordingExecutor:
    def __init__(self, status=CommandExecutionStatus.EXECUTED):
        self.calls = []
        self.status = status

    def execute(self, route, authorization, *, source_language=None):
        self.calls.append((route, authorization))
        return CommandExecutionResult(self.status, route.command, route.parameters,
                                      None if self.status is CommandExecutionStatus.EXECUTED else "application_error")


def _coordinator(*, execution_enabled, executor=None, resolver=None):
    bus = EventBus()
    resolver = resolver or DeterministicIntentResolver(default_language="en", supported_languages=("en", "it"))
    return bus, InteractionCoordinator(
        bus, enabled=True, configured_resolver="deterministic", default_language="en",
        supported_languages=("en", "it"), resolver=resolver,
        execution_enabled=execution_enabled, executor=executor,
    )


def _publish(bus, text, language="en"):
    asyncio.run(bus.publish(Event(VOICE_TRANSCRIPTION_COMPLETED, {"text": text, "language": language})))


def test_execution_disabled_preserves_observational_voice_resolution():
    executor = RecordingExecutor()
    bus, coordinator = _coordinator(execution_enabled=False, executor=executor)
    resolved = []
    bus.subscribe(INTERACTION_INTENT_RESOLVED, lambda event: resolved.append(event.data))
    _publish(bus, "go to sleep")
    assert resolved[-1]["intent"] == "sleep"
    assert executor.calls == []
    assert coordinator.status()["last_execution"]["status"] is None


def test_enabled_bridge_routes_and_authorizes_only_the_voice_allowlist():
    executor = RecordingExecutor()
    bus, coordinator = _coordinator(execution_enabled=True, executor=executor)
    events = []
    bus.subscribe(INTERACTION_COMMAND_EXECUTED, lambda event: events.append(event.data))
    for text, command, parameters in (("go to sleep", "robot.sleep", {}),
                                      ("wake up", "robot.wake", {}),
                                      ("be happy", "face.set_expression", {"expression": "happy"})):
        _publish(bus, text)
        route, authorization = executor.calls[-1]
        assert (route.command, route.parameters) == (command, parameters)
        assert authorization.source is InteractionSource.VOICE
        assert authorization.allowed_commands == frozenset({"robot.sleep", "robot.wake", "face.set_expression",
                                                            "environment.get_temperature", "environment.get_air_quality"})
        assert events[-1] == {"intent": route.intent, "command": command, "parameters": parameters,
                              "source": "voice", "source_language": "en", "status": "executed"}
    assert coordinator.status()["last_execution"] == {
        "status": "executed", "intent": "set_expression", "command": "face.set_expression", "source_language": "en"}


def test_italian_source_is_preserved_through_execution_event():
    executor = RecordingExecutor()
    bus, _ = _coordinator(execution_enabled=True, executor=executor)
    events = []
    bus.subscribe(INTERACTION_COMMAND_EXECUTED, lambda event: events.append(event.data))
    _publish(bus, "vai a dormire", "it")
    assert executor.calls[-1][0].command == "robot.sleep"
    assert events[-1]["source_language"] == "it"


def test_unresolved_and_failed_resolution_never_reach_executor():
    executor = RecordingExecutor()
    bus, _ = _coordinator(execution_enabled=True, executor=executor)
    _publish(bus, "this is not a command")

    class FailingResolver:
        name = "failing"
        async def resolve(self, interaction):
            raise RuntimeError("resolver unavailable")

    failed_bus, _ = _coordinator(execution_enabled=True, executor=executor, resolver=FailingResolver())
    _publish(failed_bus, "go to sleep")
    assert executor.calls == []


def test_query_routes_emit_a_bounded_semantic_outcome():
    class QueryExecutor(RecordingExecutor):
        def execute(self, route, authorization, *, source_language=None):
            self.calls.append((route, authorization))
            return InteractionOutcome(InteractionOutcomeStatus.COMPLETED, route.intent,
                                      authorization.source, source_language, {"temperature_c": 26.8})

    executor = QueryExecutor()
    bus, coordinator = _coordinator(execution_enabled=True, executor=executor)
    events = []
    responses = []
    bus.subscribe(INTERACTION_OUTCOME_COMPLETED, lambda event: events.append(event.data))
    bus.subscribe(INTERACTION_RESPONSE_READY, lambda event: responses.append(event.data))
    _publish(bus, "what is the temperature")
    assert executor.calls[-1][0].command == "environment.get_temperature"
    assert events[-1] == {"status": "completed", "intent": "get_temperature", "source": "voice",
                          "source_language": "en", "result": {"temperature_c": 26.8}, "reason": None}
    assert coordinator.status()["last_outcome"] == {
        "status": "completed", "intent": "get_temperature", "source_language": "en"}
    assert responses[-1] == {"text": "The temperature is 26.8 degrees Celsius.", "language": "en",
                             "intent": "get_temperature", "status": "ready"}
    assert coordinator.status()["last_response"] == responses[-1]


def test_italian_query_preserves_language_in_its_semantic_outcome():
    class QueryExecutor(RecordingExecutor):
        def execute(self, route, authorization, *, source_language=None):
            self.calls.append((route, authorization))
            return InteractionOutcome(InteractionOutcomeStatus.COMPLETED, route.intent,
                                      authorization.source, source_language, {"temperature_c": 21.5})

    executor = QueryExecutor()
    bus, _ = _coordinator(execution_enabled=True, executor=executor)
    events = []
    bus.subscribe(INTERACTION_OUTCOME_COMPLETED, lambda event: events.append(event.data))
    _publish(bus, "che temperatura c'è", "it")
    assert events[-1]["source_language"] == "it"
    assert events[-1]["result"] == {"temperature_c": 21.5}


def test_query_failure_is_a_bounded_outcome_and_does_not_crash_resolution():
    class FailingQueryExecutor(RecordingExecutor):
        def execute(self, route, authorization, *, source_language=None):
            return InteractionOutcome(InteractionOutcomeStatus.FAILED, route.intent,
                                      authorization.source, source_language, reason="application_error")

    bus, coordinator = _coordinator(execution_enabled=True, executor=FailingQueryExecutor())
    events = []
    bus.subscribe(INTERACTION_OUTCOME_FAILED, lambda event: events.append(event.data))
    _publish(bus, "what is the temperature")
    assert events[-1]["reason"] == "application_error"
    assert coordinator.status()["last_outcome"]["status"] == "failed"


def test_executor_failure_is_recorded_without_crashing_the_event_loop():
    executor = RecordingExecutor(CommandExecutionStatus.FAILED)
    bus, coordinator = _coordinator(execution_enabled=True, executor=executor)
    events = []
    bus.subscribe(INTERACTION_COMMAND_FAILED, lambda event: events.append(event.data))
    _publish(bus, "go to sleep")
    assert events[-1]["command"] == "robot.sleep"
    assert coordinator.status()["last_execution"]["status"] == "failed"
