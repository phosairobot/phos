"""Selection of configured interaction resolvers without fallback."""
from __future__ import annotations

from enum import Enum

from .deterministic import DeterministicIntentResolver
from .resolver import IntentResolverConfigurationError


class ResolverId(str, Enum):
    DETERMINISTIC = "deterministic"


class IntentResolverFactory:
    """Create the configured resolver; callers never depend on provider classes."""

    @staticmethod
    def create(config):
        if config.interaction_resolver == ResolverId.DETERMINISTIC.value:
            return DeterministicIntentResolver(
                default_language=config.interaction_default_language,
                supported_languages=config.interaction_supported_languages,
            )
        raise IntentResolverConfigurationError(
            f"Unknown or unavailable interaction resolver: {config.interaction_resolver}"
        )
