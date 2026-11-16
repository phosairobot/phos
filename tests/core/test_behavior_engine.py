import asyncio
import random

import pytest

from robot.core import BehaviorEngine, Event, EventBus, RobotState, STATE_CHANGED
from robot.core.behavior_engine import IMU_MOTION_STATE
from robot.core.behavior_engine import ENVIRONMENTAL_STATE_CHANGED
from robot.ui import BlinkPhase, EnvironmentalLEDIntent, FaceExpression, VisualAccent


def test_behavior_engine_maps_core_state_to_face_state():
    async def exercise():
        events = EventBus()
        engine = BehaviorEngine(events, blink_interval=(60, 60), gaze_interval=(60, 60))
        await engine.start()
        await events.publish(Event(STATE_CHANGED, {"current": RobotState.SLEEPING.value}))
        state = engine.face_state
        await engine.stop()
        return state

    state = asyncio.run(exercise())
    assert state.expression is FaceExpression.SLEEPY
    assert state.reaction_strength == 1.0


@pytest.mark.parametrize(("preset", "blink", "gaze", "offsets", "double", "hold"), [
    ("analytical", (4.8, 7.2), (4.0, 7.0), {(-.25, 0.0), (.25, 0.0)}, .015, (1.4, 2.3)),
    ("playful", (2.4, 4.2), (1.5, 3.2), {(-.35, 0.0), (.35, 0.0), (0.0, -.20), (0.0, .20), (-.25, -.15), (.25, -.15)}, .18, (.65, 1.25)),
    ("calm", (5.5, 8.0), (4.8, 8.0), {(-.20, 0.0), (.20, 0.0), (0.0, -.12)}, .04, (1.8, 3.0)),
])
def test_physical_personality_changes_only_bounded_idle_policy(preset, blink, gaze, offsets, double, hold):
    engine = BehaviorEngine(EventBus(), clock=lambda: 10.0)
    engine.configure_physical_personality(preset)
    assert engine._blink_interval == blink and engine._gaze_interval == gaze
    assert set(engine._idle_gaze_offsets) == offsets
    assert engine._double_blink_probability == double and engine._gaze_hold_duration == hold
    assert engine.face_state.expression is FaceExpression.NEUTRAL


def test_physical_personality_idle_timers_reset_without_overriding_sleep_or_error():
    async def exercise():
        now = [0.0]
        events = EventBus()
        engine = BehaviorEngine(events, clock=lambda: now[0])
        await engine.start()
        engine.configure_physical_personality("playful")
        await events.publish(Event(STATE_CHANGED, {"current": RobotState.SLEEPING.value}))
        sleeping = (engine.face_state, engine._next_gaze_at)
        await events.publish(Event(STATE_CHANGED, {"current": RobotState.ERROR.value}))
        error = engine.face_state
        await events.publish(Event(STATE_CHANGED, {"current": RobotState.IDLE.value}))
        idle = engine._next_gaze_at
        await engine.stop()
        return sleeping, error, idle
    sleeping, error, idle = asyncio.run(exercise())
    assert sleeping[0].expression is FaceExpression.SLEEPY and sleeping[1] == float("inf")
    assert error.expression is FaceExpression.WORRIED
    assert idle != float("inf")


def test_double_blink_has_nonblocking_ordered_phases_and_only_one_follow_up():
    engine = BehaviorEngine(EventBus(), clock=lambda: 0.0, rng=random.Random(3))
    engine.configure_physical_personality("playful")
    engine._double_blink_probability = 1.0
    engine._next_blink_at = 0.0
    phases = []
    for now in (0.0, .08, .13, .25, .40, .48, .53, .65):
        engine._advance_blink(now)
        phases.append(engine._blink_phase)
    assert phases == [BlinkPhase.CLOSING, BlinkPhase.CLOSED, BlinkPhase.OPENING,
                      BlinkPhase.DOUBLE_PAUSE, BlinkPhase.CLOSING, BlinkPhase.CLOSED,
                      BlinkPhase.OPENING, BlinkPhase.OPEN]
    assert not engine._double_blink_pending


