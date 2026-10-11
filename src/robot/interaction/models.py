"""Plain application-domain data for transcription and intent resolution."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import re
from typing import Any

from .language import normalize_language_tag


_INTENT_NAME = re.compile(r"^[a-z][a-z0-9_]*$")


@dataclass(frozen=True)
class TranscriptResult:
    """Provider-neutral output from a bounded speech transcription request."""

    text: str
    provider: str | None = None
    model: str | None = None
    confidence: float | None = None
    language: str | None = None
    duration_ms: int | None = None
    completed_at: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.text, str):
            raise TypeError("TranscriptResult.text must be a string")
        object.__setattr__(self, "language", normalize_language_tag(self.language))

    def document(self) -> dict[str, Any]:
        return {"text": self.text, "provider": self.provider, "model": self.model,
                "confidence": self.confidence, "language": self.language,
                "duration_ms": self.duration_ms, "completed_at": self.completed_at}


class InteractionSource(str, Enum):
    VOICE = "voice"
    API = "api"
    WEB = "web"


class CanonicalIntent(str, Enum):
    """Small stable intent vocabulary; routing and execution remain separate."""

    SLEEP = "sleep"
    WAKE = "wake"
    GET_TEMPERATURE = "get_temperature"
    GET_AIR_QUALITY = "get_air_quality"
    SET_EXPRESSION = "set_expression"


@dataclass(frozen=True)
class InteractionInput:
    """Language-bearing input before any resolver interprets it."""

    text: str
    source: InteractionSource
    language: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.text, str):
            raise TypeError("InteractionInput.text must be a string")
        if not isinstance(self.source, InteractionSource):
            raise TypeError("InteractionInput.source must be an InteractionSource")
        object.__setattr__(self, "language", normalize_language_tag(self.language))
        object.__setattr__(self, "metadata", dict(self.metadata))

    def document(self) -> dict[str, Any]:
        return {"text": self.text, "language": self.language, "source": self.source.value,
                "metadata": dict(self.metadata)}


@dataclass(frozen=True)
class ResolvedIntent:
    """Language-independent intent emitted by an IntentResolver, never executed here."""

    name: str
    parameters: dict[str, Any] = field(default_factory=dict)
    source_language: str | None = None
    confidence: float | None = None
    resolver: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not _INTENT_NAME.fullmatch(self.name):
            raise ValueError("ResolvedIntent.name must be a canonical lowercase identifier")
        object.__setattr__(self, "parameters", dict(self.parameters))
        object.__setattr__(self, "source_language", normalize_language_tag(self.source_language))

    def document(self) -> dict[str, Any]:
        return {"resolved": True, "name": self.name, "parameters": dict(self.parameters),
                "source_language": self.source_language, "confidence": self.confidence,
                "resolver": self.resolver}


@dataclass(frozen=True)
class UnresolvedIntent:
    """Normal no-match result; resolver failures use IntentResolverError instead."""

    source_language: str | None = None
    resolver: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_language", normalize_language_tag(self.source_language))

    def document(self) -> dict[str, Any]:
        return {"resolved": False, "source_language": self.source_language, "resolver": self.resolver}
