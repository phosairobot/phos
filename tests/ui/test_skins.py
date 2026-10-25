import copy
import base64
import logging
import yaml
import zipfile
import io

import pytest

from robot.config import ConfigurationError, load_document
from robot.ui.skins import (SkinRepository, apply_appearance_snapshot, appearance_snapshot,
                            appearance_semantically_matches, available_appearance_snapshot, built_in_skins, validate_appearance_snapshot,
                            MAX_SKIN_IMPORT_BYTES, MAX_SKIN_PACKAGE_BYTES)


def test_package_limit_is_32_mib():
    assert MAX_SKIN_PACKAGE_BYTES == 32 * 1024 * 1024


def _package(manifest, entries=()):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("skin.yaml", yaml.safe_dump(manifest))
        for name, content in entries: archive.writestr(name, content)
    return stream.getvalue()


_ONE_PIXEL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/"
    "iZ4QWQAAAABJRU5ErkJggg=="
)


def _manifest_with_idle_asset():
    manifest = {"schema_version": 2, "name": "Package", "description": "", "appearance": appearance_snapshot(load_document())}
    manifest["appearance"]["backgrounds"].update({"enabled": True, "idle": "assets/idle.png"})
    return manifest


def test_built_in_default_reproduces_the_canonical_default_appearance():
    default = load_document()
    skin = next(item for item in built_in_skins() if item.id == "phos-default")
    assert apply_appearance_snapshot(default, skin.appearance)["display"] == default["display"]


def test_custom_skin_persists_rename_keeps_id_and_builtin_cannot_be_deleted(tmp_path):
    repository = SkinRepository(tmp_path / "phos.yaml")
    skin = repository.create("My look", "", appearance_snapshot(load_document()))
    assert SkinRepository(tmp_path / "phos.yaml").get(skin.id).name == "My look"
    renamed = repository.rename(skin.id, "Renamed", "Calm")
    assert renamed.id == skin.id and renamed.name == "Renamed"
    with pytest.raises(ConfigurationError): repository.delete("phos-default")
    repository.delete(skin.id)
    with pytest.raises(ConfigurationError): repository.get(skin.id)


def test_skin_validation_rejects_malformed_assets_and_keeps_input_unchanged():
    snapshot = appearance_snapshot(load_document())
    bad = copy.deepcopy(snapshot); bad["backgrounds"]["idle"] = "../../secret.png"
    with pytest.raises(ConfigurationError): validate_appearance_snapshot(bad)
    assert snapshot["backgrounds"]["idle"] is None


def test_skin_normalizes_legacy_presets_and_preserves_custom_iris_hex(tmp_path):
    appearance = appearance_snapshot(load_document())
    appearance["eyes"]["iris_color"] = "violet"
    legacy = validate_appearance_snapshot(appearance)
    assert legacy["eyes"]["iris_color"] == "#A670F5"

    appearance["eyes"]["iris_color"] = "#7a5cff"
    original = SkinRepository(tmp_path / "phos.yaml").create("Custom color", "", appearance)
    imported = SkinRepository(tmp_path / "phos.yaml").import_package(
        SkinRepository(tmp_path / "phos.yaml").export(original.id)
    )
    assert imported.appearance["eyes"]["iris_color"] == "#7A5CFF"


def test_skin_semantic_matching_treats_a_preset_and_its_resolved_hex_as_equal(tmp_path):
    current = appearance_snapshot(load_document())
    legacy_skin = copy.deepcopy(current)
    legacy_skin["eyes"]["iris_color"] = "violet"
    current["eyes"]["iris_color"] = "#A670F5"

    assert appearance_semantically_matches(current, legacy_skin, tmp_path)


def test_missing_skin_background_is_retained_but_falls_back_when_applied(tmp_path):
    snapshot = appearance_snapshot(load_document())
    snapshot["backgrounds"].update({"enabled": True, "idle": "backgrounds/missing.jpg"})
    resolved, missing = available_appearance_snapshot(snapshot, tmp_path)
    assert missing == ["idle"] and resolved["backgrounds"]["idle"] is None
    assert snapshot["backgrounds"]["idle"] == "backgrounds/missing.jpg"


@pytest.mark.parametrize("skin_id, state_effects", [
    ("phos-default", {"enabled": False, "preset": "balanced", "palette": "phos", "reduce_motion": False}),
    ("ambient", {"enabled": True, "preset": "balanced", "palette": "aqua", "reduce_motion": False}),
])
def test_builtin_skin_export_manifest_and_import_round_trip(tmp_path, skin_id, state_effects):
    repository = SkinRepository(tmp_path / "phos.yaml")
    exported = repository.export(skin_id)
    with zipfile.ZipFile(io.BytesIO(exported)) as package:
        manifest = yaml.safe_load(package.read("skin.yaml"))
    imported = repository.import_package(exported)
    expected = next(skin for skin in built_in_skins() if skin.id == skin_id)

    assert manifest == {"schema_version": 2, "name": expected.name, "description": expected.description,
                        "appearance": expected.appearance}
    assert imported.id.startswith("custom-") and imported.id != skin_id
    assert imported.appearance == expected.appearance
    assert imported.appearance["state_effects"] == state_effects


