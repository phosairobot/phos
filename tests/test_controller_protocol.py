import pytest
import asyncio
from types import SimpleNamespace

from robot.hardware.controller_protocol import (ControllerMessage, JsonLinesDecoder, ProtocolError,
                                                 decode_frame, validate_led_animation)
from robot.hardware.serial_controller import ControllerService, SerialControllerTransport
from robot.sensors import AirQualityReading, AirQualitySensorService, EnvironmentalSensorService
from robot.sensors import IMUReading, IMUSensorService
from robot.motion import MotionSettings, MotionState


def test_json_lines_decodes_partial_and_multiple_frames():
    frame = ControllerMessage("heartbeat", {}).document()
    encoded = (str(frame).replace("'", '"')).encode()
    decoder = JsonLinesDecoder()
    assert decoder.feed(encoded[:8]) == []
    assert [item.type for item in decoder.feed(encoded[8:] + b"\n" + encoded + b"\n")] == ["heartbeat", "heartbeat"]


@pytest.mark.parametrize("frame", [b"{", b'{"protocol":"phos-hw","version":2,"type":"ping","payload":{}}', b'{"protocol":"phos-hw","version":1,"type":"bad","payload":{}}'])
def test_invalid_frames_are_bounded(frame):
    with pytest.raises(ProtocolError): decode_frame(frame)


@pytest.mark.parametrize("state", ["stationary", "moving", "tilt_left", "tilt_right", "tilt_forward", "tilt_backward", "shake", "impact"])
def test_motion_update_contract_accepts_semantic_states(state):
    message = decode_frame((f'{{"protocol":"phos-hw","version":1,"type":"motion_update","payload":{{"state":"{state}","intensity":"high"}}}}').encode())
    assert message.payload["state"] == state


def test_orientation_update_contract_rejects_incomplete_vector():
    frame = b'{"protocol":"phos-hw","version":1,"type":"orientation_update","payload":{"acceleration_m_s2":{"x":1,"y":2},"angular_velocity_dps":{"x":1,"y":2,"z":3}}}'
    with pytest.raises(ProtocolError, match="requires x, y and z"):
        decode_frame(frame)


@pytest.mark.parametrize(("frame", "reason"), [
    (b'{"protocol":"phos-hw","version":1,"type":"heartbeat"}', "missing payload field."),
    (b'{"protocol":"phos-hw","version":1,"type":"heartbeat","payload":[]}', "payload must be an object."),
])
def test_invalid_payload_reports_a_bounded_field_reason(frame, reason):
    with pytest.raises(ProtocolError, match=reason) as raised:
        decode_frame(frame)
    assert raised.value.code == "invalid_payload"


def test_line_decoder_retains_only_the_rejected_raw_frame_for_debugging():
    decoder = JsonLinesDecoder()
    frame = b'{"protocol":"phos-hw","version":1,"type":"heartbeat","payload":false}'
    with pytest.raises(ProtocolError) as raised:
        decoder.feed(frame + b"\n")
    assert raised.value.frame == frame


def test_led_animation_validation_includes_breathe_and_rejects_bad_parameters():
    validate_led_animation({"animation": "breathe", "color": "#6F5CFF", "brightness": .65, "period_ms": 3200, "min_intensity": .08, "max_intensity": 1, "duration_ms": 0})
    with pytest.raises(ProtocolError): validate_led_animation({"animation": "breathe", "brightness": 2})


def test_pushed_push_sensor_enters_the_existing_touch_boundary():
    received = []
    service = ControllerService(SimpleNamespace(tasks=None), enabled=True, device="/dev/test", baud_rate=115200,
                                reconnect_delay_seconds=1, heartbeat_interval_seconds=2,
                                heartbeat_timeout_seconds=3, touch_sink=received.append)
    asyncio.run(service._message(ControllerMessage("sensor_update", {"sensor": "push", "event": "pressed"})))
    assert len(received) == 1
    assert received[0].kind.value == "tap"
    assert received[0].source == "external_controller"


