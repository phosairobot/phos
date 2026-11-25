from robot.services.physical_assessment import PhysicalContextAssessmentService
from robot.services.temporal_history import TemporalHistoryService


def make_history(stamps):
    now = [0.0]
    iterator = iter(stamps)
    last = stamps[-1]
    return TemporalHistoryService(clock=lambda: now[0], utc_now=lambda: next(iterator, last)), now


def sensors(status="available", available=True, age=1.0):
    return {"environmental": {"status": status, "available": available,
                               "last_update": "2026-01-01T00:02:00+00:00", "age_seconds": age},
            "ens160": {"status": status, "available": available,
                        "last_update": "2026-01-01T00:02:00+00:00", "age_seconds": age}}


def test_valid_evidence_is_bounded_and_read_only():
    history, now = make_history(["2026-01-01T00:00:00Z", "2026-01-01T00:01:00+00:00", "2026-01-01T00:02:00+00:00"])
    for value in (20, 21, 22):
        assert history.record_measurement("temperature_c", value, "c", source="environment")
        now[0] += 60
    service = PhysicalContextAssessmentService(history, utc_now=lambda: "2026-01-01T00:02:00Z")
    before = history.query_measurements("temperature_c")
    result = service.assess(sensors())["metrics"]["temperature_c"]
    assert result["outcome"] == "valid" and result["reason"] == "evidence_ready"
    assert result["unit"] == "c" and result["source"] == "environment"
    assert result["evidence"] == {"sample_count": 3, "first_observed_at": "2026-01-01T00:00:00Z",
                                  "last_observed_at": "2026-01-01T00:02:00+00:00", "coverage_seconds": 120.0,
                                  "largest_gap_seconds": 60.0, "continuity": "no_observed_discontinuity",
                                  "availability_discontinuity": False}
    assert history.query_measurements("temperature_c") == before


def test_unknown_and_insufficient_assessment_reasons_are_explicit():
    history, now = make_history(["2026-01-01T00:00:00+00:00", "2026-01-01T00:01:00+00:00"])
    service = PhysicalContextAssessmentService(history)
    assert service.assess_metric("not_a_metric", sensors=sensors())["reason"] == "unsupported_metric"
    assert service.assess_metric("temperature_c", sensors=sensors())["reason"] == "no_retained_samples"
    assert history.record_measurement("temperature_c", 20, "c", source="environment")
    now[0] = 60
    assert history.record_measurement("temperature_c", 21, "c", source="environment")
    result = service.assess_metric("temperature_c", sensors=sensors())
    assert result["outcome"] == "insufficient_data" and result["reason"] == "insufficient_sample_count"


def test_coverage_gap_stale_and_unavailable_take_precedence():
    history, now = make_history(["2026-01-01T00:00:00+00:00", "2026-01-01T00:03:00+00:00", "2026-01-01T00:04:00+00:00"])
    for value, minute in ((20, 0), (21, 180), (22, 240)):
        now[0] = minute
        assert history.record_measurement("temperature_c", value, "c", source="environment")
    service = PhysicalContextAssessmentService(history)
    assert service.assess_metric("temperature_c", sensors=sensors())["reason"] == "observed_gap"
    assert service.assess_metric("temperature_c", sensors=sensors(status="stale", available=False, age=99))["outcome"] == "stale"
    assert service.assess_metric("temperature_c", sensors=sensors(status="unavailable", available=False))["outcome"] == "unavailable"


def test_availability_transition_marks_reconnect_discontinuity():
    stamps = ["2026-01-01T00:00:00+00:00", "2026-01-01T00:01:00+00:00", "2026-01-01T00:01:30+00:00",
              "2026-01-01T00:01:40+00:00", "2026-01-01T00:02:00+00:00"]
    history, now = make_history(stamps)
    assert history.record_measurement("temperature_c", 20, "c", source="environment")
    now[0] = 60; assert history.record_measurement("temperature_c", 21, "c", source="environment")
    now[0] = 90; assert history.record_transition("environment_availability_changed", "unavailable", "available", source="environment")
    now[0] = 100; assert history.record_transition("environment_availability_changed", "available", "unavailable", source="environment")
    now[0] = 120; assert history.record_measurement("temperature_c", 22, "c", source="environment")
    result = PhysicalContextAssessmentService(history).assess_metric("temperature_c", sensors=sensors())
    assert result["outcome"] == "insufficient_data" and result["reason"] == "availability_discontinuity"
    assert result["evidence"]["continuity"] == "observed_discontinuity"
    assert result["recent_continuous_segment"]["reason"] == "insufficient_sample_count"
    assert result["recent_continuous_segment"]["recovery_boundary"] == {"observed_at": "2026-01-01T00:01:40+00:00"}


def test_recent_segment_becomes_valid_only_after_sufficient_post_recovery_evidence():
    stamps = ["2026-01-01T00:00:00+00:00", "2026-01-01T00:01:00+00:00", "2026-01-01T00:01:30+00:00",
              "2026-01-01T00:01:40+00:00", "2026-01-01T00:02:00+00:00", "2026-01-01T00:03:00+00:00",
              "2026-01-01T00:04:00+00:00"]
    history, now = make_history(stamps)
    for value, moment in ((20, 0), (21, 60)):
        now[0] = moment; assert history.record_measurement("temperature_c", value, "c", source="environment")
    now[0] = 90; assert history.record_transition("environment_availability_changed", "unavailable", "available", source="environment")
    now[0] = 100; assert history.record_transition("environment_availability_changed", "available", "unavailable", source="environment")
    for value, moment in ((22, 120), (23, 180), (24, 240)):
        now[0] = moment; assert history.record_measurement("temperature_c", value, "c", source="environment")
    result = PhysicalContextAssessmentService(history).assess_metric("temperature_c", sensors=sensors())
    assert result["outcome"] == "insufficient_data" and result["reason"] == "availability_discontinuity"
    segment = result["recent_continuous_segment"]
    assert segment["outcome"] == "valid" and segment["reason"] == "evidence_ready"
    assert segment["sample_count"] == 3 and segment["coverage_seconds"] == 120.0
    assert segment["continuity"] == "recovery_boundary_verified"


