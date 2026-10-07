"""Release metadata and packaged canonical configuration remain consistent."""
import importlib.util
from pathlib import Path
import shutil
import sys

from robot import __version__
from robot.config import DEFAULT_CONFIG_PATH
from robot.web.openapi import load_spec


def test_authoritative_version_and_package_metadata():
    assert __version__ == "1.4.0"
    project = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text()
    assert 'dynamic = ["version"]' in project
    assert 'version = {attr = "robot.__version__"}' in project
    assert load_spec()["info"]["version"] == __version__


def test_installed_package_uses_shipped_canonical_document(tmp_path, monkeypatch):
    # Mimic a venv install with no source-checkout config/ directory.
    source = Path(__file__).resolve().parents[1] / "src/robot/config.py"
    module_path = tmp_path / "lib/robot/config.py"
    module_path.parent.mkdir(parents=True)
    shutil.copyfile(source, module_path)
    config = tmp_path / "share/phos/config/phos.json"
    config.parent.mkdir(parents=True)
    shutil.copyfile(DEFAULT_CONFIG_PATH, config)
    monkeypatch.setattr(sys, "prefix", str(tmp_path))
    spec = importlib.util.spec_from_file_location("packaged_robot_config", module_path)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    assert module.DEFAULT_CONFIG_PATH == config
    assert module.RuntimeConfig.from_file().display_width == 800
