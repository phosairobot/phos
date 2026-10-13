"""Pure localized rendering tests for semantic interaction outcomes."""
import pytest

from robot.interaction import (InteractionOutcome, InteractionOutcomeStatus, InteractionResponseRenderer,
                               InteractionSource)


def _outcome(status, intent, *, language="en", result=None, reason=None):
    return InteractionOutcome(status, intent, InteractionSource.VOICE, language, result, reason)


@pytest.mark.parametrize(("language", "expected"), [
    ("en", "The temperature is 25 degrees Celsius."),
    ("it", "La temperatura è di 25 gradi Celsius."),
    ("en-US", "The temperature is 25 degrees Celsius."),
    ("it-IT", "La temperatura è di 25 gradi Celsius."),
])
def test_temperature_rendering_uses_localized_templates_and_bounded_rounding(language, expected):
    response = InteractionResponseRenderer("en").render(
        _outcome(InteractionOutcomeStatus.COMPLETED, "get_temperature", language=language,
                 result={"temperature_c": 24.950364}))
    assert response.document() == {"text": expected, "language": language,
                                   "intent": "get_temperature", "status": "ready"}


def test_temperature_decimal_rounding_is_deterministic_and_compact():
    renderer = InteractionResponseRenderer("en")
    assert renderer.render(_outcome(InteractionOutcomeStatus.COMPLETED, "get_temperature",
                                    result={"temperature_c": 24.4})).text == "The temperature is 24.4 degrees Celsius."
    assert renderer.render(_outcome(InteractionOutcomeStatus.COMPLETED, "get_temperature",
                                    result={"temperature_c": 24.05})).text == "The temperature is 24.1 degrees Celsius."


@pytest.mark.parametrize(("language", "expected"), [
    ("en", "The air quality reading is 520 ppm eCO2 and 12 ppb TVOC."),
    ("it", "La qualità dell'aria rilevata è 520 ppm di eCO2 e 12 ppb di TVOC."),
])
def test_air_quality_rendering_is_factual_and_localized(language, expected):
    response = InteractionResponseRenderer("en").render(
        _outcome(InteractionOutcomeStatus.COMPLETED, "get_air_quality", language=language,
                 result={"eco2_ppm": 520, "tvoc_ppb": 12}))
    assert response.text == expected


@pytest.mark.parametrize(("intent", "language", "expected"), [
    ("get_temperature", "en", "Temperature data is currently unavailable."),
    ("get_temperature", "it", "I dati sulla temperatura non sono disponibili al momento."),
    ("get_air_quality", "en", "Air quality data is currently unavailable."),
    ("get_air_quality", "it", "I dati sulla qualità dell'aria non sono disponibili al momento."),
])
def test_unavailable_outcomes_have_safe_localized_text(intent, language, expected):
    response = InteractionResponseRenderer("en").render(
        _outcome(InteractionOutcomeStatus.UNAVAILABLE, intent, language=language, reason="internal_code"))
    assert (response.status.value, response.text) == ("unavailable", expected)
    assert "internal_code" not in response.text


@pytest.mark.parametrize(("language", "expected"), [
    ("en", "I couldn't retrieve that information."),
    ("it", "Non sono riuscito a recuperare quell'informazione."),
])
def test_failed_outcomes_do_not_expose_internal_details(language, expected):
    response = InteractionResponseRenderer("en").render(
        _outcome(InteractionOutcomeStatus.FAILED, "get_temperature", language=language, reason="application_error"))
    assert (response.status.value, response.text) == ("failed", expected)
    assert "application_error" not in response.text


def test_missing_or_unsupported_source_language_uses_default_language():
    renderer = InteractionResponseRenderer("it")
    completed = InteractionOutcome(InteractionOutcomeStatus.COMPLETED, "get_temperature",
                                   InteractionSource.VOICE, result={"temperature_c": 25})
    unsupported = _outcome(InteractionOutcomeStatus.COMPLETED, "get_temperature", language="fr",
                           result={"temperature_c": 25})
    assert renderer.render(completed).language == "it"
    assert renderer.render(unsupported).language == "it"
    assert renderer.render(unsupported).text == "La temperatura è di 25 gradi Celsius."


def test_action_outcomes_do_not_generate_a_response():
    outcome = _outcome(InteractionOutcomeStatus.COMPLETED, "sleep", result={})
    assert InteractionResponseRenderer("en").render(outcome) is None
