"""Application-boundary speech for rendered interaction responses."""
from __future__ import annotations

from typing import Protocol

from .models import InteractionResponse


class InteractionSpeechApplicationService(Protocol):
    async def speak_interaction_response(self, text: str, language: str, *, intent: str) -> dict: ...


class InteractionSpeechCoordinator:
    """Keep interaction orchestration independent of TTS and audio providers."""

    def __init__(self, service: InteractionSpeechApplicationService) -> None:
        self._service = service

    async def speak(self, response: InteractionResponse) -> dict:
        return await self._service.speak_interaction_response(response.text, response.language,
                                                              intent=response.intent)
