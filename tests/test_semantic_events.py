import pytest

from robot.services.semantic_events import SemanticEventPublisher


def test_subscriber_event_type_allow_list_receives_only_requested_semantic_events():
    publisher = SemanticEventPublisher()
    received = []
    publisher.subscribe(received.append, event_types={"presence_changed", "motion_state_changed"})

    publisher.publish("robot_state_changed", "robot", {"previous": "idle", "current": "thinking"})
    publisher.publish("presence_changed", "presence", {"previous": "no_one", "current": "person_present"})
    publisher.publish("motion_state_changed", "motion", {"previous": "stationary", "current": "moving"})

    assert [event.type for event in received] == ["presence_changed", "motion_state_changed"]


def test_event_type_filter_rejects_unknown_stable_event_type():
    publisher = SemanticEventPublisher()

    with pytest.raises(ValueError, match="presnce_changed"):
        publisher.subscribe(lambda event: None, event_types={"presnce_changed"})


def test_deduplication_suppresses_only_adjacent_identical_semantic_events():
    clock = [10.0]
    publisher = SemanticEventPublisher(deduplication_window_seconds=1.0, clock=lambda: clock[0])
    received = []
    publisher.subscribe(received.append)

    entered = {"previous": "no_one", "current": "person_present"}
    left = {"previous": "person_present", "current": "no_one"}
    publisher.publish("presence_changed", "presence", entered)
    clock[0] += .1
    publisher.publish("presence_changed", "presence", dict(reversed(tuple(entered.items()))))
    clock[0] += .1
    publisher.publish("presence_changed", "presence", left)
    clock[0] += .1
    publisher.publish("presence_changed", "presence", entered)

    assert [event.payload for event in received] == [entered, left, entered]


def test_identical_event_after_the_deduplication_window_is_delivered():
    clock = [0.0]
    publisher = SemanticEventPublisher(deduplication_window_seconds=.5, clock=lambda: clock[0])
    received = []
    publisher.subscribe(received.append)
    payload = {"previous": "idle", "current": "thinking"}

    publisher.publish("robot_state_changed", "robot", payload)
    clock[0] += .6
    publisher.publish("robot_state_changed", "robot", payload)

    assert len(received) == 2
