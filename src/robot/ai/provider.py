# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class LLMMessage:
    role: str
    content: str


@dataclass(frozen=True)
class LLMTool:
    name: str
    description: str
    input_schema: Mapping[str, Any]


@dataclass(frozen=True)
class LLMToolCall:
    id: str
    name: str
    arguments: Mapping[str, Any]


@dataclass(frozen=True)
class LLMResponse:
    text: str = ""
    tool_calls: Sequence[LLMToolCall] = field(default_factory=tuple)
    model: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


class LLMProvider(ABC):
    """Provider-independent contract for language-model access.

    The rest of the robot must depend on this interface, never directly on a
    vendor SDK. Provider adapters translate vendor-specific requests and
    responses into these neutral data structures.
    """

    @abstractmethod
    async def generate(
        self,
        messages: Sequence[LLMMessage],
        *,
        tools: Sequence[LLMTool] = (),
        temperature: float | None = None,
    ) -> LLMResponse:
        raise NotImplementedError
