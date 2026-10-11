"""Release metadata and packaged canonical configuration remain consistent."""
import importlib.util
from pathlib import Path
import shutil
import sys

from robot import __version__
from robot.config import ConfigRepository, DEFAULT_CONFIG_PATH
from robot.web.openapi import load_spec


def test_authoritative_version_and_package_metadata():
    assert __version__ == "1.4.0"
    project = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text()
    assert 'dynamic = ["version"]' in project
    assert 'version = {attr = "robot.__version__"}' in project
    assert '"share/phos/config" = ["config/phos.yaml", "config/phos.json"]' in project
    assert load_spec()["info"]["version"] == __version__


def test_installed_package_uses_shipped_canonical_yaml_and_legacy_json(tmp_path, monkeypatch):
    # Mimic a venv install with no source-checkout config/ directory.
    source = Path(__file__).resolve().parents[1] / "src/robot/config.py"
    module_path = tmp_path / "lib/robot/config.py"
    module_path.parent.mkdir(parents=True)
    shutil.copyfile(source, module_path)
    config_directory = tmp_path / "share/phos/config"
    config_directory.mkdir(parents=True)
    shutil.copyfile(Path(__file__).resolve().parents[1] / "config/phos.yaml", config_directory / "phos.yaml")
    shutil.copyfile(DEFAULT_CONFIG_PATH, config_directory / "phos.json")
    monkeypatch.setattr(sys, "prefix", str(tmp_path))
    spec = importlib.util.spec_from_file_location("packaged_robot_config", module_path)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    assert module.DEFAULT_CONFIG_PATH == config_directory / "phos.json"
    assert module.ConfigRepository().active_path == config_directory / "phos.yaml"
    assert module.ConfigRepository().load().display_width == 800
    assert module.RuntimeConfig.from_file().display_width == 800
