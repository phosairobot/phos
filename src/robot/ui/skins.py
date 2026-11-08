"""Named, validated presets for PHOS's existing appearance configuration."""
from __future__ import annotations

import copy
import json
import re
import uuid
import io
import zipfile
import logging
from dataclasses import dataclass
from pathlib import Path
import yaml

from robot.config import ConfigurationError, atomic_write_text
from robot.iris_colors import IrisColorResolver


_STATES = ("idle", "listening", "thinking", "speaking", "sleeping", "error")
_SAFE_ASSET = re.compile(r"backgrounds/(?:skins/custom-[a-f0-9]+/)?[A-Za-z0-9][A-Za-z0-9._ -]*\.(?:png|jpe?g)$", re.IGNORECASE)
_PACKAGE_ASSET = re.compile(r"assets/[A-Za-z0-9][A-Za-z0-9._ -]*\.(?:png|jpe?g)$", re.IGNORECASE)
_EYE_SAFE_MODES = {"subtle", "balanced", "expressive"}
_EFFECT_PRESETS = _EYE_SAFE_MODES
_EFFECT_PALETTES = {"phos", "cyan", "blue", "violet", "aqua", "amber", "white", "green"}
# Themeable semantic intents. Warning and error retain their safety colours in
# SemanticLedService and are deliberately not skin-configurable.
LED_PALETTE_STATES = ("idle", "listening", "thinking", "speaking", "sleeping", "success", "attention", "ambient")
_LED_COLOR = re.compile(r"#[0-9A-Fa-f]{6}")
SKIN_SCHEMA_VERSION = 3
MAX_SKIN_IMPORT_BYTES = 256 * 1024
MAX_SKIN_PACKAGE_BYTES = 32 * 1024 * 1024
MAX_SKIN_UNPACKED_BYTES = 32 * 1024 * 1024
MAX_SKIN_PACKAGE_FILES = 16
MAX_SKIN_ENTRY_BYTES = 4 * 1024 * 1024
MAX_SKIN_IMAGE_BYTES = MAX_SKIN_ENTRY_BYTES
CATALOG_SCHEMA_VERSION = 1
MAX_CATALOG_INHERITANCE_DEPTH = 8
logger = logging.getLogger(__name__)


def appearance_snapshot(document: dict) -> dict:
    """Return only stable, user-facing appearance settings from a config document."""
    display = document["display"]
    return {
        "eyes": {"iris_color": display["iris_color"]},
        "backgrounds": {key: display["backgrounds"].get(key) for key in ("enabled", *_STATES)},
        "eye_safe_layer": copy.deepcopy(display["eye_safe_layer"]),
        "state_effects": copy.deepcopy(display["state_effects"]),
    }


def validate_appearance_snapshot(snapshot: object, *, asset_pattern: re.Pattern = _SAFE_ASSET) -> dict:
    if not isinstance(snapshot, dict) or set(snapshot) != {"eyes", "backgrounds", "eye_safe_layer", "state_effects"}:
        raise ConfigurationError("Skin appearance must contain eyes, backgrounds, eye_safe_layer and state_effects")
    value = copy.deepcopy(snapshot)
    eyes, backgrounds = value["eyes"], value["backgrounds"]
    safe, effects = value["eye_safe_layer"], value["state_effects"]
    if not isinstance(eyes, dict) or set(eyes) != {"iris_color"}:
        raise ConfigurationError("Skin eyes.iris_color is unsupported")
    try:
        eyes["iris_color"] = IrisColorResolver.normalize(eyes["iris_color"])
    except ValueError as error:
        raise ConfigurationError("Skin eyes.iris_color is unsupported") from error
    if not isinstance(backgrounds, dict) or set(backgrounds) != {"enabled", *_STATES} or type(backgrounds["enabled"]) is not bool:
        raise ConfigurationError("Skin backgrounds must contain enabled and every supported robot state")
    for state in _STATES:
        asset = backgrounds[state]
        if asset is not None and (not isinstance(asset, str) or not asset_pattern.fullmatch(asset)):
            raise ConfigurationError(f"Skin background {state} must be a managed background asset or null")
    if not isinstance(safe, dict) or set(safe) != {"enabled", "mode"} or type(safe["enabled"]) is not bool or safe["mode"] not in _EYE_SAFE_MODES:
        raise ConfigurationError("Skin eye_safe_layer is invalid")
    if (not isinstance(effects, dict) or set(effects) != {"enabled", "preset", "palette", "reduce_motion"}
            or type(effects["enabled"]) is not bool or type(effects["reduce_motion"]) is not bool
            or effects["preset"] not in _EFFECT_PRESETS or effects["palette"] not in _EFFECT_PALETTES):
        raise ConfigurationError("Skin state_effects is invalid")
    return value


