"""Named, validated presets for PHOS's existing appearance configuration."""
from __future__ import annotations

import copy
import json
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
import yaml

from robot.config import ConfigurationError, atomic_write_text


_STATES = ("idle", "listening", "thinking", "speaking", "sleeping", "error")
_SAFE_ASSET = re.compile(r"backgrounds/[A-Za-z0-9][A-Za-z0-9._ -]*\.(?:png|jpe?g)$", re.IGNORECASE)
_EYE_SAFE_MODES = {"subtle", "balanced", "expressive"}
_EFFECT_PRESETS = _EYE_SAFE_MODES
_EFFECT_PALETTES = {"phos", "cyan", "blue", "violet", "aqua", "amber", "white", "green"}
_IRIS_COLORS = {"cyan", "blue", "green", "turquoise", "amber", "violet", "white"}
SKIN_SCHEMA_VERSION = 1
MAX_SKIN_IMPORT_BYTES = 256 * 1024


def appearance_snapshot(document: dict) -> dict:
    """Return only stable, user-facing appearance settings from a config document."""
    display = document["display"]
    return {
        "eyes": {"iris_color": display["iris_color"]},
        "backgrounds": {key: display["backgrounds"].get(key) for key in ("enabled", *_STATES)},
        "eye_safe_layer": copy.deepcopy(display["eye_safe_layer"]),
        "state_effects": copy.deepcopy(display["state_effects"]),
    }


def validate_appearance_snapshot(snapshot: object) -> dict:
    if not isinstance(snapshot, dict) or set(snapshot) != {"eyes", "backgrounds", "eye_safe_layer", "state_effects"}:
        raise ConfigurationError("Skin appearance must contain eyes, backgrounds, eye_safe_layer and state_effects")
    value = copy.deepcopy(snapshot)
    eyes, backgrounds = value["eyes"], value["backgrounds"]
    safe, effects = value["eye_safe_layer"], value["state_effects"]
    if not isinstance(eyes, dict) or set(eyes) != {"iris_color"} or eyes["iris_color"] not in _IRIS_COLORS:
        raise ConfigurationError("Skin eyes.iris_color is unsupported")
    if not isinstance(backgrounds, dict) or set(backgrounds) != {"enabled", *_STATES} or type(backgrounds["enabled"]) is not bool:
        raise ConfigurationError("Skin backgrounds must contain enabled and every supported robot state")
    for state in _STATES:
        asset = backgrounds[state]
        if asset is not None and (not isinstance(asset, str) or not _SAFE_ASSET.fullmatch(asset)):
            raise ConfigurationError(f"Skin background {state} must be a managed background asset or null")
    if not isinstance(safe, dict) or set(safe) != {"enabled", "mode"} or type(safe["enabled"]) is not bool or safe["mode"] not in _EYE_SAFE_MODES:
        raise ConfigurationError("Skin eye_safe_layer is invalid")
    if (not isinstance(effects, dict) or set(effects) != {"enabled", "preset", "palette", "reduce_motion"}
            or type(effects["enabled"]) is not bool or type(effects["reduce_motion"]) is not bool
            or effects["preset"] not in _EFFECT_PRESETS or effects["palette"] not in _EFFECT_PALETTES):
        raise ConfigurationError("Skin state_effects is invalid")
    return value


def apply_appearance_snapshot(document: dict, snapshot: object) -> dict:
    """Copy a validated skin into canonical display settings, without saving."""
    snapshot = validate_appearance_snapshot(snapshot)
    result = copy.deepcopy(document)
    display = result["display"]
    display["iris_color"] = snapshot["eyes"]["iris_color"]
    display["backgrounds"].update(snapshot["backgrounds"])
    display["eye_safe_layer"] = snapshot["eye_safe_layer"]
    display["state_effects"] = snapshot["state_effects"]
    return result


def available_appearance_snapshot(snapshot: object, config_dir: Path) -> tuple[dict, list[str]]:
    """Keep missing managed assets in a skin, but omit them from an applied config."""
    value = validate_appearance_snapshot(snapshot)
    missing = []
    for state in _STATES:
        asset = value["backgrounds"][state]
        if asset is not None and not (Path(config_dir) / asset).is_file():
            value["backgrounds"][state] = None
            missing.append(state)
    return value, missing


@dataclass(frozen=True)
class SkinDefinition:
    id: str
    name: str
    description: str
    appearance: dict
    built_in: bool = False

    def document(self) -> dict:
        return {"id": self.id, "name": self.name, "description": self.description,
                "appearance": copy.deepcopy(self.appearance), "built_in": self.built_in}


