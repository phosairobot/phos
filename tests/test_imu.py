"""MPU-6050 adapter, worker, runtime and configuration checks without hardware."""
import asyncio
import json
import sys
from types import SimpleNamespace

import pytest

from robot.config import ConfigurationError, RuntimeConfig, load_document
from robot.hardware.mpu6050 import MPU6050Provider
from robot.lifecycle import LifecycleService
from robot.motion import MotionInterpreter, MotionSettings, MotionState
from robot.runtime import build_runtime
from robot.sensors import IMUReading, IMUSensorService
from robot.ui import MemoryEyeDisplay


def motion_settings(**changes):
    values = dict(movement_threshold_m_s2=1.5, tilt_threshold_m_s2=4.0,
                  shake_threshold_deg_s=180, impact_threshold_m_s2=25,
                  confirmation_seconds=1, cooldown_seconds=2)
    values.update(changes)
    return MotionSettings(**values)


def reading(*, x=0, y=0, z=9.80665, gx=0, gy=0, gz=0):
    return IMUReading(x, y, z, gx, gy, gz)


class Bus:
    def __init__(self, identity=0x68, data=None):
        self.identity, self.data, self.calls = identity, data or [0, 0, 0x40, 0, 0xC0, 0, 0, 0, 0, 0x83, 0, 0xFF, 0xFF, 0x7D], []
    def read_byte_data(self, address, register): self.calls.append(("read", address, register)); return self.identity
    def write_byte_data(self, *args): self.calls.append(("write", *args))
    def read_i2c_block_data(self, *args): self.calls.append(("sample", *args)); return self.data
    def close(self): self.calls.append(("close",))


@pytest.fixture
def adapter(monkeypatch):
    bus, opened = Bus(), []
    monkeypatch.setitem(sys.modules, "smbus2", SimpleNamespace(SMBus=lambda number: opened.append(number) or bus))
    return bus, opened


@pytest.mark.parametrize("address", [0x68, 0x69])
def test_adapter_initializes_maps_units_and_closes(adapter, address):
    bus, opened = adapter
    provider = MPU6050Provider(address=address)
    assert not opened
    provider.start()
    assert opened == [1]
    assert ("read", address, 0x75) in bus.calls
    assert ("write", address, 0x6B, 0) in bus.calls
    assert ("write", address, 0x1C, 0) in bus.calls
    assert ("write", address, 0x1B, 0) in bus.calls
    reading = provider.read()
    assert reading.acceleration_y_m_s2 == pytest.approx(9.80665)
    assert reading.acceleration_z_m_s2 == pytest.approx(-9.80665)
    assert reading.angular_velocity_y_deg_s == pytest.approx(255 / 131)
    assert reading.angular_velocity_z_deg_s == pytest.approx(-1)
    provider.close(); provider.close()
    assert bus.calls[-1] == ("close",) and bus.calls.count(("close",)) == 1


@pytest.mark.parametrize("identity,data", [(0, None), (0x68, [0] * 13)])
def test_adapter_rejects_wrong_device_or_incomplete_sample(adapter, identity, data):
    bus, _ = adapter; bus.identity = identity
    provider = MPU6050Provider(address=0x68)
    if identity != 0x68:
        with pytest.raises(ValueError, match="WHO_AM_I"): provider.start()
        assert bus.calls[-1] == ("close",)
    else:
        provider.start(); bus.data = data
        with pytest.raises(OSError, match="incomplete"): provider.read()


class FakeIMU:
    def __init__(self, fail=False): self.starts = self.reads = self.closes = 0; self.fail = fail
    def start(self): self.starts += 1
    def close(self): self.closes += 1
    def read(self):
        self.reads += 1
        if self.fail and self.reads == 1: raise OSError("bus fault")
        return IMUReading(1, 2, 3, 4, 5, 6)


class Steps:
    def __init__(self, count): self.count, self.delays = count, []
    def is_set(self): return False
    def wait(self, delay): self.delays.append(delay); return len(self.delays) >= self.count


