"""Provider-neutral contracts for future multilingual interaction."""

from .language import normalize_language_tag
from .models import (CanonicalIntent, InteractionInput, InteractionSource, ResolvedIntent,
                     TranscriptResult, UnresolvedIntent)
from .factory import IntentResolverFactory, ResolverId
from .resolver import IntentResolver, IntentResolverConfigurationError, IntentResolverError
from .coordinator import (INTERACTION_INTENT_RESOLVED, INTERACTION_INTENT_UNRESOLVED,
                          INTERACTION_RESOLUTION_FAILED, InteractionCoordinator)

__all__ = (
    "CanonicalIntent", "InteractionInput", "InteractionSource", "IntentResolver",
    "IntentResolverConfigurationError", "IntentResolverError", "IntentResolverFactory",
    "InteractionCoordinator", "INTERACTION_INTENT_RESOLVED", "INTERACTION_INTENT_UNRESOLVED",
    "INTERACTION_RESOLUTION_FAILED",
    "ResolvedIntent", "ResolverId", "TranscriptResult",
    "UnresolvedIntent", "normalize_language_tag",
)
