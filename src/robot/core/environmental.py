"""Stable, provider-neutral interpretation of environmental measurements."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import logging
from typing import Callable

logger = logging.getLogger(__name__)


class EnvironmentalState(str, Enum):
    NORMAL = "normal"
    COLD = "cold"
    WARM = "warm"
    AIR_QUALITY_WARNING = "air_quality_warning"
    AIR_QUALITY_BAD = "air_quality_bad"


class TemperatureOverlay(str, Enum):
    NONE = "none"
    COLD = "cold"
    WARM = "warm"


class AirQualityOverlay(str, Enum):
    NONE = "none"
    WARNING = "warning"
    BAD = "bad"


@dataclass(frozen=True)
class EnvironmentalSettings:
    enabled: bool = False
    cold_enter_temperature: float = 17.0
    cold_exit_temperature: float = 18.0
    warm_enter_temperature: float = 27.0
    warm_exit_temperature: float = 26.0
    air_quality_warning_eco2: int = 1200
    air_quality_warning_tvoc: int = 220
    air_quality_bad_eco2: int = 2000
    air_quality_bad_tvoc: int = 660
    confirmation_seconds: float = 30.0
    recovery_seconds: float = 60.0

    def __post_init__(self):
        if not self.cold_enter_temperature < self.cold_exit_temperature < self.warm_exit_temperature < self.warm_enter_temperature:
            raise ValueError("Environmental temperature thresholds must include hysteresis")
        if not (0 < self.air_quality_warning_eco2 < self.air_quality_bad_eco2 and 0 < self.air_quality_warning_tvoc < self.air_quality_bad_tvoc):
            raise ValueError("Environmental air-quality thresholds must ascend")
        if self.confirmation_seconds <= 0 or self.recovery_seconds <= 0:
            raise ValueError("Environmental confirmation and recovery must be positive")


class EnvironmentalInterpreter:
    """Debounce environmental context; missing data never creates an alarm."""

    def __init__(self, settings: EnvironmentalSettings, sink: Callable[[EnvironmentalState, str], None] = lambda *_: None):
        self._settings, self._sink = settings, sink
        self._state = EnvironmentalState.NORMAL
        self._temperature_overlay = TemperatureOverlay.NONE
        self._air_quality_overlay = AirQualityOverlay.NONE
        self._temperature_candidate = self._air_quality_candidate = None
        self._temperature_candidate_since = self._air_quality_candidate_since = None
        self._temperature = None
        self._eco2 = self._tvoc = None
        self._environmental_status = "unavailable"
        self._air_quality_status = "unavailable"
        self._reason = "environmental behavior disabled" if not settings.enabled else "awaiting readings"
        self._last_diagnostic_at = float("-inf")

    @property
    def state(self): return self._state

    @property
    def reason(self): return self._reason

    @property
    def temperature_overlay(self): return self._temperature_overlay

    @property
    def air_quality_overlay(self): return self._air_quality_overlay

    def configure(self, settings: EnvironmentalSettings):
        self._settings = settings
        self._temperature_candidate = self._air_quality_candidate = None
        self._temperature_candidate_since = self._air_quality_candidate_since = None
        if not settings.enabled:
            self._set(EnvironmentalState.NORMAL, "environmental behavior disabled")

    def observe_environmental(self, temperature_c, *, now: float):
        if temperature_c is not None:
            self._temperature = float(temperature_c)
            self._environmental_status = "available"
        self._evaluate(now)

    def observe_air_quality(self, eco2_ppm, tvoc_ppb, *, now: float):
        if eco2_ppm is not None and tvoc_ppb is not None:
            self._eco2, self._tvoc = int(eco2_ppm), int(tvoc_ppb)
            self._air_quality_status = "available"
        self._evaluate(now)

    def unavailable(self, *, now: float, source="all", status="unavailable"):
        """Invalidate only the source which has ceased providing fresh data."""
        if source == "environmental":
            self._temperature = None
            self._environmental_status = status
        elif source == "air_quality":
            self._eco2 = self._tvoc = None
            self._air_quality_status = status
        else:  # Compatibility for callers without a domain identity.
            self._temperature = self._eco2 = self._tvoc = None
            self._environmental_status = self._air_quality_status = status
        if source == "environmental" or source not in {"environmental", "air_quality"}:
            self._temperature_overlay = TemperatureOverlay.NONE
            self._temperature_candidate = self._temperature_candidate_since = None
        if source == "air_quality" or source not in {"environmental", "air_quality"}:
            self._air_quality_overlay = AirQualityOverlay.NONE
            self._air_quality_candidate = self._air_quality_candidate_since = None
        # Never keep a state whose only supporting source is stale/unavailable.
        if not self._state_has_valid_source():
            self._set(EnvironmentalState.NORMAL, self._no_input_reason())
        self._evaluate(now)

    def _desired(self):
        if self._air_quality_overlay is AirQualityOverlay.BAD:
            return EnvironmentalState.AIR_QUALITY_BAD, "eCO2/TVOC above bad threshold"
        if self._air_quality_overlay is AirQualityOverlay.WARNING:
            return EnvironmentalState.AIR_QUALITY_WARNING, "eCO2/TVOC above warning threshold"
        if self._temperature_overlay is TemperatureOverlay.COLD:
            return EnvironmentalState.COLD, "temperature below cold threshold"
        if self._temperature_overlay is TemperatureOverlay.WARM:
            return EnvironmentalState.WARM, "temperature above warm threshold"
        return EnvironmentalState.NORMAL, self._no_input_reason() if self._no_valid_inputs() else "measurements normal"

    def _no_valid_inputs(self):
        return self._temperature is None and self._eco2 is None

    def _no_input_reason(self):
        return "environmental data unavailable" if self._no_valid_inputs() else "measurements normal"

    def _state_has_valid_source(self):
        if self._state in {EnvironmentalState.COLD, EnvironmentalState.WARM}:
            return self._temperature is not None
        if self._state in {EnvironmentalState.AIR_QUALITY_WARNING, EnvironmentalState.AIR_QUALITY_BAD}:
            return self._eco2 is not None
        return True

    def _evaluate(self, now):
        if not self._settings.enabled:
            self._diagnose(now, "disabled")
            return
        previous_overlays = (self._temperature_overlay, self._air_quality_overlay)
        self._update_temperature_overlay(now)
        self._update_air_quality_overlay(now)
        desired, reason = self._desired()
        # Stateful exit hysteresis.  A lower pollutant/warmer-or-cooler temperature
        # must remain normal before recovery can begin.
        if desired is EnvironmentalState.NORMAL and self._state is EnvironmentalState.COLD and self._temperature is not None and self._temperature < self._settings.cold_exit_temperature:
            desired, reason = self._state, "temperature below cold recovery threshold"
        elif desired is EnvironmentalState.NORMAL and self._state is EnvironmentalState.WARM and self._temperature is not None and self._temperature > self._settings.warm_exit_temperature:
            desired, reason = self._state, "temperature above warm recovery threshold"
        elif desired is EnvironmentalState.NORMAL and self._state is EnvironmentalState.AIR_QUALITY_WARNING and self._eco2 is not None and (self._eco2 >= self._settings.air_quality_warning_eco2 or self._tvoc >= self._settings.air_quality_warning_tvoc):
            desired, reason = self._state, "eCO2/TVOC above warning recovery threshold"
        elif desired is EnvironmentalState.NORMAL and self._state is EnvironmentalState.AIR_QUALITY_BAD and self._eco2 is not None and (self._eco2 >= self._settings.air_quality_bad_eco2 or self._tvoc >= self._settings.air_quality_bad_tvoc):
            desired, reason = self._state, "eCO2/TVOC above bad recovery threshold"
        if desired is self._state:
            self._reason = reason
            self._diagnose(now, desired.value)
            if previous_overlays != (self._temperature_overlay, self._air_quality_overlay):
                try:
                    self._sink(self._state, self._reason, self._temperature_overlay, self._air_quality_overlay)
                except TypeError:
                    self._sink(self._state, self._reason)
            return
        self._set(desired, reason)
        self._diagnose(now, desired.value)

    def _update_temperature_overlay(self, now):
        value, current = self._temperature, self._temperature_overlay
        if value is None: return
        if current is TemperatureOverlay.COLD and value < self._settings.cold_exit_temperature: desired = current
        elif current is TemperatureOverlay.WARM and value > self._settings.warm_exit_temperature: desired = current
        elif value <= self._settings.cold_enter_temperature: desired = TemperatureOverlay.COLD
        elif value >= self._settings.warm_enter_temperature: desired = TemperatureOverlay.WARM
        else: desired = TemperatureOverlay.NONE
        self._temperature_overlay = self._confirm(current, desired, now, "temperature")

    def _update_air_quality_overlay(self, now):
        if self._eco2 is None: return
        s, current = self._settings, self._air_quality_overlay
        if self._eco2 >= s.air_quality_bad_eco2 or self._tvoc >= s.air_quality_bad_tvoc: desired = AirQualityOverlay.BAD
        elif current is AirQualityOverlay.WARNING and (self._eco2 >= s.air_quality_warning_eco2 or self._tvoc >= s.air_quality_warning_tvoc): desired = current
        elif self._eco2 >= s.air_quality_warning_eco2 or self._tvoc >= s.air_quality_warning_tvoc: desired = AirQualityOverlay.WARNING
        else: desired = AirQualityOverlay.NONE
        self._air_quality_overlay = self._confirm(current, desired, now, "air_quality")

    def _confirm(self, current, desired, now, domain):
        candidate = getattr(self, f"_{domain}_candidate")
        since = getattr(self, f"_{domain}_candidate_since")
        if desired is current:
            setattr(self, f"_{domain}_candidate", None); setattr(self, f"_{domain}_candidate_since", None); return current
        if candidate is not desired:
            setattr(self, f"_{domain}_candidate", desired); setattr(self, f"_{domain}_candidate_since", now); return current
        delay = self._settings.recovery_seconds if desired.value == "none" else self._settings.confirmation_seconds
        return desired if now - since >= delay else current

    def _diagnose(self, now, decision):
        """Expose interpretation inputs without logging every sensor sample."""
        if now - self._last_diagnostic_at < 60:
            return
        self._last_diagnostic_at = now
        logger.debug("Environmental evaluation=%s state=%s temperature_c=%s eco2_ppm=%s tvoc_ppb=%s",
                    decision, self._state.value, self._temperature, self._eco2, self._tvoc)

    def _set(self, state, reason):
        previous = self._state
        self._state, self._reason = state, reason
        if state is not previous:
            logger.info("Environmental state %s -> %s (%s)", previous.value, state.value, reason)
            try:
                self._sink(state, reason, self._temperature_overlay, self._air_quality_overlay)
            except TypeError:
                self._sink(state, reason)