def test_imu_service_transient_failure_recovers_and_serializes():
    provider = FakeIMU(fail=True)
    sensor = IMUSensorService(lambda: provider, enabled=True, poll_interval_seconds=5, stale_after_seconds=30,
                               motion_settings=motion_settings())
    sensor._stop = Steps(2); sensor._run()
    state = sensor.snapshot()
    assert state["available"] and state["measurements"] == {"acceleration_x_m_s2": 1, "acceleration_y_m_s2": 2, "acceleration_z_m_s2": 3, "angular_velocity_x_deg_s": 4, "angular_velocity_y_deg_s": 5, "angular_velocity_z_deg_s": 6}
    assert state["calibration"] == "factory_scale_only" and provider.starts == provider.closes == 2
    json.dumps(state, allow_nan=False)


@pytest.mark.parametrize("enabled", [False, True])
def test_runtime_imu_disabled_never_opens_provider(enabled):
    provider, calls = FakeIMU(), []
    def factory(): calls.append(True); return provider
    async def exercise():
        runtime = build_runtime(config=RuntimeConfig(imu_enabled=enabled), eye_display=MemoryEyeDisplay(), imu_provider_factory=factory)
        await runtime.start()
        try:
            for _ in range(100):
                state = runtime.sensor_status()["imu"]
                if state["status"] != "starting": break
                await asyncio.sleep(.01)
            assert state["status"] == ("available" if enabled else "disabled")
        finally: await runtime.stop()
    asyncio.run(exercise()); assert len(calls) == int(enabled)


@pytest.mark.parametrize("field,value", [("enabled", 1), ("i2c_address", "0x67"), ("poll_interval_seconds", 0), ("stale_after_seconds", .05)])
def test_imu_configuration_validation(field, value, tmp_path):
    document = load_document(); document["sensors"]["imu"][field] = value
    with pytest.raises(ConfigurationError): RuntimeConfig.from_dict(document, base_dir=tmp_path)


@pytest.mark.parametrize("field,value", [("movement_threshold_m_s2", 0), ("tilt_threshold_m_s2", float("nan")),
    ("shake_threshold_deg_s", -1), ("impact_threshold_m_s2", 1), ("confirmation_seconds", 0),
    ("cooldown_seconds", -1)])
def test_invalid_motion_configuration_is_rejected(field, value, tmp_path):
    document = load_document(); document["sensors"]["imu"]["motion"][field] = value
    with pytest.raises(ConfigurationError): RuntimeConfig.from_dict(document, base_dir=tmp_path)


def test_imu_restart_policy(tmp_path):
    path = tmp_path / "phos.json"; RuntimeConfig(log_file=None).save(path)
    lifecycle = LifecycleService(path, RuntimeConfig.from_file(path))
    document = load_document(path)
    document["sensors"]["imu"].update(enabled=True, i2c_address="0x69", poll_interval_seconds=10, stale_after_seconds=60)
    path.write_text(json.dumps(document)); result = lifecycle.execute("reload")
    assert result["ok"] and result["applied"] == []
    assert result["restart_required"] == ["sensors.imu.enabled", "sensors.imu.i2c_address",
                                           "sensors.imu.poll_interval_seconds", "sensors.imu.stale_after_seconds"]


def test_motion_thresholds_reload_without_reopening_imu(tmp_path):
    path = tmp_path / "phos.json"; RuntimeConfig(log_file=None).save(path)
    config = RuntimeConfig.from_file(path)
    lifecycle = LifecycleService(path, config)
    calls = []
    lifecycle.register_imu_motion_applier(lambda candidate: calls.append(candidate.imu_motion_impact_threshold_m_s2))
    document = load_document(path); document["sensors"]["imu"]["motion"]["impact_threshold_m_s2"] = 30
    path.write_text(json.dumps(document)); result = lifecycle.execute("reload")
    assert result["ok"] and result["applied"] == ["sensors.imu.motion.impact_threshold_m_s2"]
    assert not result["restart_required"] and calls == [30]


