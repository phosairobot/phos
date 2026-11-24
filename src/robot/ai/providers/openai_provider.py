# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from robot.ai.provider import LLMMessage, LLMProvider, LLMResponse, LLMTool


class OpenAIProvider(LLMProvider):
    """OpenAI adapter placeholder.

    Keep OpenAI SDK types inside this module. Do not leak them into core/agent.
    """

    def __init__(self, *, model: str, api_key: str | None = None) -> None:
        self.model = model
        self.api_key = api_key

    async def generate(self, messages, *, tools=(), temperature=None) -> LLMResponse:
        raise NotImplementedError("Implement the OpenAI adapter when selected")
