from robot.services.temporal_history import TemporalHistoryService
from robot.sensors import EnvironmentalSensorService, AirQualitySensorService
import pytest


def test_measurements_are_rate_limited_expire_and_summarized():
    now = [0.0]
    history = TemporalHistoryService(clock=lambda: now[0], utc_now=lambda: "2026-01-01T00:00:00+00:00")
    assert history.record_measurement("temperature_c", 22, "c", source="environment")
    assert not history.record_measurement("temperature_c", 23, "c", source="environment")
    now[0] = 3600
    assert history.record_measurement("temperature_c", 23, "c", source="environment")
    result = history.query_measurements("temperature_c")
    assert result["summary"]["minimum"] == 22
    assert result["summary"]["maximum"] == 23
    assert result["summary"]["delta"] == 1
    now[0] = 6 * 3600 + 61
    assert history.query_measurements("temperature_c")["count"] == 1


def test_invalid_data_and_transitions_are_bounded_and_keep_a_b_a():
    now = [0.0]
    history = TemporalHistoryService(clock=lambda: now[0], utc_now=lambda: "now")
    assert not history.record_measurement("temperature_c", float("nan"), "c", source="environment")
    assert not history.record_transition("presence_changed", "present", "present", source="presence")
    assert history.record_transition("presence_changed", "present", "absent", source="presence")
    assert history.record_transition("presence_changed", "absent", "present", source="presence")
    assert [item["current"] for item in history.list_transitions()["transitions"]] == ["present", "absent"]


def test_utc_window_is_validated_and_filters_actual_observations():
    now = [0.0]; stamps = iter(["2026-01-01T10:00:00+00:00", "2026-01-01T11:00:00+00:00", "2026-01-01T12:00:00+00:00"])
    history = TemporalHistoryService(clock=lambda: now[0], utc_now=lambda: next(stamps))
    history.record_measurement("temperature_c", 20, "c", source="environment")
    now[0] = 60; history.record_measurement("temperature_c", 21, "c", source="environment")
    result = history.query_measurements("temperature_c", start="2026-01-01T10:30:00Z", end="2026-01-01T11:30:00Z")
    assert [item["value"] for item in result["samples"]] == [21]
    with pytest.raises(ValueError): history.query_measurements("temperature_c", start="2026-01-01T11:00:00")


def test_capacity_expiry_and_ordering_are_bounded_deterministically():
    now = [0.0]
    history = TemporalHistoryService(clock=lambda: now[0], utc_now=lambda: "2026-01-01T00:00:00+00:00")
    for minute in range(361):
        now[0] = minute * 60
        assert history.record_measurement("temperature_c", minute, "c", source="environment")
    assert history.query_measurements("temperature_c")["count"] == 360
    for index in range(15):
        assert history.record_measurement(f"metric_{index}", index, "u", source="test")
    assert not history.record_measurement("metric_overflow", 1, "u", source="test")
    now[0] -= 61
    assert not history.record_measurement("temperature_c", 999, "c", source="environment")
    now[0] = 6 * 3600 + 61
    assert history.query_measurements("temperature_c")["count"] < 360


def test_event_capacity_expiry_and_window_errors():
    now = [0.0]
    history = TemporalHistoryService(clock=lambda: now[0], utc_now=lambda: "2026-01-01T00:00:00+00:00")
    for value in range(501):
        assert history.record_transition("motion_state_changed", str(value), str(value - 1), source="motion")
    assert history.list_transitions(limit=360)["count"] == 360
    assert len(history._events) == 500
    now[0] = 6 * 3600 + 1
    assert history.list_transitions()["count"] == 0
    for kwargs in ({"limit": 0}, {"start": "bad"}, {"start": "2026-01-01T01:00:00Z", "end": "2026-01-01T00:00:00Z"},
                   {"start": "2026-01-01T00:00:00Z", "end": "2026-01-01T07:00:01Z"}):
        with pytest.raises(ValueError): history.query_measurements("temperature_c", **kwargs)


def test_close_resets_history_and_single_sample_has_insufficient_delta():
    history = TemporalHistoryService(utc_now=lambda: "2026-01-01T00:00:00+00:00")
    assert history.record_measurement("temperature_c", 20, "c", source="environment")
    assert history.query_measurements("temperature_c")["summary"]["coverage"] == "insufficient"
    history.close()
    assert history.query_measurements("temperature_c")["count"] == 0


def test_controller_sensor_services_only_record_completed_valid_recoveries():
    now = [0.0]; history = TemporalHistoryService(clock=lambda: now[0], utc_now=lambda: "2026-01-01T00:00:00+00:00")
    env = EnvironmentalSensorService(lambda: None, enabled=True, externally_managed=True, poll_interval_seconds=1, stale_after_seconds=2,
        reading_sink=lambda value, _: history.record_measurement("temperature_c", value.temperature_c, "c", source="environment"))
    env.accept_controller_update("temperature", 22.0)
    env.accept_controller_update("pressure", 1000.0)
    assert history.query_measurements("temperature_c")["count"] == 1
    env.mark_external_unavailable()
    assert history.query_measurements("temperature_c")["count"] == 1
    now[0] = 60; env.accept_controller_update("temperature", 23.0)
    assert history.query_measurements("temperature_c")["samples"][-1]["value"] == 23

    air = AirQualitySensorService(lambda: None, enabled=True, externally_managed=True, poll_interval_seconds=1, stale_after_seconds=2,
        reading_sink=lambda value, _: history.record_measurement("eco2_ppm", value.eco2_ppm, "ppm", source="air"))
    air.activate_controller_generation(1)
    air.accept_controller_update("eco2", 500, generation=1)
    assert history.query_measurements("eco2_ppm")["count"] == 0
    air.accept_controller_update("tvoc", 20, generation=1)
    assert history.query_measurements("eco2_ppm")["count"] == 1


def test_non_normal_ens160_telemetry_is_diagnostic_not_temporal_evidence():
    now = [0.0]
    history = TemporalHistoryService(clock=lambda: now[0], utc_now=lambda: "2026-01-01T00:00:00+00:00")
    air = AirQualitySensorService(
        lambda: None, enabled=True, externally_managed=True, poll_interval_seconds=1, stale_after_seconds=30,
        clock=lambda: now[0],
        reading_sink=lambda value, _: (
            history.record_measurement("eco2_ppm", value.eco2_ppm, "ppm", source="air_quality"),
            history.record_measurement("tvoc_ppb", value.tvoc_ppb, "ppb", source="air_quality"),
        ),
    )
    air.activate_controller_generation(1)
    air.accept_controller_status({"validity": 2, "compensation_active": False}, generation=1)
    air.accept_controller_update("eco2", 500, generation=1)
    air.accept_controller_update("tvoc", 20, generation=1)
    assert history.query_measurements("eco2_ppm")["count"] == 0
    assert air.snapshot()["measurements"]["eco2_ppm"] == 500
    assert air.snapshot()["measurements"]["tvoc_ppb"] == 20

    air.accept_controller_status({"validity": 0, "compensation_active": True}, generation=1)
    air.accept_controller_update("eco2", 501, generation=1)
    air.accept_controller_update("tvoc", 21, generation=1)
    assert history.query_measurements("eco2_ppm")["count"] == 1