def test_runtime_applies_motion_settings_without_reopening_imu():
    provider = FakeIMU()
    async def exercise():
        runtime = build_runtime(config=RuntimeConfig(imu_enabled=True), eye_display=MemoryEyeDisplay(),
                                imu_provider_factory=lambda: provider)
        await runtime.start()
        try:
            for _ in range(100):
                if runtime.sensor_status()["imu"]["available"]: break
                await asyncio.sleep(.01)
            runtime.apply_imu_motion(RuntimeConfig(imu_motion_impact_threshold_m_s2=30))
            return runtime._imu_service._motion._settings.impact_threshold_m_s2
        finally:
            await runtime.stop()
    assert asyncio.run(exercise()) == 30
    assert provider.starts == provider.closes == 1


def test_interpreter_requires_confirmation_for_still_movement_and_tilts():
    def confirmed(sample):
        interpreter = MotionInterpreter(motion_settings())
        assert interpreter.observe(reading(), 0)[0] is MotionState.STILL
        for now in range(1, 6):
            state, event = interpreter.observe(sample, now)
        return state, event
    assert confirmed(reading(x=-7))[0] is MotionState.TILT_LEFT
    assert confirmed(reading(x=7))[0] is MotionState.TILT_RIGHT
    assert confirmed(reading(y=7))[0] is MotionState.TILT_FORWARD
    assert confirmed(reading(y=-7))[0] is MotionState.TILT_BACK
    assert confirmed(reading(z=13))[0] is MotionState.MOVING


def test_interpreter_filters_noise_and_detects_shake_impact_and_cooldown():
    interpreter = MotionInterpreter(motion_settings(confirmation_seconds=.5, cooldown_seconds=2))
    for now, value in enumerate((.4, -.5, .3, -.4)):
        assert interpreter.observe(reading(x=value), now)[0] is MotionState.STILL
    assert interpreter.observe(reading(gx=220), 5)[0] is MotionState.STILL
    state, event = interpreter.observe(reading(gx=-220), 5.5)
    assert state is MotionState.SHAKE and event.state is MotionState.SHAKE
    state, event = interpreter.observe(reading(z=30), 6)
    assert state is MotionState.IMPACT and event is None  # shake cooldown
    state, event = interpreter.observe(reading(z=30), 8)
    assert state is MotionState.IMPACT and event.state is MotionState.IMPACT


def oriented(axis="x", angle=0, scale=1):
    import math
    values = {axis: 9.80665 * math.sin(math.radians(angle)) * scale,
              "z": 9.80665 * math.cos(math.radians(angle)) * scale}
    return reading(**values)


def feed(interpreter, sample, start=0, count=30):
    return [interpreter.observe(sample(i) if callable(sample) else sample, (start + i) * .05)[0]
            for i in range(count)]


@pytest.mark.parametrize("axis,angle,expected", [
    ("x", -35, MotionState.TILT_LEFT), ("x", 35, MotionState.TILT_RIGHT),
    ("y", 35, MotionState.TILT_FORWARD), ("y", -35, MotionState.TILT_BACK)])
@pytest.mark.parametrize("scale", [1, 1.2])
def test_sustained_normalized_tilts_win_over_movement(axis, angle, expected, scale):
    interpreter = MotionInterpreter(motion_settings(confirmation_seconds=.3))
    feed(interpreter, reading())
    states = feed(interpreter, oriented(axis, angle, scale), start=30)
    assert states[0] is MotionState.STILL  # no instantaneous transition
    assert states[-10:] == [expected] * 10
    assert feed(interpreter, reading(), start=60)[-1] is MotionState.STILL


def test_level_movement_and_vibration():
    interpreter = MotionInterpreter(motion_settings(confirmation_seconds=.3))
    assert feed(interpreter, lambda i: reading(x=.3 * (-1)**i))[-1] is MotionState.STILL
    assert feed(interpreter, reading(z=12), start=30)[-1] is MotionState.MOVING
    assert feed(interpreter, reading(), start=60)[-1] is MotionState.STILL


