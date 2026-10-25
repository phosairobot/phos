import pytest
import asyncio
import json
import threading
import time
from types import SimpleNamespace

from robot.hardware.controller_protocol import (ControllerMessage, JsonLinesDecoder, ProtocolError,
                                                 decode_frame, validate_led_animation)
from robot.hardware.serial_controller import ControllerConnection, ControllerService, SerialControllerTransport
from robot.core.events import EventBus
from robot.core.semantic_led import SemanticLedService
from robot.core.state import RobotState
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


@pytest.mark.parametrize("payload", [
    {"animation": "spin", "direction": "clockwise"},
    {"animation": "spin", "direction": "counterclockwise"},
    {"animation": "chase", "mode": "circular", "direction": "clockwise"},
    {"animation": "chase", "mode": "circular", "direction": "counterclockwise"},
    {"animation": "chase", "mode": "symmetric", "origin": "left", "direction": "outward"},
    {"animation": "wipe", "mode": "circular", "origin": "top", "direction": "clockwise"},
    {"animation": "wipe", "mode": "symmetric", "origin": "top", "direction": "outward"},
    {"animation": "wipe", "mode": "symmetric", "origin": "bottom", "direction": "inward"},
    {"animation": "spin"}, {"animation": "chase"}, {"animation": "wipe"},
])
def test_led_directional_animation_contract_accepts_only_supported_combinations(payload):
    validate_led_animation(payload)


@pytest.mark.parametrize("payload", [
    {"animation": "spin", "direction": "outward"},
    {"animation": "spin", "origin": "top"},
    {"animation": "solid", "direction": "clockwise"},
    {"animation": "chase", "mode": "symmetric", "direction": "outward"},
    {"animation": "wipe", "mode": "symmetric", "origin": "top", "direction": "clockwise"},
    {"animation": "chase", "mode": "circular", "origin": "top"},
    {"animation": "wipe", "mode": "diagonal", "direction": "clockwise"},
    {"animation": "wipe", "mode": "symmetric", "origin": "north", "direction": "outward"},
])
def test_led_directional_animation_contract_rejects_incompatible_combinations(payload):
    with pytest.raises(ProtocolError, match="LED|direction|origin|mode|symmetric"):
        validate_led_animation(payload)


def test_led_directional_fields_are_validated_in_the_phos_hw_envelope_and_preserved():
    payload = {"animation": "wipe", "mode": "symmetric", "origin": "top", "direction": "outward",
               "color": "#00AAFF", "brightness": .5, "period_ms": 2000, "duration_ms": 0}
    message = decode_frame((json.dumps({"protocol": "phos-hw", "version": 1,
                                        "type": "set_led_animation", "payload": payload})).encode())
    assert message.payload == payload
    invalid = {**payload, "direction": "clockwise"}
    with pytest.raises(ProtocolError):
        decode_frame((json.dumps({"protocol": "phos-hw", "version": 1,
                                  "type": "set_led_animation", "payload": invalid})).encode())


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
    assert service.status()["session_generation"] == 0
    asyncio.run(service._message(ControllerMessage("capabilities", {"sensors": ["temperature"], "led_count": 12})))
    asyncio.run(service._message(ControllerMessage("heartbeat", {})))
    status = service.status()
    assert status["protocol"] == "phos-hw v1"
    assert status["capabilities"] == {"sensors": ["temperature"], "led_count": 12}
    assert status["last_activity"] and status["last_heartbeat"]


def test_controller_session_generation_invalidates_only_active_led_capabilities():
    capability_notifications = []
    service = ControllerService(SimpleNamespace(tasks=None), enabled=True, device="/dev/ttyUSB0", baud_rate=115200,
                                reconnect_delay_seconds=1, heartbeat_interval_seconds=2,
                                heartbeat_timeout_seconds=3, touch_sink=lambda _event: None,
                                led_capabilities_sink=lambda: capability_notifications.append(service.led_output_available()))
    service._state = ControllerConnection.CONNECTED
    asyncio.run(service._message(ControllerMessage("capabilities", {"led_count": 12})))
    assert service.led_output_available()
    assert capability_notifications == [True]

    service._new_session()

    assert service.status()["session_generation"] == 1
    assert service.status()["capabilities"] == {"led_count": 12}
    assert service.status()["active_capabilities"] is None
    assert not service.led_output_available()


