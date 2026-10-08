"""Editor conversion and persistence through the canonical configuration model."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
from threading import RLock
import yaml

from robot.config import (ConfigRepository, ConfigurationError, LED_RING_COLOR_CHOICES,
                          RuntimeConfig, load_document)


class ConfigurationService:
    def __init__(self, repository):
        self.repository = repository if isinstance(repository, ConfigRepository) else ConfigRepository(repository)
        self.path = self.repository.active_path
        self.lock = RLock()

    def read(self):
        document = self.repository.document()
        # Validate schema and types before reflecting data into the UI. Inactive
        # or removed model files must not prevent fixing their paths in the editor.
        config = RuntimeConfig.from_dict(document, base_dir=self.path.parent, check_paths=False)
        return config.to_dict()

    @staticmethod
    def revision(document):
        return hashlib.sha256(json.dumps(document, sort_keys=True).encode()).hexdigest()

    def save_form(self, form, *, area=None):
        with self.lock:
            document = self.read()
            if form.get("revision") != self.revision(document):
                raise ConfigurationError("Configuration changed since this page was loaded. Reload before saving.")
            # Domain pages merge only their own controls. Validate the full
            # result so cross-domain constraints still use startup's rules.
            if area is None:
                groups = editor_sections(document)  # Existing service callers.
            else:
                from robot.web.domains import domain_sections
                groups = domain_sections(document, area)
            allowed = {item["name"] for group in groups for item in group["fields"]}
            if not allowed or set(form) - allowed - {"csrf_token", "revision", "area"}:
                raise ConfigurationError("Unexpected fields for this configuration area.")
            edited = deepcopy(document)
            for group in groups:
                for item in group["fields"]:
                    route = item["name"]
                    value = form.get(route, "")
                    try:
                        if item["kind"] == "checkbox":
                            value = value == "on"
                        elif item["kind"] == "number":
                            value = json.loads(value)
                        elif item["kind"] == "array":
                            value = ([part.strip() for part in value.split(",") if part.strip()]
                                     if route == "expression.local.labels" else json.loads("[" + value + "]"))
                        elif item["nullable"] and not value.strip():
                            value = None
                    except (ValueError, TypeError):
                        raise ConfigurationError(f"{route}: enter a valid number or comma-separated list.") from None
                    keys, target = route.split("."), edited
                    for key in keys[:-1]:
                        target = target[key]
                    target[keys[-1]] = value
            self.repository.save(RuntimeConfig.from_dict(edited, base_dir=self.path.parent))

    def editable_yaml(self):
        """Return the validated active configuration as human-editable YAML."""
        with self.lock:
            config = RuntimeConfig.from_dict(self.repository.document(), base_dir=self.path.parent,
                                             check_paths=False)
            document = config.persistence_document(self.path)
            return yaml.safe_dump(document, default_flow_style=False, allow_unicode=True, sort_keys=False)

    def validate_yaml(self, source):
        try:
            document = yaml.safe_load(source)
        except yaml.YAMLError as error:
            mark = getattr(error, "problem_mark", None)
            location = (f" at line {mark.line + 1}, column {mark.column + 1}" if mark else "")
            raise ConfigurationError(f"Invalid YAML{location}: {error.problem or 'syntax error'}") from error
        if not isinstance(document, dict):
            raise ConfigurationError("YAML root must be an object.")
        return RuntimeConfig.from_dict(document, base_dir=self.path.parent)

    def save_yaml(self, source, revision):
        with self.lock:
            document = self.read()
            if revision != self.revision(document):
                raise ConfigurationError("Configuration changed since this page was loaded. Reload before saving.")
            self.repository.save(self.validate_yaml(source))


# Presentation hints only. Field structure and validation belong to robot.config.
CHOICES = {"sensors.imu.motion.lateral_axis": ("x", "-x", "y", "-y", "z", "-z"),
           "sensors.imu.motion.forward_axis": ("x", "-x", "y", "-y", "z", "-z"),
           "sensors.ccs811.i2c_address": ("0x5a", "0x5b"),
           "sensors.imu.i2c_address": ("0x68", "0x69"),
           "sensors.environmental.type": ("bme280", "bmp280"),
           "expression.provider": ("local", "aws"),
           "sensors.environmental.i2c_address": ("0x76", "0x77"),
           "logging.level": ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"),
           "display.iris_color": ("cyan", "blue", "green", "turquoise", "amber", "violet", "white"),
           "display.base_visual_source": ("manual", "environment", "state"),
           "led_ring.base_color": LED_RING_COLOR_CHOICES,
           "led_ring.imu_animation_color": LED_RING_COLOR_CHOICES,
           "vision.camera_preview.position": ("bottom_right", "bottom_left", "top_right", "top_left")}
HELP = {
    "behavior.environmental": "Environmental reactions use confirmed, hysteretic BMP280/BME280 temperature and CCS811 eCO2/TVOC readings. eCO2 is estimated equivalent CO2, not direct NDIR CO2. These behavior thresholds apply after configuration reload; sensor hardware settings still require restart.",
    "sensors.ccs811": "CCS811: estimated equivalent CO2 (eCO2, ppm), not direct NDIR CO2; TVOC (ppb). Disabled by default. I2C bus 1. Readings withheld during 20-minute conditioning after initialization. Poll interval 1–3600 seconds; stale timeout must be longer, at most 86400 seconds. Fresh environmental temperature/humidity are used automatically when available; BMP280 cannot supply humidity. Save, then Restart PHOS for every change.",
    "sensors.imu": "GY-521 / MPU-6050: acceleration in m/s² and angular velocity in °/s. I2C bus 1; AD0 selects 0x68 or 0x69. Hardware settings require Restart PHOS. Motion thresholds below are provider-neutral, apply after Save → Reload configuration, and do not reopen I2C.",
    "sensors.imu.motion": "Priority: impact, shake, sustained tilt, moving, still. Tilt enter/exit thresholds are normalized gravity components in m/s² (4 ≈ 24°, 3 ≈ 18°). Exit must be below enter. Confirmation is seconds. Signed mounting axes map positive acceleration to right/forward; choose distinct axes. Save → Reload configuration applies all motion settings. See installation guide for mounting and diagnostics.",
    "sensors.environmental": "BME280: temperature, humidity, pressure. BMP280: temperature and pressure; humidity not supported. I2C bus 1. Disabled by default. Poll interval: 1–3600 seconds; stale timeout must be longer (at most 86400 seconds). Save, then Restart PHOS to apply any sensor change. No sensor hot reload.",
    "web": "Disabled by default. Use the Pi's LAN IP or 0.0.0.0 for trusted LAN access. Restart after changing these settings.",
    "display": "Display dimensions are pixels; fps controls animation cadence. Iris color is a named eye theme and applies after configuration reload. Display geometry and fullscreen changes require restart.",
    "led_ring": "WS2812B semantic feedback. Left/right begin at their physical quarter-ring positions and fill in mirrored directions; back grows in two fronts from bottom_led_index, while forward grows in two fronts from forward_led_index (the physical top). Clockwise means increasing indices rotate clockwise. Fill speed, strengths and mapping apply after Save → Reload configuration. Pin and LED count require Restart PHOS. The ring remains optional and cannot affect PHOS eyes.",
    "behavior": "Timing pairs are minimum, maximum in seconds. IMU reaction strength controls persistent moving/tilt emphasis; tilt gaze uses normalized safe pupil range. Tilt eye asymmetry splits horizontal tilt eye openness equally and mirrors left/right. Shake and impact have separate strengths (impact must be stronger), each with hold then smooth decay. These IMU behavior settings apply after Save → Reload configuration; they do not reopen I2C.",
    "vision": "Tracking uses the local camera. Resolution is width, height in pixels. Preview is local to the PHOS display, disabled by default and applies after System → Reload configuration without restarting PHOS.",
    "vision.camera_preview": "Preview frames stay in memory and appear only on the PHOS display. Scale is a fraction of display width; maximum FPS is capped at 10. Preview changes apply after configuration reload and do not restart Vision unless the camera must be started or stopped for the new enabled state.",
    "vision.detector": "Leave cascade path blank for platform discovery. Minimum size is width, height in pixels.",
    "expression": "Local runs ONNX on the Pi. AWS sends selected face crops to AWS when expressions are enabled. There is no automatic fallback.",
    "expression.local": "Paths are relative to the configuration file. Labels must match model output order; input size is width, height and mean is three channel values.",
    "expression.aws": "Non-secret request policy only. Credentials stay external in the AWS SDK chain; availability is not checked here. Times are seconds; zero session limit means unlimited.",
    "expression.smoothing": "Requires repeated confident observations. Keep neutral disabled until calibrated.",
    "logging": "Blank file means console only. Paths are relative to the configuration file. Enable diagnostics temporarily.",
}


def editor_sections(document, prefix=""):
    sections = []
    for name, values in document.items():
        route = f"{prefix}.{name}" if prefix else name
        direct, nested = [], {}
        for key, value in values.items():
            if isinstance(value, dict):
                nested[key] = value
                continue
            field_name = f"{route}.{key}"
            kind = ("checkbox" if isinstance(value, bool) else "number" if isinstance(value, (float, int))
                    else "array" if isinstance(value, list) else "text")
            direct.append({"name": field_name, "label": key.replace("_", " ").capitalize(),
                           "kind": kind, "value": ", ".join(map(str, value)) if isinstance(value, list) else value,
                           "nullable": value is None or field_name in {"expression.local.model_path", "vision.detector.cascade_path", "logging.file", "expression.aws.region"},
                           "choices": CHOICES.get(field_name)})
        sections.append({"name": route, "title": route.replace(".", " / ").replace("_", " ").title(),
                         "help": HELP.get(route, ""), "fields": direct})
        sections.extend(editor_sections(nested, route))
    return sections