def validate_led_palette(palette: object) -> dict | None:
    """Validate optional semantic LED theme data without selecting LED output.

    ``None`` means no palette is defined.  A disabled palette is an explicit
    inheritance stop; enabled palettes may be partial so catalog children can
    override only the colours they name.
    """
    if palette is None:
        return None
    if not isinstance(palette, dict) or set(palette) != {"enabled", "colors"} or type(palette["enabled"]) is not bool:
        raise ConfigurationError("Skin LED palette must contain enabled and colors")
    colors = palette["colors"]
    if not isinstance(colors, dict) or set(colors) - set(LED_PALETTE_STATES):
        raise ConfigurationError("Skin LED palette contains unsupported semantic states")
    if not palette["enabled"] and colors:
        raise ConfigurationError("A disabled skin LED palette must not define colors")
    normalized = {}
    for state, color in colors.items():
        if not isinstance(color, str) or not _LED_COLOR.fullmatch(color):
            raise ConfigurationError(f"Skin LED palette color for {state} must be #RRGGBB")
        normalized[state] = color.upper()
    return {"enabled": palette["enabled"], "colors": normalized}


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


def appearance_semantically_matches(current: object, skin: object, config_dir: Path) -> bool:
    """Compare stable appearance settings, allowing identical managed images at different paths."""
    try:
        current = validate_appearance_snapshot(current)
        skin = validate_appearance_snapshot(skin)
    except ConfigurationError:
        return False
    if any(current[key] != skin[key] for key in ("eyes", "eye_safe_layer", "state_effects")):
        return False
    current_backgrounds, skin_backgrounds = current["backgrounds"], skin["backgrounds"]
    if current_backgrounds["enabled"] != skin_backgrounds["enabled"]:
        return False
    root = (Path(config_dir) / "backgrounds").resolve()
    for state in _STATES:
        current_asset, skin_asset = current_backgrounds[state], skin_backgrounds[state]
        if current_asset == skin_asset:
            continue
        if not isinstance(current_asset, str) or not isinstance(skin_asset, str):
            return False
        current_path, skin_path = (Path(config_dir) / current_asset).resolve(), (Path(config_dir) / skin_asset).resolve()
        if (root not in current_path.parents or root not in skin_path.parents or not current_path.is_file()
                or not skin_path.is_file() or current_path.read_bytes() != skin_path.read_bytes()):
            return False
    return True


@dataclass(frozen=True)
class SkinDefinition:
    id: str
    name: str
    description: str
    appearance: dict
    built_in: bool = False
    led_palette: dict | None = None

    def document(self) -> dict:
        return {"id": self.id, "name": self.name, "description": self.description,
                "appearance": copy.deepcopy(self.appearance), "built_in": self.built_in,
                "led_palette": copy.deepcopy(self.led_palette)}


def skin_to_manifest(skin: SkinDefinition, appearance: object) -> dict:
    """Serialize the one v2 package manifest shape used by every exporter."""
    if not isinstance(skin.name, str) or not isinstance(skin.description, str):
        raise ConfigurationError("Skin package metadata is invalid")
    result = {"schema_version": SKIN_SCHEMA_VERSION, "name": skin.name,
              "description": skin.description, "appearance": copy.deepcopy(appearance)}
    if skin.led_palette is not None:
        result["led_palette"] = validate_led_palette(skin.led_palette)
    return result


