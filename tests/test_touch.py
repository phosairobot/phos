import asyncio

from robot.core import Event
from robot.core.runtime import RobotCore
from robot.core.behavior_engine import BehaviorEngine
from robot.core.touch import TOUCH_EVENT_NAMES, TouchStatus
from robot.services.application import PhosApplicationService
from robot.ui.touch import TouchInputAdapter, TouchKind


def _adapter(*, clock, emitted):
    return TouchInputAdapter(emitted.append, clock=lambda: clock[0])


def test_completed_gestures_update_the_canonical_touch_status_only():
    clock, emitted = [0.0], []
    adapter = _adapter(clock=clock, emitted=emitted)
    adapter.down(400, 300); clock[0] = .12
    tap = adapter.release(410, 290)
    status = TouchStatus(enabled=True).record(tap).document()
    assert status["last_event"] == "tap"
    assert status["x"] == 410 and status["y"] == 290
    assert status["normalized_x"] == .5125 and status["duration_ms"] == 120

    adapter.down(100, 200); clock[0] += .9
    assert adapter.release(100, 200).kind is TouchKind.LONG_PRESS
    adapter.down(300, 200); clock[0] += .1
    assert adapter.release(150, 200).kind is TouchKind.SWIPE_LEFT
    adapter.down(150, 200); clock[0] += .1
    assert adapter.release(300, 200).kind is TouchKind.SWIPE_RIGHT


def test_raw_pointer_motion_does_not_produce_a_touch_event():
    clock, emitted = [0.0], []
    adapter = _adapter(clock=clock, emitted=emitted)
    adapter.down(10, 10); clock[0] = .5
    assert adapter.release(50, 100) is None
    assert emitted == []


class _Runtime:
    def __init__(self):
        self.core = RobotCore()
        self._behavior_engine = BehaviorEngine(self.core.events)
        self._touch = TouchStatus(enabled=True)

    def sensor_status(self): return {}
    def touch_status(self): return self._touch.document()


def test_touch_status_and_each_semantic_event_cross_the_application_boundary_once():
    runtime = _Runtime()
    service = PhosApplicationService(runtime)
    received = []
    service.subscribe(received.append)
    for kind in TOUCH_EVENT_NAMES:
        payload = {"kind": kind, "x": 410, "y": 290, "normalized_x": .5125,
                   "normalized_y": .4833, "duration_ms": 120}
        asyncio.run(runtime.core.events.publish(Event(TOUCH_EVENT_NAMES[kind], payload)))
    # Edge events intentionally bypass snapshot de-duplication: identical
    # completed gestures remain separate physical interactions.
    payload = {"kind": "tap", "x": 410, "y": 290, "normalized_x": .5125,
               "normalized_y": .4833, "duration_ms": 120}
    asyncio.run(runtime.core.events.publish(Event(TOUCH_EVENT_NAMES["tap"], payload)))
    assert [event["type"] for event in received] == [*TOUCH_EVENT_NAMES.values(), "touch_tap"]
    assert received[0]["payload"] == payload
    assert service.status()["touch"]["enabled"] is True
    service.close()


def test_web_admin_frontend_lists_all_touch_semantic_events_and_diagnostics():
    source = open("src/robot/web/static/admin.js", encoding="utf-8").read()
    template = open("src/robot/web/templates/configuration.html", encoding="utf-8").read()
    for event_name in TOUCH_EVENT_NAMES.values():
        assert event_name in source
    for label in ("Touch", "Last Gesture", "Position", "Normalized Position", "Duration", "Last Touch"):
        assert label in template
