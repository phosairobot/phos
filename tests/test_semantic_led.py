import asyncio

import pytest

from robot.core.events import Event, EventBus
from robot.core.semantic_led import SemanticLedIntent, SemanticLedService
from robot.core.state import RobotState
from robot.core.runtime import STATE_CHANGED


def test_semantic_led_arbitrates_sources_expires_transients_and_deduplicates():
    now, sent = [0.0], []
    async def sender(command): sent.append(dict(command)); return True
    service = SemanticLedService(EventBus(), sender, lambda: RobotState.IDLE, clock=lambda: now[0])

    async def exercise():
        await service.set_intent("robot_state", "idle")
        await service.set_intent("environment", "ambient")
        assert service.status()["active_intent"] == "idle"
        await service.set_intent("user_signal", "warning", duration_seconds=2)
        assert service.status()["active_intent"] == "warning"
        await service.set_intent("robot_state", "listening")
        assert service.status()["active_intent"] == "warning"
        await service.clear_intent("user_signal")
        assert service.status()["active_intent"] == "listening"
        before = len(sent); await service.set_intent("robot_state", "listening")
        assert len(sent) == before
        await service.set_intent("user_signal", "success", duration_seconds=1)
        now[0] = 2; await service._reconcile()
        assert service.status()["active_intent"] == "listening"
        await service.set_intent("system", "error")
        now[0] = 99; await service._reconcile()
        assert service.status()["active_intent"] == "error"

    asyncio.run(exercise())
    assert all(command["animation"] in {"solid", "breathe", "pulse", "blink"} for command in sent)


def test_semantic_led_robot_and_environment_events_use_semantic_sources():
    events, sent = EventBus(), []
    async def sender(command): sent.append(command); return True
    service = SemanticLedService(events, sender, lambda: RobotState.IDLE)

    async def exercise():
        await service.start()
        await events.publish(Event(STATE_CHANGED, {"current": "thinking"}))
        assert service.status()["active_intent"] == "thinking"
        await events.publish(Event("environmental.state_changed", {"state": "warm"}))
        assert service.status()["active_intent"] == "thinking"
        await events.publish(Event(STATE_CHANGED, {"current": "idle"}))
        assert service.status()["active_intent"] == "idle"
        await service.clear_intent("robot_state")
        assert service.status()["active_intent"] == "ambient"
        await service.stop()

    asyncio.run(exercise())


def test_user_error_is_transient_and_never_clears_robot_state_error():
    now, sent = [0.0], []

    async def sender(command):
        sent.append(dict(command))
        return True

    service = SemanticLedService(EventBus(), sender, lambda: RobotState.IDLE, clock=lambda: now[0])

    async def exercise():
        await service.set_intent("robot_state", "idle")
        await service.signal_user("error")
        assert service.status()["active_intent"] == "error"
        assert service.status()["winning_source"] == "user_signal"
        now[0] = 5.0
        await service._reconcile()
        assert service.status()["active_intent"] == "idle"
        assert service.status()["winning_source"] == "robot_state"

        await service.set_intent("robot_state", "listening")
        await service.signal_user("error")
        assert service.status()["winning_source"] == "user_signal"
        now[0] = 10.0
        await service._reconcile()
        assert service.status()["active_intent"] == "listening"
        assert service.status()["winning_source"] == "robot_state"

        await service.set_intent("robot_state", "error")
        await service.signal_user("success")
        assert service.status()["active_intent"] == "error"
        now[0] = 13.0
        await service._reconcile()
        assert service.status()["active_intent"] == "error"
        assert service.status()["winning_source"] == "robot_state"

        await service.signal_user("error")
        now[0] = 18.0
        await service._reconcile()
        assert service.status()["active_intent"] == "error"
        assert service.status()["winning_source"] == "robot_state"

    asyncio.run(exercise())
    assert sent


@pytest.mark.parametrize(("signal", "duration"), (("success", 2.0), ("warning", 5.0), ("attention", 3.0)))
def test_existing_user_signal_durations_remain_transient(signal, duration):
    now = [0.0]

    async def sender(_command):
        return True

    service = SemanticLedService(EventBus(), sender, lambda: RobotState.IDLE, clock=lambda: now[0])

    async def exercise():
        await service.set_intent("robot_state", "idle")
        await service.signal_user(signal)
        assert service.status()["active_intent"] == signal
        assert service.status()["winning_source"] == "user_signal"
        now[0] = duration
        await service._reconcile()
        assert service.status()["active_intent"] == "idle"
        assert service.status()["winning_source"] == "robot_state"

    asyncio.run(exercise())


def test_semantic_led_reapplies_the_same_winner_after_controller_session_change():
    connected, sent = [False], []

    async def sender(command):
        if not connected[0]:
            return False
        sent.append(dict(command))
        return True

    service = SemanticLedService(EventBus(), sender, lambda: RobotState.IDLE)

    async def exercise():
        await service.set_intent("robot_state", "idle")
        assert sent == []

        connected[0] = True
        await service.reapply(reason="controller_capabilities")
        assert len(sent) == 1
        await service.set_intent("robot_state", "idle")
        assert len(sent) == 1  # Deduplication is retained within one session.

        await service.reapply(reason="controller_capabilities")
        assert len(sent) == 2  # The current winner is reapplied to a new session.

        await service.set_intent("robot_state", "listening")
        assert len(sent) == 3
        assert sent[-1]["animation"] == "breathe"

        await service.reapply(reason="controller_capabilities")
        assert len(sent) == 4
        assert sent[-1]["animation"] == "breathe"

    asyncio.run(exercise())