def test_controller_led_output_availability_accepts_controller_advertised_led_shape():
    service = ControllerService(SimpleNamespace(tasks=None), enabled=True, device="/dev/ttyUSB0", baud_rate=115200,
                                reconnect_delay_seconds=1, heartbeat_interval_seconds=2,
                                heartbeat_timeout_seconds=3, touch_sink=lambda _event: None)
    service._state = ControllerConnection.CONNECTED
    payload = {"capabilities": ["temperature", "pressure", "air_quality", "motion", "led"],
               "led": {"count": 12}}

    asyncio.run(service._message(ControllerMessage("capabilities", payload)))

    assert service.led_output_available()
    assert service.led_output_status()["led_advertised"]
    assert service.led_output_status()["active_capabilities"] == payload


def test_controller_disconnect_retains_diagnostic_capabilities_but_disables_active_led_output():
    service = ControllerService(SimpleNamespace(tasks=None), enabled=True, device="/dev/ttyUSB0", baud_rate=115200,
                                reconnect_delay_seconds=1, heartbeat_interval_seconds=2,
                                heartbeat_timeout_seconds=3, touch_sink=lambda _event: None)
    service._state = ControllerConnection.CONNECTED
    asyncio.run(service._message(ControllerMessage("capabilities", {"led_count": 12})))
    assert service.led_output_available()

    service._state = ControllerConnection.DISCONNECTED
    service._deactivate_session_capabilities()

    assert service.status()["capabilities"] == {"led_count": 12}
    assert service.status()["active_capabilities"] is None
    assert not service.led_output_available()


def test_capability_callback_reapplies_current_led_winner_on_every_session_without_state_event():
    class Transport:
        def __init__(self): self.device, self.writes = "/dev/ttyUSB0", []
        async def write(self, raw): self.writes.append(raw)

    async def exercise():
        holder = {}
        controller = ControllerService(
            SimpleNamespace(tasks=None), enabled=True, device="/dev/ttyUSB0", baud_rate=115200,
            reconnect_delay_seconds=1, heartbeat_interval_seconds=2, heartbeat_timeout_seconds=3,
            touch_sink=lambda _event: None,
            led_capabilities_sink=lambda: asyncio.create_task(
                holder["semantic_led"].reapply(reason="controller_capabilities")),
        )
        semantic_led = SemanticLedService(EventBus(), controller.set_led_animation,
                                          lambda: RobotState.LISTENING)
        holder["semantic_led"] = semantic_led
        await semantic_led.set_intent("robot_state", "listening")

        for generation in range(1, 4):
            transport = Transport()
            controller._transport = transport
            controller._state = ControllerConnection.CONNECTED
            controller._new_session()
            await controller._message(ControllerMessage("capabilities", {"led_count": 12}))
            await asyncio.sleep(0)
            led_writes = [raw for raw in transport.writes if b'"type":"set_led_animation"' in raw]
            assert len(led_writes) == 1
            assert b'"animation":"breathe"' in led_writes[0]
            assert controller.status()["session_generation"] == generation

            # A normal semantic reconciliation remains deduplicated within
            # the session; no RobotState event is needed for recovery.
            await semantic_led.set_intent("robot_state", "listening")
            assert len([raw for raw in transport.writes if b'"type":"set_led_animation"' in raw]) == 1

            controller._transport = None
            controller._deactivate_session_capabilities()
            controller._state = ControllerConnection.DISCONNECTED
            assert not controller.led_output_available()

    asyncio.run(exercise())


def test_new_serial_session_resets_driver_buffers_and_connection_local_decoder():
    class Serial:
        def __init__(self): self.input_reset = self.output_reset = False
        def reset_input_buffer(self): self.input_reset = True
        def reset_output_buffer(self): self.output_reset = True
        def close(self): pass
    serial = Serial()
    transport = SerialControllerTransport("/dev/ttyUSB0", 115200, serial_factory=lambda *_args, **_kwargs: serial)
    transport.open()
    assert serial.input_reset and not serial.output_reset
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


def test_serial_transport_reports_pyserial_write_result_after_flush():
    class Serial:
        def __init__(self): self.writes, self.flushed = [], False
        def write(self, data): self.writes.append(data); return len(data)
        def flush(self): self.flushed = True
        def close(self): pass

    serial = Serial()
    transport = SerialControllerTransport("/dev/ttyUSB0", 115200, serial_factory=lambda *_args, **_kwargs: serial)
    transport.open()
    result = asyncio.run(transport.write(b"test\n"))

    assert result == 5
    assert serial.writes == [b"test\n"]
    assert serial.flushed


