"""Provider-neutral contracts for future multilingual interaction."""

from .language import normalize_language_tag
from .models import (CanonicalIntent, InteractionInput, InteractionOutcome, InteractionOutcomeStatus, InteractionSource, ResolvedIntent,
                     TranscriptResult, UnresolvedIntent)
from .factory import IntentResolverFactory, ResolverId
from .resolver import IntentResolver, IntentResolverConfigurationError, IntentResolverError
from .coordinator import (INTERACTION_INTENT_RESOLVED, INTERACTION_INTENT_UNRESOLVED,
                          INTERACTION_RESOLUTION_FAILED, INTERACTION_COMMAND_EXECUTED,
                          INTERACTION_COMMAND_DENIED, INTERACTION_COMMAND_UNSUPPORTED,
                          INTERACTION_COMMAND_FAILED, INTERACTION_COMMAND_REJECTED,
                          INTERACTION_OUTCOME_COMPLETED, INTERACTION_OUTCOME_UNAVAILABLE,
                          INTERACTION_OUTCOME_FAILED,
                          InteractionCoordinator)
from .router import (AllowListedCommandPolicy, CommandPolicy, CommandPolicyError,
                     CommandRoute, CommandRouteResult, CommandRouter, CommandRouterError,
                     CommandRouteStatus, CommandValidationError)
from .executor import (CommandApplicationService, CommandAuthorization, CommandExecutionError,
                       CommandExecutionResult, CommandExecutionStatus, CommandExecutor)

__all__ = (
    "CanonicalIntent", "InteractionInput", "InteractionOutcome", "InteractionOutcomeStatus", "InteractionSource", "IntentResolver",
    "IntentResolverConfigurationError", "IntentResolverError", "IntentResolverFactory",
    "InteractionCoordinator", "INTERACTION_INTENT_RESOLVED", "INTERACTION_INTENT_UNRESOLVED",
    "INTERACTION_RESOLUTION_FAILED",
    "INTERACTION_COMMAND_EXECUTED", "INTERACTION_COMMAND_DENIED", "INTERACTION_COMMAND_UNSUPPORTED",
    "INTERACTION_COMMAND_FAILED", "INTERACTION_COMMAND_REJECTED",
    "INTERACTION_OUTCOME_COMPLETED", "INTERACTION_OUTCOME_UNAVAILABLE", "INTERACTION_OUTCOME_FAILED",
    "AllowListedCommandPolicy", "CommandPolicy", "CommandPolicyError", "CommandRoute",
    "CommandRouteResult", "CommandRouteStatus", "CommandRouter", "CommandRouterError",
    "CommandValidationError",
    "CommandApplicationService", "CommandAuthorization", "CommandExecutionError",
    "CommandExecutionResult", "CommandExecutionStatus", "CommandExecutor",
    "ResolvedIntent", "ResolverId", "TranscriptResult",
    "UnresolvedIntent", "normalize_language_tag",
)
