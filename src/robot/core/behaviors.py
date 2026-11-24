# SPDX-License-Identifier: Apache-2.0
"""Lifecycle management for optional robot behaviors."""

from __future__ import annotations

from abc import ABC, abstractmethod
import asyncio


class Behavior(ABC):
    """A component whose work is tied to the robot runtime lifecycle."""

    name: str

    @abstractmethod
    async def start(self) -> None:
        raise NotImplementedError

    @abstractmethod
    async def stop(self) -> None:
        raise NotImplementedError


class BehaviorManager:
    def __init__(self) -> None:
        self._behaviors: list[Behavior] = []
        self._started: list[Behavior] = []

    def add(self, behavior: Behavior) -> None:
        if any(existing.name == behavior.name for existing in self._behaviors):
            raise ValueError(f"A behavior named {behavior.name!r} is already registered.")
        self._behaviors.append(behavior)

    async def start_all(self) -> None:
        if self._started:
            raise RuntimeError("Behaviors are already running.")
        try:
            for behavior in self._behaviors:
                await behavior.start()
                self._started.append(behavior)
        except (Exception, asyncio.CancelledError):
            await self.stop_all()
            raise

    async def stop_all(self) -> None:
        failures: list[Exception] = []
        while self._started:
            behavior = self._started.pop()
            try:
                await behavior.stop()
            except Exception as error:
                failures.append(error)
        if failures:
            raise failures[0]