def test_custom_skin_export_import_preserves_appearance_and_state_effects(tmp_path):
    source = tmp_path / "backgrounds" / "idle.png"
    source.parent.mkdir()
    source.write_bytes(_ONE_PIXEL_PNG)
    appearance = appearance_snapshot(load_document())
    appearance["backgrounds"].update({"enabled": True, "idle": "backgrounds/idle.png"})
    appearance["eyes"] = {"iris_color": "amber"}
    appearance["eye_safe_layer"] = {"enabled": False, "mode": "expressive"}
    appearance["state_effects"] = {"enabled": True, "preset": "expressive", "palette": "amber", "reduce_motion": True}
    repository = SkinRepository(tmp_path / "phos.yaml")
    original = repository.create("Custom", "Round trip", appearance)

    imported = repository.import_package(repository.export(original.id))

    assert imported.name == original.name and imported.description == original.description
    assert imported.appearance["eyes"] == original.appearance["eyes"]
    assert imported.appearance["eye_safe_layer"] == original.appearance["eye_safe_layer"]
    assert imported.appearance["state_effects"] == original.appearance["state_effects"]
    assert imported.appearance["backgrounds"]["enabled"] is True
    assert (tmp_path / imported.appearance["backgrounds"]["idle"]).read_bytes() == _ONE_PIXEL_PNG
    assert all(imported.appearance["backgrounds"][state] is None for state in ("listening", "thinking", "speaking", "sleeping", "error"))


@pytest.mark.parametrize("explicit_assets_directory", [True, False])
def test_package_accepts_canonical_assets_directory_entry_or_its_omission(tmp_path, explicit_assets_directory):
    manifest = _manifest_with_idle_asset()
    entries = [("assets/idle.png", _ONE_PIXEL_PNG)]
    if explicit_assets_directory:
        entries.insert(0, ("assets/", b""))

    imported = SkinRepository(tmp_path / "phos.yaml").import_package(_package(manifest, entries))

    assert imported.appearance["backgrounds"]["idle"].endswith("/idle.png")


def test_package_rejects_invalid_state_effects_clearly(tmp_path):
    manifest = _manifest_with_idle_asset()
    manifest["appearance"]["state_effects"] = {"enabled": True}

    with pytest.raises(ConfigurationError, match="Skin state_effects is invalid"):
        SkinRepository(tmp_path / "phos.yaml").import_package(_package(manifest, [("assets/idle.png", _ONE_PIXEL_PNG)]))


@pytest.mark.parametrize("content", [b"[", b"schema_version: 2", b"x" * (MAX_SKIN_IMPORT_BYTES + 1), b"!!python/object/apply:os.system ['bad']"])
def test_skin_import_rejects_malformed_unsupported_or_oversized_documents(tmp_path, content):
    with pytest.raises(ConfigurationError): SkinRepository(tmp_path / "phos.yaml").import_yaml(content)


def test_package_rejects_duplicate_and_unreferenced_archive_entries(tmp_path):
    manifest = {"schema_version": 2, "name": "Package", "description": "", "appearance": appearance_snapshot(load_document())}
    duplicate = io.BytesIO()
    with zipfile.ZipFile(duplicate, "w") as archive:
        archive.writestr("skin.yaml", yaml.safe_dump(manifest)); archive.writestr("skin.yaml", yaml.safe_dump(manifest))
    with pytest.raises(ConfigurationError): SkinRepository(tmp_path / "phos.yaml").import_package(duplicate.getvalue())
    with pytest.raises(ConfigurationError): SkinRepository(tmp_path / "phos.yaml").import_package(_package(manifest, [("assets/unused.jpg", b"\xff\xd8")]))


@pytest.mark.parametrize("entry", ["../skin.yaml", "assets/../bad.jpg", "/assets/bad.jpg", "assets\\bad.jpg", "other.txt"])
def test_package_rejects_unsafe_archive_layout(tmp_path, entry):
    manifest = {"schema_version": 2, "name": "Package", "description": "", "appearance": appearance_snapshot(load_document())}
    with pytest.raises(ConfigurationError): SkinRepository(tmp_path / "phos.yaml").import_package(_package(manifest, [(entry, b"x")]))


def test_package_logs_each_unsafe_archive_entry(tmp_path, caplog):
    manifest = {"schema_version": 2, "name": "Package", "description": "", "appearance": appearance_snapshot(load_document())}
    caplog.set_level(logging.INFO, logger="robot.ui.skins")

    with pytest.raises(ConfigurationError):
        SkinRepository(tmp_path / "phos.yaml").import_package(
            _package(manifest, [("assets/subdir/", b""), ("assets\\bad.png", b"x")])
        )

    assert "PHOS SKIN IMPORT: unsafe_entry name=<assets/subdir/> reason=<unsupported_directory>" in caplog.messages
    assert "PHOS SKIN IMPORT: unsafe_entry name=<assets\\bad.png> reason=<backslash_path>" in caplog.messages