def test_tilt_hysteresis_and_temporal_confirmation():
    interpreter = MotionInterpreter(motion_settings(confirmation_seconds=.3))
    feed(interpreter, reading())
    # A brief excursion does not establish a tilt or latch the exit threshold.
    feed(interpreter, oriented(angle=40), start=30, count=2)
    assert feed(interpreter, oriented(angle=20), start=32)[-1] is MotionState.STILL
    assert feed(interpreter, oriented(angle=35), start=62)[-1] is MotionState.TILT_RIGHT
    # Noise near the enter threshold cannot release an already confirmed tilt.
    states = feed(interpreter, lambda i: oriented(angle=24 + (-1)**i), start=92)
    assert set(states) == {MotionState.TILT_RIGHT}
    assert feed(interpreter, oriented(angle=15), start=122)[-1] is MotionState.STILL


def test_mounting_mapping_and_zero_gravity():
    interpreter = MotionInterpreter(motion_settings(lateral_axis="-y", forward_axis="x", confirmation_seconds=.3))
    assert feed(interpreter, oriented("y", 35))[-1] is MotionState.TILT_LEFT
    assert feed(interpreter, reading(z=0), start=30, count=60)[-1] is MotionState.MOVING


def test_priority_and_rate_limited_diagnostics(caplog):
    interpreter = MotionInterpreter(motion_settings(confirmation_seconds=.3))
    with caplog.at_level("DEBUG", logger="robot.motion"):
        assert feed(interpreter, oriented(angle=35, scale=1.2))[-1] is MotionState.TILT_RIGHT
        assert feed(interpreter, reading(z=12), start=30)[-1] is MotionState.MOVING
    records = [record.message for record in caplog.records if "IMU motion" in record.message]
    assert len(records) == 3
    assert any("confirmed=tilt_right" in message and "tilt priority" in message for message in records)
    assert any("tilt below enter/exit threshold" in message and "moving_metric=" in message for message in records)
    assert interpreter.observe(reading(x=20, z=20), 3)[0] is MotionState.IMPACT
    interpreter.observe(reading(gx=220), 3.05)
    assert interpreter.observe(reading(gx=-220), 3.1)[0] is MotionState.SHAKE


@pytest.mark.parametrize("changes", [
    {"tilt_threshold_m_s2": 10}, {"tilt_exit_threshold_m_s2": 4},
    {"tilt_exit_threshold_m_s2": 0}, {"tilt_exit_threshold_m_s2": float("nan")},
    {"lateral_axis": "bad"}, {"lateral_axis": "-y"}, {"forward_axis": 1}])
def test_tilt_configuration_validation(changes, tmp_path):
    document = load_document()
    document["sensors"]["imu"]["motion"].update(changes)
    with pytest.raises(ConfigurationError):
        RuntimeConfig.from_dict(document, base_dir=tmp_path)


def test_new_tilt_settings_reload_and_reset_pending_evidence(tmp_path):
    path = tmp_path / "phos.json"
    RuntimeConfig(log_file=None).save(path)
    interpreter = MotionInterpreter(motion_settings())
    calls = []
    from robot.runtime import _motion_settings
    lifecycle = LifecycleService(path, RuntimeConfig.from_file(path))
    def apply(config):
        interpreter.configure(_motion_settings(config))
        calls.append(config)
    lifecycle.register_imu_motion_applier(apply)
    interpreter.observe(oriented(angle=35), 0)
    document = load_document(path)
    document["sensors"]["imu"]["motion"].update(tilt_exit_threshold_m_s2=2.5, confirmation_seconds=.2,
                                                lateral_axis="-y", forward_axis="x")
    path.write_text(json.dumps(document))
    result = lifecycle.execute("reload")
    assert result["ok"] and not result["restart_required"] and len(result["applied"]) == 4
    assert calls and interpreter._candidate is None
    assert feed(interpreter, oriented("y", 35))[-1] is MotionState.TILT_LEFT


def test_reload_reconfirms_hysteresis_for_unchanged_tilt():
    settings = motion_settings(confirmation_seconds=.3)
    interpreter = MotionInterpreter(settings)
    assert feed(interpreter, oriented(angle=35))[-1] is MotionState.TILT_RIGHT
    interpreter.configure(settings)
    feed(interpreter, oriented(angle=35), start=30)
    assert set(feed(interpreter, oriented(angle=20), start=60)) == {MotionState.TILT_RIGHT}


