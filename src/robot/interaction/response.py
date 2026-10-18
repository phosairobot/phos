"""Pure localized rendering of semantic interaction outcomes."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import math

from .language import normalize_language_tag
from .models import (InteractionOutcome, InteractionOutcomeStatus, InteractionResponse,
                     InteractionResponseStatus)


_SUPPORTED_LANGUAGES = frozenset({"en", "it"})
_QUERY_INTENTS = frozenset({"get_temperature", "get_air_quality", "get_sensor_value"})


class InteractionResponseRenderer:
    """Render bounded query outcomes without services, providers, or side effects."""

    def __init__(self, default_language: str) -> None:
        normalized = normalize_language_tag(default_language)
        if not isinstance(normalized, str) or normalized.split("-", 1)[0] not in _SUPPORTED_LANGUAGES:
            raise ValueError("Interaction response default language must be en or it")
        self._default_language = normalized

    def render(self, outcome: InteractionOutcome) -> InteractionResponse | None:
        """Return localized text for query outcomes; actions intentionally have no reply."""
        if outcome.intent not in _QUERY_INTENTS:
            return None
        language = self._language_for(outcome.source_language)
        base = language.split("-", 1)[0]
        if outcome.status is InteractionOutcomeStatus.COMPLETED:
            text = self._completed(outcome, base)
            status = InteractionResponseStatus.READY
        elif outcome.status is InteractionOutcomeStatus.UNAVAILABLE:
            text = self._unavailable(outcome.intent, outcome.reason, base)
            status = InteractionResponseStatus.UNAVAILABLE
        else:
            text = "I couldn't retrieve that information." if base == "en" else "Non sono riuscito a recuperare quell'informazione."
            status = InteractionResponseStatus.FAILED
        return InteractionResponse(text, language, outcome.intent, status)

    def _language_for(self, source_language: str | None) -> str:
        candidate = normalize_language_tag(source_language) or self._default_language
        return candidate if candidate.split("-", 1)[0] in _SUPPORTED_LANGUAGES else self._default_language

    @staticmethod
    def _completed(outcome: InteractionOutcome, language: str) -> str:
        result = outcome.result or {}
        sensor = result.get("sensor")
        if outcome.intent == "get_temperature" or sensor == "temperature":
            value = result.get("temperature_c")
            formatted = InteractionResponseRenderer._temperature(value)
            if formatted is None:
                return ("I couldn't retrieve that information." if language == "en"
                        else "Non sono riuscito a recuperare quell'informazione.")
            return (f"The temperature is {formatted} degrees Celsius." if language == "en"
                    else f"La temperatura è di {formatted} gradi Celsius.")
        if sensor == "pressure":
            value = result.get("pressure_hpa")
            formatted = InteractionResponseRenderer._temperature(value)
            if formatted is None:
                return ("I couldn't retrieve that information." if language == "en"
                        else "Non sono riuscito a recuperare quell'informazione.")
            return (f"The pressure is {formatted} hectopascals." if language == "en"
                    else f"La pressione è di {formatted} ettopascal.")
        eco2, tvoc = result.get("eco2_ppm"), result.get("tvoc_ppb")
        if type(eco2) is not int or type(tvoc) is not int:
            return ("I couldn't retrieve that information." if language == "en"
                    else "Non sono riuscito a recuperare quell'informazione.")
        return (f"The air quality reading is {eco2} ppm eCO2 and {tvoc} ppb TVOC."
                if language == "en" else
                f"La qualità dell'aria rilevata è {eco2} ppm di eCO2 e {tvoc} ppb di TVOC.")

    @staticmethod
    def _unavailable(intent: str, reason: str | None, language: str) -> str:
        if intent == "get_temperature" or reason == "temperature_unavailable":
            return ("Temperature data is currently unavailable." if language == "en"
                    else "I dati sulla temperatura non sono disponibili al momento.")
        if reason == "pressure_unavailable":
            return ("Pressure data is currently unavailable." if language == "en"
                    else "I dati sulla pressione non sono disponibili al momento.")
        return ("Air quality data is currently unavailable." if language == "en"
                else "I dati sulla qualità dell'aria non sono disponibili al momento.")

    @staticmethod
    def _temperature(value: object) -> str | None:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            return None
        try:
            rounded = Decimal(str(value)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
        except (InvalidOperation, ValueError):
            return None
        return str(int(rounded)) if rounded == rounded.to_integral() else format(rounded, ".1f")