def test_personality_sequences_cancel_on_non_idle_and_personality_switch():
    async def exercise():
        now = [0.0]
        engine = BehaviorEngine(EventBus(), clock=lambda: now[0], rng=random.Random(4))
        engine.configure_physical_personality("playful")
        engine._blink_phase = BlinkPhase.DOUBLE_PAUSE
        engine._double_blink_pending = True
        engine._gaze_phase = "hold"
        await engine._on_robot_state(Event(STATE_CHANGED, {"current": RobotState.SLEEPING.value}))
        cancelled = (engine._blink_phase, engine._double_blink_pending, engine._gaze_phase, engine._next_gaze_at)
        await engine._on_robot_state(Event(STATE_CHANGED, {"current": RobotState.IDLE.value}))
        engine._blink_phase = BlinkPhase.DOUBLE_PAUSE
        engine._double_blink_pending = True
        engine.configure_physical_personality("calm")
        return cancelled, (engine._blink_phase, engine._double_blink_pending, engine._gaze_phase)
    cancelled, switched = asyncio.run(exercise())
    assert cancelled == (BlinkPhase.OPEN, False, "rest", float("inf"))
    assert switched == (BlinkPhase.OPEN, False, "rest")


def test_idle_gaze_holds_then_returns_to_neutral_and_is_seeded():
    now = [0.0]
    engine = BehaviorEngine(EventBus(), clock=lambda: now[0], rng=random.Random(12))
    engine.configure_physical_personality("playful")
    engine._next_gaze_at = 0.0
    engine._advance_idle_gaze(now[0])
    target = (engine.face_state.pupil_x, engine.face_state.pupil_y)
    hold_until = engine._gaze_hold_until
    assert target in engine._idle_gaze_offsets
    assert engine._gaze_phase == "hold"
    assert .65 <= hold_until <= 1.25
    now[0] = hold_until
    engine._advance_idle_gaze(now[0])
    assert (engine.face_state.pupil_x, engine.face_state.pupil_y) == (0.0, 0.0)
    assert engine._gaze_phase == "rest"
    assert 1.5 <= engine._next_gaze_at - now[0] <= 3.2


def test_attention_cancels_idle_gaze_and_prevents_stale_return():
    async def exercise():
        now = [0.0]
        engine = BehaviorEngine(EventBus(), clock=lambda: now[0], rng=random.Random(1))
        engine.configure_physical_personality("playful")
        engine._next_gaze_at = 0.0
        engine._advance_idle_gaze(now[0])
        await engine._on_attention(Event("attention.changed", {"state": "tracking", "target": {"x": 1, "y": 0}}))
        now[0] = 10
        engine._advance_idle_gaze(now[0])
        return engine.face_state, engine._gaze_phase
    state, phase = asyncio.run(exercise())
    assert state.pupil_x > 0 and phase == "rest"


def test_behavior_engine_gives_stable_happy_and_surprised_observations_subtle_reactions():
    async def exercise():
        events = EventBus()
        engine = BehaviorEngine(events, blink_interval=(60, 60), gaze_interval=(60, 60))
        await engine.start()
        await events.publish(
            Event("vision.visual_expression_stable", {"visual_expression": {"label": "surprised", "confidence": 0.72}})
        )
        state = engine.face_state
        await engine.stop()
        return state

    state = asyncio.run(exercise())
    assert state.expression is FaceExpression.SURPRISED
    assert state.accent is VisualAccent.ALERT
    assert state.reaction_strength == pytest.approx(0.72)


def test_negative_visual_observations_do_not_select_an_expression():
    async def exercise():
        events = EventBus()
        engine = BehaviorEngine(events, blink_interval=(60, 60), gaze_interval=(60, 60))
        await engine.start()
        await events.publish(
            Event("vision.visual_expression_stable", {"visual_expression": {"label": "sad", "confidence": 0.95}})
        )
        state = engine.face_state
        await engine.stop()
        return state

    state = asyncio.run(exercise())
    assert state.expression is FaceExpression.NEUTRAL
    assert state.accent is VisualAccent.NEUTRAL
    assert state.reaction_strength == 0.0