def test_controller_retries_capabilities_request_until_first_valid_peer_frame():
    class Transport:
        device = "/dev/ttyUSB0"
        def __init__(self): self.writes = []
        def read(self): time.sleep(.02); return b""
        async def write(self, raw): self.writes.append(raw); return len(raw)

    async def exercise():
        transport = Transport()
        service = ControllerService(SimpleNamespace(tasks=None), enabled=True, device="/dev/ttyUSB0", baud_rate=115200,
                                    reconnect_delay_seconds=1, heartbeat_interval_seconds=10,
                                    heartbeat_timeout_seconds=.8, touch_sink=lambda _event: None)
        service._transport, service._state = transport, ControllerConnection.CONNECTED
        _decoder, next_heartbeat = service._new_session()
        await service._request_capabilities()
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(service._read_connected(JsonLinesDecoder(), next_heartbeat), timeout=.58)
        requests = [raw for raw in transport.writes if b'"type":"request_capabilities"' in raw]
        assert len(requests) >= 2
        assert all(b'"id":"phos-1-cap-' in raw for raw in requests)
        assert len(set(requests)) == len(requests)

    asyncio.run(exercise())


def test_controller_reader_is_active_before_initial_capabilities_request():
    class Transport:
        device = "/dev/ttyUSB0"
        def __init__(self):
            self.events, self.release_read, self.read_entered, self.writes = [], threading.Event(), threading.Event(), []
        def read(self):
            self.events.append("reader")
            self.read_entered.set()
            self.release_read.wait(1)
            return b""
        async def write(self, raw):
            self.events.append("request_capabilities")
            self.writes.append(raw)
            return len(raw)

    async def exercise():
        transport = Transport()
        service = ControllerService(SimpleNamespace(tasks=None), enabled=True, device="/dev/ttyUSB0", baud_rate=115200,
                                    reconnect_delay_seconds=1, heartbeat_interval_seconds=10,
                                    heartbeat_timeout_seconds=3, touch_sink=lambda _event: None)
        service._transport, service._state = transport, ControllerConnection.CONNECTED
        decoder, deadline = service._new_session()
        ready = asyncio.Event()
        reader = asyncio.create_task(service._read_connected(decoder, deadline, reader_ready=ready))
        try:
            await ready.wait()
            await asyncio.to_thread(transport.read_entered.wait)
            await service._request_capabilities()
            assert transport.events.index("reader") < transport.events.index("request_capabilities")
        finally:
            transport.release_read.set()
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)

    asyncio.run(exercise())


def test_controller_settles_once_after_reader_readiness_before_initial_handshake():
    async def exercise():
        service = ControllerService(SimpleNamespace(tasks=None), enabled=True, device="/dev/ttyUSB0", baud_rate=115200,
                                    reconnect_delay_seconds=1, heartbeat_interval_seconds=2,
                                    heartbeat_timeout_seconds=3, touch_sink=lambda _event: None)
        service._session_generation = 4
        service._open_settle_seconds = .5
        observed = []

        async def sleep(seconds): observed.append(("settle", seconds))
        service._sleep = sleep
        ready = asyncio.Event()
        waiter = asyncio.create_task(service._await_reader_and_settle(ready))
        await asyncio.sleep(0)
        assert observed == []
        ready.set()
        await waiter
        assert observed == [("settle", .5)]

    asyncio.run(exercise())


def test_controller_settle_can_be_cancelled_before_any_initial_handshake():
    async def exercise():
        service = ControllerService(SimpleNamespace(tasks=None), enabled=True, device="/dev/ttyUSB0", baud_rate=115200,
                                    reconnect_delay_seconds=1, heartbeat_interval_seconds=2,
                                    heartbeat_timeout_seconds=3, touch_sink=lambda _event: None)
        service._open_settle_seconds = 60
        ready = asyncio.Event(); ready.set()
        waiter = asyncio.create_task(service._await_reader_and_settle(ready))
        await asyncio.sleep(0)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter

    asyncio.run(exercise())


