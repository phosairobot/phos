import asyncio
from pathlib import Path

import pytest

from robot.interaction import (InteractionInput, InteractionSource, IntentResolverError,
                               IntentResolverFactory, ResolvedIntent, TranscriptResult, UnresolvedIntent,
                               normalize_language_tag)
from robot.interaction import (INTERACTION_INTENT_RESOLVED, INTERACTION_INTENT_UNRESOLVED,
                               INTERACTION_RESOLUTION_FAILED, InteractionCoordinator)
from robot.interaction.resolver import IntentResolver
from robot.voice import SpeechRecognitionResult
from robot.voice.session import VOICE_TRANSCRIPTION_COMPLETED
from robot.config import load_document, RuntimeConfig
from robot.core import Event, EventBus, RobotCore
from robot.runtime import build_runtime
from robot.ui import MemoryEyeDisplay


def test_transcript_result_is_plain_optional_metadata_and_stt_compatible():
    transcript = TranscriptResult("ciao", language="it_it")
    assert transcript.document() == {"text": "ciao", "provider": None, "model": None,
                                     "confidence": None, "language": "it-IT",
                                     "duration_ms": None, "completed_at": None}
    assert SpeechRecognitionResult is TranscriptResult
    with pytest.raises(TypeError, match="text"):
        TranscriptResult(None)  # type: ignore[arg-type]


def test_interaction_input_supports_all_initial_sources_and_language_context():
    assert {InteractionInput("x", source).source for source in InteractionSource} == {
        InteractionSource.VOICE, InteractionSource.API, InteractionSource.WEB,
    }
    value = InteractionInput("vai a dormire", InteractionSource.VOICE, "it_it", {"request_id": "1"})
    assert value.document() == {"text": "vai a dormire", "language": "it-IT", "source": "voice",
                                "metadata": {"request_id": "1"}}


def test_resolved_intent_is_canonical_and_keeps_structured_parameters():
    intent = ResolvedIntent("set_expression", {"expression": "happy"}, "en-us", .8, "test")
    assert intent.document() == {"resolved": True, "name": "set_expression",
                                 "parameters": {"expression": "happy"}, "source_language": "en-US",
                                 "confidence": .8, "resolver": "test"}
    with pytest.raises(ValueError, match="canonical"):
        ResolvedIntent("vai a dormire")


def test_resolver_no_match_is_not_a_provider_failure():
    class StubResolver:
        async def resolve(self, interaction):
            if interaction.text == "sleep":
                return ResolvedIntent("sleep", source_language=interaction.language, resolver="stub")
            return UnresolvedIntent(interaction.language, "stub")

    resolver: IntentResolver = StubResolver()
    resolved = asyncio.run(resolver.resolve(InteractionInput("sleep", InteractionSource.API, "en")))
    assert resolved.document()["name"] == "sleep"
    result = asyncio.run(resolver.resolve(InteractionInput("???", InteractionSource.API, "it")))
    assert result.document() == {"resolved": False, "source_language": "it", "resolver": "stub"}
    with pytest.raises(IntentResolverError):
        raise IntentResolverError("provider_unavailable")


@pytest.mark.parametrize(("raw", "expected"), [
    (None, None), ("en-us", "en-US"), ("it", "it"), ("IT_it", "it-IT"),
    ("unknown language", "unknown language"), ("x", "x"),
])
def test_language_normalization_is_conservative(raw, expected):
    assert normalize_language_tag(raw) == expected


def _resolver(*, default_language="en", supported_languages=("en", "it")):
    document = load_document()
    document["interaction"].update(enabled=True, resolver="deterministic",
                                   default_language=default_language,
                                   supported_languages=list(supported_languages))
    config = RuntimeConfig.from_dict(document, base_dir=Path("config").resolve(), check_paths=False)
    return IntentResolverFactory.create(config)


def test_interaction_config_defaults_and_factory_selection():
    config = RuntimeConfig.from_file()
    assert (config.interaction_enabled, config.interaction_execution_enabled, config.interaction_resolver,
            config.interaction_default_language, config.interaction_supported_languages) == (
                False, False, "deterministic", "en", ("en", "it"))
    assert type(_resolver()).__name__ == "DeterministicIntentResolver"


def test_existing_interaction_document_defaults_execution_to_disabled():
    document = load_document()
    document["interaction"].pop("execution_enabled")
    config = RuntimeConfig.from_dict(document, base_dir=Path("config").resolve(), check_paths=False)
    assert config.interaction_execution_enabled is False


