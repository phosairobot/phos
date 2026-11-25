# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from collections.abc import Sequence

from robot.ai.provider import LLMMessage, LLMProvider, LLMResponse, LLMTool


class RobotAgent:
    """Provider-independent conversational agent."""

    def __init__(self, provider: LLMProvider) -> None:
        self._provider = provider

    async def respond(
        self,
        messages: Sequence[LLMMessage],
        *,
        tools: Sequence[LLMTool] = (),
    ) -> LLMResponse:
        return await self._provider.generate(messages, tools=tools)