def built_in_skins() -> tuple[SkinDefinition, ...]:
    default = {"eyes": {"iris_color": "cyan"},
               "backgrounds": {"enabled": False, **{state: None for state in _STATES}},
               "eye_safe_layer": {"enabled": True, "mode": "balanced"},
               "state_effects": {"enabled": False, "preset": "balanced", "palette": "phos", "reduce_motion": False}}
    minimal = copy.deepcopy(default); minimal["eyes"]["iris_color"] = "white"; minimal["eye_safe_layer"]["mode"] = "subtle"
    ambient = copy.deepcopy(default); ambient["eyes"]["iris_color"] = "violet"; ambient["state_effects"] = {"enabled": True, "preset": "balanced", "palette": "aqua", "reduce_motion": False}
    return (SkinDefinition("phos-default", "PHOS Default", "The standard PHOS appearance.", default, True),
            SkinDefinition("minimal", "Minimal", "Reduced visual complexity.", minimal, True),
            SkinDefinition("ambient", "Ambient", "A calm PHOS effect palette.", ambient, True))


class SkinRepository:
    """Persistent custom skins beside the canonical runtime configuration."""
    def __init__(self, config_path: Path): self.path = Path(config_path).parent / "skins.json"

    def _custom(self) -> list[SkinDefinition]:
        if not self.path.exists(): return []
        try: raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error: raise ConfigurationError("Skin repository is unreadable") from error
        if not isinstance(raw, dict) or set(raw) != {"skins"} or not isinstance(raw["skins"], list):
            raise ConfigurationError("Skin repository is malformed")
        result = []
        for item in raw["skins"]:
            if not isinstance(item, dict) or set(item) != {"id", "name", "description", "appearance"}:
                raise ConfigurationError("Skin definition is malformed")
            if (not isinstance(item["id"], str) or not item["id"].startswith("custom-") or not isinstance(item["name"], str)
                    or not item["name"].strip() or len(item["name"]) > 80 or not isinstance(item["description"], str) or len(item["description"]) > 240):
                raise ConfigurationError("Skin definition has invalid metadata")
            result.append(SkinDefinition(item["id"], item["name"].strip(), item["description"].strip(), validate_appearance_snapshot(item["appearance"])))
        if len({skin.id for skin in result}) != len(result): raise ConfigurationError("Skin identifiers must be unique")
        return result

    def _save(self, skins: list[SkinDefinition]) -> None:
        payload = {"skins": [{"id": skin.id, "name": skin.name, "description": skin.description, "appearance": skin.appearance} for skin in skins]}
        atomic_write_text(self.path, json.dumps(payload, indent=2, sort_keys=True) + "\n")

    def list(self) -> list[SkinDefinition]: return [*built_in_skins(), *self._custom()]
    def get(self, skin_id: str) -> SkinDefinition:
        for skin in self.list():
            if skin.id == skin_id: return skin
        raise ConfigurationError("Skin was not found")
    def create(self, name: str, description: str, appearance: dict) -> SkinDefinition:
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 80 or not isinstance(description, str) or len(description) > 240:
            raise ConfigurationError("Skin name or description is invalid")
        skins = self._custom(); skin = SkinDefinition(f"custom-{uuid.uuid4().hex}", name.strip(), description.strip(), validate_appearance_snapshot(appearance))
        skins.append(skin); self._save(skins); return skin
    def rename(self, skin_id: str, name: str, description: str) -> SkinDefinition:
        skins = self._custom()
        for index, skin in enumerate(skins):
            if skin.id == skin_id:
                replacement = SkinDefinition(skin.id, name.strip() if isinstance(name, str) else "", description.strip() if isinstance(description, str) else "", skin.appearance)
                if not replacement.name or len(replacement.name) > 80 or len(replacement.description) > 240: raise ConfigurationError("Skin name or description is invalid")
                skins[index] = replacement; self._save(skins); return replacement
        raise ConfigurationError("Built-in skins cannot be renamed or skin was not found")
    def delete(self, skin_id: str) -> None:
        skins = self._custom(); remaining = [skin for skin in skins if skin.id != skin_id]
        if len(remaining) == len(skins): raise ConfigurationError("Built-in skins cannot be deleted or skin was not found")
        self._save(remaining)

    def export(self, skin_id: str) -> bytes:
        skin = self.get(skin_id)
        return yaml.safe_dump({"schema_version": SKIN_SCHEMA_VERSION, "name": skin.name,
                               "description": skin.description, "appearance": skin.appearance},
                              default_flow_style=False, allow_unicode=True, sort_keys=False).encode("utf-8")

    def import_yaml(self, content: bytes) -> SkinDefinition:
        if not isinstance(content, (bytes, bytearray)) or not content or len(content) > MAX_SKIN_IMPORT_BYTES:
            raise ConfigurationError("Skin import must be a YAML file no larger than 256 KiB")
        try: value = yaml.safe_load(bytes(content).decode("utf-8"))
        except (UnicodeDecodeError, yaml.YAMLError) as error: raise ConfigurationError("Skin import is not valid YAML") from error
        if not isinstance(value, dict) or set(value) != {"schema_version", "name", "description", "appearance"}:
            raise ConfigurationError("Skin import has unsupported fields")
        if value["schema_version"] != SKIN_SCHEMA_VERSION:
            raise ConfigurationError("Skin import has an unsupported schema version")
        # Never accept an exported/internal id: imported skins are always local custom skins.
        return self.create(value["name"], value["description"], value["appearance"])
