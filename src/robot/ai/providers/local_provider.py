# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from robot.ai.provider import LLMProvider, LLMResponse


class LocalLLMProvider(LLMProvider):
    """Adapter placeholder for a local/remote self-hosted model endpoint.

    The Raspberry Pi 3 should not be assumed capable of running the primary
    conversational model locally. A LAN server is also a valid local provider.
    """

    def __init__(self, *, model: str, endpoint: str | None = None) -> None:
        self.model = model
        self.endpoint = endpoint

    async def generate(self, messages, *, tools=(), temperature=None) -> LLMResponse:
        raise NotImplementedError("Implement the local model adapter when selected")
