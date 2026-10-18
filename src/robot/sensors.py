"""Provider-neutral measurements and the shared bounded sensor worker.

One worker per enabled sensor owns its provider. No driver work runs on the asyncio/render thread,
and readers only copy a small in-memory snapshot. There is no sample queue.
"""
from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import logging
import math
from threading import Event, Lock, Thread
import time
from typing import Callable, Protocol

from robot.motion import MotionInterpreter, MotionSettings, tilt_direction

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EnvironmentalReading:
    temperature_c: float
    humidity_percent: float | None
    pressure_hpa: float

    def __post_init__(self):
        values = (self.temperature_c, self.pressure_hpa)
        if self.humidity_percent is not None:
            values += (self.humidity_percent,)
        for value in values:
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError("Environmental measurements must be finite numbers")
        if (self.temperature_c < -273.15 or self.pressure_hpa <= 0
                or (self.humidity_percent is not None and not 0 <= self.humidity_percent <= 100)):
            raise ValueError("Invalid environmental measurement range")

    @property
    def relative_humidity_percent(self):
        """Compatibility alias for existing Python consumers."""
        return self.humidity_percent


class EnvironmentalSensorProvider(Protocol):
    """Synchronous device boundary; all calls occur on the owning worker."""

    def start(self) -> None: ...
    def read(self) -> EnvironmentalReading: ...
    def close(self) -> None: ...


class SensorNotReady(Exception):
    """Expected device readiness, distinct from faults requiring reconnect."""

    def __init__(self, message, *, warming_up=False):
        super().__init__(message)
        self.status = "warming_up" if warming_up else "unavailable"


@dataclass(frozen=True)
class AirQualityReading:
    eco2_ppm: int
    tvoc_ppb: int

    def __post_init__(self):
        if (type(self.eco2_ppm) is not int or self.eco2_ppm < 0
                or type(self.tvoc_ppb) is not int or self.tvoc_ppb < 0):
            raise ValueError("Invalid air-quality measurements")


@dataclass(frozen=True)
class EnvironmentalCompensation:
    temperature_c: float
    humidity_percent: float

    def __post_init__(self):
        for value in (self.temperature_c, self.humidity_percent):
            if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
                raise ValueError("Compensation must contain finite numbers")
        if not -25 <= self.temperature_c <= 50 or not 0 <= self.humidity_percent <= 100:
            raise ValueError("Compensation outside supported range")


class AirQualitySensorProvider(Protocol):
    def start(self) -> None: ...
    def set_compensation(self, values: EnvironmentalCompensation | None) -> None: ...
    def read(self) -> AirQualityReading: ...
    def close(self) -> None: ...


@dataclass(frozen=True)
class IMUReading:
    """Six-axis motion sample in SI acceleration and degrees-per-second rotation."""

    acceleration_x_m_s2: float
    acceleration_y_m_s2: float
    acceleration_z_m_s2: float
    angular_velocity_x_deg_s: float
    angular_velocity_y_deg_s: float
    angular_velocity_z_deg_s: float

    def __post_init__(self):
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
               for value in (self.acceleration_x_m_s2, self.acceleration_y_m_s2, self.acceleration_z_m_s2,
                             self.angular_velocity_x_deg_s, self.angular_velocity_y_deg_s,
                             self.angular_velocity_z_deg_s)):
            raise ValueError("IMU measurements must be finite numbers")


class IMUSensorProvider(Protocol):
    def start(self) -> None: ...
    def read(self) -> IMUReading: ...
    def close(self) -> None: ...


