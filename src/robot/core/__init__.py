"""Robot lifecycle, state, events and orchestration."""

from .behaviors import Behavior, BehaviorManager
from .behavior_engine import BehaviorEngine
from .environmental import EnvironmentalInterpreter, EnvironmentalSettings, EnvironmentalState
from .overlay import OverlayArbiter, OverlayOverride
from .events import Event, EventBus
from .presence import PresenceInterpreter, PresenceKind, PresenceState
from .attention import AttentionManager, AttentionKind, AttentionState
from .touch import TouchStatus
from .runtime import CORE_STARTED, CORE_STOPPED, STATE_CHANGED, RobotCore
from .state import InvalidStateTransition, RobotState, RobotStateMachine, StateTransition
from .tasks import BackgroundTasks

__all__ = [
    "BackgroundTasks",
    "Behavior",
    "BehaviorEngine",
    "BehaviorManager",
    "EnvironmentalInterpreter",
    "EnvironmentalSettings",
    "EnvironmentalState",
    "OverlayArbiter",
    "OverlayOverride",
    "CORE_STARTED",
    "CORE_STOPPED",
    "Event",
    "EventBus",
    "PresenceInterpreter",
    "PresenceKind",
    "PresenceState",
    "AttentionManager",
    "AttentionKind",
    "AttentionState",
    "TouchStatus",
    "InvalidStateTransition",
    "RobotCore",
    "RobotState",
    "RobotStateMachine",
    "STATE_CHANGED",
    "StateTransition",
]
