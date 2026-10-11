"""Provider-neutral boundary between interaction input and canonical intent."""
from __future__ import annotations

from typing import Protocol, TypeAlias

from .models import InteractionInput, ResolvedIntent, UnresolvedIntent


class IntentResolverError(RuntimeError):
    """A resolver/provider failure, distinct from an ordinary no-match."""


class IntentResolverConfigurationError(IntentResolverError):
    """The configured resolver cannot be selected."""


IntentResolution: TypeAlias = ResolvedIntent | UnresolvedIntent


class IntentResolver(Protocol):
    """Interpret language asynchronously; implementations must not execute commands."""

    async def resolve(self, interaction: InteractionInput) -> IntentResolution:
        """Return a canonical intent or an ordinary no-match result."""