def test_ferplus_model_labels_map_to_phos_behavior_semantics():
    async def exercise():
        events = EventBus()
        engine = BehaviorEngine(events, blink_interval=(60, 60), gaze_interval=(60, 60))
        await engine.start()
        await events.publish(
            Event("vision.visual_expression_stable", {"visual_expression": {"label": "happiness", "confidence": 0.9}})
        )
        happy = engine.face_state
        await events.publish(
            Event("vision.visual_expression_stable", {"visual_expression": {"label": "surprise", "confidence": 0.9}})
        )
        alert = engine.face_state
        await events.publish(
            Event("vision.visual_expression_stable", {"visual_expression": {"label": "contempt", "confidence": 0.9}})
        )
        attentive = engine.face_state
        await events.publish(
            Event("vision.visual_expression_stable", {"visual_expression": {"label": "neutral", "confidence": 0.9}})
        )
        neutral = engine.face_state
        await engine.stop()
        return happy, alert, attentive, neutral

    happy, alert, attentive, neutral = asyncio.run(exercise())
    assert happy.expression is FaceExpression.HAPPY
    assert happy.accent is VisualAccent.WARM
    assert alert.expression is FaceExpression.SURPRISED
    assert alert.accent is VisualAccent.ALERT
    assert attentive.expression is FaceExpression.SURPRISED
    assert attentive.accent is VisualAccent.ALERT
    assert neutral.expression is FaceExpression.NEUTRAL
    assert neutral.accent is VisualAccent.NEUTRAL


def test_visual_reactions_decay_smoothly_back_to_neutral():
    async def exercise():
        now = [0.0]
        events = EventBus()
        engine = BehaviorEngine(
            events,
            blink_interval=(60, 60),
            gaze_interval=(60, 60),
            reaction_decay_per_second=0.25,
            clock=lambda: now[0],
        )
        await engine.start()
        await events.publish(
            Event("vision.visual_expression_stable", {"visual_expression": {"label": "happy", "confidence": 1.0}})
        )
        now[0] = 1.0
        engine._decay_visual_reaction(now[0])
        fading = engine.face_state
        now[0] = 5.0
        engine._decay_visual_reaction(now[0])
        settled = engine.face_state
        await engine.stop()
        return fading, settled

    fading, settled = asyncio.run(exercise())
    assert fading.expression is FaceExpression.HAPPY
    assert fading.reaction_strength == pytest.approx(0.65)
    assert settled.expression is FaceExpression.NEUTRAL
    assert settled.accent is VisualAccent.NEUTRAL
    assert settled.reaction_strength == 0.0


def test_non_idle_robot_state_overrides_visual_expression_reaction():
    async def exercise():
        events = EventBus()
        engine = BehaviorEngine(events, blink_interval=(60, 60), gaze_interval=(60, 60))
        await engine.start()
        await events.publish(Event(STATE_CHANGED, {"current": RobotState.SPEAKING.value}))
        await events.publish(
            Event("vision.visual_expression_stable", {"visual_expression": {"label": "surprised", "confidence": 0.9}})
        )
        state = engine.face_state
        await engine.stop()
        return state

    state = asyncio.run(exercise())
    assert state.expression is FaceExpression.HAPPY


def test_behavior_engine_smooths_face_position_and_resets_gaze_on_face_loss():
    async def exercise():
        events = EventBus()
        engine = BehaviorEngine(events, blink_interval=(60, 60), gaze_interval=(60, 60))
        await engine.start()
        await events.publish(Event("vision.face_position", {"face_position": {"x": 1.0, "y": -1.0}}))
        first = engine.face_state
        await events.publish(Event("vision.face_position", {"face_position": {"x": 1.0, "y": -1.0}}))
        second = engine.face_state
        await events.publish(Event("vision.face_lost"))
        lost = engine.face_state
        await engine.stop()
        return first, second, lost

    first, second, lost = asyncio.run(exercise())
    assert (first.pupil_x, first.pupil_y) == pytest.approx((0.2275, -0.1575))
    assert first.pupil_x < second.pupil_x < 0.65
    assert first.pupil_y > second.pupil_y > -0.45
    assert (lost.pupil_x, lost.pupil_y) == (0.0, 0.0)


