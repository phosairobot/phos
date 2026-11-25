# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from robot.ai.provider import LLMProvider, LLMResponse


class AnthropicProvider(LLMProvider):
    """Anthropic adapter placeholder. Vendor SDK types stay in this module."""

    def __init__(self, *, model: str, api_key: str | None = None) -> None:
        self.model = model
        self.api_key = api_key

    async def generate(self, messages, *, tools=(), temperature=None) -> LLMResponse:
        raise NotImplementedError("Implement the Anthropic adapter when selected")