def test_unknown_resolver_is_rejected_without_fallback():
    class Config:
        interaction_resolver = "unknown"
    with pytest.raises(IntentResolverError, match="unknown"):
        IntentResolverFactory.create(Config())


@pytest.mark.parametrize(("language", "text", "name", "parameters"), [
    ("en", "sleep", "sleep", {}), ("en", "wake up", "wake", {}),
    ("en", "what is the temperature", "get_temperature", {}),
    ("en", "how is the air quality", "get_air_quality", {}),
    ("en", "be happy", "set_expression", {"expression": "happy"}),
    ("en", "look curious", "set_expression", {"expression": "curious"}),
    ("en", "be surprised", "set_expression", {"expression": "surprised"}),
    ("it", "vai a dormire", "sleep", {}), ("it", "svegliati", "wake", {}),
    ("it", "che temperatura c'è", "get_temperature", {}),
    ("it", "com'è la qualità dell'aria", "get_air_quality", {}),
    ("it", "sii felice", "set_expression", {"expression": "happy"}),
    ("it", "sii curioso", "set_expression", {"expression": "curious"}),
    ("it", "sii sorpreso", "set_expression", {"expression": "surprised"}),
])
def test_deterministic_resolver_has_small_english_and_italian_catalog(language, text, name, parameters):
    result = asyncio.run(_resolver().resolve(InteractionInput(text, InteractionSource.API, language)))
    assert isinstance(result, ResolvedIntent)
    assert (result.name, result.parameters, result.source_language, result.resolver) == (
        name, parameters, language, "deterministic")


@pytest.mark.parametrize("text", [
    "sii felice", "si felice", "felice", "sii allegro", "si allegro", "allegro",
    "fammi felice", "fammi allegro", "mostrati felice", "mostrati allegro",
])
def test_italian_happy_expression_aliases_remain_exact_catalog_entries(text):
    result = asyncio.run(_resolver().resolve(InteractionInput(text, InteractionSource.VOICE, "it")))
    assert isinstance(result, ResolvedIntent)
    assert (result.name, result.parameters, result.source_language) == (
        "set_expression", {"expression": "happy"}, "it")


@pytest.mark.parametrize(("language", "text", "expression"), [
    ("en", "be happy", "happy"), ("en", "be curious", "curious"),
    ("en", "be surprised", "surprised"), ("en", "be neutral", "neutral"),
    ("it", "sii allegro", "happy"), ("it", "sii curioso", "curious"),
    ("it", "sii sorpreso", "surprised"), ("it", "torna neutro", "neutral"),
])
def test_expression_aliases_resolve_to_the_canonical_writable_value(language, text, expression):
    result = asyncio.run(_resolver().resolve(InteractionInput(text, InteractionSource.VOICE, language)))
    assert isinstance(result, ResolvedIntent)
    assert (result.name, result.parameters, result.source_language) == (
        "set_expression", {"expression": expression}, language)


def test_unrelated_italian_expression_phrase_remains_unresolved():
    result = asyncio.run(_resolver().resolve(InteractionInput("fammi triste", InteractionSource.VOICE, "it")))
    assert isinstance(result, UnresolvedIntent)


def test_resolution_prefers_explicit_language_and_preserves_full_tag():
    resolver = _resolver(default_language="en")
    italian = asyncio.run(resolver.resolve(InteractionInput("dormi", InteractionSource.API, "it-IT")))
    english = asyncio.run(resolver.resolve(InteractionInput("sleep", InteractionSource.API, "en-US")))
    assert italian.source_language == "it-IT" and italian.name == "sleep"
    assert english.source_language == "en-US" and english.name == "sleep"


def test_resolution_uses_default_language_and_exact_safe_normalization_only():
    resolver = _resolver(default_language="it")
    resolved = asyncio.run(resolver.resolve(InteractionInput("  SII   FELICE! ", InteractionSource.WEB)))
    partial = asyncio.run(resolver.resolve(InteractionInput("please sleep now", InteractionSource.WEB, "en")))
    unsupported = asyncio.run(resolver.resolve(InteractionInput("sleep", InteractionSource.WEB, "fr")))
    assert (resolved.name, resolved.parameters, resolved.source_language) == (
        "set_expression", {"expression": "happy"}, "it")
    assert isinstance(partial, UnresolvedIntent)
    assert isinstance(unsupported, UnresolvedIntent) and unsupported.source_language == "fr"