def skin_from_manifest(manifest: object) -> tuple[str, str, dict, dict | None]:
    """Validate v2 legacy and v3 package manifests."""
    if (not isinstance(manifest, dict) or set(manifest) - {"schema_version", "name", "description", "appearance", "led_palette"}
            or set(manifest) - {"schema_version", "name", "description", "appearance"} and manifest.get("schema_version") != 3
            or manifest.get("schema_version") not in {2, 3}
            or not {"schema_version", "name", "description", "appearance"} <= set(manifest)):
        raise ConfigurationError("Skin package has an unsupported manifest")
    if manifest["schema_version"] == 2 and "led_palette" in manifest:
        raise ConfigurationError("Skin package has an unsupported manifest")
    if not isinstance(manifest["name"], str) or not isinstance(manifest["description"], str):
        raise ConfigurationError("Skin package metadata is invalid")
    return (manifest["name"], manifest["description"],
            validate_appearance_snapshot(manifest["appearance"], asset_pattern=_PACKAGE_ASSET),
            validate_led_palette(manifest.get("led_palette")))


def built_in_skins() -> tuple[SkinDefinition, ...]:
    default = {"eyes": {"iris_color": IrisColorResolver.PRESETS["cyan"]},
               "backgrounds": {"enabled": False, **{state: None for state in _STATES}},
               "eye_safe_layer": {"enabled": True, "mode": "balanced"},
               "state_effects": {"enabled": False, "preset": "balanced", "palette": "phos", "reduce_motion": False}}
    minimal = copy.deepcopy(default); minimal["eyes"]["iris_color"] = IrisColorResolver.PRESETS["white"]; minimal["eye_safe_layer"]["mode"] = "subtle"
    ambient = copy.deepcopy(default); ambient["eyes"]["iris_color"] = IrisColorResolver.PRESETS["violet"]; ambient["state_effects"] = {"enabled": True, "preset": "balanced", "palette": "aqua", "reduce_motion": False}
    ambient_palette = {"enabled": True, "colors": {"idle": "#38BDF8", "listening": "#22D3EE",
                      "thinking": "#818CF8", "speaking": "#60A5FA", "sleeping": "#1E3A8A",
                      "ambient": "#F59E0B"}}
    return (SkinDefinition("phos-default", "PHOS Default", "The standard PHOS appearance.", default, True),
            SkinDefinition("minimal", "Minimal", "Reduced visual complexity.", minimal, True),
            SkinDefinition("ambient", "Ambient", "A calm PHOS effect palette.", ambient, True, ambient_palette))


