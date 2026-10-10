"""Canonical, secret-free PHOS settings. No hardware, SDK or CLI dependencies."""
from __future__ import annotations

import json
import ipaddress
import logging
import math
import os
import tempfile
import sys
import copy
import yaml
from importlib import resources
from dataclasses import asdict, dataclass, fields, field
from pathlib import Path
from typing import Optional, Tuple

from robot.semantics import VisualSource

logger = logging.getLogger(__name__)

# Source checkouts use the one canonical document. Wheels install that same
# source file as data; no independent defaults are maintained in the package.
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "phos.json"
if not DEFAULT_CONFIG_PATH.is_file():
    DEFAULT_CONFIG_PATH = Path(sys.prefix) / "share" / "phos" / "config" / "phos.json"


# Logical RGB values sent by PHOS.  The WS2812B adapter owns conversion to its
# GRB wire format, so these values must never be rearranged for a particular
# ring.  Keep this separate from the display-only iris theme palette.
LED_RING_COLOR_RGB = {
    "green": (0, 255, 64),
    "red": (255, 26, 26),
    "yellow": (255, 212, 0),
    "blue": (0, 123, 255),
    "violet": (160, 32, 240),
    "white": (255, 255, 255),
    "cyan": (0, 229, 255),
    "turquoise": (0, 255, 200),
    "orange": (255, 122, 0),
    "magenta": (255, 0, 200),
}
LED_RING_COLOR_CHOICES = tuple(LED_RING_COLOR_RGB)


class ConfigurationError(ValueError):
    """Invalid settings, reported before any subsystem is constructed."""


def atomic_write_text(path: Path, content: str) -> None:
    """Durably replace *path* with already-serialized UTF-8 text.

    The temporary file is created beside the target so ``os.replace`` remains
    atomic on the target filesystem.  Serialization and validation deliberately
    remain outside this helper.
    """
    path = Path(path).resolve()
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=f".{path.name}.", delete=False) as output:
            temporary = Path(output.name)
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


class ConfigRepository:
    """One active configuration source and its validation/persistence boundary."""
    _legacy_json_warning_emitted = False

    def __init__(self, path: Optional[Path] = None) -> None:
        self._active_path = (self.discover_active_path() if path is None else Path(path).resolve())
        self._format = self._format_for_path(self._active_path)
        if self._format == "json" and not self._legacy_json_warning_emitted:
            logger.warning("PHOS CONFIG: legacy JSON configuration loaded from %s; YAML is preferred",
                           self._active_path)
            type(self)._legacy_json_warning_emitted = True

    @staticmethod
    def discover_active_path() -> Path:
        """Choose one default source, without merging or later rediscovery."""
        candidates = (
            DEFAULT_CONFIG_PATH.with_suffix(".yaml"),
            DEFAULT_CONFIG_PATH.with_suffix(".yml"),
            DEFAULT_CONFIG_PATH,
        )
        return next((path.resolve() for path in candidates if path.is_file()),
                    DEFAULT_CONFIG_PATH.resolve())

    @staticmethod
    def _format_for_path(path: Path) -> str:
        formats = {".json": "json", ".yaml": "yaml", ".yml": "yaml"}
        try:
            return formats[path.suffix.lower()]
        except KeyError as error:
            raise ConfigurationError(
                f"Unsupported configuration file extension: {path.suffix or '<none>'}; "
                "expected .json, .yaml, or .yml"
            ) from error

    @property
    def active_path(self) -> Path: return self._active_path

    @property
    def format(self) -> str: return self._format

    def document(self) -> dict:
        if self.format == "json":
            return load_document(self._active_path)
        try:
            document = yaml.safe_load(self._active_path.read_text(encoding="utf-8"))
        except yaml.YAMLError as error:
            raise ConfigurationError(f"{self._active_path}: invalid YAML") from error
        except (OSError, UnicodeError) as error:
            raise ConfigurationError(
                f"Cannot read configuration file: {self._active_path} ({type(error).__name__})"
            ) from error
        if not isinstance(document, dict):
            raise ConfigurationError(f"{self._active_path}: YAML root must be an object")
        return document

    def load(self, document=None, *, overrides=None) -> "RuntimeConfig":
        return RuntimeConfig.from_dict(self.document() if document is None else document,
                                       base_dir=self._active_path.parent, overrides=overrides)

    def save(self, config: "RuntimeConfig") -> None:
        if self.format == "json":
            content = config.serialized_json(self._active_path)
        else:
            content = yaml.safe_dump(config.persistence_document(self._active_path),
                                     default_flow_style=False, allow_unicode=True, sort_keys=False)
            if not content.endswith("\n"):
                content += "\n"
        atomic_write_text(self._active_path, content)


def _keys(value, expected, location):
    if not isinstance(value, dict):
        raise ConfigurationError(f"{location} must be an object")
    missing, unknown = set(expected) - value.keys(), value.keys() - set(expected)
    if missing:
        raise ConfigurationError(f"{location}: missing fields: {', '.join(sorted(missing))}")
    if unknown:
        raise ConfigurationError(f"{location}: unknown fields: {', '.join(sorted(unknown))}; secrets are not supported")


