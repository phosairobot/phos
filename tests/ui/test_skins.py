import copy
import yaml

import pytest

from robot.config import ConfigurationError, load_document
from robot.ui.skins import (SkinRepository, apply_appearance_snapshot, appearance_snapshot,
                            available_appearance_snapshot, built_in_skins, validate_appearance_snapshot,
                            MAX_SKIN_IMPORT_BYTES)


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


def test_missing_skin_background_is_retained_but_falls_back_when_applied(tmp_path):
    snapshot = appearance_snapshot(load_document())
    snapshot["backgrounds"].update({"enabled": True, "idle": "backgrounds/missing.jpg"})
    resolved, missing = available_appearance_snapshot(snapshot, tmp_path)
    assert missing == ["idle"] and resolved["backgrounds"]["idle"] is None
    assert snapshot["backgrounds"]["idle"] == "backgrounds/missing.jpg"


def test_skin_export_import_round_trip_regenerates_custom_id(tmp_path):
    repository = SkinRepository(tmp_path / "phos.yaml")
    exported = repository.export("ambient")
    imported = repository.import_yaml(exported)
    assert imported.id.startswith("custom-") and imported.id != "ambient"
    assert imported.appearance == next(skin.appearance for skin in built_in_skins() if skin.id == "ambient")
    assert yaml.safe_load(exported)["schema_version"] == 1


@pytest.mark.parametrize("content", [b"[", b"schema_version: 2", b"x" * (MAX_SKIN_IMPORT_BYTES + 1), b"!!python/object/apply:os.system ['bad']"])
def test_skin_import_rejects_malformed_unsupported_or_oversized_documents(tmp_path, content):
    with pytest.raises(ConfigurationError): SkinRepository(tmp_path / "phos.yaml").import_yaml(content)
