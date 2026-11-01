"""Provider-independent semantic physical-context read model."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping


def _section(value: dict) -> Mapping[str, object]:
    return MappingProxyType(dict(value))


@dataclass(frozen=True)
class SemanticPhysicalContext:
    robot: Mapping[str, object]
    environment: Mapping[str, object]
    air_quality: Mapping[str, object]
    motion: Mapping[str, object]
    presence: Mapping[str, object]
    attention: Mapping[str, object]
    observed_expression: Mapping[str, object]

    def document(self) -> dict:
        """Return a detached, serializable copy; never expose canonical state."""
        return deepcopy({"robot": dict(self.robot), "environment": dict(self.environment),
                         "air_quality": dict(self.air_quality), "motion": dict(self.motion),
                         "presence": dict(self.presence), "attention": dict(self.attention),
                         "observed_expression": dict(self.observed_expression)})


class SemanticPhysicalContextService:
    """Build on read from existing semantic snapshots without polling providers."""
    def build(self, *, robot: dict, sensors: dict, presence: dict, attention: dict,
              observed_expression: dict) -> SemanticPhysicalContext:
        environmental = sensors.get("environmental", {})
        air = sensors.get("ccs811", {})
        imu = sensors.get("imu", {})
        behavior = sensors.get("environmental_behavior", {})
        env_values = environmental.get("measurements") or {}
        air_values = air.get("measurements") or {}
        condition = behavior.get("state")
        environment = {"status": environmental.get("status", "unavailable"),
                       "available": bool(environmental.get("available", False)),
                       "temperature_c": env_values.get("temperature_c"),
                       "pressure_hpa": env_values.get("pressure_hpa"),
                       "humidity_percent": env_values.get("humidity_percent"),
                       "last_update": environmental.get("last_update"), "age_seconds": environmental.get("age_seconds"),
                       "condition": condition, "reason": behavior.get("reason")}
        air_quality = {"status": air.get("status", "unavailable"), "available": bool(air.get("available", False)),
                       "eco2_ppm": air_values.get("eco2_ppm"), "tvoc_ppb": air_values.get("tvoc_ppb"),
                       "last_update": air.get("last_update"), "age_seconds": air.get("age_seconds"),
                       "condition": condition if condition in {"air_quality_warning", "air_quality_bad"} else "normal"}
        motion = {key: imu.get(key) for key in ("status", "available", "motion_state", "motion_intensity",
                                                 "tilt_direction", "last_motion_event", "last_update", "age_seconds")}
        motion["status"] = motion["status"] or "unavailable"
        motion["available"] = bool(motion["available"])
        context_attention = attention.get("target") or {}
        attention_view = {"state": attention.get("state", "idle"),
                          "target": {key: context_attention.get(key) for key in ("x", "y", "confidence")}}
        expression = {key: observed_expression.get(key) for key in ("available", "label", "confidence", "unavailable_reason")}
        expression["available"] = bool(expression["available"])
        return SemanticPhysicalContext(
            _section({"state": robot.get("state", "idle")}), _section(environment), _section(air_quality),
            _section(motion), _section({key: presence.get(key) for key in ("state", "people_count", "confidence")}),
            _section(attention_view), _section(expression))
