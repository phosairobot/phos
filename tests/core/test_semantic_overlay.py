import asyncio

import pytest

from robot.core.events import Event, EventBus
from robot.core.behavior_engine import ENVIRONMENTAL_STATE_CHANGED
from robot.core.semantic_overlay import SemanticOverlayIntent, SemanticOverlayService


def test_semantic_overlay_validates_bounded_plain_text_and_intents():
    service = SemanticOverlayService()
    assert asyncio.run(service.request("application", "info", message="Ready")).message == "Ready"
    with pytest.raises(ValueError):
        asyncio.run(service.request("application", "unknown"))
    with pytest.raises(ValueError):
        asyncio.run(service.request("application", "info", message="x\n"))
    with pytest.raises(ValueError):
        asyncio.run(service.request("application", "info", message="x" * 121))


def test_source_ownership_priority_expiry_and_deterministic_tie_breaking():
    now = [0.0]
    service = SemanticOverlayService(clock=lambda: now[0])
    asyncio.run(service.request("sensor", "sensor_alert", message="Air", duration_seconds=3))
    asyncio.run(service.request("application", "info", message="Ready", duration_seconds=3))
    assert service.view_state().intent is SemanticOverlayIntent.SENSOR_ALERT
    asyncio.run(service.request("behavior", "warning", message="Care", duration_seconds=1))
    assert service.view_state().message == "Care"
    now[0] = 1.0
    assert service.view_state().intent is SemanticOverlayIntent.SENSOR_ALERT
    asyncio.run(service.request("sensor", "attention", message="Look", duration_seconds=3))
    assert service.view_state().intent is SemanticOverlayIntent.ATTENTION
    asyncio.run(service.clear("sensor"))
    assert service.view_state().message == "Ready"
    asyncio.run(service.request("one", "info", message="Old", duration_seconds=3))
    asyncio.run(service.request("two", "info", message="New", duration_seconds=3))
    assert service.view_state().message == "New"


def test_service_lifecycle_owns_one_cancellable_expiry_task():
    async def exercise():
        service = SemanticOverlayService()
        await service.start()
        task = service._task
        assert task is not None
        await service.request("application", "success")
        await service.stop()
        assert service._task is None and service.view_state().visible is False
    asyncio.run(exercise())


def test_environmental_alert_uses_existing_interpreted_event_not_raw_sensor_data():
    async def exercise():
        events = EventBus()
        service = SemanticOverlayService(events)
        await service.start()
        await events.publish(Event(ENVIRONMENTAL_STATE_CHANGED, {"state": "air_quality_bad"}))
        assert service.view_state().intent is SemanticOverlayIntent.SENSOR_ALERT
        assert service.view_state().message == "Air quality alert"
        await events.publish(Event(ENVIRONMENTAL_STATE_CHANGED, {"state": "normal"}))
        assert service.view_state().visible is False
        await service.stop()
    asyncio.run(exercise())
