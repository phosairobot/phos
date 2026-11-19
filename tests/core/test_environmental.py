from robot.core.environmental import EnvironmentalInterpreter, EnvironmentalSettings, EnvironmentalState


def interpreter(**overrides):
    events = []
    result = EnvironmentalInterpreter(EnvironmentalSettings(**overrides), lambda state, reason: events.append((state, reason)))
    return result, events


def test_normal_and_transient_readings_do_not_create_an_alarm():
    subject, events = interpreter(enabled=True, confirmation_seconds=10)
    subject.observe_environmental(22, now=0)
    subject.observe_air_quality(600, 50, now=0)
    subject.observe_environmental(29, now=1)
    subject.observe_environmental(22, now=5)
    assert subject.state is EnvironmentalState.NORMAL
    assert events == []


def test_temperature_confirmation_hysteresis_and_recovery():
    subject, _ = interpreter(enabled=True, confirmation_seconds=10, recovery_seconds=20)
    subject.observe_environmental(28, now=0)
    subject.observe_environmental(28, now=10)
    assert subject.state is EnvironmentalState.WARM
    subject.observe_environmental(26.5, now=11)  # still above warm exit
    subject.observe_environmental(25, now=12)
    subject.observe_environmental(25, now=31)
    assert subject.state is EnvironmentalState.WARM
    subject.observe_environmental(25, now=32)
    assert subject.state is EnvironmentalState.NORMAL


def test_air_quality_warning_bad_and_invalid_data_recover_without_false_alarm():
    subject, _ = interpreter(enabled=True, confirmation_seconds=5, recovery_seconds=5)
    subject.observe_air_quality(1300, 10, now=0)
    subject.observe_air_quality(1300, 10, now=5)
    assert subject.state is EnvironmentalState.AIR_QUALITY_WARNING
    subject.observe_air_quality(2100, 10, now=6)
    subject.observe_air_quality(2100, 10, now=11)
    assert subject.state is EnvironmentalState.AIR_QUALITY_BAD
    subject.unavailable(now=12, source="stale")
    assert subject.state is EnvironmentalState.NORMAL


def test_warm_temperature_survives_ens160_warming_up():
    subject, _ = interpreter(enabled=True, confirmation_seconds=5)
    subject.observe_environmental(28.18, now=0)
    subject.unavailable(now=1, source="air_quality", status="warming_up")
    subject.observe_environmental(28.18, now=5)
    assert subject.state is EnvironmentalState.WARM
    assert subject.reason == "temperature above warm threshold"


def test_cold_temperature_survives_ens160_warming_up_and_normal_stays_normal():
    cold, _ = interpreter(enabled=True, confirmation_seconds=5)
    cold.observe_environmental(16, now=0)
    cold.unavailable(now=1, source="air_quality", status="warming_up")
    cold.observe_environmental(16, now=5)
    assert cold.state is EnvironmentalState.COLD
    normal, _ = interpreter(enabled=True, confirmation_seconds=5)
    normal.observe_environmental(22, now=0)
    normal.unavailable(now=1, source="air_quality", status="warming_up")
    assert normal.state is EnvironmentalState.NORMAL


def test_unavailable_temperature_and_warming_air_quality_do_not_create_temperature_state():
    subject, _ = interpreter(enabled=True, confirmation_seconds=5)
    subject.observe_environmental(28, now=0)
    subject.unavailable(now=1, source="environmental", status="stale")
    subject.unavailable(now=2, source="air_quality", status="warming_up")
    assert subject.state is EnvironmentalState.NORMAL
    assert subject.reason == "environmental data unavailable"


def test_air_quality_priority_over_valid_warm_temperature():
    warning, _ = interpreter(enabled=True, confirmation_seconds=5)
    warning.observe_environmental(28, now=0)
    warning.observe_air_quality(1300, 10, now=0)
    warning.observe_environmental(28, now=5)
    assert warning.state is EnvironmentalState.AIR_QUALITY_WARNING
    bad, _ = interpreter(enabled=True, confirmation_seconds=5)
    bad.observe_environmental(28, now=0)
    bad.observe_air_quality(2100, 10, now=0)
    bad.observe_environmental(28, now=5)
    assert bad.state is EnvironmentalState.AIR_QUALITY_BAD


def test_warm_state_survives_ens160_unavailability():
    subject, _ = interpreter(enabled=True, confirmation_seconds=5)
    subject.observe_environmental(28, now=0)
    subject.observe_environmental(28, now=5)
    assert subject.state is EnvironmentalState.WARM
    subject.unavailable(now=6, source="air_quality", status="unavailable")
    assert subject.state is EnvironmentalState.WARM
    assert subject.reason == "temperature above warm threshold"


def test_confirmed_overlay_intents_are_composable_without_changing_priority_state():
    subject, _ = interpreter(enabled=True, confirmation_seconds=5)
    subject.observe_environmental(28, now=0)
    subject.observe_air_quality(1300, 10, now=0)
    subject.observe_environmental(28, now=5)
    assert subject.state is EnvironmentalState.AIR_QUALITY_WARNING
    assert subject.temperature_overlay.value == "warm"
    assert subject.air_quality_overlay.value == "warning"
    cold_bad, _ = interpreter(enabled=True, confirmation_seconds=5)
    cold_bad.observe_environmental(16, now=0)
    cold_bad.observe_air_quality(2100, 10, now=0)
    cold_bad.observe_environmental(16, now=5)
    assert cold_bad.state is EnvironmentalState.AIR_QUALITY_BAD
    assert cold_bad.temperature_overlay.value == "cold"
    assert cold_bad.air_quality_overlay.value == "bad"