def test_non_idle_robot_state_overrides_face_position_tracking():
    async def exercise():
        events = EventBus()
        engine = BehaviorEngine(events, blink_interval=(60, 60), gaze_interval=(60, 60))
        await engine.start()
        await events.publish(Event(STATE_CHANGED, {"current": RobotState.SLEEPING.value}))
        await events.publish(Event("vision.face_position", {"face_position": {"x": 1.0, "y": 1.0}}))
        state = engine.face_state
        await engine.stop()
        return state

    state = asyncio.run(exercise())
    assert state.expression is FaceExpression.SLEEPY
    assert (state.pupil_x, state.pupil_y) == (0.0, 0.0)


def test_unknown_preserves_reaction_then_decays_without_changing_tracking():
    async def exercise():
        now = [0.0]
        engine = BehaviorEngine(EventBus(), clock=lambda: now[0])
        await engine._on_face_position(Event("", {"face_position": {"x": 0.5, "y": 0.5}}))
        await engine._on_visual_expression(Event("", {"visual_expression": {"label": "happy", "confidence": 0.9}}))
        before = engine.face_state
        await engine._on_visual_expression(Event("", {"visual_expression": {"label": "unknown", "confidence": 0.0}}))
        assert engine.face_state == before
        engine._decay_visual_reaction(4.0)
        assert engine.face_state.expression is FaceExpression.NEUTRAL
        assert engine.face_state.pupil_x == before.pupil_x
    asyncio.run(exercise())


def test_surprise_is_temporary_and_requires_confirmed_alternative_to_rearm():
    async def exercise():
        now = [0.0]
        engine = BehaviorEngine(EventBus(), clock=lambda: now[0])
        async def send(label):
            await engine._on_visual_expression(Event("", {"visual_expression": {"label": label, "confidence": 0.9}}))
        await send("surprised")
        for tick in range(1, 7):
            now[0] = float(tick)
            engine._decay_visual_reaction(now[0])
            await send("surprised")
        assert engine.face_state.expression is FaceExpression.NEUTRAL
        await send("unknown")
        await send("surprised")
        assert engine.face_state.expression is FaceExpression.NEUTRAL
        await send("happy")
        await send("surprised")
        assert engine.face_state.expression is FaceExpression.SURPRISED
        now[0] = 7.0
        await send("neutral")
        await send("surprised")
        assert engine.face_state.expression is FaceExpression.NEUTRAL  # cooldown
    asyncio.run(exercise())


@pytest.mark.parametrize("label,confidence", [([], 0.9), ("neutral", float("nan")), ("happy", 0)])
def test_invalid_events_do_not_rearm_surprise(label, confidence):
    async def exercise():
        engine = BehaviorEngine(EventBus())
        engine._surprise_armed = False
        await engine._on_visual_expression(Event("", {"visual_expression": {"label": label, "confidence": confidence}}))
        assert not engine._surprise_armed
    asyncio.run(exercise())


@pytest.mark.parametrize("motion,x,y,eye_open,asymmetry", [
    ("tilt_left", -.86, 0, 1.12, .18), ("tilt_right", .86, 0, 1.12, -.18),
    ("tilt_forward", 0, -.86, 1.23, 0), ("tilt_back", 0, .86, .82, 0),
])
def test_imu_tilts_have_large_safe_mirrored_gaze_offsets(motion, x, y, eye_open, asymmetry):
    async def exercise():
        events = EventBus(); engine = BehaviorEngine(events, blink_interval=(60, 60), gaze_interval=(60, 60))
        await engine.start(); await events.publish(Event(IMU_MOTION_STATE, {"state": motion}))
        state = engine.face_state; await engine.stop(); return state
    state = asyncio.run(exercise())
    assert (state.pupil_x, state.pupil_y) == (x, y)
    assert state.eye_open == eye_open
    assert state.eye_asymmetry == asymmetry
    assert state.expression is FaceExpression.CURIOUS
    assert state.reaction_strength >= .55
    assert -1 <= state.pupil_x <= 1 and -1 <= state.pupil_y <= 1