def test_latest_recovery_boundary_wins_and_later_loss_invalidates_recent_segment():
    stamps = [f"2026-01-01T00:{minute:02d}:00+00:00" for minute in range(9)]
    history, now = make_history(stamps)
    now[0] = 0; assert history.record_measurement("temperature_c", 20, "c", source="environment")
    now[0] = 60; assert history.record_transition("environment_availability_changed", "unavailable", "available", source="environment")
    now[0] = 120; assert history.record_transition("environment_availability_changed", "available", "unavailable", source="environment")
    now[0] = 180; assert history.record_measurement("temperature_c", 21, "c", source="environment")
    now[0] = 240; assert history.record_transition("environment_availability_changed", "unavailable", "available", source="environment")
    now[0] = 300; assert history.record_transition("environment_availability_changed", "available", "unavailable", source="environment")
    for value, moment in ((22, 360), (23, 420), (24, 480)):
        now[0] = moment; assert history.record_measurement("temperature_c", value, "c", source="environment")
    result = PhysicalContextAssessmentService(history).assess_metric("temperature_c", sensors=sensors())
    assert result["recent_continuous_segment"]["outcome"] == "valid"
    assert result["recent_continuous_segment"]["recovery_boundary"] == {"observed_at": "2026-01-01T00:05:00+00:00"}
    now[0] = 540; assert history.record_transition("environment_availability_changed", "unavailable", "available", source="environment")
    result = PhysicalContextAssessmentService(history).assess_metric("temperature_c", sensors=sensors())
    assert result["recent_continuous_segment"]["outcome"] == "insufficient_data"
    assert result["recent_continuous_segment"]["reason"] == "recovery_boundary_missing"


def test_recent_segment_reports_missing_or_unordered_recovery_metadata_honestly():
    history, now = make_history(["2026-01-01T00:00:00+00:00", "2026-01-01T00:01:00+00:00", "2026-01-01T00:02:00+00:00"])
    now[0] = 0; assert history.record_measurement("temperature_c", 20, "c", source="environment")
    now[0] = 60; assert history.record_transition("environment_availability_changed", "unavailable", "available", source="environment")
    now[0] = 120; assert history.record_measurement("temperature_c", 21, "c", source="environment")
    result = PhysicalContextAssessmentService(history).assess_metric("temperature_c", sensors=sensors())
    assert result["recent_continuous_segment"]["reason"] == "recovery_boundary_missing"

    unordered, clock = make_history(["2026-01-01T00:00:00+00:00", "2026-01-01T00:02:00+00:00",
                                     "2026-01-01T00:01:00+00:00", "2026-01-01T00:03:00+00:00"])
    clock[0] = 0; assert unordered.record_measurement("temperature_c", 20, "c", source="environment")
    clock[0] = 60; assert unordered.record_transition("environment_availability_changed", "unavailable", "available", source="environment")
    clock[0] = 120; assert unordered.record_transition("environment_availability_changed", "available", "unavailable", source="environment")
    clock[0] = 180; assert unordered.record_measurement("temperature_c", 21, "c", source="environment")
    result = PhysicalContextAssessmentService(unordered).assess_metric("temperature_c", sensors=sensors())
    assert result["recent_continuous_segment"]["reason"] == "recovery_boundary_ordering_unknown"


def test_source_change_is_not_treated_as_a_continuous_recent_segment():
    history, now = make_history(["2026-01-01T00:00:00+00:00", "2026-01-01T00:01:00+00:00", "2026-01-01T00:02:00+00:00"])
    now[0] = 0; assert history.record_measurement("temperature_c", 20, "c", source="environment")
    now[0] = 60; assert history.record_measurement("temperature_c", 21, "c", source="replacement_environment")
    now[0] = 120; assert history.record_measurement("temperature_c", 22, "c", source="replacement_environment")
    result = PhysicalContextAssessmentService(history).assess_metric("temperature_c", sensors=sensors())
    assert result["outcome"] == "unknown" and result["reason"] == "inconsistent_source"
    assert result["recent_continuous_segment"]["reason"] == "source_changed"


def test_timestamp_offsets_normalize_and_history_rejects_out_of_order_insertions():
    history, now = make_history(["2026-01-01T01:00:00+01:00", "2026-01-01T00:01:00Z", "2026-01-01T00:02:00Z"])
    assert history.record_measurement("temperature_c", 20, "c", source="environment")
    now[0] = 60; assert history.record_measurement("temperature_c", 21, "c", source="environment")
    now[0] = 120; assert history.record_measurement("temperature_c", 22, "c", source="environment")
    now[0] = 1
    assert not history.record_measurement("temperature_c", 23, "c", source="environment")
    result = PhysicalContextAssessmentService(history).assess_metric("temperature_c", sensors=sensors())
    assert result["evidence"]["coverage_seconds"] == 120.0