class _SensorService:
    """Shared worker, freshness, retry and shutdown implementation."""
    _label = "Sensor"

    def __init__(self, provider_factory: Callable, *,
                 enabled: bool, poll_interval_seconds: float, stale_after_seconds: float,
                 sensor_type="bme280",
                 available_measurements=("temperature_c", "humidity_percent", "pressure_hpa"),
                 reading_sink=lambda reading, now: None, unavailable_sink=lambda status: None, clock=time.monotonic):
        self._sensor_type = sensor_type
        self._measurements = tuple(available_measurements)
        self._factory = provider_factory
        self._enabled = enabled
        self._interval = poll_interval_seconds
        self._stale_after = stale_after_seconds
        self._clock = clock
        self._reading_sink = reading_sink
        self._unavailable_sink = unavailable_sink
        self._lock = Lock()
        self._stop = Event()
        self._thread = None
        self._status = "disabled" if not enabled else "starting"
        self._reading = None
        self._updated_at = None
        self._sample_time = None
        self._started_at = None
        self._last_poll = None
        self._error = None

    async def start(self):
        if not self._enabled:
            return
        if self._thread is not None and self._thread.is_alive():
            raise RuntimeError("Sensor service is already running or still stopping")
        self._stop.clear()
        with self._lock:
            self._started_at = self._clock()
            self._reading = self._updated_at = self._sample_time = self._error = None
            self._last_poll = None
            self._status = "starting"
        self._thread = Thread(target=self._run, name=f"phos-{self._sensor_type}", daemon=True)
        self._thread.start()

    async def stop(self):
        self._stop.set()
        with self._lock:
            if self._enabled:
                self._status = "stopped"
        # Native I2C calls cannot be cancelled safely. Never close a bus while a
        # read is in flight, spawn replacement workers, or block runtime exit.
        deadline = time.monotonic() + 1.0
        while self._thread is not None and self._thread.is_alive() and time.monotonic() < deadline:
            await asyncio.sleep(.01)
        if self._thread is not None and self._thread.is_alive():
            logger.warning("Sensor worker still in I/O at shutdown; cleanup will follow when it returns")

    def snapshot(self) -> dict:
        with self._lock:
            age = None if self._sample_time is None else max(0.0, self._clock() - self._sample_time)
            status = self._status
            error = self._error
            if status == "available" and age >= self._stale_after:
                status, error = "stale", "No fresh reading within the stale-data timeout"
            elif status == "starting" and self._started_at is not None and self._clock() - self._started_at >= self._stale_after:
                status, error = "unavailable", "Sensor initialization has not produced a reading"
            elif status == "warming_up" and self._last_poll is not None and self._clock() - self._last_poll >= self._stale_after:
                status, error = "unavailable", "Sensor readiness polling stopped"
            available = status == "available"
            return {"sensor_type": self._sensor_type,
                    "available_measurements": list(self._measurements), "status": status, "available": available,
                    "measurements": asdict(self._reading) if available else None,
                    "last_update": self._updated_at, "age_seconds": age, "error": error,
                    **self._snapshot_details(status)}

    def _snapshot_details(self, status):
        return {}

    @staticmethod
    def _close(provider):
        if provider is not None:
            try:
                provider.close()
            except Exception:
                logger.debug("Sensor cleanup failed", exc_info=True)

    def _read(self, provider):
        raise NotImplementedError

    def _run(self):
        provider = None
        retry = self._interval
        last_warning = last_readiness_log = float("-inf")
        recovering = False
        try:
            while not self._stop.is_set():
                try:
                    if provider is None:
                        provider = self._factory()
                        provider.start()
                    if self._stop.is_set():
                        break
                    reading = self._read(provider)
                    with self._lock:
                        if self._stop.is_set():
                            break
                        first_reading = self._reading is None
                        self._reading = reading
                        self._sample_time = self._clock()
                        self._updated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
                        self._status, self._error = "available", None
                    self._reading_sink(reading, self._sample_time)
                    if first_reading:
                        logger.info("%s available", self._label)
                    elif recovering:
                        logger.info("%s recovered", self._label)
                    logger.debug("%s reading: %s", self._label, reading)
                    recovering = False
                    retry = self._interval
                    delay = self._interval
                except SensorNotReady as error:
                    with self._lock:
                        if self._stop.is_set():
                            break
                        previous = self._status
                        self._status, self._error = error.status, str(error)
                        self._last_poll = self._clock()
                    self._unavailable_sink(error.status)
                    if previous != error.status and self._clock() - last_readiness_log >= 60:
                        logger.warning("%s %s: %s", self._label, error.status, error)
                        last_readiness_log = self._clock()
                    # Preserve device/conditioning, never restart on DATA_READY=0.
                    # Ordinary readiness polling does not log recovery per sample.
                    retry = self._interval
                    delay = self._interval
                except Exception as error:
                    with self._lock:
                        if self._stop.is_set():
                            break
                        self._status, self._error = "unavailable", type(error).__name__
                    self._unavailable_sink("unavailable")
                    now = self._clock()
                    if now - last_warning >= 60:
                        logger.warning("%s unavailable (%s, %s: %s); retrying",
                                       self._label, self._sensor_type, type(error).__name__, error)
                        last_warning = now
                    self._close(provider)
                    provider = None
                    recovering = True
                    delay = retry
                    retry = min(retry * 2, max(60, self._interval))
                if self._stop.wait(delay):
                    break
        finally:
            self._close(provider)


