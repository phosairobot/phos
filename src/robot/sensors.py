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

from robot.motion import MotionInterpreter, MotionSettings, MotionState, tilt_direction

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
    # ENS160 UBA air-quality index.  It is deliberately optional so existing
    # Controller telemetry supplies ENS160 eCO2/TVOC/AQI and AHT21 compensation.
    aqi_uba: int | None = None

    def __post_init__(self):
        if (type(self.eco2_ppm) is not int or self.eco2_ppm < 0
                or type(self.tvoc_ppb) is not int or self.tvoc_ppb < 0):
            raise ValueError("Invalid air-quality measurements")
        if self.aqi_uba is not None and (type(self.aqi_uba) is not int or not 1 <= self.aqi_uba <= 5):
            raise ValueError("ENS160 AQI-UBA must be an integer from 1 to 5")


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
                 reading_sink=lambda reading, now: None, unavailable_sink=lambda status: None, status_sink=lambda: None, clock=time.monotonic,
                 externally_managed=False):
        self._sensor_type = sensor_type
        self._measurements = tuple(available_measurements)
        self._factory = provider_factory
        self._enabled = enabled
        self._interval = poll_interval_seconds
        self._stale_after = stale_after_seconds
        self._clock = clock
        self._reading_sink = reading_sink
        self._unavailable_sink = unavailable_sink
        self._status_sink = status_sink
        self._externally_managed = externally_managed
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
        if self._externally_managed:
            with self._lock:
                self._started_at = self._clock()
                self._status = "starting"
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
            measurements = (dict(self._reading) if isinstance(self._reading, dict)
                            else asdict(self._reading) if available else None)
            return {"sensor_type": self._sensor_type,
                    "available_measurements": list(self._measurements), "status": status, "available": available,
                    "measurements": measurements,
                    "last_update": self._updated_at, "age_seconds": age, "error": error,
                    **self._snapshot_details(status)}

    def _snapshot_details(self, status):
        return {}

    def _accept_external_fields(self, values: dict[str, object]) -> None:
        """Merge a controller push into this service's existing snapshot state."""
        with self._lock:
            current = dict(self._reading) if isinstance(self._reading, dict) else {}
            current.update(values)
            self._reading = current
            self._sample_time = self._clock()
            self._updated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            self._status, self._error = "available", None
        self._status_sink()

    def mark_external_unavailable(self) -> None:
        """Reflect controller loss through the normal sensor availability boundary."""
        with self._lock:
            if not self._externally_managed or not self._enabled:
                return
            self._status, self._error = "unavailable", "External sensor provider disconnected"
        self._unavailable_sink("unavailable")
        self._status_sink()

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
                    self._status_sink()
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
                    self._status_sink()
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
                    self._status_sink()
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

    def accept_controller_update(self, name: str, value: float) -> None:
        field = {"temperature": "temperature_c", "pressure": "pressure_hpa", "humidity": "humidity_percent"}[name]
        self._accept_external_fields({field: value})
        measurements = self.snapshot()["measurements"]
        if measurements is None or not {"temperature_c", "pressure_hpa"} <= measurements.keys():
            return
        reading = EnvironmentalReading(measurements["temperature_c"],
                                      measurements.get("humidity_percent"),
                                      measurements["pressure_hpa"])
        try:
            # Match the local-provider callback contract: a complete typed
            # environmental reading, never an individual controller scalar.
            self._reading_sink(reading, self._clock())
        except Exception as error:
            # The semantic snapshot is already valid. Do not turn a UI/event
            # notification failure into a rejected controller sample.
            logger.warning("Environmental controller notification failure field=%s exception=%s message=%s",
                           field, type(error).__name__, str(error)[:240])
            logger.debug("Environmental controller notification traceback", exc_info=True)

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
                 unavailable_sink=lambda status: None, status_sink=lambda: None, clock=time.monotonic, externally_managed=False):
        super().__init__(provider_factory, enabled=enabled,
                         poll_interval_seconds=poll_interval_seconds,
                         stale_after_seconds=stale_after_seconds, sensor_type="ens160",
                         available_measurements=("eco2_ppm", "tvoc_ppb", "aqi_uba", "aht21_humidity_percent", "aht21_temperature_c"), reading_sink=reading_sink,
                         unavailable_sink=unavailable_sink, status_sink=status_sink, clock=clock, externally_managed=externally_managed)
        self._compensation_supplier = compensation_supplier
        self._compensation_source = None
        self._field_times = {}
        self._status_time = None
        self._controller_generation = None
        self._retired_generations = set()

    def activate_controller_generation(self, generation: int) -> None:
        with self._lock:
            if self._controller_generation == generation:
                return
            self._controller_generation = generation
            self._retired_generations.discard(generation)
            self._field_times.clear(); self._status_time = None
            self._status, self._error = "starting", "Awaiting current controller telemetry"

    def retire_controller_generation(self, generation: int) -> None:
        with self._lock:
            self._retired_generations.add(generation)
            if self._controller_generation != generation:
                return
            self._controller_generation = None
            self._status_time = None
            self._status, self._error = "unavailable", "External sensor provider disconnected"

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
        now = self._clock()
        return {"compensation_input": self._compensation_source if status in {"available", "warming_up"} else None,
                "measurement_age_seconds": {name: max(0.0, now - at) for name, at in self._field_times.items()},
                "status_age_seconds": None if self._status_time is None else max(0.0, now - self._status_time)}

    def snapshot(self) -> dict:
        """Keep legacy aggregate fields, but make each controller field authoritative."""
        state = super().snapshot()
        now = self._clock()
        with self._lock:
            values = dict(self._reading) if isinstance(self._reading, dict) else {}
            ages = {name: max(0.0, now - at) for name, at in self._field_times.items()}
            validity = values.get("ens160_validity")
            status_fresh = self._status_time is not None and now - self._status_time < self._stale_after
            normal_after = self._status_time if validity == 0 else None
            fresh = {name: age < self._stale_after for name, age in ages.items()}
            gas = ("eco2_ppm", "tvoc_ppb", "aqi_uba")
            usable = {name: fresh.get(name, False) for name in values}
            if validity is not None:
                for name in gas:
                    usable[name] = bool(fresh.get(name) and status_fresh and normal_after is not None
                                        and self._field_times.get(name, float("-inf")) >= normal_after)
            state["measurement_age_seconds"] = ages
            state["measurement_fresh"] = fresh
            state["measurement_usable"] = usable
            state["ens160_validity"] = validity
            state["ens160_status_fresh"] = status_fresh if validity is not None else None
        return state

    def accept_controller_update(self, name: str, value: int, *, generation=None) -> None:
        if generation is not None and generation != self._controller_generation:
            return
        if generation is not None: self._controller_generation = generation
        field = {"eco2": "eco2_ppm", "tvoc": "tvoc_ppb", "aqi": "aqi_uba",
                 "humidity": "aht21_humidity_percent", "aht21_temperature": "aht21_temperature_c"}[name]
        if name in {"humidity", "aht21_temperature"}:
            # AHT21 data is independently useful.  Do not make an ENS160 that
            # is warming up, invalid, or stale appear available merely because
            # its separate compensation sensor published a fresh value.
            with self._lock:
                current = dict(self._reading) if isinstance(self._reading, dict) else {}
                current[field] = value
                self._reading = current
                self._updated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            self._status_sink()
        else:
            self._accept_external_fields({field: value})
        with self._lock:
            self._field_times[field] = self._clock()
        measurements = self.snapshot()["measurements"]
        if name in {"eco2", "tvoc", "aqi"} and measurements is not None and {"eco2_ppm", "tvoc_ppb"} <= measurements.keys():
            self._reading_sink(AirQualityReading(measurements["eco2_ppm"], measurements["tvoc_ppb"],
                                                 measurements.get("aqi_uba")), self._clock())

    def accept_controller_status(self, status: dict, *, generation=None) -> None:
        """Receive additive phos-hw v1 ENS160 status without fabricating a sample."""
        if generation is not None and generation != self._controller_generation:
            return
        if generation is not None: self._controller_generation = generation
        validity = status.get("validity")
        state = {0: "available", 1: "warming_up", 2: "starting", 3: "invalid"}[validity]
        with self._lock:
            self._reading = {**(self._reading or {}), "ens160_validity": validity,
                             "ens160_compensation_active": status.get("compensation_active")}
            self._status_time = self._clock()
            self._updated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            self._status, self._error = state, None if validity == 0 else "ENS160 validity is not normal"
        self._status_sink()

