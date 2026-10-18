"""Observational transcript-to-intent coordination owned by the main runtime."""
from __future__ import annotations

import logging

from robot.core import Event
from robot.voice.session import VOICE_TRANSCRIPTION_COMPLETED

from .models import InteractionInput, InteractionOutcome, InteractionResponse, InteractionSource, ResolvedIntent
from .resolver import IntentResolverError
from .router import CommandRoute, CommandRouteStatus, CommandRouter
from .executor import CommandAuthorization, CommandExecutor
from .response import InteractionResponseRenderer
from .speech import InteractionSpeechCoordinator


logger = logging.getLogger(__name__)

INTERACTION_INTENT_RESOLVED = "interaction_intent_resolved"
INTERACTION_INTENT_UNRESOLVED = "interaction_intent_unresolved"
INTERACTION_RESOLUTION_FAILED = "interaction_resolution_failed"
INTERACTION_COMMAND_EXECUTED = "interaction_command_executed"
INTERACTION_COMMAND_DENIED = "interaction_command_denied"
INTERACTION_COMMAND_UNSUPPORTED = "interaction_command_unsupported"
INTERACTION_COMMAND_FAILED = "interaction_command_failed"
INTERACTION_COMMAND_REJECTED = "interaction_command_rejected"
INTERACTION_OUTCOME_COMPLETED = "interaction_outcome_completed"
INTERACTION_OUTCOME_UNAVAILABLE = "interaction_outcome_unavailable"
INTERACTION_OUTCOME_FAILED = "interaction_outcome_failed"
INTERACTION_RESPONSE_READY = "interaction_response_ready"
INTERACTION_RESPONSE_UNAVAILABLE = "interaction_response_unavailable"
INTERACTION_RESPONSE_FAILED = "interaction_response_failed"
INTERACTION_RESPONSE_SPEECH_COMPLETED = "interaction_response_speech_completed"
INTERACTION_RESPONSE_SPEECH_FAILED = "interaction_response_speech_failed"

_VOICE_EXECUTABLE_COMMANDS = frozenset({"robot.sleep", "robot.wake", "robot.set_state", "face.set_expression",
                                        "environment.get_temperature", "environment.get_air_quality", "environment.get_sensor_value"})
_SPEAKABLE_RESPONSE_INTENTS = frozenset({"get_temperature", "get_air_quality", "get_sensor_value"})