def load_document(path: Path = DEFAULT_CONFIG_PATH) -> dict:
    """Read a legacy JSON document; ConfigRepository owns format dispatch."""
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ConfigurationError(f"Duplicate configuration key: {key}")
            result[key] = value
        return result
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=unique_object)
        # Explicit schema evolution only: no general missing-key permissiveness.
        if isinstance(document, dict) and Path(path).resolve() != DEFAULT_CONFIG_PATH.resolve():
            evolving_sections = ("presence", "attention", "expression_reactions", "startup", "touch", "voice", "tts")
            missing_sections = tuple(section for section in evolving_sections if section not in document)
            voice_document = document.get("voice")
            needs_voice_defaults = (isinstance(voice_document, dict)
                                    and ("processing" not in voice_document
                                         or not isinstance(voice_document.get("input"), dict)
                                         or "device_index" not in voice_document.get("input", {})
                                         or not isinstance(voice_document.get("vad"), dict)
                                         or "pre_roll_ms" not in voice_document.get("vad", {})
                                         or not isinstance(voice_document.get("debug"), dict)
                                         or "dump_utterance_wav" not in voice_document.get("debug", {})
                                         or "utterance_wav_path" not in voice_document.get("debug", {})))
            tts_document = document.get("tts")
            needs_tts_audio_defaults = (isinstance(tts_document, dict)
                                        and (not isinstance(tts_document.get("audio_output"), dict)
                                             or not isinstance(tts_document.get("audio_output", {}).get("debug"), dict)
                                             or "retain_final_wav" not in tts_document.get("audio_output", {}).get("debug", {})
                                             or not isinstance(tts_document.get("audio_output", {}).get("retry"), dict)
                                             or "max_attempts" not in tts_document.get("audio_output", {}).get("retry", {})
                                             or "delay_ms" not in tts_document.get("audio_output", {}).get("retry", {})))
            # A complete explicit config is self-contained; do not consult a
            # separate default document merely to validate it.
            default = (json.loads(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"), object_pairs_hook=unique_object)
                       if missing_sections or needs_voice_defaults or needs_tts_audio_defaults else None)
            for section in missing_sections:
                if section not in document:
                    document[section] = copy.deepcopy(default[section])
                    if section == "startup":
                        # Existing configurations did not opt in to an audio asset;
                        # retain their hardware-independent startup behavior.
                        document[section]["ready_sound"]["enabled"] = False
                        document[section]["ready_sound"]["file"] = None
            if isinstance(document.get("voice"), dict) and default is not None:
                voice = document["voice"]
                voice.setdefault("processing", copy.deepcopy(default["voice"]["processing"]))
                voice.setdefault("input", copy.deepcopy(default["voice"]["input"]))
                voice["input"].setdefault("device_index", None)
                voice.setdefault("vad", copy.deepcopy(default["voice"]["vad"]))
                voice["vad"].setdefault("pre_roll_ms", default["voice"]["vad"]["pre_roll_ms"])
                voice.setdefault("debug", copy.deepcopy(default["voice"]["debug"]))
                voice["debug"].setdefault("dump_utterance_wav", default["voice"]["debug"]["dump_utterance_wav"])
                voice["debug"].setdefault("utterance_wav_path", default["voice"]["debug"]["utterance_wav_path"])
            if isinstance(document.get("tts"), dict) and default is not None:
                audio_output = document["tts"].get("audio_output")
                if isinstance(audio_output, dict):
                    audio_output.setdefault("debug", copy.deepcopy(default["tts"]["audio_output"]["debug"]))
                    audio_output["debug"].setdefault("retain_final_wav", default["tts"]["audio_output"]["debug"]["retain_final_wav"])
                    audio_output.setdefault("retry", copy.deepcopy(default["tts"]["audio_output"]["retry"]))
                    audio_output["retry"].setdefault("max_attempts", default["tts"]["audio_output"]["retry"]["max_attempts"])
                    audio_output["retry"].setdefault("delay_ms", default["tts"]["audio_output"]["retry"]["delay_ms"])
            startup = document.get("startup")
            default_startup = default["startup"] if default is not None else None
            if isinstance(startup, dict) and default_startup is not None:
                for section in ("splash", "ready_sound"):
                    value = startup.get(section)
                    if isinstance(value, dict):
                        for key, default_value in default_startup[section].items():
                            value.setdefault(key, copy.deepcopy(default_value))
        return document
    except json.JSONDecodeError as error:
        raise ConfigurationError(f"{path}: invalid JSON at line {error.lineno}, column {error.colno}") from error
    except (OSError, UnicodeError) as error:
        raise ConfigurationError(f"Cannot read configuration file: {path} ({type(error).__name__})") from error


@dataclass(frozen=True, init=False)
class CloudExpressionConfig:
    region: Optional[str]
    cooldown_seconds: float
    stable_seconds: float
    cache_ttl_seconds: float
    refresh_seconds: float
    max_requests_per_minute: float
    max_requests_per_session: int
    change_threshold: float
    minimum_face_confidence: float
    retry_initial_seconds: float
    retry_max_seconds: float
    connect_timeout_seconds: float
    read_timeout_seconds: float

    def __init__(self, **overrides) -> None:
        values = ConfigRepository().document()["expression"]["aws"]
        values.update(overrides)
        self._assign(values)

    def _assign(self, values) -> None:
        names = {f.name for f in fields(self)}
        _keys(values, names, "expression.aws")
        for name, value in values.items():
            object.__setattr__(self, name, value)
        self.__post_init__()

    @classmethod
    def from_dict(cls, values) -> CloudExpressionConfig:
        result = object.__new__(cls)
        result._assign(values)
        return result

    def __post_init__(self) -> None:
        for name in ("cooldown_seconds", "stable_seconds", "cache_ttl_seconds", "refresh_seconds", "max_requests_per_minute",
                     "retry_initial_seconds", "retry_max_seconds", "connect_timeout_seconds",
                     "read_timeout_seconds"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be positive and finite")
        for name in ("change_threshold", "minimum_face_confidence"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError(f"{name} must be between zero and one")
        if type(self.max_requests_per_session) is not int or self.max_requests_per_session < 0:
            raise ValueError("max_requests_per_session must be a nonnegative integer")
        if self.refresh_seconds >= self.cache_ttl_seconds:
            raise ValueError("refresh_seconds must be less than cache_ttl_seconds")
        if self.retry_max_seconds < self.retry_initial_seconds:
            raise ValueError("retry_max_seconds must be at least retry_initial_seconds")
        if self.region is not None and (not isinstance(self.region, str) or not self.region.strip()):
            raise ValueError("region must be a nonempty string or null")


# The section structure maps to the existing typed RuntimeConfig surface. Values
# live only in phos.json; this mapping is schema, not a second set of defaults.
_SCHEMA = {
    "web": {"enabled": "web_enabled", "host": "web_host", "port": "web_port"},
    "display": {"width": "display_width", "height": "display_height", "fps": "display_fps",
                "fullscreen": "fullscreen", "transition_seconds": "display_transition_seconds",
                "iris_color": "iris_color", "base_visual_source": "base_visual_source",
                "environment_overlays_enabled": "environment_overlays_enabled"},
    "led_ring": {"enabled": "led_ring_enabled", "led_count": "led_ring_led_count",
                 "gpio_pin": "led_ring_gpio_pin", "brightness": "led_ring_brightness",
                 "base_color": "led_ring_base_color", "follow_visual_state": "led_ring_follow_visual_state",
                 "update_rate_hz": "led_ring_update_rate_hz", "imu_reactions_enabled": "led_ring_imu_reactions_enabled",
                 "directional_strength": "led_ring_directional_strength", "directional_sector_size": "led_ring_directional_sector_size",
                 "shake_strength": "led_ring_shake_strength", "impact_strength": "led_ring_impact_strength",
                 "imu_animation_color": "led_ring_imu_animation_color",
                 "directional_animation_speed": "led_ring_directional_animation_speed",
                 "bottom_led_index": "led_ring_bottom_led_index", "forward_led_index": "led_ring_forward_led_index", "clockwise": "led_ring_clockwise"},
    "presence": {"led_reactions": {"enabled": "presence_led_reactions_enabled", "entered": {"duration_ms": "presence_led_entered_duration_ms", "direction": "presence_led_entered_direction"}, "left": {"duration_ms": "presence_led_left_duration_ms", "direction": "presence_led_left_direction"}}},
    "attention": {"lost_hold_ms": "attention_lost_hold_ms"},
    "voice": {"enabled": "voice_enabled", "input": {"device": "voice_input_device", "device_index": "voice_input_device_index", "sample_rate": "voice_capture_sample_rate", "channels": "voice_channels", "chunk_ms": "voice_chunk_ms"}, "processing": {"sample_rate": "voice_processing_sample_rate"}, "vad": {"speech_start_ms": "voice_speech_start_ms", "silence_end_ms": "voice_silence_end_ms", "min_utterance_ms": "voice_min_utterance_ms", "max_utterance_ms": "voice_max_utterance_ms", "pre_roll_ms": "voice_pre_roll_ms", "threshold": "voice_vad_threshold"}, "debug": {"dump_utterance_wav": "voice_debug_dump_utterance_wav", "utterance_wav_path": "voice_debug_utterance_wav_path"}, "stt": {"provider": "voice_stt_provider", "model_path": "voice_stt_model_path", "language": "voice_stt_language"}},
    "tts": {"enabled": "tts_enabled", "provider": "tts_provider", "local": {"engine": "tts_local_engine", "executable": "tts_local_executable", "model_path": "tts_local_model_path", "speaker_id": "tts_local_speaker_id"}, "audio_output": {"player": "tts_audio_output_player", "device": "tts_audio_output_device", "sample_rate": "tts_audio_output_sample_rate", "channels": "tts_audio_output_channels", "sample_width": "tts_audio_output_sample_width", "preroll_ms": "tts_audio_output_preroll_ms", "retry": {"max_attempts": "tts_audio_output_retry_max_attempts", "delay_ms": "tts_audio_output_retry_delay_ms"}, "debug": {"retain_final_wav": "tts_audio_output_debug_retain_final_wav"}}},
    "touch": {"enabled": "touch_enabled", "tap": {"max_duration_ms": "touch_tap_max_duration_ms", "max_movement_px": "touch_tap_max_movement_px"}, "long_press": {"min_duration_ms": "touch_long_press_min_duration_ms", "max_movement_px": "touch_long_press_max_movement_px"}, "swipe": {"min_distance_px": "touch_swipe_min_distance_px", "max_vertical_drift_px": "touch_swipe_max_vertical_drift_px", "max_duration_ms": "touch_swipe_max_duration_ms"}, "reaction": {"enabled": "touch_reaction_enabled", "duration_ms": "touch_reaction_duration_ms", "cooldown_ms": "touch_reaction_cooldown_ms"}},
    "startup": {"splash": {"enabled": "startup_splash_enabled", "image": "startup_splash_image", "title": "startup_splash_title", "subtitle": "startup_splash_subtitle"},
                "ready_sound": {"enabled": "startup_ready_sound_enabled", "file": "startup_ready_sound_file", "player": "startup_ready_sound_player", "device": "startup_ready_sound_device"}},
    "behavior": {"blink_interval_seconds": "blink_interval_seconds", "gaze_interval_seconds": "gaze_interval_seconds",
                 "face_gaze_smoothing": "face_gaze_smoothing", "reaction_decay_per_second": "reaction_decay_per_second",
                 "imu_reaction_strength": "imu_reaction_strength", "imu_tilt_gaze_strength": "imu_tilt_gaze_strength",
                 "imu_tilt_eye_asymmetry_strength": "imu_tilt_eye_asymmetry_strength",
                 "imu_shake_reaction_strength": "imu_shake_reaction_strength",
                 "imu_impact_reaction_strength": "imu_impact_reaction_strength",
                 "imu_shake_reaction_duration_seconds": "imu_shake_reaction_duration_seconds",
                 "imu_impact_reaction_duration_seconds": "imu_impact_reaction_duration_seconds",
                 "imu_reaction_cooldown_seconds": "imu_reaction_cooldown_seconds",
                 "environmental": {"enabled": "environmental_behavior_enabled",
                    "cold_enter_temperature": "cold_enter_temperature", "cold_exit_temperature": "cold_exit_temperature",
                    "warm_enter_temperature": "warm_enter_temperature", "warm_exit_temperature": "warm_exit_temperature",
                    "air_quality_warning_eco2": "air_quality_warning_eco2", "air_quality_warning_tvoc": "air_quality_warning_tvoc",
                    "air_quality_bad_eco2": "air_quality_bad_eco2", "air_quality_bad_tvoc": "air_quality_bad_tvoc",
                    "confirmation_seconds": "environmental_confirmation_seconds", "recovery_seconds": "environmental_recovery_seconds"}},
    "vision": {"face_tracking_enabled": "face_tracking_enabled", "camera_resolution": "camera_resolution",
               "capture_fps": "vision_capture_fps", "detection_fps": "face_detection_fps",
               "camera_preview": {"enabled": "camera_preview_enabled", "position": "camera_preview_position",
                                  "scale": "camera_preview_scale", "max_fps": "camera_preview_max_fps",
                                  "show_face_box": "camera_preview_show_face_box",
                                  "show_expression": "camera_preview_show_expression",
                                  "show_confidence": "camera_preview_show_confidence"},
               "detector": {"cascade_path": "cascade_path", "scale_factor": "detector_scale_factor",
                            "min_neighbors": "detector_min_neighbors", "min_size": "detector_min_size"}},
    "expression": {
        "enabled": "expression_enabled", "provider": "expression_provider", "inference_fps": "expression_inference_fps",
        "crop_margin": "expression_crop_margin",
        "smoothing": {"minimum_confidence": "expression_minimum_confidence",
                      "minimum_observations": "expression_minimum_observations",
                      "local_maximum_gap_seconds": "expression_local_maximum_gap_seconds",
                      "neutral_enabled": "expression_neutral_enabled"},
        "local": {"model_path": "expression_model_path", "labels": "expression_labels",
                  "input_size": "expression_input_size", "scale": "expression_scale", "mean": "expression_mean",
                  "swap_rb": "expression_swap_rb", "grayscale": "expression_grayscale"},
        "aws": "cloud_expression",
    },
    "expression_reactions": {"enabled": "expression_reactions_enabled", "min_confidence": "expression_reactions_min_confidence", "confirmation_ms": "expression_reactions_confirmation_ms", "cooldown_ms": "expression_reactions_cooldown_ms", "reaction_duration_ms": "expression_reactions_duration_ms"},
    "sensors": {"environmental": {"type": "environmental_type",
                           "enabled": "environmental_enabled",
                           "i2c_address": "environmental_i2c_address",
                           "poll_interval_seconds": "environmental_poll_interval_seconds",
                           "stale_after_seconds": "environmental_stale_after_seconds"},
                "ccs811": {"enabled": "ccs811_enabled", "i2c_address": "ccs811_i2c_address",
                           "poll_interval_seconds": "ccs811_poll_interval_seconds",
                           "stale_after_seconds": "ccs811_stale_after_seconds"},
                "imu": {"enabled": "imu_enabled", "i2c_address": "imu_i2c_address",
                        "poll_interval_seconds": "imu_poll_interval_seconds",
                        "stale_after_seconds": "imu_stale_after_seconds",
                        "motion": {"movement_threshold_m_s2": "imu_motion_movement_threshold_m_s2",
                                   "tilt_exit_threshold_m_s2": "imu_motion_tilt_exit_threshold_m_s2",
                                   "lateral_axis": "imu_motion_lateral_axis",
                                   "forward_axis": "imu_motion_forward_axis",
                                   "tilt_threshold_m_s2": "imu_motion_tilt_threshold_m_s2",
                                   "shake_threshold_deg_s": "imu_motion_shake_threshold_deg_s",
                                   "impact_threshold_m_s2": "imu_motion_impact_threshold_m_s2",
                                   "confirmation_seconds": "imu_motion_confirmation_seconds",
                                   "cooldown_seconds": "imu_motion_cooldown_seconds"}}},
    "logging": {"level": "log_level", "file": "log_file", "expression_diagnostics": "expression_diagnostics"},
}
_PATH_FIELDS = {"expression_model_path", "cascade_path", "log_file", "startup_ready_sound_file", "startup_splash_image", "voice_stt_model_path", "voice_debug_utterance_wav_path", "tts_local_model_path"}
_TUPLE_FIELDS = {"camera_resolution", "expression_labels", "expression_input_size", "expression_mean",
                 "blink_interval_seconds", "gaze_interval_seconds", "detector_min_size"}


def _decode(document, schema=_SCHEMA, location="config"):
    _keys(document, schema, location)
    result = {}
    for key, target in schema.items():
        value, name = document[key], f"{location}.{key}"
        if isinstance(target, dict):
            result.update(_decode(value, target, name))
        elif target == "cloud_expression":
            try:
                result[target] = CloudExpressionConfig.from_dict(value)
            except (ValueError, TypeError) as error:
                raise ConfigurationError(f"{name}: {error}") from error
        else:
            if target in _TUPLE_FIELDS:
                if not isinstance(value, (list, tuple)):
                    raise ConfigurationError(f"{name} must be an array")
                value = tuple(value)
            if target in _PATH_FIELDS and value is not None:
                if not isinstance(value, (str, Path)) or not str(value).strip():
                    raise ConfigurationError(f"{name} must be a nonempty path or null")
                value = Path(value)
            result[target] = value
    return result


@dataclass(frozen=True, init=False)
class RuntimeConfig:
    """Typed application settings; JSON is the authoritative default path.

    Existing Python keyword construction remains a compatibility convenience:
    it overlays canonical settings. File/dict loading requires the full schema.
    """

    environmental_type: str
    presence_led_reactions_enabled: bool
    presence_led_entered_duration_ms: int
    presence_led_entered_direction: str
    presence_led_left_duration_ms: int
    presence_led_left_direction: str
    attention_lost_hold_ms: int
    voice_enabled: bool
    voice_input_device: Optional[str]
    voice_input_device_index: Optional[int]
    voice_capture_sample_rate: Optional[int]
    voice_processing_sample_rate: int
    voice_channels: int
    voice_chunk_ms: int
    voice_speech_start_ms: int
    voice_silence_end_ms: int
    voice_min_utterance_ms: int
    voice_max_utterance_ms: int
    voice_pre_roll_ms: int
    voice_vad_threshold: int
    voice_debug_dump_utterance_wav: bool
    voice_debug_utterance_wav_path: Path
    voice_stt_provider: str
    voice_stt_model_path: Optional[Path]
    voice_stt_language: Optional[str]
    tts_enabled: bool
    tts_provider: str
    tts_local_engine: str
    tts_local_executable: str
    tts_local_model_path: Optional[Path]
    tts_local_speaker_id: Optional[int]
    tts_audio_output_player: str
    tts_audio_output_device: Optional[str]
    tts_audio_output_sample_rate: int
    tts_audio_output_channels: int
    tts_audio_output_sample_width: int
    tts_audio_output_preroll_ms: int
    tts_audio_output_retry_max_attempts: int
    tts_audio_output_retry_delay_ms: int
    tts_audio_output_debug_retain_final_wav: bool
    touch_enabled: bool
    touch_tap_max_duration_ms: int
    touch_tap_max_movement_px: int
    touch_long_press_min_duration_ms: int
    touch_long_press_max_movement_px: int
    touch_swipe_min_distance_px: int
    touch_swipe_max_vertical_drift_px: int
    touch_swipe_max_duration_ms: int
    touch_reaction_enabled: bool
    touch_reaction_duration_ms: int
    touch_reaction_cooldown_ms: int
    startup_splash_enabled: bool
    startup_splash_image: Optional[Path]
    startup_splash_title: str
    startup_splash_subtitle: str
    startup_ready_sound_enabled: bool
    startup_ready_sound_file: Optional[Path]
    startup_ready_sound_player: str
    startup_ready_sound_device: Optional[str]
    environmental_enabled: bool
    environmental_i2c_address: str
    environmental_poll_interval_seconds: float
    environmental_stale_after_seconds: float
    ccs811_enabled: bool
    ccs811_i2c_address: str
    ccs811_poll_interval_seconds: float
    ccs811_stale_after_seconds: float
    imu_enabled: bool
    imu_i2c_address: str
    imu_poll_interval_seconds: float
    imu_stale_after_seconds: float
    imu_motion_movement_threshold_m_s2: float
    imu_motion_tilt_exit_threshold_m_s2: float
    imu_motion_lateral_axis: str
    imu_motion_forward_axis: str
    imu_motion_tilt_threshold_m_s2: float
    imu_motion_shake_threshold_deg_s: float
    imu_motion_impact_threshold_m_s2: float
    imu_motion_confirmation_seconds: float
    imu_motion_cooldown_seconds: float
    web_enabled: bool
    web_host: str
    web_port: int
    display_width: int
    display_height: int
    display_fps: int
    fullscreen: bool
    display_transition_seconds: float
    iris_color: str
    base_visual_source: str
    environment_overlays_enabled: bool
    led_ring_enabled: bool
    led_ring_led_count: int
    led_ring_gpio_pin: int
    led_ring_brightness: float
    led_ring_base_color: str
    led_ring_follow_visual_state: bool
    led_ring_update_rate_hz: float
    led_ring_imu_reactions_enabled: bool
    led_ring_directional_strength: float
    led_ring_directional_sector_size: int
    led_ring_shake_strength: float
    led_ring_impact_strength: float
    led_ring_imu_animation_color: str
    led_ring_directional_animation_speed: float
    led_ring_bottom_led_index: int
    led_ring_forward_led_index: int
    led_ring_clockwise: bool
    blink_interval_seconds: Tuple[float, float]
    gaze_interval_seconds: Tuple[float, float]
    face_gaze_smoothing: float
    reaction_decay_per_second: float
    imu_reaction_strength: float
    imu_tilt_gaze_strength: float
    imu_tilt_eye_asymmetry_strength: float
    imu_shake_reaction_strength: float
    imu_impact_reaction_strength: float
    imu_shake_reaction_duration_seconds: float
    imu_impact_reaction_duration_seconds: float
    imu_reaction_cooldown_seconds: float
    environmental_behavior_enabled: bool
    cold_enter_temperature: float
    cold_exit_temperature: float
    warm_enter_temperature: float
    warm_exit_temperature: float
    air_quality_warning_eco2: int
    air_quality_warning_tvoc: int
    air_quality_bad_eco2: int
    air_quality_bad_tvoc: int
    environmental_confirmation_seconds: float
    environmental_recovery_seconds: float
    face_tracking_enabled: bool
    camera_preview_enabled: bool
    camera_preview_position: str
    camera_preview_scale: float
    camera_preview_max_fps: int
    camera_preview_show_face_box: bool
    camera_preview_show_expression: bool
    camera_preview_show_confidence: bool
    camera_resolution: Tuple[int, int]
    vision_capture_fps: float
    face_detection_fps: float
    cascade_path: Optional[Path]
    detector_scale_factor: float
    detector_min_neighbors: int
    detector_min_size: Tuple[int, int]
    expression_enabled: bool
    expression_inference_fps: float
    expression_provider: str
    cloud_expression: CloudExpressionConfig
    expression_minimum_confidence: float
    expression_minimum_observations: int
    expression_local_maximum_gap_seconds: float
    expression_neutral_enabled: bool
    expression_model_path: Optional[Path]
    expression_labels: Tuple[str, ...]
    expression_input_size: Tuple[int, int]
    expression_scale: float
    expression_mean: Tuple[float, float, float]
    expression_swap_rb: bool
    expression_grayscale: bool
    expression_diagnostics: bool
    expression_crop_margin: float
    expression_reactions_enabled: bool
    expression_reactions_min_confidence: float
    expression_reactions_confirmation_ms: int
    expression_reactions_cooldown_ms: int
    expression_reactions_duration_ms: int
    log_level: str
    log_file: Optional[Path]
    _base_dir: Path = field(init=False, repr=False, compare=False)

    def __init__(self, **overrides) -> None:
        # Preserve existing Python calls; new application entry points use from_file.
        if "expression_enabled" not in overrides and (
            overrides.get("expression_model_path") is not None or overrides.get("expression_provider") == "aws"
        ):
            overrides["expression_enabled"] = True
        for name in _PATH_FIELDS & overrides.keys():
            if overrides[name] is not None:
                overrides[name] = Path(overrides[name]).resolve()
        repository = ConfigRepository()
        self._assign(_decode(repository.document()), repository.active_path.parent, overrides)

    def _assign(self, values, base_dir, overrides=None):
        names = {f.name for f in fields(self) if f.init}
        if overrides:
            unknown = overrides.keys() - names
            if unknown:
                raise ConfigurationError(f"Unknown configuration overrides: {', '.join(sorted(unknown))}")
            values.update(overrides)
        for name, value in values.items():
            object.__setattr__(self, name, value)
        object.__setattr__(self, "_base_dir", Path(base_dir).resolve())
        self.validate()

    @classmethod
    def from_file(cls, path: Optional[Path] = None, *, overrides=None) -> RuntimeConfig:
        """Load through the one format-aware ConfigRepository boundary."""
        return ConfigRepository(path).load(overrides=overrides)

    @classmethod
    def from_dict(cls, document: dict, *, base_dir: Path, overrides=None, check_paths=True) -> RuntimeConfig:
        """Validate a document, independent of argparse and AWS credentials.

        Editors may skip filesystem checks to repair removed model paths; schema
        and value validation still run. Startup and save always check paths.
        """
        result = object.__new__(cls)
        result._assign(_decode(document), base_dir, overrides)
        if check_paths:
            result.validate_paths()
        return result

    def to_dict(self) -> dict:
        """Return the complete secret-free JSON structure for editing/persistence."""
        def encode(schema):
            result = {}
            for key, target in schema.items():
                if isinstance(target, dict):
                    result[key] = encode(target)
                else:
                    value = getattr(self, target)
                    if isinstance(value, CloudExpressionConfig):
                        value = asdict(value)
                    elif isinstance(value, Path):
                        value = str(value)
                    elif isinstance(value, tuple):
                        value = list(value)
                    result[key] = value
            return result
        return encode(_SCHEMA)

    def persistence_document(self, path: Path) -> dict:
        """Return a validated persistence document, with paths rebased for *path*."""
        path = Path(path).resolve()
        document = self.to_dict()
        for section, key, name in ((document["expression"]["local"], "model_path", "expression_model_path"),
                                   (document["vision"]["detector"], "cascade_path", "cascade_path"),
                                   (document["logging"], "file", "log_file")):
            value = self.resolve_path(getattr(self, name))
            if value is not None:
                section[key] = os.path.relpath(value, path.parent)
        validated = self.from_dict(document, base_dir=path.parent)
        return validated.to_dict()

    def serialized_json(self, path: Path) -> str:
        """Return validated JSON persistence content, rebased for *path*."""
        return json.dumps(self.persistence_document(path), indent=2, allow_nan=False) + "\n"

    def save(self, path: Path) -> None:
        """Atomically persist validated settings, rebasing paths if relocated."""
        atomic_write_text(path, self.serialized_json(path))

    def resolve_path(self, path: Optional[Path]) -> Optional[Path]:
        return None if path is None else (self._base_dir / path).resolve()

    @staticmethod
    def _bundled_asset_path(relative_path: str) -> Path:
        return Path(str(resources.files("robot.assets").joinpath(relative_path)))

    def resolve_startup_splash_image(self) -> Path:
        return self.resolve_path(self.startup_splash_image) or self._bundled_asset_path("images/phos-startup-800x600.png")

    def resolve_startup_ready_sound(self) -> Path:
        return self.resolve_path(self.startup_ready_sound_file) or self._bundled_asset_path("audio/phos-startup.wav")

    @property
    def vision_enabled(self) -> bool:
        return self.face_tracking_enabled or self.expression_enabled or self.camera_preview_enabled

    def validate(self) -> None:
        def number(name, *, minimum=0, inclusive=False, maximum=None, integer=False):
            value = getattr(self, name)
            if (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
                    or (integer and type(value) is not int)
                    or (value < minimum if inclusive else value <= minimum)
                    or (maximum is not None and value > maximum)):
                raise ConfigurationError(f"{name}: invalid number/range")

        if not isinstance(self.environmental_type, str) or self.environmental_type not in {"bme280", "bmp280"}:
            raise ConfigurationError("sensors.environmental.type must be bme280 or bmp280")
        number("environmental_poll_interval_seconds", minimum=1, inclusive=True, maximum=3600)
        number("environmental_stale_after_seconds", maximum=86400)
        if self.environmental_stale_after_seconds <= self.environmental_poll_interval_seconds:
            raise ConfigurationError("sensors.environmental.stale_after_seconds must exceed poll_interval_seconds")
        if not isinstance(self.environmental_i2c_address, str) or self.environmental_i2c_address not in {"0x76", "0x77"}:
            raise ConfigurationError("sensors.environmental.i2c_address must be 0x76 or 0x77")
        number("ccs811_poll_interval_seconds", minimum=1, inclusive=True, maximum=3600)
        number("ccs811_stale_after_seconds", maximum=86400)
        if self.ccs811_stale_after_seconds <= self.ccs811_poll_interval_seconds:
            raise ConfigurationError("sensors.ccs811.stale_after_seconds must exceed poll_interval_seconds")
        if not isinstance(self.ccs811_i2c_address, str) or self.ccs811_i2c_address not in {"0x5a", "0x5b"}:
            raise ConfigurationError("sensors.ccs811.i2c_address must be 0x5a or 0x5b")
        number("imu_poll_interval_seconds", minimum=.05, inclusive=True, maximum=3600)
        number("imu_stale_after_seconds", maximum=86400)
        if self.imu_stale_after_seconds <= self.imu_poll_interval_seconds:
            raise ConfigurationError("sensors.imu.stale_after_seconds must exceed poll_interval_seconds")
        if not isinstance(self.imu_i2c_address, str) or self.imu_i2c_address not in {"0x68", "0x69"}:
            raise ConfigurationError("sensors.imu.i2c_address must be 0x68 or 0x69")
        for name in ("imu_motion_movement_threshold_m_s2", "imu_motion_tilt_threshold_m_s2",
                     "imu_motion_shake_threshold_deg_s", "imu_motion_impact_threshold_m_s2",
                     "imu_motion_confirmation_seconds"):
            number(name, minimum=.01, inclusive=True, maximum=1000)
        number("imu_motion_tilt_threshold_m_s2", minimum=.01, inclusive=True, maximum=9.80665)
        number("imu_motion_tilt_exit_threshold_m_s2", minimum=.01, inclusive=True, maximum=9.80665)
        if self.imu_motion_tilt_exit_threshold_m_s2 >= self.imu_motion_tilt_threshold_m_s2:
            raise ConfigurationError("IMU tilt exit threshold must be below enter threshold")
        axes = (self.imu_motion_lateral_axis, self.imu_motion_forward_axis)
        if any(not isinstance(axis, str) or axis not in {"x", "y", "z", "-x", "-y", "-z"} for axis in axes):
            raise ConfigurationError("IMU mounting axes must be signed x, y or z")
        if axes[0][-1] == axes[1][-1]:
            raise ConfigurationError("IMU mounting axes must be distinct")
        number("imu_motion_cooldown_seconds", minimum=0, inclusive=True, maximum=3600)
        if self.imu_motion_impact_threshold_m_s2 <= self.imu_motion_movement_threshold_m_s2:
            raise ConfigurationError("sensors.imu.motion.impact_threshold_m_s2 must exceed movement_threshold_m_s2")
        number("led_ring_led_count", minimum=0, inclusive=True, maximum=1024, integer=True)
        number("led_ring_gpio_pin", minimum=0, inclusive=True, maximum=27, integer=True)
        number("led_ring_brightness", minimum=0, inclusive=True, maximum=1)
        number("led_ring_update_rate_hz", minimum=1, inclusive=True, maximum=30)
        number("led_ring_directional_strength", inclusive=True, maximum=1)
        number("led_ring_shake_strength", inclusive=True, maximum=1)
        number("led_ring_impact_strength", inclusive=True, maximum=1)
        number("led_ring_directional_animation_speed", minimum=.1, inclusive=True, maximum=30)
        if self.led_ring_impact_strength <= self.led_ring_shake_strength:
            raise ConfigurationError("LED ring impact strength must exceed shake strength")
        number("led_ring_directional_sector_size", minimum=1, inclusive=True, maximum=64, integer=True)
        number("led_ring_forward_led_index", minimum=0, inclusive=True, maximum=1023, integer=True)
        number("led_ring_bottom_led_index", minimum=0, inclusive=True, maximum=1023, integer=True)
        if self.led_ring_led_count and self.led_ring_forward_led_index >= self.led_ring_led_count:
            raise ConfigurationError("led_ring.forward_led_index must be below led_count")
        if self.led_ring_led_count and self.led_ring_bottom_led_index >= self.led_ring_led_count:
            raise ConfigurationError("led_ring.bottom_led_index must be below led_count")
        if self.led_ring_base_color not in LED_RING_COLOR_RGB:
            raise ConfigurationError("led_ring.base_color must be a supported named color")
        if self.led_ring_imu_animation_color not in LED_RING_COLOR_RGB:
            raise ConfigurationError("led_ring.imu_animation_color must be a supported named color")
        if self.led_ring_enabled and self.led_ring_led_count <= 0:
            raise ConfigurationError("led_ring.led_count must be positive when LED ring is enabled")
        number("web_port", integer=True, maximum=65535)
        try:
            ipaddress.ip_address(self.web_host)
        except (ValueError, TypeError):
            raise ConfigurationError("web.host must be an IPv4 or IPv6 bind address") from None
        if not isinstance(self.web_host, str):
            raise ConfigurationError("web.host must be an IP address string")

        for name in ("display_width", "display_height", "display_fps", "expression_minimum_observations"):
            number(name, integer=True)
        number("detector_min_neighbors", inclusive=True, integer=True)
        for name in ("display_transition_seconds", "reaction_decay_per_second", "vision_capture_fps",
                     "face_detection_fps", "expression_inference_fps", "expression_local_maximum_gap_seconds",
                     "expression_scale"):
            number(name)
        number("detector_scale_factor", minimum=1)
        number("face_gaze_smoothing", maximum=1)
        number("imu_reaction_strength", inclusive=True, maximum=1)
        number("imu_tilt_gaze_strength", inclusive=True, maximum=1)
        number("imu_tilt_eye_asymmetry_strength", minimum=0, inclusive=True, maximum=.5)
        number("imu_shake_reaction_strength", inclusive=True, maximum=1)
        number("imu_impact_reaction_strength", inclusive=True, maximum=1)
        if self.imu_impact_reaction_strength <= self.imu_shake_reaction_strength:
            raise ConfigurationError("IMU impact reaction strength must exceed shake reaction strength")
        number("imu_shake_reaction_duration_seconds", minimum=.1, inclusive=True, maximum=30)
        number("imu_impact_reaction_duration_seconds", minimum=.1, inclusive=True, maximum=30)
        number("imu_reaction_cooldown_seconds", minimum=0, inclusive=True, maximum=3600)
        for name in ("cold_enter_temperature", "cold_exit_temperature", "warm_enter_temperature", "warm_exit_temperature"):
            number(name, minimum=-50, inclusive=True, maximum=80)
        if not self.cold_enter_temperature < self.cold_exit_temperature < self.warm_exit_temperature < self.warm_enter_temperature:
            raise ConfigurationError("Environmental temperature thresholds must ascend with hysteresis")
        for name in ("air_quality_warning_eco2", "air_quality_warning_tvoc", "air_quality_bad_eco2", "air_quality_bad_tvoc"):
            number(name, minimum=1, inclusive=True, maximum=100000, integer=True)
        if self.air_quality_warning_eco2 >= self.air_quality_bad_eco2 or self.air_quality_warning_tvoc >= self.air_quality_bad_tvoc:
            raise ConfigurationError("Environmental air-quality thresholds must ascend")
        for name in ("environmental_confirmation_seconds", "environmental_recovery_seconds"):
            number(name, minimum=1, inclusive=True, maximum=86400)
        number("expression_minimum_confidence", inclusive=True, maximum=1)
        number("expression_reactions_min_confidence", inclusive=True, maximum=1)
        for name in ("expression_reactions_confirmation_ms", "expression_reactions_cooldown_ms", "expression_reactions_duration_ms"):
            number(name, minimum=0 if name != "expression_reactions_duration_ms" else 1, inclusive=True, maximum=60000, integer=True)
        number("expression_crop_margin", inclusive=True, maximum=.5)
        number("attention_lost_hold_ms", minimum=0, inclusive=True, maximum=60000, integer=True)
        for name in ("voice_processing_sample_rate", "voice_channels", "voice_chunk_ms", "voice_speech_start_ms", "voice_silence_end_ms", "voice_min_utterance_ms", "voice_max_utterance_ms", "voice_vad_threshold"):
            number(name, minimum=1, inclusive=True, maximum=192000 if name == "voice_processing_sample_rate" else 60000, integer=True)
        number("voice_pre_roll_ms", minimum=0, inclusive=True, maximum=1000, integer=True)
        if self.voice_capture_sample_rate is not None:
            number("voice_capture_sample_rate", minimum=8000, inclusive=True, maximum=192000, integer=True)
        if self.voice_processing_sample_rate != 16000 or self.voice_channels != 1:
            raise ConfigurationError("voice.processing requires 16 kHz mono PCM")
        if self.voice_chunk_ms not in {10, 20, 30, 40, 50, 60} or self.voice_speech_start_ms < self.voice_chunk_ms or self.voice_silence_end_ms < self.voice_chunk_ms or self.voice_min_utterance_ms >= self.voice_max_utterance_ms:
            raise ConfigurationError("voice VAD timing is invalid")
        if self.voice_input_device is not None and (not isinstance(self.voice_input_device, str) or not self.voice_input_device.strip()):
            raise ConfigurationError("voice input or STT provider is invalid")
        if self.voice_input_device_index is not None and (type(self.voice_input_device_index) is not int or self.voice_input_device_index < 0):
            raise ConfigurationError("voice.input.device_index must be a nonnegative integer or null")
        if self.voice_debug_utterance_wav_path is None:
            raise ConfigurationError("voice.debug.utterance_wav_path must be a nonempty path")
        if self.voice_stt_provider != "local":
            raise ConfigurationError("voice input or STT provider is invalid")
        if self.voice_stt_language is not None and (not isinstance(self.voice_stt_language, str) or not self.voice_stt_language.strip()):
            raise ConfigurationError("voice.stt.language must be a nonempty string or null")
        if self.tts_provider != "local" or self.tts_local_engine != "piper":
            raise ConfigurationError("tts provider must be local Piper")
        if not isinstance(self.tts_local_executable, str) or not self.tts_local_executable.strip():
            raise ConfigurationError("tts.local.executable must be a nonempty command")
        if self.tts_local_speaker_id is not None and (type(self.tts_local_speaker_id) is not int or self.tts_local_speaker_id < 0):
            raise ConfigurationError("tts.local.speaker_id must be a nonnegative integer or null")
        if not isinstance(self.tts_audio_output_player, str) or self.tts_audio_output_player != "aplay":
            raise ConfigurationError("tts.audio_output.player must be aplay")
        if self.tts_audio_output_device is not None and (not isinstance(self.tts_audio_output_device, str) or not self.tts_audio_output_device.strip()):
            raise ConfigurationError("tts.audio_output.device must be a nonempty ALSA device or null")
        number("tts_audio_output_preroll_ms", minimum=0, inclusive=True, maximum=10000, integer=True)
        number("tts_audio_output_retry_max_attempts", minimum=1, inclusive=True, maximum=2, integer=True)
        number("tts_audio_output_retry_delay_ms", minimum=0, inclusive=True, maximum=5000, integer=True)
        number("tts_audio_output_sample_rate", minimum=1, inclusive=True, maximum=192000, integer=True)
        if self.tts_audio_output_channels not in {1, 2}:
            raise ConfigurationError("tts.audio_output.channels must be 1 or 2")
        if self.tts_audio_output_sample_width != 2:
            raise ConfigurationError("tts.audio_output.sample_width must be 2")
        for name in ("presence_led_entered_duration_ms", "presence_led_left_duration_ms"):
            number(name, minimum=1, inclusive=True, maximum=60000, integer=True)
        if self.presence_led_entered_direction not in {"clockwise", "counter_clockwise"} or self.presence_led_left_direction not in {"clockwise", "counter_clockwise"}:
            raise ConfigurationError("presence LED directions must be clockwise or counter_clockwise")
        for name in ("ccs811_enabled", "environmental_enabled", "environmental_behavior_enabled", "imu_enabled", "led_ring_enabled", "led_ring_follow_visual_state", "led_ring_imu_reactions_enabled", "led_ring_clockwise", "presence_led_reactions_enabled", "voice_enabled", "voice_debug_dump_utterance_wav", "web_enabled", "fullscreen", "face_tracking_enabled", "camera_preview_enabled",
                     "camera_preview_show_face_box", "camera_preview_show_expression", "camera_preview_show_confidence", "expression_enabled", "expression_neutral_enabled",
                     "expression_swap_rb", "expression_grayscale", "expression_diagnostics", "expression_reactions_enabled",
                     "startup_splash_enabled", "startup_ready_sound_enabled", "tts_enabled", "tts_audio_output_debug_retain_final_wav"):
            if type(getattr(self, name)) is not bool:
                raise ConfigurationError(f"{name} must be a boolean")
        for name in ("camera_resolution", "expression_input_size", "detector_min_size"):
            value = getattr(self, name)
            if not isinstance(value, tuple) or len(value) != 2 or any(type(v) is not int or v <= 0 for v in value):
                raise ConfigurationError(f"{name} must contain two positive integers")
        if any(size > dimension for size, dimension in zip(self.detector_min_size, self.camera_resolution)):
            raise ConfigurationError("vision.detector.min_size cannot exceed camera_resolution")
        for name in ("blink_interval_seconds", "gaze_interval_seconds"):
            value = getattr(self, name)
            if (not isinstance(value, tuple) or len(value) != 2
                    or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0 for v in value)
                    or value[1] < value[0]):
                raise ConfigurationError(f"{name} must contain two positive ascending numbers")
        if not isinstance(self.expression_provider, str) or self.expression_provider not in {"local", "aws"}:
            raise ConfigurationError("expression.provider must be local or aws")
        if not isinstance(self.iris_color, str) or self.iris_color not in {
            "cyan", "blue", "green", "turquoise", "amber", "violet", "white"
        }:
            raise ConfigurationError("display.iris_color must be cyan, blue, green, turquoise, amber, violet or white")
        if self.base_visual_source not in {item.value for item in VisualSource}:
            raise ConfigurationError("display.base_visual_source must be manual, environment or state")
        if type(self.environment_overlays_enabled) is not bool:
            raise ConfigurationError("display.environment_overlays_enabled must be a boolean")
        for name in ("startup_splash_title", "startup_splash_subtitle"):
            if not isinstance(getattr(self, name), str) or not getattr(self, name).strip():
                raise ConfigurationError(f"{name} must be a nonempty string")
        if not isinstance(self.startup_ready_sound_player, str) or self.startup_ready_sound_player != "aplay":
            raise ConfigurationError("startup.ready_sound.player must be aplay")
        if self.startup_ready_sound_device is not None and (not isinstance(self.startup_ready_sound_device, str) or not self.startup_ready_sound_device.strip()):
            raise ConfigurationError("startup.ready_sound.device must be a nonempty ALSA device or null")
        if not isinstance(self.camera_preview_position, str) or self.camera_preview_position not in {
            "top_left", "top_right", "bottom_left", "bottom_right"
        }:
            raise ConfigurationError("vision.camera_preview.position must be a supported corner")
        number("camera_preview_scale", minimum=0, inclusive=True, maximum=.4)
        if self.camera_preview_scale < .1:
            raise ConfigurationError("vision.camera_preview.scale must be between 0.1 and 0.4")
        number("camera_preview_max_fps", integer=True, maximum=10)
        if not isinstance(self.cloud_expression, CloudExpressionConfig):
            raise ConfigurationError("expression.aws must be CloudExpressionConfig")
        if not isinstance(self.log_level, str) or self.log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ConfigurationError("logging.level must be DEBUG, INFO, WARNING, ERROR or CRITICAL")
        for name in _PATH_FIELDS:
            value = getattr(self, name)
            if value is not None and not isinstance(value, Path):
                raise ConfigurationError(f"{name} must be a Path or null")
        labels = self.expression_labels
        if (not isinstance(labels, tuple) or any(not isinstance(v, str) or not v.strip() for v in labels)
                or len(set(labels)) != len(labels)):
            raise ConfigurationError("expression.local.labels must contain unique nonempty strings")
        if (not isinstance(self.expression_mean, tuple) or len(self.expression_mean) != 3
                or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in self.expression_mean)):
            raise ConfigurationError("expression.local.mean must contain three finite numbers")
        if self.expression_enabled and self.expression_provider == "local" and (self.expression_model_path is None or not labels):
            raise ConfigurationError("Enabled local expressions require expression.local.model_path and labels")

    def validate_paths(self) -> None:
        """Check active model and explicit detector paths before hardware starts."""
        required = []
        if self.expression_enabled and self.expression_provider == "local":
            required.append(("expression.local.model_path", self.expression_model_path))
        if self.vision_enabled and self.cascade_path is not None:
            required.append(("vision.detector.cascade_path", self.cascade_path))
        for name, value in required:
            path = self.resolve_path(value)
            if path is None or not path.is_file() or not os.access(path, os.R_OK):
                raise ConfigurationError(f"{name}: readable file required: {path}")
        log = self.resolve_path(self.log_file)
        if log is not None and (not log.parent.is_dir() or not os.access(log.parent, os.W_OK)
                                or (log.exists() and (not log.is_file() or not os.access(log, os.W_OK)))):
            raise ConfigurationError(f"logging.file: writable file/parent required: {log}")
        if self.startup_ready_sound_enabled:
            sound = self.resolve_startup_ready_sound()
            if not sound.is_file() or not os.access(sound, os.R_OK):
                raise ConfigurationError(f"startup.ready_sound.file: readable file required: {sound}")
        if self.voice_enabled and self.voice_stt_model_path is not None:
            model = self.resolve_path(self.voice_stt_model_path)
            if model is None or not model.is_dir() or not os.access(model, os.R_OK):
                raise ConfigurationError(f"voice.stt.model_path: readable model directory required: {model}")
