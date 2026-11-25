# SPDX-License-Identifier: Apache-2.0
"""Semantic arbitration between environmental and temporary overlay intent."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import time

from robot.ui.state import AmbientOverlayState


@dataclass(frozen=True)
class OverlayOverride:
    temperature: str | None = None
    air_quality: str | None = None
    expires_at: float | None = None
    expires_at_iso: str | None = None


class OverlayArbiter:
    """Resolve independent overlay channels without changing environmental state."""

    def __init__(self, *, clock=time.monotonic):
        self._clock = clock
        self._environmental = AmbientOverlayState()
        self._override: OverlayOverride | None = None

    def set_environmental(self, intent: AmbientOverlayState) -> None:
        self._environmental = intent

    def set_override(self, *, temperature=None, air_quality=None, duration_ms=None) -> None:
        expires_at = None if duration_ms is None else self._clock() + duration_ms / 1000
        expires_at_iso = None if duration_ms is None else (datetime.now(timezone.utc) + timedelta(milliseconds=duration_ms)).isoformat(timespec="milliseconds")
        self._override = OverlayOverride(temperature, air_quality, expires_at, expires_at_iso)

    def clear_override(self) -> None:
        self._override = None

    def _active_override(self) -> OverlayOverride | None:
        if self._override is not None and self._override.expires_at is not None and self._clock() >= self._override.expires_at:
            self._override = None
        return self._override

    def resolved(self) -> AmbientOverlayState:
        override = self._active_override()
        if override is None:
            return self._environmental
        return AmbientOverlayState(
            self._environmental.temperature if override.temperature is None else override.temperature,
            self._environmental.air_quality if override.air_quality is None else override.air_quality,
        )

    def snapshot(self) -> tuple[AmbientOverlayState, OverlayOverride | None, AmbientOverlayState]:
        override = self._active_override()
        return self._environmental, override, self.resolved()