class SkinRepository:
    """Built-in, declarative, and persistent custom skins in one catalog."""
    def __init__(self, config_path: Path, *, catalog_file: Path | None = None):
        self.path = Path(config_path).parent / "skins.json"
        self.catalog_file = None if catalog_file is None else Path(catalog_file)

    @staticmethod
    def _merge(base: dict, override: dict) -> dict:
        result = copy.deepcopy(base)
        for key, value in override.items():
            if isinstance(value, dict) and isinstance(result.get(key), dict):
                result[key] = SkinRepository._merge(result[key], value)
            else:
                result[key] = copy.deepcopy(value)
        return result

    def _catalog(self) -> list[SkinDefinition]:
        if self.catalog_file is None:
            return []
        root = self.path.parent.resolve()
        catalog = self.catalog_file.resolve()
        if root not in catalog.parents or catalog.suffix.lower() not in {".yaml", ".yml"}:
            raise ConfigurationError("Skin catalog must be a YAML file beneath the PHOS configuration directory")
        try:
            raw = yaml.safe_load(catalog.read_text(encoding="utf-8"))
        except FileNotFoundError as error:
            raise ConfigurationError(f"Configured skin catalog is missing: {catalog.name}") from error
        except (OSError, UnicodeError, yaml.YAMLError) as error:
            raise ConfigurationError("Configured skin catalog is unreadable or invalid YAML") from error
        if not isinstance(raw, dict) or set(raw) != {"schema_version", "skins"} or raw["schema_version"] != CATALOG_SCHEMA_VERSION or not isinstance(raw["skins"], list):
            raise ConfigurationError("Skin catalog must contain schema_version: 1 and a skins list")
        definitions = {}
        for item in raw["skins"]:
            if (not isinstance(item, dict) or not {"id", "display_name", "inherits", "appearance"} <= set(item)
                    or set(item) - {"id", "display_name", "inherits", "appearance", "led_palette"}):
                raise ConfigurationError("Skin catalog definition has unsupported fields")
            skin_id = item["id"]
            if not isinstance(skin_id, str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", skin_id):
                raise ConfigurationError("Skin catalog identifiers must be lowercase stable IDs")
            if skin_id in definitions:
                raise ConfigurationError(f"Duplicate skin catalog identifier: {skin_id}")
            if (not isinstance(item["display_name"], str) or not item["display_name"].strip()
                    or len(item["display_name"].strip()) > 80 or item["inherits"] is not None and not isinstance(item["inherits"], str)
                    or not isinstance(item["appearance"], dict)):
                raise ConfigurationError(f"Skin catalog definition is invalid: {skin_id}")
            definitions[skin_id] = item
        bases = {skin.id: skin for skin in built_in_skins()}
        resolved, resolving = {}, set()

        def resolve(skin_id: str, depth: int = 0) -> SkinDefinition:
            if skin_id in resolved:
                return resolved[skin_id]
            if depth > MAX_CATALOG_INHERITANCE_DEPTH:
                raise ConfigurationError("Skin catalog inheritance exceeds the maximum depth")
            if skin_id in resolving:
                raise ConfigurationError("Skin catalog inheritance contains a cycle")
            item = definitions.get(skin_id)
            if item is None:
                if skin_id in bases:
                    return bases[skin_id]
                raise ConfigurationError(f"Skin catalog parent is unknown: {skin_id}")
            resolving.add(skin_id)
            parent_id = item["inherits"] or "phos-default"
            parent = resolve(parent_id, depth + 1)
            appearance = validate_appearance_snapshot(self._merge(parent.appearance, item["appearance"]))
            if "led_palette" not in item:
                led_palette = parent.led_palette
            elif item["led_palette"] is None:
                led_palette = None
            else:
                child_palette = validate_led_palette(item["led_palette"])
                if not child_palette["enabled"]:
                    led_palette = child_palette
                else:
                    inherited = parent.led_palette or {"enabled": True, "colors": {}}
                    led_palette = validate_led_palette({"enabled": True,
                        "colors": {**(inherited["colors"] if inherited["enabled"] else {}), **child_palette["colors"]}})
            resolving.remove(skin_id)
            value = SkinDefinition(skin_id, item["display_name"].strip(), "", appearance, False, led_palette)
            resolved[skin_id] = value
            return value

        catalog = [resolve(skin_id) for skin_id in definitions]
        for skin in catalog:
            for state in _STATES:
                asset = skin.appearance["backgrounds"][state]
                if asset is None:
                    continue
                target = (root / asset).resolve()
                backgrounds = (root / "backgrounds").resolve()
                if backgrounds not in target.parents or not target.is_file():
                    raise ConfigurationError(f"Skin catalog background is unavailable: {skin.id}/{state}")
        return catalog

    def _custom(self) -> list[SkinDefinition]:
        if not self.path.exists(): return []
        try: raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error: raise ConfigurationError("Skin repository is unreadable") from error
        if not isinstance(raw, dict) or set(raw) != {"skins"} or not isinstance(raw["skins"], list):
            raise ConfigurationError("Skin repository is malformed")
        result = []
        for item in raw["skins"]:
            if (not isinstance(item, dict) or not {"id", "name", "description", "appearance"} <= set(item)
                    or set(item) - {"id", "name", "description", "appearance", "led_palette"}):
                raise ConfigurationError("Skin definition is malformed")
            if (not isinstance(item["id"], str) or not item["id"].startswith("custom-") or not isinstance(item["name"], str)
                    or not item["name"].strip() or len(item["name"]) > 80 or not isinstance(item["description"], str) or len(item["description"]) > 240):
                raise ConfigurationError("Skin definition has invalid metadata")
            result.append(SkinDefinition(item["id"], item["name"].strip(), item["description"].strip(), validate_appearance_snapshot(item["appearance"]), False, validate_led_palette(item.get("led_palette"))))
        if len({skin.id for skin in result}) != len(result): raise ConfigurationError("Skin identifiers must be unique")
        return result

    def _save(self, skins: list[SkinDefinition]) -> None:
        payload = {"skins": [{"id": skin.id, "name": skin.name, "description": skin.description,
                               "appearance": skin.appearance,
                               **({"led_palette": skin.led_palette} if skin.led_palette is not None else {})} for skin in skins]}
        atomic_write_text(self.path, json.dumps(payload, indent=2, sort_keys=True) + "\n")

    def list(self) -> list[SkinDefinition]:
        skins = [*built_in_skins(), *self._catalog(), *self._custom()]
        ids = [skin.id for skin in skins]
        if len(ids) != len(set(ids)):
            raise ConfigurationError("Skin identifiers conflict between built-in, catalog, or custom skins")
        return skins
    def _asset_dir(self, skin_id: str) -> Path:
        if not re.fullmatch(r"custom-[a-f0-9]+", skin_id): raise ConfigurationError("Invalid custom skin identifier")
        return self.path.parent / "backgrounds" / "skins" / skin_id
    def get(self, skin_id: str) -> SkinDefinition:
        for skin in self.list():
            if skin.id == skin_id: return skin
        raise ConfigurationError("Skin was not found")

    def resolve_led_palette(self, skin_id: str) -> dict:
        """Return read-only palette metadata; never alters LED configuration.

        A skin without a palette falls back to the semantic LED service's
        canonical command colors.  Disabled means the skin intentionally opts
        out, while enabled partial palettes retain those defaults for omitted
        semantic states.
        """
        skin = self.get(skin_id)
        palette = validate_led_palette(skin.led_palette)
        if palette is None:
            return {"state": "missing", "skin_id": skin.id, "colors": {}}
        if not palette["enabled"]:
            return {"state": "disabled", "skin_id": skin.id, "colors": {}}
        return {"state": "configured", "skin_id": skin.id, "colors": copy.deepcopy(palette["colors"])}
    def create(self, name: str, description: str, appearance: dict, led_palette: dict | None = None) -> SkinDefinition:
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 80 or not isinstance(description, str) or len(description) > 240:
            raise ConfigurationError("Skin name or description is invalid")
        skin_id = f"custom-{uuid.uuid4().hex}"; snapshot = validate_appearance_snapshot(appearance)
        led_palette = validate_led_palette(led_palette)
        target = self._asset_dir(skin_id); target.mkdir(parents=True, exist_ok=False)
        copied = {}
        try:
            for state in _STATES:
                source = snapshot["backgrounds"][state]
                if source is None: continue
                path = (self.path.parent / source).resolve()
                root = (self.path.parent / "backgrounds").resolve()
                if not path.is_file() or root not in path.parents: raise ConfigurationError(f"Skin background {state} is unavailable")
                if path not in copied:
                    copied_name = f"{len(copied)}-{path.name}"; destination = target / copied_name
                    destination.write_bytes(path.read_bytes()); copied[path] = destination
                snapshot["backgrounds"][state] = f"backgrounds/skins/{skin_id}/{copied[path].name}"
            skin = SkinDefinition(skin_id, name.strip(), description.strip(), snapshot, False, led_palette)
            skins = self._custom(); skins.append(skin); self._save(skins); return skin
        except Exception:
            for item in target.glob("*"): item.unlink()
            target.rmdir(); raise
    def rename(self, skin_id: str, name: str, description: str) -> SkinDefinition:
        skins = self._custom()
        for index, skin in enumerate(skins):
            if skin.id == skin_id:
                replacement = SkinDefinition(skin.id, name.strip() if isinstance(name, str) else "", description.strip() if isinstance(description, str) else "", skin.appearance, skin.built_in, skin.led_palette)
                if not replacement.name or len(replacement.name) > 80 or len(replacement.description) > 240: raise ConfigurationError("Skin name or description is invalid")
                skins[index] = replacement; self._save(skins); return replacement
        raise ConfigurationError("Built-in skins cannot be renamed or skin was not found")
    def delete(self, skin_id: str) -> None:
        skins = self._custom(); remaining = [skin for skin in skins if skin.id != skin_id]
        if len(remaining) == len(skins): raise ConfigurationError("Built-in skins cannot be deleted or skin was not found")
        self._save(remaining)
        directory = self._asset_dir(skin_id)
        if directory.exists():
            for item in directory.iterdir():
                if item.is_file(): item.unlink()
            directory.rmdir()

    def export(self, skin_id: str) -> bytes:
        skin = self.get(skin_id)
        appearance = validate_appearance_snapshot(skin.appearance)
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            included = {}
            for state in _STATES:
                reference = appearance["backgrounds"][state]
                if reference is None: continue
                path = (self.path.parent / reference).resolve()
                root = (self.path.parent / "backgrounds").resolve()
                if not path.is_file() or root not in path.parents: raise ConfigurationError(f"Skin background {state} is unavailable for export")
                if path not in included:
                    filename = f"{len(included)}-{path.name}"; included[path] = filename
                    archive.writestr(f"assets/{filename}", path.read_bytes())
                appearance["backgrounds"][state] = f"assets/{included[path]}"
            archive.writestr("skin.yaml", yaml.safe_dump(skin_to_manifest(skin, appearance),
                                                          default_flow_style=False, allow_unicode=True, sort_keys=False))
        return stream.getvalue()

    def import_yaml(self, content: bytes) -> SkinDefinition:
        if not isinstance(content, (bytes, bytearray)) or not content or len(content) > MAX_SKIN_IMPORT_BYTES:
            raise ConfigurationError("Skin import must be a YAML file no larger than 256 KiB")
        try: value = yaml.safe_load(bytes(content).decode("utf-8"))
        except (UnicodeDecodeError, yaml.YAMLError) as error: raise ConfigurationError("Skin import is not valid YAML") from error
        if (not isinstance(value, dict) or set(value) - {"schema_version", "name", "description", "appearance", "led_palette"}
                or not {"schema_version", "name", "description", "appearance"} <= set(value)):
            raise ConfigurationError("Skin import has unsupported fields")
        if value["schema_version"] != 1:
            raise ConfigurationError("Skin import has an unsupported schema version")
        # Never accept an exported/internal id: imported skins are always local custom skins.
        return self.create(value["name"], value["description"], value["appearance"], value.get("led_palette"))

    def import_package(self, content: bytes) -> SkinDefinition:
        """Install a v2 ZIP package into the managed background namespace."""
        if not isinstance(content, (bytes, bytearray)) or not content or len(content) > MAX_SKIN_PACKAGE_BYTES:
            raise ConfigurationError("Skin package must be no larger than 32 MiB")
        try:
            archive = zipfile.ZipFile(io.BytesIO(content))
            entries = archive.infolist()
        except zipfile.BadZipFile as error: raise ConfigurationError("Skin package is not a valid archive") from error
        unpacked_bytes = sum(item.file_size for item in entries)
        if (len(entries) > MAX_SKIN_PACKAGE_FILES or unpacked_bytes > MAX_SKIN_UNPACKED_BYTES
                or any(item.file_size > MAX_SKIN_ENTRY_BYTES for item in entries)):
            logger.info("PHOS SKIN IMPORT: package_bytes=%d max_package_bytes=%d unpacked_bytes=%d max_unpacked_bytes=%d entry_count=%d",
                        len(content), MAX_SKIN_PACKAGE_BYTES, unpacked_bytes, MAX_SKIN_UNPACKED_BYTES, len(entries))
            for entry in entries:
                logger.info("PHOS SKIN ENTRY: name=%s file_size=%d compress_size=%d",
                            entry.filename, entry.file_size, entry.compress_size)
            raise ConfigurationError("Skin package exceeds file or unpacked-size limits")
        names = [item.filename for item in entries]
        if len(set(names)) != len(names):
            raise ConfigurationError("Skin package contains duplicate archive entries")
        unsafe_entries = []
        for item in entries:
            name = item.filename
            file_type = (item.external_attr >> 16) & 0o170000
            reasons = []
            if "\\" in name:
                reasons.append("backslash_path")
            if name.startswith("/"):
                reasons.append("absolute_path")
            if ".." in Path(name).parts:
                reasons.append("traversal_path")
            if item.is_dir():
                if name != "assets/":
                    reasons.append("unsupported_directory")
                if file_type not in (0, 0o040000):
                    reasons.append("special_file")
            elif file_type not in (0, 0o100000):
                reasons.append("special_file")
            if reasons:
                unsafe_entries.append((name, ",".join(reasons)))
        if names.count("skin.yaml") != 1 or unsafe_entries:
            for name, reason in unsafe_entries:
                logger.info("PHOS SKIN IMPORT: unsafe_entry name=<%s> reason=<%s>", name, reason)
            raise ConfigurationError("Skin package has unsafe archive entries")
        asset_entries = [item for item in entries if not item.is_dir()]
        asset_names = {item.filename for item in asset_entries if item.filename != "skin.yaml"}
        if any(item.filename != "skin.yaml" and not re.fullmatch(r"assets/[A-Za-z0-9][A-Za-z0-9._ -]*\.(?:png|jpe?g)", item.filename, re.I) for item in asset_entries):
            raise ConfigurationError("Skin package contains unsupported files")
        try: manifest = yaml.safe_load(archive.read("skin.yaml").decode("utf-8"))
        except (UnicodeDecodeError, yaml.YAMLError) as error: raise ConfigurationError("Skin package manifest is not valid YAML") from error
        name, description, appearance, led_palette = skin_from_manifest(manifest)
        backgrounds = appearance["backgrounds"]
        skin_id = f"custom-{uuid.uuid4().hex}"
        referenced = {backgrounds[state] for state in _STATES if backgrounds.get(state) is not None}
        packaged_assets = asset_names
        if referenced != packaged_assets:
            raise ConfigurationError("Skin package assets must be referenced exactly once or more by the manifest")
        target = self.path.parent / "backgrounds" / "skins" / skin_id
        target.mkdir(parents=True, exist_ok=False)
        try:
            for state in _STATES:
                asset = backgrounds.get(state)
                if asset is None: continue
                if not isinstance(asset, str) or not _PACKAGE_ASSET.fullmatch(asset) or asset not in names:
                    raise ConfigurationError(f"Skin package background {state} is invalid or missing")
                data = archive.read(asset)
                is_png, is_jpeg = data.startswith(b"\x89PNG\r\n\x1a\n"), data.startswith(b"\xff\xd8")
                if len(data) > MAX_SKIN_IMAGE_BYTES or not (is_png or is_jpeg) or (asset.lower().endswith(".png") != is_png):
                    raise ConfigurationError("Skin package contains an invalid image")
                try:
                    import cv2, numpy as np
                    decoded = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
                    if decoded is None or decoded.shape[1] > 1920 or decoded.shape[0] > 1080: raise ValueError
                except ImportError:
                    pass
                except ValueError:
                    raise ConfigurationError("Skin package image is corrupt or exceeds dimensions")
                filename = Path(asset).name
                with open(target / filename, "xb") as output: output.write(data)
                backgrounds[state] = f"backgrounds/skins/{skin_id}/{filename}"
            skin = SkinDefinition(skin_id, name.strip(), description.strip(), validate_appearance_snapshot(appearance), False, led_palette)
            skins = self._custom(); skins.append(skin); self._save(skins); return skin
        except Exception:
            for path in target.glob("*"): path.unlink()
            target.rmdir()
            raise