class EnvironmentalSensorService(_SensorService):
    _label = "Environmental sensor"

    def _read(self, provider):
        reading = provider.read()
        if not isinstance(reading, EnvironmentalReading):
            raise ValueError("Provider did not return an environmental reading")
        if (reading.humidity_percent is not None) != ("humidity_percent" in self._measurements):
            raise ValueError("Provider humidity does not match configured capabilities")
        return reading

    def compensation(self) -> EnvironmentalCompensation | None:
        """Only fresh, available temperature AND humidity may cross services."""
        state = self.snapshot()
        if not state["available"] or "humidity_percent" not in state["available_measurements"]:
            return None
        values = state["measurements"]
        try:
            return EnvironmentalCompensation(values["temperature_c"], values["humidity_percent"])
        except ValueError:
            return None


class AirQualitySensorService(_SensorService):
    _label = "Air-quality sensor"

    def __init__(self, provider_factory: Callable[[], AirQualitySensorProvider], *,
                 enabled, poll_interval_seconds, stale_after_seconds,
                 compensation_supplier=lambda: None, reading_sink=lambda reading, now: None,
                 unavailable_sink=lambda status: None, clock=time.monotonic):
        super().__init__(provider_factory, enabled=enabled,
                         poll_interval_seconds=poll_interval_seconds,
                         stale_after_seconds=stale_after_seconds, sensor_type="ccs811",
                         available_measurements=("eco2_ppm", "tvoc_ppb"), reading_sink=reading_sink,
                         unavailable_sink=unavailable_sink, clock=clock)
        self._compensation_supplier = compensation_supplier
        self._compensation_source = None

    def _read(self, provider):
        values = self._compensation_supplier()
        provider.set_compensation(values)
        source = "environmental" if values is not None else "device_defaults"
        with self._lock:
            changed = source != self._compensation_source
            self._compensation_source = source
        if changed:
            logger.debug("Air-quality compensation input: %s", source)
        reading = provider.read()
        if not isinstance(reading, AirQualityReading):
            raise ValueError("Provider did not return an air-quality reading")
        return reading

    def _snapshot_details(self, status):
        # Called under the snapshot lock. This is the input last written,
        # not instantaneous algorithm use for the displayed measurement.
        return {"compensation_input": self._compensation_source
                if status in {"available", "warming_up"} else None}


class IMUSensorService(_SensorService):
    _label = "IMU sensor"

    def __init__(self, provider_factory: Callable[[], IMUSensorProvider], *,
                 enabled, poll_interval_seconds, stale_after_seconds, motion_settings: MotionSettings,
                 motion_state_sink=lambda state: None, clock=time.monotonic):
        super().__init__(provider_factory, enabled=enabled,
                         poll_interval_seconds=poll_interval_seconds,
                         stale_after_seconds=stale_after_seconds, sensor_type="mpu6050",
                         available_measurements=("acceleration_x_m_s2", "acceleration_y_m_s2", "acceleration_z_m_s2",
                                                 "angular_velocity_x_deg_s", "angular_velocity_y_deg_s",
                                                 "angular_velocity_z_deg_s"), clock=clock)
        self._motion = MotionInterpreter(motion_settings)
        self._motion_state = None
        self._motion_last_event = None
        self._motion_state_sink = motion_state_sink

    def configure_motion(self, settings: MotionSettings):
        """Update interpretation parameters without touching the IMU provider."""
        with self._lock:
            self._motion.configure(settings)

    def _read(self, provider):
        reading = provider.read()
        if not isinstance(reading, IMUReading):
            raise ValueError("Provider did not return an IMU reading")
        with self._lock:
            state, event = self._motion.observe(reading, self._clock())
            changed = state is not self._motion_state
            self._motion_state = state
            if event is not None:
                self._motion_last_event = {"state": event.state.value,
                                           "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        if changed:
            self._motion_state_sink(state)
        return reading

    def _snapshot_details(self, status):
        active = status == "available"
        state = self._motion_state if active else None
        return {"calibration": "factory_scale_only" if status in {"available", "starting"} else None,
                "motion_state": state.value if state is not None else None,
                "tilt_direction": tilt_direction(state) if state is not None else None,
                "last_motion_event": self._motion_last_event if active else None}