def test_imu_moving_is_attentive_and_robot_state_has_priority():
    async def exercise():
        events = EventBus(); engine = BehaviorEngine(events, blink_interval=(60, 60), gaze_interval=(60, 60))
        await engine.start(); await events.publish(Event(IMU_MOTION_STATE, {"state": "moving"}))
        moving = engine.face_state
        await events.publish(Event(STATE_CHANGED, {"current": RobotState.SLEEPING.value}))
        sleeping = engine.face_state; await engine.stop(); return moving, sleeping
    moving, sleeping = asyncio.run(exercise())
    assert moving.eye_open >= 1.16 and moving.expression is FaceExpression.CURIOUS
    assert moving.reaction_strength >= .63
    assert moving.eye_asymmetry == 0
    assert sleeping.expression is FaceExpression.SLEEPY and sleeping.pupil_x == 0


def test_imu_tilt_asymmetry_clears_for_still_and_higher_priority_state():
    async def exercise():
        events = EventBus()
        engine = BehaviorEngine(events, blink_interval=(60, 60), gaze_interval=(60, 60))
        await engine.start()
        await events.publish(Event(IMU_MOTION_STATE, {"state": "tilt_left"}))
        left = engine.face_state
        await events.publish(Event(IMU_MOTION_STATE, {"state": "tilt_right"}))
        right = engine.face_state
        await events.publish(Event(IMU_MOTION_STATE, {"state": "still"}))
        still = engine.face_state
        await events.publish(Event(IMU_MOTION_STATE, {"state": "tilt_left"}))
        await events.publish(Event(STATE_CHANGED, {"current": RobotState.SPEAKING.value}))
        speaking = engine.face_state
        await engine.stop()
        return left, right, still, speaking

    left, right, still, speaking = asyncio.run(exercise())
    assert left.eye_asymmetry == .18 and right.eye_asymmetry == -.18
    assert still.eye_asymmetry is None
    assert speaking.eye_asymmetry is None


def test_imu_shake_impact_decay_and_cooldown():
    async def exercise():
        now = [0.0]; events = EventBus()
        engine = BehaviorEngine(events, clock=lambda: now[0], blink_interval=(60, 60), gaze_interval=(60, 60),
                                imu_shake_reaction_duration_seconds=1, imu_impact_reaction_duration_seconds=.5,
                                imu_reaction_cooldown_seconds=2)
        await engine.start(); await events.publish(Event(IMU_MOTION_STATE, {"state": "shake"}))
        shake = engine.face_state
        now[0] = .8; fading = engine.face_state
        await events.publish(Event(IMU_MOTION_STATE, {"state": "impact"}))
        blocked = engine.face_state
        now[0] = 2.1; await events.publish(Event(IMU_MOTION_STATE, {"state": "impact"}))
        impact = engine.face_state
        now[0] = 3; baseline = engine.face_state; await engine.stop()
        return shake, fading, blocked, impact, baseline
    shake, fading, blocked, impact, baseline = asyncio.run(exercise())
    assert shake.expression is FaceExpression.SURPRISED and shake.accent is VisualAccent.ALERT and shake.eye_open == 1.18
    assert 0 < fading.reaction_strength < shake.reaction_strength
    assert blocked.pupil_x == shake.pupil_x
    assert impact.pupil_x == -.76 and impact.eye_open == 1.25
    assert impact.reaction_strength > shake.reaction_strength
    assert baseline.accent is VisualAccent.NEUTRAL