class InteractionCoordinator:
    """Resolve voice transcripts and optionally bridge approved intents to commands."""

    def __init__(self, events, *, enabled: bool, configured_resolver: str,
                 default_language: str, supported_languages: tuple[str, ...], resolver=None,
                 unavailable_reason: str | None = None, execution_enabled: bool = False,
                 router: CommandRouter | None = None, executor: CommandExecutor | None = None,
                 response_renderer: InteractionResponseRenderer | None = None,
                 response_tts_enabled: bool = False,
                 speech_coordinator: InteractionSpeechCoordinator | None = None) -> None:
        self._events = events
        self._enabled = bool(enabled)
        self._configured_resolver = configured_resolver
        self._default_language = default_language
        self._supported_languages = list(supported_languages)
        self._resolver = resolver
        self._unavailable_reason = unavailable_reason
        self._execution_enabled = bool(execution_enabled)
        self._router = router or CommandRouter()
        self._executor = executor
        self._response_renderer = response_renderer or InteractionResponseRenderer(default_language)
        self._response_tts_enabled = bool(response_tts_enabled)
        self._speech_coordinator = speech_coordinator
        self._voice_authorization = CommandAuthorization(InteractionSource.VOICE, _VOICE_EXECUTABLE_COMMANDS)
        self._last_resolution = {"status": None, "intent": None, "source_language": None}
        self._last_execution = {"status": None, "intent": None, "command": None, "source_language": None}
        self._last_outcome = {"status": None, "intent": None, "source_language": None}
        self._last_response = {"status": None, "intent": None, "language": None, "text": None}
        self._last_speech = {"status": None, "intent": None, "language": None}
        self._unsubscribe = None
        logger.info("PHOS INTERACTION: enabled=%s execution_enabled=%s response_tts_enabled=%s resolver=%s",
                    self._enabled, self._execution_enabled, self._response_tts_enabled,
                    self._configured_resolver)
        if self._enabled and self._resolver is not None:
            self._unsubscribe = events.subscribe(VOICE_TRANSCRIPTION_COMPLETED, self._resolve_transcript)
            logger.info("PHOS INTERACTION: resolution enabled resolver=%s", self._resolver.name)
        elif self._enabled:
            logger.warning("PHOS INTERACTION: resolver=%s unavailable reason=%s",
                           configured_resolver, unavailable_reason or "initialization_failed")

    def status(self) -> dict:
        return {"enabled": self._enabled, "configured_resolver": self._configured_resolver,
                "active_resolver": getattr(self._resolver, "name", None),
                "available": self._resolver is not None,
                "execution_enabled": self._execution_enabled,
                "response_tts_enabled": self._response_tts_enabled,
                "default_language": self._default_language,
                "supported_languages": list(self._supported_languages),
                "last_resolution": dict(self._last_resolution),
                "last_execution": dict(self._last_execution),
                "last_outcome": dict(self._last_outcome),
                "last_response": dict(self._last_response),
                "last_speech": dict(self._last_speech),
                "unavailable_reason": self._unavailable_reason}

    def set_executor(self, executor: CommandExecutor) -> None:
        """Attach the runtime-owned executor once its application service exists."""
        self._executor = executor

    def set_speech_coordinator(self, speech_coordinator: InteractionSpeechCoordinator) -> None:
        """Attach the runtime-owned application speech boundary."""
        self._speech_coordinator = speech_coordinator

    def close(self) -> None:
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None

    async def _resolve_transcript(self, event) -> None:
        if self._resolver is None:
            return
        input_value = InteractionInput(
            text=event.data.get("text", ""), language=event.data.get("language"),
            source=InteractionSource.VOICE,
        )
        try:
            result = await self._resolver.resolve(input_value)
        except IntentResolverError as error:
            await self._failed(error)
            return
        except Exception as error:
            await self._failed(error)
            return
        if isinstance(result, ResolvedIntent):
            payload = {"intent": result.name, "parameters": dict(result.parameters),
                       "source": InteractionSource.VOICE.value,
                       "source_language": result.source_language, "resolver": result.resolver}
            self._last_resolution = {"status": "resolved", "intent": result.name,
                                     "source_language": result.source_language}
            logger.info("PHOS INTERACTION: intent resolved intent=%s language=%s source=voice",
                        result.name, result.source_language)
            await self._events.publish(Event(INTERACTION_INTENT_RESOLVED, payload))
            if self._execution_enabled:
                await self._execute_resolved(result)
            return
        payload = {"source": InteractionSource.VOICE.value,
                   "source_language": result.source_language, "resolver": result.resolver}
        self._last_resolution = {"status": "unresolved", "intent": None,
                                 "source_language": result.source_language}
        logger.info("PHOS INTERACTION: input unresolved language=%s source=voice", result.source_language)
        await self._events.publish(Event(INTERACTION_INTENT_UNRESOLVED, payload))

    async def _execute_resolved(self, intent: ResolvedIntent) -> None:
        """Route and authorize a voice intent without retaining transcript text."""
        try:
            routed = self._router.route(intent)
            if routed.status is not CommandRouteStatus.ROUTABLE or routed.command is None:
                await self._record_execution("rejected", intent, routed.command, routed.parameters)
                return
            if self._executor is None:
                await self._record_execution("failed", intent, routed.command, routed.parameters)
                return
            result = self._executor.execute(
                CommandRoute(intent.name, routed.command, routed.parameters), self._voice_authorization,
                source_language=intent.source_language)
            if isinstance(result, InteractionOutcome):
                await self._record_outcome(result)
                return
            await self._record_execution(result.status.value, intent, result.command, result.parameters)
        except Exception:
            logger.exception("PHOS INTERACTION: command bridge failed intent=%s", intent.name)
            await self._record_execution("failed", intent, None, {})

    async def _record_execution(self, status: str, intent: ResolvedIntent,
                                command: str | None, parameters: dict) -> None:
        self._last_execution = {"status": status, "intent": intent.name,
                                "command": command, "source_language": intent.source_language}
        event_names = {
            "executed": INTERACTION_COMMAND_EXECUTED,
            "denied": INTERACTION_COMMAND_DENIED,
            "unsupported": INTERACTION_COMMAND_UNSUPPORTED,
            "failed": INTERACTION_COMMAND_FAILED,
            "rejected": INTERACTION_COMMAND_REJECTED,
        }
        payload = {"intent": intent.name, "command": command, "parameters": dict(parameters),
                   "source": InteractionSource.VOICE.value, "source_language": intent.source_language,
                   "status": status}
        await self._events.publish(Event(event_names[status], payload))

    async def _record_outcome(self, outcome: InteractionOutcome) -> None:
        self._last_outcome = {"status": outcome.status.value, "intent": outcome.intent,
                              "source_language": outcome.source_language}
        event_names = {
            "completed": INTERACTION_OUTCOME_COMPLETED,
            "unavailable": INTERACTION_OUTCOME_UNAVAILABLE,
            "failed": INTERACTION_OUTCOME_FAILED,
        }
        detail = ""
        if outcome.status.value == "completed":
            values = outcome.result or {}
            pairs = [(name, values[name]) for name in ("temperature_c", "eco2_ppm", "tvoc_ppb")
                     if name in values]
            detail = "" if not pairs else " " + " ".join(f"{name}={value}" for name, value in pairs)
            logger.debug("PHOS INTERACTION: outcome completed intent=%s language=%s%s",
                        outcome.intent, outcome.source_language, detail)
        else:
            logger.debug("PHOS INTERACTION: outcome %s intent=%s language=%s reason=%s",
                        outcome.status.value, outcome.intent, outcome.source_language,
                        outcome.reason or "unspecified")
        await self._events.publish(Event(event_names[outcome.status.value], outcome.document()))
        response = self._response_renderer.render(outcome)
        if response is not None:
            await self._record_response(response)

    async def _record_response(self, response: InteractionResponse) -> None:
        self._last_response = {"status": response.status.value, "intent": response.intent,
                               "language": response.language, "text": response.text}
        logger.debug("PHOS INTERACTION: response %s intent=%s language=%s text=%r",
                    response.status.value, response.intent, response.language, response.text)
        event_names = {
            "ready": INTERACTION_RESPONSE_READY,
            "unavailable": INTERACTION_RESPONSE_UNAVAILABLE,
            "failed": INTERACTION_RESPONSE_FAILED,
        }
        await self._events.publish(Event(event_names[response.status.value], response.document()))
        logger.debug("PHOS INTERACTION: response speech decision intent=%s enabled=%s status=%s",
                    response.intent, self._response_tts_enabled, response.status.value)
        if response.intent not in _SPEAKABLE_RESPONSE_INTENTS:
            logger.debug("PHOS INTERACTION: response speech skipped intent=%s reason=non_query_intent",
                        response.intent)
        elif not self._response_tts_enabled:
            logger.debug("PHOS INTERACTION: response speech skipped intent=%s reason=response_tts_disabled",
                        response.intent)
        else:
            await self._speak_response(response)

    async def _speak_response(self, response: InteractionResponse) -> None:
        try:
            if self._speech_coordinator is None:
                raise RuntimeError("speech coordinator unavailable")
            logger.debug("PHOS INTERACTION: response speech scheduled intent=%s language=%s",
                        response.intent, response.language)
            await self._speech_coordinator.speak(response)
        except Exception as error:
            self._last_speech = {"status": "failed", "intent": response.intent,
                                 "language": response.language}
            reason = getattr(error, "code", type(error).__name__)
            logger.exception("PHOS INTERACTION: response speech failed intent=%s language=%s reason=%s",
                             response.intent, response.language, reason)
            await self._events.publish(Event(INTERACTION_RESPONSE_SPEECH_FAILED, {
                "status": "failed", "intent": response.intent, "language": response.language,
            }))
            return
        self._last_speech = {"status": "completed", "intent": response.intent,
                             "language": response.language}
        logger.debug("PHOS INTERACTION: response speech completed intent=%s language=%s",
                    response.intent, response.language)
        await self._events.publish(Event(INTERACTION_RESPONSE_SPEECH_COMPLETED, {
            "status": "completed", "intent": response.intent, "language": response.language,
        }))

    async def _failed(self, error: Exception) -> None:
        resolver = getattr(self._resolver, "name", self._configured_resolver)
        category = getattr(error, "code", type(error).__name__)
        self._last_resolution = {"status": "failed", "intent": None, "source_language": None}
        logger.warning("PHOS INTERACTION: resolver failed resolver=%s error=%s", resolver, category)
        await self._events.publish(Event(INTERACTION_RESOLUTION_FAILED, {
            "source": InteractionSource.VOICE.value, "resolver": resolver, "error": str(category),
        }))