def test_disabled_interaction_leaves_voice_transcript_events_unchanged():
    class Resolver:
        name = "test"
        async def resolve(self, interaction):
            raise AssertionError("disabled interaction must not invoke the resolver")
    bus, received = EventBus(), []
    InteractionCoordinator(bus, enabled=False, configured_resolver="deterministic",
                           default_language="en", supported_languages=("en", "it"), resolver=Resolver())
    bus.subscribe(VOICE_TRANSCRIPTION_COMPLETED, lambda event: received.append(event.data))
    asyncio.run(bus.publish(Event(VOICE_TRANSCRIPTION_COMPLETED, {"text": "go to sleep", "language": "en"})))
    assert received == [{"text": "go to sleep", "language": "en"}]


@pytest.mark.parametrize(("text", "language", "intent", "source_language"), [
    ("go to sleep", "en", "sleep", "en"),
    ("che temperatura c'è", "it-IT", "get_temperature", "it-IT"),
    ("dormi", None, "sleep", "it"),
])
def test_voice_transcripts_publish_observational_resolution_only(text, language, intent, source_language):
    bus, received, core = EventBus(), [], RobotCore()
    resolver = _resolver(default_language="it")
    coordinator = InteractionCoordinator(bus, enabled=True, configured_resolver="deterministic",
                                         default_language="it", supported_languages=("en", "it"), resolver=resolver)
    bus.subscribe(INTERACTION_INTENT_RESOLVED, lambda event: received.append(event.data))
    asyncio.run(bus.publish(Event(VOICE_TRANSCRIPTION_COMPLETED, {"text": text, "language": language})))
    assert received[-1]["intent"] == intent and received[-1]["source_language"] == source_language
    assert core.state.value == "idle"
    assert coordinator.status()["last_resolution"] == {
        "status": "resolved", "intent": intent, "source_language": source_language}


def test_voice_transcript_no_match_and_resolver_failure_are_distinct():
    bus, unresolved, failures = EventBus(), [], []
    coordinator = InteractionCoordinator(bus, enabled=True, configured_resolver="deterministic",
                                         default_language="en", supported_languages=("en",), resolver=_resolver())
    bus.subscribe(INTERACTION_INTENT_UNRESOLVED, lambda event: unresolved.append(event.data))
    asyncio.run(bus.publish(Event(VOICE_TRANSCRIPTION_COMPLETED, {"text": "today was a long day", "language": "en"})))
    assert unresolved == [{"source": "voice", "source_language": "en", "resolver": "deterministic"}]
    assert coordinator.status()["last_resolution"]["status"] == "unresolved"

    class FailingResolver:
        name = "failing"
        async def resolve(self, interaction):
            raise IntentResolverError("provider_unavailable")
    failing = InteractionCoordinator(bus, enabled=True, configured_resolver="failing",
                                     default_language="en", supported_languages=("en",), resolver=FailingResolver())
    bus.subscribe(INTERACTION_RESOLUTION_FAILED, lambda event: failures.append(event.data))
    asyncio.run(bus.publish(Event(VOICE_TRANSCRIPTION_COMPLETED, {"text": "sleep", "language": "en"})))
    assert failures[-1] == {"source": "voice", "resolver": "failing", "error": "IntentResolverError"}
    assert failing.status()["last_resolution"]["status"] == "failed"


def test_runtime_status_exposes_configured_and_active_observational_resolver():
    document = load_document()
    document["interaction"]["enabled"] = True
    config = RuntimeConfig.from_dict(document, base_dir=Path("config").resolve(), check_paths=False)
    runtime = build_runtime(config=config, eye_display=MemoryEyeDisplay())
    assert runtime.application_status()["interaction"] == {
        "enabled": True, "execution_enabled": False, "configured_resolver": "deterministic",
        "active_resolver": "deterministic", "available": True,
        "default_language": "en", "supported_languages": ["en", "it"],
        "last_resolution": {"status": None, "intent": None, "source_language": None},
        "last_execution": {"status": None, "intent": None, "command": None, "source_language": None},
        "last_outcome": {"status": None, "intent": None, "source_language": None},
        "last_response": {"status": None, "intent": None, "language": None, "text": None},
        "unavailable_reason": None,
    }