def test_imu_reactions_stay_within_facestate_bounds_and_restore_base_appearance():
    async def exercise():
        now = [0.0]
        events = EventBus()
        engine = BehaviorEngine(events, clock=lambda: now[0], blink_interval=(60, 60), gaze_interval=(60, 60),
                                imu_tilt_gaze_strength=1, imu_shake_reaction_duration_seconds=.2,
                                imu_impact_reaction_duration_seconds=.2)
        await engine.start()
        await events.publish(Event(IMU_MOTION_STATE, {"state": "tilt_left"}))
        tilt = engine.face_state
        await events.publish(Event(IMU_MOTION_STATE, {"state": "impact"}))
        impact = engine.face_state
        now[0] = .3
        restored = engine.face_state
        await engine.stop()
        return tilt, impact, restored

    tilt, impact, restored = asyncio.run(exercise())
    assert tilt.pupil_x == -1 and tilt.eye_open <= 1.25
    assert impact.eye_open == 1.25 and impact.reaction_strength == 1
    assert restored.accent is VisualAccent.NEUTRAL
    for state in (tilt, impact, restored):
        assert -1 <= state.pupil_x <= 1 and -1 <= state.pupil_y <= 1
        assert 0 <= state.eye_open <= 1.25 and 0 <= state.reaction_strength <= 1
        assert state.eye_asymmetry is None or -.5 <= state.eye_asymmetry <= .5


def test_environmental_bad_is_visible_but_imu_transient_temporarily_wins():
    async def exercise():
        now = [0.0]
        events = EventBus()
        engine = BehaviorEngine(events, clock=lambda: now[0], blink_interval=(60, 60), gaze_interval=(60, 60),
                                imu_shake_reaction_duration_seconds=.2, imu_impact_reaction_duration_seconds=.2)
        await engine.start()
        await events.publish(Event(ENVIRONMENTAL_STATE_CHANGED, {"state": "air_quality_bad"}))
        environmental = engine.face_state
        await events.publish(Event(IMU_MOTION_STATE, {"state": "impact"}))
        impact = engine.face_state
        now[0] = .3
        restored = engine.face_state
        await engine.stop()
        return environmental, impact, restored

    environmental, impact, restored = asyncio.run(exercise())
    assert environmental.accent is VisualAccent.ALERT and environmental.eye_open >= 1.20
    assert environmental.environmental_led_intent is EnvironmentalLEDIntent.AIR_QUALITY_BAD
    assert impact.pupil_x == -.76
    assert restored.accent is VisualAccent.ALERT and restored.eye_open >= 1.20


def test_base_visual_source_selects_manual_environment_or_existing_state_intent():
    async def exercise():
        events = EventBus(); engine = BehaviorEngine(events, blink_interval=(60, 60), gaze_interval=(60, 60))
        await engine.start()
        await events.publish(Event(ENVIRONMENTAL_STATE_CHANGED, {"state": "warm"}))
        engine.configure_base_visual_source("manual")
        manual = engine.face_state
        engine.configure_base_visual_source("environment")
        environment = engine.face_state
        engine.configure_base_visual_source("state")
        await events.publish(Event("vision.visual_expression_stable", {"visual_expression": {"label": "happy", "confidence": 1}}))
        state = engine.face_state
        await engine.stop()
        return manual, environment, state
    manual, environment, state = asyncio.run(exercise())
    assert manual.accent is VisualAccent.NEUTRAL and manual.environmental_led_intent is None
    assert environment.environmental_led_intent is EnvironmentalLEDIntent.WARM
    assert state.accent is VisualAccent.WARM and state.environmental_led_intent is None


def test_ambient_overlays_are_additive_and_can_be_disabled():
    async def exercise():
        engine = BehaviorEngine(EventBus(), blink_interval=(60, 60), gaze_interval=(60, 60))
        await engine._on_environmental_state(Event("", {"state": "air_quality_warning", "temperature_overlay": "warm", "air_quality_overlay": "warning"}))
        active = engine.face_state
        engine.configure_environment_overlays(False)
        disabled = engine.face_state
        return active, disabled
    active, disabled = asyncio.run(exercise())
    assert active.ambient_overlay.temperature == "warm" and active.ambient_overlay.air_quality == "warning"
    assert disabled.ambient_overlay.temperature == disabled.ambient_overlay.air_quality == "none"
