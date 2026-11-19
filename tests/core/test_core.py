import asyncio

import pytest

from robot.core import (
    CORE_STARTED,
    CORE_STOPPED,
    STATE_CHANGED,
    BackgroundTasks,
    Behavior,
    Event,
    EventBus,
    InvalidStateTransition,
    RobotCore,
    RobotState,
    RobotStateMachine,
)


def test_event_bus_dispatches_sync_and_async_handlers_in_order():
    received = []
    bus = EventBus()
    bus.subscribe("wake_word.detected", lambda event: received.append(("sync", event.data["source"])))

    async def asynchronous_handler(event):
        received.append(("async", event.data["source"]))

    bus.subscribe("wake_word.detected", asynchronous_handler)
    asyncio.run(bus.publish(Event("wake_word.detected", {"source": "test"})))

    assert received == [("sync", "test"), ("async", "test")]


def test_state_machine_rejects_invalid_transition():
    machine = RobotStateMachine()

    with pytest.raises(InvalidStateTransition):
        machine.transition_to(RobotState.THINKING)

    transition = machine.transition_to(RobotState.LISTENING, reason="wake word")
    assert transition.current is RobotState.LISTENING
    assert machine.state is RobotState.LISTENING


class RecordingBehavior(Behavior):
    def __init__(self, name, calls, *, fail_to_start=False, fail_to_stop=False):
        self.name = name
        self.calls = calls
        self.fail_to_start = fail_to_start
        self.fail_to_stop = fail_to_stop

    async def start(self):
        self.calls.append(f"start:{self.name}")
        if self.fail_to_start:
            raise RuntimeError("unavailable")

    async def stop(self):
        self.calls.append(f"stop:{self.name}")
        if self.fail_to_stop:
            raise RuntimeError("shutdown failed")


def test_core_lifecycle_starts_in_order_and_stops_in_reverse_order():
    async def exercise():
        calls = []
        events = []
        core = RobotCore()
        core.add_behavior(RecordingBehavior("first", calls))
        core.add_behavior(RecordingBehavior("second", calls))
        core.events.subscribe(CORE_STARTED, lambda event: events.append(event.name))
        core.events.subscribe(CORE_STOPPED, lambda event: events.append(event.name))
        core.events.subscribe(STATE_CHANGED, lambda event: events.append(event.data["current"]))

        await core.start()
        await core.transition_to(RobotState.LISTENING, reason="wake word")
        await core.stop()
        return core, calls, events

    core, calls, events = asyncio.run(exercise())

    assert not core.is_running
    assert calls == ["start:first", "start:second", "stop:second", "stop:first"]
    assert events == [CORE_STARTED, "listening", CORE_STOPPED]


def test_failed_behavior_start_rolls_back_started_behaviors():
    async def exercise():
        calls = []
        core = RobotCore()
        core.add_behavior(RecordingBehavior("first", calls))
        core.add_behavior(RecordingBehavior("broken", calls, fail_to_start=True))
        with pytest.raises(RuntimeError, match="unavailable"):
            await core.start()
        return calls

    assert asyncio.run(exercise()) == ["start:first", "start:broken", "stop:first"]


def test_background_tasks_are_cancelled_at_shutdown():
    async def exercise():
        cancelled = asyncio.Event()
        tasks = BackgroundTasks()

        async def worker():
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise

        task = tasks.create(worker())
        await asyncio.sleep(0)
        await tasks.shutdown()
        return task.cancelled(), cancelled.is_set(), tasks.active_count

    assert asyncio.run(exercise()) == (True, True, 0)


def test_core_shutdown_marks_core_stopped_when_a_behavior_shutdown_fails():
    async def exercise():
        calls = []
        core = RobotCore()
        core.add_behavior(RecordingBehavior("first", calls))
        core.add_behavior(RecordingBehavior("broken", calls, fail_to_stop=True))
        await core.start()
        with pytest.raises(RuntimeError, match="shutdown failed"):
            await core.stop()
        return core, calls

    core, calls = asyncio.run(exercise())
    assert not core.is_running
    assert calls == ["start:first", "start:broken", "stop:broken", "stop:first"]


def test_cancelled_behavior_start_rolls_back_started_behaviors():
    async def exercise():
        calls = []
        entered = asyncio.Event()
        class WaitingBehavior(RecordingBehavior):
            async def start(self):
                entered.set()
                await asyncio.Event().wait()
        core = RobotCore()
        core.add_behavior(RecordingBehavior("first", calls))
        core.add_behavior(WaitingBehavior("waiting", calls))
        task = asyncio.create_task(core.start())
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert calls == ["start:first", "stop:first"]
        assert not core.is_running
    asyncio.run(exercise())