def test_controller_diagnostic_buffer_is_bounded_and_keeps_semantic_rx_entries():
    service = ControllerService(SimpleNamespace(tasks=None), enabled=True, device="/dev/test", baud_rate=115200,
                                reconnect_delay_seconds=1, heartbeat_interval_seconds=2,
                                heartbeat_timeout_seconds=3, touch_sink=lambda _event: None)
    asyncio.run(service._message(ControllerMessage("sensor_update", {"sensor": "temperature", "value": 24.8, "unit": "c"})))
    entry = service.recent_messages()[-1]
    assert entry["direction"] == "RX"
    assert entry["type"] == "sensor_update"
    assert entry["summary"] == "temperature=24.8 c"
    class Transport:
        async def write(self, _data): pass
    service._transport = Transport()
    asyncio.run(service._send("ping", {}))
    assert service.recent_messages()[-1]["direction"] == "TX"
    assert service.recent_messages()[-1]["type"] == "ping"
    assert len(service.recent_messages()) == len(service.recent_messages())
    for index in range(101):
        service._record("TX", "ping", str(index), b"x" * 9000)
    entries = service.recent_messages()
    assert len(entries) == 100
    assert len(entries[-1]["raw"]) <= 8192 + 80
    service.clear_recent_messages()
    assert service.recent_messages() == []
    asyncio.run(service._message(ControllerMessage("sensor_update", {"sensor": "temperature", "value": 25, "unit": "c"})))
    assert service.recent_messages()[-1]["summary"] == "temperature=25 c"


def test_controller_status_keeps_configuration_and_learns_protocol_capabilities_and_heartbeat():
    service = ControllerService(SimpleNamespace(tasks=None), enabled=True, device="/dev/ttyUSB0", baud_rate=115200,
                                reconnect_delay_seconds=1, heartbeat_interval_seconds=2,
                                heartbeat_timeout_seconds=3, touch_sink=lambda _event: None)
    assert service.status()["configured_device"] == "/dev/ttyUSB0"
    assert service.status()["protocol"] is None
    asyncio.run(service._message(ControllerMessage("capabilities", {"sensors": ["temperature"], "led_count": 12})))
    asyncio.run(service._message(ControllerMessage("heartbeat", {})))
    status = service.status()
    assert status["protocol"] == "phos-hw v1"
    assert status["capabilities"] == {"sensors": ["temperature"], "led_count": 12}
    assert status["last_activity"] and status["last_heartbeat"]


def test_new_serial_session_resets_driver_buffers_and_connection_local_decoder():
    class Serial:
        def __init__(self): self.input_reset = self.output_reset = False
        def reset_input_buffer(self): self.input_reset = True
        def reset_output_buffer(self): self.output_reset = True
        def close(self): pass
    serial = Serial()
    transport = SerialControllerTransport("/dev/ttyUSB0", 115200, serial_factory=lambda *_args, **_kwargs: serial)
    transport.open()
    assert serial.input_reset and serial.output_reset
    service = ControllerService(SimpleNamespace(tasks=None), enabled=True, device="/dev/ttyUSB0", baud_rate=115200,
                                reconnect_delay_seconds=1, heartbeat_interval_seconds=2,
                                heartbeat_timeout_seconds=3, touch_sink=lambda _event: None)
    first, first_deadline = service._new_session()
    service._last_peer_activity = 0
    second, second_deadline = service._new_session()
    assert first is not second
    assert service._last_peer_activity > 0
    assert second_deadline > service._last_peer_activity
    assert first_deadline > 0