def test_controller_reconfigure_replaces_only_connection_level_transport_and_resets_session():
    class Tasks:
        def create(self, coroutine, *, name): return asyncio.create_task(coroutine, name=name)

    class Transport:
        def __init__(self, device, baud_rate):
            self.device, self.baud_rate, self.closed, self.writes = device, baud_rate, False, []
        def open(self): pass
        def read(self, _size=1024): time.sleep(.01); return b""
        async def write(self, data): self.writes.append(data)
        def close(self): self.closed = True

    transports = []
    def factory(device, baud_rate):
        transport = Transport(device, baud_rate); transports.append(transport); return transport
    disconnected = []
    service = ControllerService(SimpleNamespace(tasks=Tasks()), enabled=True, device="/dev/ttyUSB0", baud_rate=115200,
                                reconnect_delay_seconds=.1, heartbeat_interval_seconds=2,
                                heartbeat_timeout_seconds=3, touch_sink=lambda _event: None,
                                provider_disconnected_sink=lambda: disconnected.append(True), transport_factory=factory)
    service._open_settle_seconds = .001

    async def exercise():
        await service.start(); await asyncio.sleep(.03)
        assert len(transports) == 1 and b'"type":"request_capabilities"' in transports[0].writes[0]
        await service._message(ControllerMessage("capabilities", {"sensors": ["temperature"]}))
        reopened = await service.reconfigure(enabled=True, device="/dev/ttyUSB1", baud_rate=115200,
                                             reconnect_delay_seconds=.2, heartbeat_interval_seconds=4,
                                             heartbeat_timeout_seconds=6)
        assert reopened and transports[0].closed
        assert service.status()["configured_device"] == "/dev/ttyUSB1"
        assert service.status()["capabilities"] is None
        await asyncio.sleep(.03)
        assert len(transports) == 2 and transports[1].device == "/dev/ttyUSB1"
        assert service.status()["active_device"] == "/dev/ttyUSB1"
        assert b'"type":"request_capabilities"' in transports[1].writes[0]
        reopened = await service.reconfigure(enabled=True, device="/dev/ttyUSB1", baud_rate=115200,
                                             reconnect_delay_seconds=.4, heartbeat_interval_seconds=5,
                                             heartbeat_timeout_seconds=7)
        assert not reopened and len(transports) == 2
        assert service._reconnect_delay == .4 and service._heartbeat_interval == 5 and service._heartbeat_timeout == 7
        reopened = await service.reconfigure(enabled=True, device="/dev/ttyUSB1", baud_rate=57600,
                                             reconnect_delay_seconds=.4, heartbeat_interval_seconds=5,
                                             heartbeat_timeout_seconds=7)
        assert reopened and transports[1].closed
        await asyncio.sleep(.03)
        assert len(transports) == 3 and transports[2].baud_rate == 57600
        await service.stop()

    asyncio.run(exercise())
    assert disconnected == [True, True]


def test_controller_reconfigure_keeps_saved_failed_device_and_retries():
    class Tasks:
        def create(self, coroutine, *, name): return asyncio.create_task(coroutine, name=name)

    class FailingTransport:
        def __init__(self, device, _baud_rate): self.device, self.closed = device, False
        def open(self): raise OSError("device unavailable")
        def close(self): self.closed = True

    transports = []
    def factory(device, baud_rate):
        transport = FailingTransport(device, baud_rate); transports.append(transport); return transport
    service = ControllerService(SimpleNamespace(tasks=Tasks()), enabled=False, device="/dev/ttyUSB0", baud_rate=115200,
                                reconnect_delay_seconds=.01, heartbeat_interval_seconds=2,
                                heartbeat_timeout_seconds=3, touch_sink=lambda _event: None, transport_factory=factory)

    async def exercise():
        assert await service.reconfigure(enabled=True, device="/dev/missing", baud_rate=57600,
                                         reconnect_delay_seconds=.01, heartbeat_interval_seconds=2,
                                         heartbeat_timeout_seconds=3)
        await asyncio.sleep(.03)
        assert service.status()["configured_device"] == "/dev/missing"
        assert service.status()["active_device"] is None
        assert service.state is ControllerConnection.DISCONNECTED
        assert service._task is not None and not service._task.done()
        assert len(transports) >= 2 and all(transport.closed for transport in transports)
        await service.stop()

    asyncio.run(exercise())


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