def test_tilt_state_reaches_service_sink_even_during_event_cooldown():
    now = [0.0]
    sample = [reading()]
    states = []
    service = IMUSensorService(lambda: None, enabled=True, poll_interval_seconds=.05,
                               stale_after_seconds=30, motion_settings=motion_settings(confirmation_seconds=.3),
                               motion_state_sink=states.append, clock=lambda: now[0])
    provider = SimpleNamespace(read=lambda: sample[0])
    for value in (reading(z=12), oriented(angle=35), reading()):
        sample[0] = value
        for _ in range(20):
            service._read(provider)
            now[0] += .05
    assert states == [MotionState.STILL, MotionState.MOVING, MotionState.TILT_RIGHT, MotionState.STILL]


def test_controller_orientation_recomputes_tilt_and_neutral_state_from_current_gravity():
    now = [0.0]
    states = []
    service = IMUSensorService(lambda: None, enabled=True, poll_interval_seconds=.05,
                               stale_after_seconds=30, externally_managed=True,
                               motion_settings=motion_settings(confirmation_seconds=.3),
                               motion_state_sink=states.append, clock=lambda: now[0])

    def controller_samples(sample, count=20):
        for _ in range(count):
            service.accept_controller_orientation(sample)
            now[0] += .05

    controller_samples(reading())
    controller_samples(oriented(angle=-35))
    assert service.snapshot()["motion_state"] == "tilt_left"
    # Returning to level is a new current-orientation classification, not an
    # inactivity timeout or a firmware semantic-event dependency.
    controller_samples(reading())
    snapshot = service.snapshot()
    assert snapshot["motion_state"] == "still"
    assert snapshot["tilt_direction"] is None
    assert states == [MotionState.STILL, MotionState.TILT_LEFT, MotionState.STILL]


def test_controller_orientation_confirmation_does_not_overwrite_newer_semantic_motion_state():
    now = [0.0]
    service = IMUSensorService(lambda: None, enabled=True, poll_interval_seconds=.05,
                               stale_after_seconds=30, externally_managed=True,
                               motion_settings=motion_settings(confirmation_seconds=.3),
                               clock=lambda: now[0])
    # The first physical tilt sample starts host confirmation, but firmware's
    # semantic transition arrives immediately afterwards.
    service.accept_controller_orientation(oriented(angle=-35))
    service.accept_controller_motion(MotionState.TILT_LEFT, "low")
    assert service.snapshot()["motion_state"] == "tilt_left"
    # Until host confirmation completes, MotionInterpreter still returns its
    # previous state.  That historical result must not overwrite the newer
    # semantic state exposed to the dashboard.
    now[0] = .1
    service.accept_controller_orientation(oriented(angle=-35))
    assert service.snapshot()["motion_state"] == "tilt_left"
    now[0] = .35
    service.accept_controller_orientation(oriented(angle=-35))
    assert service.snapshot()["motion_state"] == "tilt_left"

    service.accept_controller_orientation(reading())
    service.accept_controller_motion(MotionState.STILL, "low")
    assert service.snapshot()["motion_state"] == "still"
    now[0] = .45
    service.accept_controller_orientation(reading())
    assert service.snapshot()["motion_state"] == "still"


def test_noise_around_enter_does_not_confirm_tilt():
    interpreter = MotionInterpreter(motion_settings(confirmation_seconds=.3))
    feed(interpreter, reading())
    states = feed(interpreter, lambda i: oriented(angle=24 + (-1)**i), start=30, count=100)
    assert set(states) == {MotionState.STILL}


def test_motion_editor_exposes_canonical_tuning_and_mounting():
    from robot.web.configuration import editor_sections
    groups = editor_sections(RuntimeConfig().to_dict())
    motion = next(group for group in groups if group["name"] == "sensors.imu.motion")
    fields = {field["name"].split(".")[-1]: field for field in motion["fields"]}
    assert {"tilt_threshold_m_s2", "tilt_exit_threshold_m_s2", "confirmation_seconds"} <= fields.keys()
    assert fields["lateral_axis"]["choices"] == ("x", "-x", "y", "-y", "z", "-z")
    assert not any("filter" in name for name in fields)