def test_controller_updates_merge_into_existing_sensor_service_snapshots():
    environmental = EnvironmentalSensorService(lambda: None, enabled=True, poll_interval_seconds=1,
                                               stale_after_seconds=30, externally_managed=True)
    air = AirQualitySensorService(lambda: None, enabled=True, poll_interval_seconds=1,
                                  stale_after_seconds=30, externally_managed=True)
    received = []
    service = ControllerService(SimpleNamespace(tasks=None), enabled=True, device="/dev/test", baud_rate=115200,
                                reconnect_delay_seconds=1, heartbeat_interval_seconds=2,
                                heartbeat_timeout_seconds=3, touch_sink=received.append,
                                sensor_update_sink=lambda name, value: (
                                    environmental.accept_controller_update(name, value)
                                    if name in {"pressure", "temperature", "humidity"}
                                    else air.accept_controller_update(name, value)))
    async def exercise():
        await service._message(ControllerMessage("sensor_update", {"sensor": "pressure", "value": 991.6266, "unit": "hpa"}))
        await service._message(ControllerMessage("sensor_update", {"sensor": "eco2", "value": 400, "unit": "ppm"}))
        await service._message(ControllerMessage("sensor_update", {"sensor": "tvoc", "value": 0, "unit": "ppb"}))
    asyncio.run(exercise())
    assert environmental.snapshot()["measurements"] == {"pressure_hpa": 991.6266}
    assert air.snapshot()["measurements"] == {"eco2_ppm": 400, "tvoc_ppb": 0}


def test_controller_air_quality_notification_uses_canonical_reading_type():
    received = []
    air = AirQualitySensorService(lambda: None, enabled=True, poll_interval_seconds=1,
                                  stale_after_seconds=30, externally_managed=True,
                                  reading_sink=lambda reading, _now: received.append(reading))
    air.accept_controller_update("eco2", 400)
    air.accept_controller_update("tvoc", 0)
    assert isinstance(received[-1], AirQualityReading)
    assert received[-1] == AirQualityReading(400, 0)


def test_controller_owned_services_do_not_construct_local_i2c_providers_and_disconnects_are_unavailable():
    constructed = []
    factory = lambda: constructed.append("called")
    environmental = EnvironmentalSensorService(factory, enabled=True, poll_interval_seconds=1,
                                               stale_after_seconds=30, externally_managed=True)
    air = AirQualitySensorService(factory, enabled=True, poll_interval_seconds=1,
                                  stale_after_seconds=30, externally_managed=True)
    imu = IMUSensorService(factory, enabled=True, poll_interval_seconds=1, stale_after_seconds=30,
                           externally_managed=True,
                           motion_settings=MotionSettings(1.5, 4, 180, 25, .25, 1, 3, "x", "y"))
    async def exercise():
        await environmental.start(); await air.start(); await imu.start()
    asyncio.run(exercise())
    assert constructed == []
    environmental.accept_controller_update("pressure", 991.6)
    assert environmental.snapshot()["available"]
    environmental.mark_external_unavailable()
    assert environmental.snapshot()["status"] == "unavailable"


def test_controller_imu_telemetry_and_semantic_motion_share_one_snapshot():
    states = []
    imu = IMUSensorService(lambda: None, enabled=True, poll_interval_seconds=1, stale_after_seconds=30,
                           externally_managed=True, motion_state_sink=states.append,
                           motion_settings=MotionSettings(1.5, 4, 180, 25, .25, 1, 3, "x", "y"))
    imu.accept_controller_orientation(IMUReading(1, 2, 3, 4, 5, 6))
    imu.accept_controller_motion(MotionState.MOVING, "medium")
    snapshot = imu.snapshot()
    assert snapshot["measurements"]["acceleration_x_m_s2"] == 1
    assert snapshot["motion_state"] == "moving"
    assert snapshot["motion_intensity"] == "medium"
    assert states == [MotionState.MOVING]


def test_controller_sensor_families_keep_independent_canonical_availability_and_values():
    environmental = EnvironmentalSensorService(lambda: None, enabled=True, poll_interval_seconds=1,
                                               stale_after_seconds=30, externally_managed=True)
    air = AirQualitySensorService(lambda: None, enabled=True, poll_interval_seconds=1,
                                  stale_after_seconds=30, externally_managed=True)
    imu = IMUSensorService(lambda: None, enabled=True, poll_interval_seconds=1, stale_after_seconds=30,
                           externally_managed=True,
                           motion_settings=MotionSettings(1.5, 4, 180, 25, .25, 1, 3, "x", "y"))
    air.accept_controller_update("eco2", 400)
    air.accept_controller_update("tvoc", 0)
    imu.accept_controller_motion(MotionState.MOVING, "medium")
    environmental.accept_controller_update("temperature", 24.8)
    assert air.snapshot()["available"]
    assert air.snapshot()["measurements"] == {"eco2_ppm": 400, "tvoc_ppb": 0}
    environmental.accept_controller_update("pressure", 991.6)
    assert air.snapshot()["available"]
    assert air.snapshot()["measurements"] == {"eco2_ppm": 400, "tvoc_ppb": 0}
    snapshot = {"environmental": environmental.snapshot(), "ccs811": air.snapshot(), "imu": imu.snapshot()}
    assert snapshot["environmental"]["available"]
    assert snapshot["ccs811"]["available"]
    assert snapshot["ccs811"]["measurements"] == {"eco2_ppm": 400, "tvoc_ppb": 0}
    assert snapshot["imu"]["available"] and snapshot["imu"]["motion_state"] == "moving"


