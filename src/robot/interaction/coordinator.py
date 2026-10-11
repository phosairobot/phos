"""Observational transcript-to-intent coordination owned by the main runtime."""
from __future__ import annotations

import logging

from robot.core import Event
from robot.voice.session import VOICE_TRANSCRIPTION_COMPLETED

from .models import InteractionInput, InteractionSource, ResolvedIntent
from .resolver import IntentResolverError


logger = logging.getLogger(__name__)

INTERACTION_INTENT_RESOLVED = "interaction_intent_resolved"
INTERACTION_INTENT_UNRESOLVED = "interaction_intent_unresolved"
INTERACTION_RESOLUTION_FAILED = "interaction_resolution_failed"


class InteractionCoordinator:
    """Observe completed transcripts and publish resolution facts, never actions."""

    def __init__(self, events, *, enabled: bool, configured_resolver: str,
                 default_language: str, supported_languages: tuple[str, ...], resolver=None,
                 unavailable_reason: str | None = None) -> None:
        self._events = events
        self._enabled = bool(enabled)
        self._configured_resolver = configured_resolver
        self._default_language = default_language
        self._supported_languages = list(supported_languages)
        self._resolver = resolver
        self._unavailable_reason = unavailable_reason
        self._last_resolution = {"status": None, "intent": None, "source_language": None}
        self._unsubscribe = None
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
                "default_language": self._default_language,
                "supported_languages": list(self._supported_languages),
                "last_resolution": dict(self._last_resolution),
                "unavailable_reason": self._unavailable_reason}

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
            return
        payload = {"source": InteractionSource.VOICE.value,
                   "source_language": result.source_language, "resolver": result.resolver}
        self._last_resolution = {"status": "unresolved", "intent": None,
                                 "source_language": result.source_language}
        logger.info("PHOS INTERACTION: input unresolved language=%s source=voice", result.source_language)
        await self._events.publish(Event(INTERACTION_INTENT_UNRESOLVED, payload))

    async def _failed(self, error: Exception) -> None:
        resolver = getattr(self._resolver, "name", self._configured_resolver)
        category = getattr(error, "code", type(error).__name__)
        self._last_resolution = {"status": "failed", "intent": None, "source_language": None}
        logger.warning("PHOS INTERACTION: resolver failed resolver=%s error=%s", resolver, category)
        await self._events.publish(Event(INTERACTION_RESOLUTION_FAILED, {
            "source": InteractionSource.VOICE.value, "resolver": resolver, "error": str(category),
        }))
