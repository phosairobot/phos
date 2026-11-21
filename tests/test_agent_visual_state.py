import pytest

from robot.core.agent_visual_state import AgentVisualStateLeaseManager
from robot.core.runtime import RobotCore
from robot.core.state import RobotState


class FakeClock:
    def __init__(self): self.now = 0.0
    def __call__(self): return self.now
    def advance(self, seconds): self.now += seconds


class FakeCall:
    def __init__(self, due, callback): self.due, self.callback, self.cancelled = due, callback, False
    def cancel(self): self.cancelled = True


class FakeScheduler:
    def __init__(self, clock): self.clock, self.calls = clock, []
    def __call__(self, delay, callback):
        call = FakeCall(self.clock() + delay, callback)
        self.calls.append(call)
        return call
    def run_due(self):
        for call in tuple(self.calls):
            if not call.cancelled and call.due <= self.clock():
                call.cancelled = True
                call.callback()


def manager():
    clock = FakeClock()
    scheduler = FakeScheduler(clock)
    return AgentVisualStateLeaseManager(clock=clock, scheduler=scheduler), clock, scheduler


def test_thinking_activation_and_result_are_visual_only():
    leases, _clock, _scheduler = manager()
    result = leases.request("agent-a", "thinking", duration_ms=1000)
    assert result.effective_state == "thinking"
    assert result.lease_active and result.remaining_expiry_ms == 1000
    assert result.suppressed_by is None
    assert leases.resolve(RobotState.IDLE) is RobotState.THINKING


def test_expiration_removes_lease_without_polling():
    leases, clock, scheduler = manager()
    leases.request("agent-a", "thinking", duration_ms=250)
    assert len(scheduler.calls) == 1
    clock.advance(.25); scheduler.run_due()
    assert leases.resolve(RobotState.IDLE) is RobotState.IDLE


def test_repeated_resolution_does_not_create_deadline_work():
    leases, _clock, scheduler = manager()
    leases.request("agent-a", "thinking", duration_ms=1000)
    for _ in range(20):
        assert leases.resolve(RobotState.IDLE) is RobotState.THINKING
    assert len(scheduler.calls) == 1


def test_explicit_and_idempotent_release():
    leases, _clock, _scheduler = manager()
    leases.request("agent-a", "thinking", duration_ms=1000)
    released = leases.request("agent-a", "idle")
    repeated = leases.request("agent-a", "idle")
    assert not released.lease_active and released.effective_state == "idle"
    assert not repeated.lease_active and repeated.accepted


@pytest.mark.parametrize("duration", [None, 0, 250, 60_001])
def test_idle_release_ignores_duration_without_creating_a_timer(duration):
    leases, _clock, scheduler = manager()
    result = leases.request("agent-a", "idle", duration_ms=duration)
    assert result.accepted and not result.lease_active and result.effective_state == "idle"
    assert scheduler.calls == []


def test_same_agent_replacement_cannot_be_expired_by_old_callback():
    leases, clock, scheduler = manager()
    leases.request("agent-a", "thinking", duration_ms=250)
    obsolete = scheduler.calls[-1]
    clock.advance(.1)
    leases.request("agent-a", "thinking", duration_ms=1000)
    obsolete.callback()
    assert leases.resolve(RobotState.IDLE) is RobotState.THINKING
    clock.advance(1); scheduler.run_due()
    assert leases.resolve(RobotState.IDLE) is RobotState.IDLE


def test_multiple_agents_are_isolated_and_latest_lease_wins():
    leases, _clock, _scheduler = manager()
    leases.request("agent-a", "thinking", duration_ms=1000)
    leases.request("agent-b", "thinking", duration_ms=1000)
    assert leases.request("agent-a", "idle").effective_state == "thinking"
    assert leases.resolve(RobotState.IDLE) is RobotState.THINKING


def test_winner_expiry_reveals_valid_older_lease():
    leases, clock, scheduler = manager()
    leases.request("agent-a", "thinking", duration_ms=1000)
    clock.advance(.1)
    leases.request("agent-b", "thinking", duration_ms=250)
    clock.advance(.25); scheduler.run_due()
    assert leases.resolve(RobotState.IDLE) is RobotState.THINKING
    clock.advance(.65); scheduler.run_due()
    assert leases.resolve(RobotState.IDLE) is RobotState.IDLE


@pytest.mark.parametrize("actual", [RobotState.SPEAKING, RobotState.LISTENING, RobotState.SLEEPING, RobotState.ERROR])
def test_higher_priority_core_states_suppress_thinking(actual):
    leases, _clock, _scheduler = manager()
    result = leases.request("agent-a", "thinking", duration_ms=1000, actual_state=actual)
    assert result.lease_active and result.effective_state == actual.value
    assert result.suppressed_by == actual.value
    assert leases.resolve(actual) is actual


def test_expiry_during_speaking_does_not_change_actual_state():
    leases, clock, scheduler = manager()
    leases.request("agent-a", "thinking", duration_ms=250)
    clock.advance(.25); scheduler.run_due()
    assert leases.resolve(RobotState.SPEAKING) is RobotState.SPEAKING
    assert leases.resolve(RobotState.IDLE) is RobotState.IDLE


def test_shutdown_is_repeatedly_safe_and_clears_all_leases():
    leases, _clock, scheduler = manager()
    leases.request("agent-a", "thinking", duration_ms=1000)
    scheduled = scheduler.calls[-1]
    leases.shutdown(); leases.shutdown()
    assert scheduled.cancelled
    with pytest.raises(RuntimeError, match="closed"):
        leases.resolve(RobotState.IDLE)


@pytest.mark.parametrize(("state", "duration"), [
    ("thinking", None), ("thinking", 249), ("thinking", 60_001), ("thinking", True),
    ("speaking", 250),
])
def test_invalid_states_and_durations_are_rejected(state, duration):
    leases, _clock, _scheduler = manager()
    with pytest.raises(ValueError):
        leases.request("agent-a", state, duration_ms=duration)


def test_manager_never_mutates_robot_core_state():
    core = RobotCore()
    leases, _clock, _scheduler = manager()
    leases.request("agent-a", "thinking", duration_ms=1000)
    assert core.state is RobotState.IDLE
    assert leases.resolve(core.state) is RobotState.THINKING
    assert core.state is RobotState.IDLE