class IMUSensorService(_SensorService):
    _label = "IMU sensor"

    def __init__(self, provider_factory: Callable[[], IMUSensorProvider], *,
                 enabled, poll_interval_seconds, stale_after_seconds, motion_settings: MotionSettings,
                 motion_state_sink=lambda state: None, status_sink=lambda: None, clock=time.monotonic, externally_managed=False):
        super().__init__(provider_factory, enabled=enabled,
                         poll_interval_seconds=poll_interval_seconds,
                         stale_after_seconds=stale_after_seconds, sensor_type="mpu6050",
                         available_measurements=("acceleration_x_m_s2", "acceleration_y_m_s2", "acceleration_z_m_s2",
                                                 "angular_velocity_x_deg_s", "angular_velocity_y_deg_s",
                                                 "angular_velocity_z_deg_s"), clock=clock,
                         externally_managed=externally_managed, status_sink=status_sink)
        self._motion = MotionInterpreter(motion_settings)
        self._motion_state = None
        # The interpreter and a controller semantic message may report the
        # same physical transition at slightly different times.  Keep the
        # interpreter's own last result separately so an in-progress
        # confirmation cannot re-publish its historical state over a newer
        # controller motion_update.
        self._interpreted_motion_state = None
        self._motion_intensity = None
        self._motion_last_event = None
        self._motion_state_sink = motion_state_sink
        self._controller_generation = None
        self._retired_controller_generations = set()

    def activate_controller_generation(self, generation: int) -> None:
        """Start a controller-owned IMU session; telemetry cannot do this itself."""
        with self._lock:
            if self._controller_generation == generation:
                return
            if generation in self._retired_controller_generations:
                return
            self._controller_generation = generation
            self._status, self._error = "starting", "Awaiting current controller telemetry"
            self._sample_time = None
            self._motion_state = self._motion_intensity = self._motion_last_event = None

    def retire_controller_generation(self, generation: int) -> None:
        """Immediately make the active controller IMU session unusable."""
        clear_visual_motion = False
        with self._lock:
            self._retired_controller_generations.add(generation)
            if self._controller_generation != generation:
                return
            clear_visual_motion = self._motion_state is not None
            self._controller_generation = None
            self._status, self._error = "unavailable", "External sensor provider disconnected"
            self._sample_time = None
            self._motion_state = self._motion_intensity = self._motion_last_event = None
        if clear_visual_motion:
            self._motion_state_sink(MotionState.STILL)
        self._status_sink()

    def mark_external_unavailable(self) -> None:
        """Clear a controller-derived visual motion state on transport loss."""
        clear_visual_motion = False
        with self._lock:
            if self._externally_managed and self._enabled:
                clear_visual_motion = self._motion_state is not None
                self._motion_state = self._motion_intensity = self._motion_last_event = None
        super().mark_external_unavailable()
        if clear_visual_motion:
            self._motion_state_sink(MotionState.STILL)

    def _accepts_controller_generation(self, generation: int | None) -> bool:
        return (generation is None or (generation == self._controller_generation
                                       and generation not in self._retired_controller_generations))

    def configure_motion(self, settings: MotionSettings):
        """Update interpretation parameters without touching the IMU provider."""
        with self._lock:
            self._motion.configure(settings)

    def _read(self, provider):
        reading = provider.read()
        if not isinstance(reading, IMUReading):
            raise ValueError("Provider did not return an IMU reading")
        changed, state = self._observe_motion(reading)
        if changed:
            self._motion_state_sink(state)
        return reading

    def _observe_motion(self, reading: IMUReading) -> tuple[bool, MotionState]:
        """Classify a current acceleration sample without relying on event history."""
        with self._lock:
            state, event = self._motion.observe(reading, self._clock())
            interpreter_changed = state is not self._interpreted_motion_state
            self._interpreted_motion_state = state
            changed = interpreter_changed and state is not self._motion_state
            if changed:
                self._motion_state = state
            if event is not None:
                self._motion_last_event = {"state": event.state.value,
                                           "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        return changed, state

    def accept_controller_orientation(self, reading: IMUReading, *, generation: int | None = None) -> None:
        if not isinstance(reading, IMUReading):
            raise ValueError("Controller orientation must be an IMU reading")
        if not self._accepts_controller_generation(generation):
            return
        # Controller orientation is a continuous physical observation.  It
        # must therefore classify both entering and leaving a tilt; treating
        # only motion_update as authoritative leaves a stale tilt latched when
        # firmware suppresses an unchanged/neutral semantic event.
        changed, state = self._observe_motion(reading)
        self._accept_external_fields(asdict(reading))
        if changed:
            self._motion_state_sink(state)

    def accept_controller_motion(self, state: MotionState, intensity: str, *, generation: int | None = None) -> None:
        if not isinstance(state, MotionState) or intensity not in {"low", "medium", "high"}:
            raise ValueError("Invalid controller semantic motion")
        if not self._accepts_controller_generation(generation):
            return
        now = self._clock()
        with self._lock:
            changed = state is not self._motion_state
            self._motion_state, self._motion_intensity = state, intensity
            self._motion_last_event = {"state": state.value,
                                       "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        self._accept_external_fields({})
        if changed:
            self._motion_state_sink(state)

    def _snapshot_details(self, status):
        active = status == "available"
        state = self._motion_state if active else None
        return {"calibration": "factory_scale_only" if status in {"available", "starting"} else None,
                "motion_state": state.value if state is not None else None,
                "motion_intensity": self._motion_intensity if active else None,
                "tilt_direction": tilt_direction(state) if state is not None else None,
                "last_motion_event": self._motion_last_event if active else None}
