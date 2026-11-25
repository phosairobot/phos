# SPDX-License-Identifier: Apache-2.0
"""Small, in-process event primitives used to connect robot subsystems."""

from __future__ import annotations

import inspect
from collections import defaultdict
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, DefaultDict


@dataclass(frozen=True)
class Event:
    """An immutable event with a stable name and optional payload."""

    name: str
    data: Mapping[str, Any] = field(default_factory=dict)


EventHandler = Callable[[Event], Awaitable[None] | None]


class EventBus:
    """Dispatch events to local handlers in their subscription order.

    Handlers may be synchronous or asynchronous. Exceptions are propagated to
    the publisher so the caller can choose an appropriate recovery action.
    """

    def __init__(self) -> None:
        self._handlers: DefaultDict[str, list[EventHandler]] = defaultdict(list)

    def subscribe(self, event_name: str, handler: EventHandler) -> Callable[[], None]:
        """Register *handler* and return a function that removes it."""
        self._handlers[event_name].append(handler)

        def unsubscribe() -> None:
            handlers = self._handlers.get(event_name)
            if handlers is not None and handler in handlers:
                handlers.remove(handler)

        return unsubscribe

    async def publish(self, event: Event) -> None:
        """Deliver an event to a snapshot of its current subscribers."""
        for handler in tuple(self._handlers.get(event.name, ())):
            result = handler(event)
            if inspect.isawaitable(result):
                await result