def test_controller_air_quality_freshness_is_refreshed_by_each_partial_push_only():
    now = [0.0]
    air = AirQualitySensorService(lambda: None, enabled=True, poll_interval_seconds=1,
                                  stale_after_seconds=10, externally_managed=True, clock=lambda: now[0])
    air.accept_controller_update("eco2", 400)
    now[0] = 1
    air.accept_controller_update("tvoc", 0)
    now[0] = 9
    # Unrelated environmental/IMU activity has no path into this service.
    assert air.snapshot()["available"]
    assert air.snapshot()["measurements"] == {"eco2_ppm": 400, "tvoc_ppb": 0}
    now[0] = 12
    assert air.snapshot()["status"] == "stale"


def test_controller_air_quality_disconnect_is_immediate_and_next_push_restores_availability():
    now = [0.0]
    air = AirQualitySensorService(lambda: None, enabled=True, poll_interval_seconds=1,
                                  stale_after_seconds=90, externally_managed=True, clock=lambda: now[0])
    air.accept_controller_update("eco2", 400)
    air.accept_controller_update("tvoc", 0)
    air.mark_external_unavailable()
    assert air.snapshot()["status"] == "unavailable"
    air.accept_controller_update("eco2", 401)
    assert air.snapshot()["status"] == "available"
    assert air.snapshot()["measurements"] == {"eco2_ppm": 401, "tvoc_ppb": 0}


def test_local_air_quality_freshness_uses_its_configured_timeout_unchanged():
    now = [0.0]
    air = AirQualitySensorService(lambda: None, enabled=True, poll_interval_seconds=1,
                                  stale_after_seconds=10, clock=lambda: now[0])
    air._accept_external_fields({"eco2_ppm": 400, "tvoc_ppb": 0})
    now[0] = 9
    assert air.snapshot()["available"]
    now[0] = 10
    assert air.snapshot()["status"] == "stale"


def test_controller_environmental_notification_uses_complete_merged_reading_and_survives_failure(caplog):
    received = []
    environmental = EnvironmentalSensorService(lambda: None, enabled=True, poll_interval_seconds=1,
                                               stale_after_seconds=30, externally_managed=True,
                                               reading_sink=lambda reading, _now: received.append(reading))
    environmental.accept_controller_update("temperature", 24.8)
    environmental.accept_controller_update("pressure", 991.6)
    environmental.accept_controller_update("humidity", 40.0)
    assert environmental.snapshot()["measurements"] == {
        "temperature_c": 24.8, "pressure_hpa": 991.6, "humidity_percent": 40.0}
    assert received[-1].temperature_c == 24.8
    assert received[-1].pressure_hpa == 991.6
    assert received[-1].humidity_percent == 40.0

    def failed_notification(_value, _now):
        raise RuntimeError("event publisher unavailable")
    environmental = EnvironmentalSensorService(lambda: None, enabled=True, poll_interval_seconds=1,
                                               stale_after_seconds=30, externally_managed=True,
                                               reading_sink=failed_notification)
    with caplog.at_level("WARNING"):
        environmental.accept_controller_update("temperature", 24.8)
        environmental.accept_controller_update("pressure", 991.6)
    assert environmental.snapshot()["measurements"] == {"temperature_c": 24.8, "pressure_hpa": 991.6}
    assert "Environmental controller notification failure" in caplog.text
