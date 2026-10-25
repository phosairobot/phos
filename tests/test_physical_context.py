from robot.services.physical_context import SemanticPhysicalContextService


def test_complete_context_is_semantic_provider_independent_and_copy_safe():
    service = SemanticPhysicalContextService()
    sensors = {"environmental": {"status": "available", "available": True, "measurements": {"temperature_c": 22.5, "pressure_hpa": 1012.0, "humidity_percent": 45.0}, "last_update": "2026-01-01T00:00:00+00:00", "age_seconds": 1.0, "provider": "local"},
               "ccs811": {"status": "available", "available": True, "measurements": {"eco2_ppm": 500, "tvoc_ppb": 12}, "last_update": "2026-01-01T00:00:00+00:00", "age_seconds": 1.0, "provider": "controller"},
               "imu": {"status": "available", "available": True, "motion_state": "moving", "motion_intensity": "medium", "tilt_direction": None, "last_motion_event": {"state": "moving"}, "last_update": "2026-01-01T00:00:00+00:00", "age_seconds": 1.0,
                       "measurements": {"acceleration_x_m_s2": 9.8}, "serial_port": "/dev/ttyUSB0"},
               "environmental_behavior": {"state": "normal", "reason": "measurements normal"}}
    context = service.build(robot={"state": "listening"}, sensors=sensors,
                            presence={"state": "person_present", "people_count": 1, "confidence": .9, "primary_candidate_id": "face-1"},
                            attention={"state": "tracking", "target": {"id": "face-1", "x": .2, "y": -.1, "confidence": .9}},
                            observed_expression={"available": True, "label": "happy", "confidence": .8, "provider": "local", "model": "x", "observed_at": 2.0, "unavailable_reason": None})
    document = context.document()
    assert set(document) == {"robot", "environment", "air_quality", "motion", "presence", "attention", "observed_expression"}
    assert document["environment"]["temperature_c"] == 22.5 and document["air_quality"]["eco2_ppm"] == 500
    assert document["motion"]["motion_state"] == "moving" and document["attention"]["target"] == {"x": .2, "y": -.1, "confidence": .9}
    assert "provider" not in document["environment"] and "serial_port" not in document["motion"]
    assert "id" not in document["attention"]["target"] and "primary_candidate_id" not in document["presence"]
    assert "provider" not in document["observed_expression"] and "model" not in document["observed_expression"]
    document["environment"]["temperature_c"] = 0
    assert context.document()["environment"]["temperature_c"] == 22.5


def test_partial_context_preserves_stale_and_unavailable_sections():
    context = SemanticPhysicalContextService().build(robot={"state": "idle"}, sensors={
        "environmental": {"status": "unavailable", "available": False, "measurements": None, "last_update": None, "age_seconds": None},
        "ccs811": {"status": "available", "available": True, "measurements": {"eco2_ppm": 400, "tvoc_ppb": 0}, "last_update": "now", "age_seconds": 2},
        "imu": {"status": "stale", "available": False, "last_update": "old", "age_seconds": 30},
    }, presence={"state": "no_one", "people_count": 0, "confidence": None}, attention={"state": "idle", "target": None},
       observed_expression={"available": False, "label": None, "confidence": None, "unavailable_reason": "unavailable"}).document()
    assert context["environment"]["status"] == "unavailable" and context["air_quality"]["available"] is True
    assert context["motion"]["status"] == "stale" and context["motion"]["available"] is False
