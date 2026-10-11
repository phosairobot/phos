"""Small exact-match multilingual resolver with no command-execution access."""
from __future__ import annotations

import re
import unicodedata

from robot.ui.state import FaceExpression

from .language import normalize_language_tag
from .models import CanonicalIntent, InteractionInput, ResolvedIntent, UnresolvedIntent


_WHITESPACE = re.compile(r"\s+")
_TERMINAL_PUNCTUATION = ".!?"


def normalize_interaction_text(text: str) -> str:
    """Normalize only presentation variation, never language or word meaning."""
    return _WHITESPACE.sub(" ", unicodedata.normalize("NFC", text).strip()).casefold().rstrip(_TERMINAL_PUNCTUATION).rstrip()


_EXPRESSIONS = {
    "en": {
        "be happy": FaceExpression.HAPPY.value, "look happy": FaceExpression.HAPPY.value,
        "be curious": FaceExpression.CURIOUS.value, "look curious": FaceExpression.CURIOUS.value,
        "be surprised": FaceExpression.SURPRISED.value, "look surprised": FaceExpression.SURPRISED.value,
    },
    "it": {
        "sii felice": FaceExpression.HAPPY.value, "fai una faccia felice": FaceExpression.HAPPY.value,
        "sii curioso": FaceExpression.CURIOUS.value, "fai una faccia curiosa": FaceExpression.CURIOUS.value,
        "sii sorpreso": FaceExpression.SURPRISED.value,
    },
}

_PHRASES = {
    "en": {
        "go to sleep": CanonicalIntent.SLEEP, "sleep": CanonicalIntent.SLEEP,
        "go to sleep mode": CanonicalIntent.SLEEP, "wake up": CanonicalIntent.WAKE,
        "wake": CanonicalIntent.WAKE, "what is the temperature": CanonicalIntent.GET_TEMPERATURE,
        "what's the temperature": CanonicalIntent.GET_TEMPERATURE,
        "tell me the temperature": CanonicalIntent.GET_TEMPERATURE,
        "what is the air quality": CanonicalIntent.GET_AIR_QUALITY,
        "how is the air quality": CanonicalIntent.GET_AIR_QUALITY,
    },
    "it": {
        "vai a dormire": CanonicalIntent.SLEEP, "dormi": CanonicalIntent.SLEEP,
        "svegliati": CanonicalIntent.WAKE, "svegliati phos": CanonicalIntent.WAKE,
        "che temperatura c'è": CanonicalIntent.GET_TEMPERATURE,
        "qual è la temperatura": CanonicalIntent.GET_TEMPERATURE,
        "dimmi la temperatura": CanonicalIntent.GET_TEMPERATURE,
        "com'è la qualità dell'aria": CanonicalIntent.GET_AIR_QUALITY,
        "qual è la qualità dell'aria": CanonicalIntent.GET_AIR_QUALITY,
    },
}


class DeterministicIntentResolver:
    """Resolve a deliberately small exact phrase catalog into canonical intents."""

    name = "deterministic"

    def __init__(self, *, default_language: str, supported_languages: tuple[str, ...]) -> None:
        self._default_language = normalize_language_tag(default_language)
        self._supported_languages = {language.split("-", 1)[0] for language in supported_languages}

    async def resolve(self, interaction: InteractionInput) -> ResolvedIntent | UnresolvedIntent:
        source_language = interaction.language or self._default_language
        language = source_language.split("-", 1)[0] if source_language else None
        if language not in self._supported_languages:
            return UnresolvedIntent(source_language=source_language, resolver=self.name)
        text = normalize_interaction_text(interaction.text)
        expression = _EXPRESSIONS.get(language, {}).get(text)
        if expression is not None:
            return ResolvedIntent(CanonicalIntent.SET_EXPRESSION.value, {"expression": expression},
                                  source_language, resolver=self.name)
        intent = _PHRASES.get(language, {}).get(text)
        if intent is None:
            return UnresolvedIntent(source_language=source_language, resolver=self.name)
        return ResolvedIntent(intent.value, source_language=source_language, resolver=self.name)
