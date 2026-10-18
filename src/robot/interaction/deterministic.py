"""Deterministic multilingual intent resolver using bounded templates and aliases."""
from __future__ import annotations

import re
import unicodedata

from .language import normalize_language_tag
from .models import CanonicalIntent, InteractionInput, ResolvedIntent, UnresolvedIntent

_WHITESPACE = re.compile(r"\s+")
_TERMINAL_PUNCTUATION = ".!?"


def normalize_interaction_text(text: str) -> str:
    """Normalize presentation only; matching remains exact and deterministic."""
    return _WHITESPACE.sub(" ", unicodedata.normalize("NFC", text).strip()).casefold().rstrip(
        _TERMINAL_PUNCTUATION).rstrip()

_EXPRESSION_ALIASES = {
    "en": {
        "happy": ("happy",), "curious": ("curious",),
        "surprised": ("surprised",), "neutral": ("neutral", "normal"),
    },
    "it": {
        "happy": ("felice", "allegro"), "curious": ("curioso", "curiosa"),
        "surprised": ("sorpreso", "sorpresa"), "neutral": ("neutro", "neutra", "normale"),
    },
}
_EXPRESSION_TEMPLATES = {
    "en": ("{value}", "be {value}", "look {value}", "face {value}",
           "make a {value} face", "show a {value} face"),
    "it": ("{value}", "diventa {value}", "sii {value}", "si {value}", "mostrati {value}",
           "faccia {value}", "fai una faccia {value}", "mostra una faccia {value}",
           "fammi {value}", "torna {value}", "espressione {value}"),
}
_EXPRESSION_SPECIAL = {
    "en": {"normal face": "neutral", "reset expression": "neutral"},
    "it": {},
}
_STATE_ALIASES = {
    "en": {
        "sleeping": ("sleep",),
        "idle": ("idle",),
    },
    "it": {
        "sleeping": ("dormire",),
        "idle": ("attivo",),
    },
}
_STATE_TEMPLATES = {
    "en": {
        "sleeping": ("{value}", "go to {value}", "go to {value} mode", "enter {value} mode"),
        "idle": ("enter {value} mode", "go {value}"),
    },
    "it": {
        "sleeping": ("vai a {value}", "mettiti a {value}"),
        "idle": ("torna {value}",),
    },
}
# Imperative forms cannot be derived from the canonical infinitive/stem without
# language morphology.  Keep only these deliberate, already-supported forms.
_STATE_SPECIAL = {
    "en": {"wake": "idle", "wake up": "idle"},
    "it": {"dormi": "sleeping", "entra in modalità sonno": "sleeping",
           "svegliati": "idle", "svegliati phos": "idle"},
}
_SENSOR_ALIASES = {
    "en": {
        "temperature": ("temperature",), "pressure": ("pressure", "atmospheric pressure"),
        "air_quality": ("air quality",),
    },
    "it": {
        "temperature": ("temperatura",), "pressure": ("pressione", "pressione atmosferica"),
        "air_quality": ("qualità dell'aria",),
    },
}
_SENSOR_TEMPLATES = {
    "en": ("what is the {value}", "what's the {value}", "tell me the {value}", "read the {value}", "show me the {value}"),
    "it": ("qual è la {value}", "dimmi la {value}", "dammi la {value}",
           "leggi la {value}", "mostrami la {value}", "controlla la {value}"),
}
_SENSOR_SPECIAL = {
    "en": {"how is the air quality": "air_quality"},
    "it": {"che temperatura c'è": "temperature", "com'è la qualità dell'aria": "air_quality"},
}


class DeterministicIntentResolver:
    """Resolve explicit language templates without probabilistic interpretation."""

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
        expression = self._match(
            text, language, _EXPRESSION_ALIASES, _EXPRESSION_TEMPLATES, _EXPRESSION_SPECIAL)
        if expression is not None:
            return ResolvedIntent(CanonicalIntent.SET_EXPRESSION.value, {"expression": expression},
                                  source_language, resolver=self.name)
        state = self._match_state(text, language)
        if state is not None:
            return ResolvedIntent(CanonicalIntent.SET_STATE.value, {"state": state},
                                  source_language, resolver=self.name)
        sensor = self._match(
            text, language, _SENSOR_ALIASES, _SENSOR_TEMPLATES, _SENSOR_SPECIAL)
        if sensor is not None:
            return ResolvedIntent(CanonicalIntent.GET_SENSOR_VALUE.value, {"sensor": sensor},
                                  source_language, resolver=self.name)
        return UnresolvedIntent(source_language=source_language, resolver=self.name)

    @staticmethod
    def _match(
            text: str, language: str, aliases: dict, templates: dict, special: dict) -> str | None:
        if text in special.get(language, {}):
            return special[language][text]
        for canonical, values in aliases.get(language, {}).items():
            if any(text == template.format(value=value)
                   for template in templates.get(language, ()) for value in values):
                return canonical
        return None

    @staticmethod
    def _match_state(text: str, language: str) -> str | None:
        if text in _STATE_SPECIAL.get(language, {}):
            return _STATE_SPECIAL[language][text]
        for state, aliases in _STATE_ALIASES.get(language, {}).items():
            templates = _STATE_TEMPLATES.get(language, {}).get(state, ())
            if any(text == template.format(value=alias)
                   for template in templates for alias in aliases):
                return state
        return None
