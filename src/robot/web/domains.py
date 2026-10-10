"""Navigation and presentation groups, never a configuration schema.

New implemented areas can register a page and select canonical field paths here.
No defaults, types or validation rules belong in this module.
"""
import re

from robot.web.configuration import editor_sections

DOMAINS = {
    "general": {"title": "Dashboard", "description": "PHOS administration overview and shortcuts."},
    "status": {"title": "PHOS Status", "description": "Live semantic state, motion, environment and subsystem health summary."},
    "controls": {"title": "Controls", "description": "Apply supported semantic PHOS commands through the authenticated Remote API."},
    "sensors": {"title": "Sensors", "description": "Detailed BMP280/BME280, CCS811 and MPU6050 readings, freshness and availability."},
    "api": {"title": "API", "description": "Remote API documentation, OpenAPI contract and authenticated capability discovery."},
    "diagnostics": {"title": "Diagnostics", "description": "Runtime health, saved-versus-active configuration and system diagnostic actions."},
    "appearance": {"title": "Appearance", "description": "Display geometry, animation and LED-ring appearance settings."},
    "eyes": {"title": "Eyes", "description": "PHOS iris color and supported eye visual settings."},
    "network": {"title": "Network", "description": "Web Admin host, bind address and local network exposure settings."},
    "runtime": {"title": "Runtime", "description": "Vision, expression and safe logging configuration."},
    "behavior": {"title": "Behavior", "description": "Supported eye and environmental behavior settings."},
    "integrations": {"title": "Integrations", "description": "Optional Web Administration integration settings."},
    "voice": {"title": "Voice & speech", "description": "Text-to-speech provider selection and local Piper settings."},
}

# Paths select fields that already exist in the canonical document. A trailing
# dot selects a whole implemented section; all other entries select one field.
GROUPS = {
    "sensors": [("environmental", "Environmental sensor", ("sensors.environmental.",), None),
                ("ccs811", "CCS811 air quality", ("sensors.ccs811.",), None),
                ("environmental-behavior", "Environmental behavior", ("behavior.environmental.",), None),
                ("imu", "GY-521 / MPU-6050 motion", ("sensors.imu.",), None)],
    "network": [("listener", "Administration address", ("web.host", "web.port"), None)],
    "display": [("display", "Display", ("display.",), None),
                ("led-ring", "WS2812B LED ring", ("led_ring.enabled", "led_ring.led_count", "led_ring.gpio_pin", "led_ring.brightness", "led_ring.base_color", "led_ring.follow_visual_state", "led_ring.update_rate_hz"), None),
                ("imu-led-reactions", "IMU LED reactions", ("led_ring.imu_reactions_enabled", "led_ring.directional_strength", "led_ring.directional_sector_size", "led_ring.shake_strength", "led_ring.impact_strength", "led_ring.forward_led_index", "led_ring.clockwise"), None),
                ("behavior", "Eye behavior", ("behavior.",), None)],
    "vision": [("vision", "Camera & tracking", ("vision.face_tracking_enabled", "vision.camera_resolution", "vision.capture_fps", "vision.detection_fps"), None),
               ("camera-preview", "Camera picture-in-picture preview", ("vision.camera_preview.",), None),
               ("detector", "Face detection", ("vision.detector.",), None)],
    "expression": [
        ("provider", "Provider selection", ("expression.enabled", "expression.provider", "expression.inference_fps", "expression.crop_margin"), None),
        ("smoothing", "Observation smoothing", ("expression.smoothing.",), None),
        ("local", "Local ONNX provider", ("expression.local.",), "local"),
        ("aws", "AWS provider — non-secret settings", ("expression.aws.region", "expression.aws.minimum_face_confidence", "expression.aws.connect_timeout_seconds", "expression.aws.read_timeout_seconds"), "aws"),
        ("cloud-limits", "Cloud cost & rate limits", ("expression.aws.cooldown_seconds", "expression.aws.stable_seconds", "expression.aws.refresh_seconds", "expression.aws.cache_ttl_seconds", "expression.aws.max_requests_per_minute", "expression.aws.max_requests_per_session", "expression.aws.change_threshold", "expression.aws.retry_initial_seconds", "expression.aws.retry_max_seconds"), "aws"),
    ],
    "logging": [("logging", "Safe logging settings", ("logging.",), None)],
    "security": [("web", "Administration service", ("web.enabled",), None)],
}

# The unified Settings editor keeps the existing canonical groups and schema;
# it changes navigation only, not configuration meaning or validation.
GROUPS["appearance"] = [("display", "Display", ("display.width", "display.height", "display.fps", "display.fullscreen"), None), *GROUPS["display"][1:3]]
GROUPS["eyes"] = [("eyes", "Eye appearance", ("display.iris_color", "display.base_visual_source", "display.environment_overlays_enabled"), None)]
GROUPS["runtime"] = [*GROUPS["vision"], *GROUPS["expression"], *GROUPS["logging"]]
GROUPS["behavior"] = [("behavior", "Eye behavior", ("behavior.",), None)]
GROUPS["integrations"] = GROUPS["security"]
GROUPS["voice"] = [("tts", "Text-to-speech", ("tts.enabled", "tts.provider"), None),
                    ("local", "Local Piper", ("tts.local.",), "local"),
                    ("elevenlabs", "ElevenLabs", ("tts.elevenlabs.",), "elevenlabs"),
                    ("audio", "Audio output", ("tts.audio_output.",), None)]


def domain_sections(document, area):
    canonical = editor_sections(document)
    fields = [item for group in canonical for item in group["fields"]]
    result = []
    for key, title, selectors, provider in GROUPS.get(area, ()):
        selected = [item for item in fields if any(
            item["name"].startswith(selector) if selector.endswith(".") else item["name"] == selector
            for selector in selectors)]
        hints = dict.fromkeys(group["help"] for group in canonical
                             if any(item in selected for item in group["fields"]))
        result.append({"name": key, "title": title, "provider": provider,
                       "fields": selected, "help": " ".join(hints)})
    return result


def error_domain(document, error):
    """Locate canonical validator messages for navigation, without revalidating."""
    message = str(error)
    candidates = []
    for area in GROUPS:
        for group in domain_sections(document, area):
            for field in group["fields"]:
                name = field["name"]
                for token in (name, name.replace(".", "_"), name.rsplit(".", 1)[-1]):
                    if re.search(r"(?<![A-Za-z0-9_])" + re.escape(token) + r"(?![A-Za-z0-9_])", message):
                        candidates.append((len(token), area, group["name"]))
    return max(candidates)[1:] if candidates else (None, None)
