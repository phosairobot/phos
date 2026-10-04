from robot.core.startup import StartupReadiness, StartupState
from robot.config import RuntimeConfig, load_document


def test_required_components_produce_ready_and_optional_absence_degrades():
    startup = StartupReadiness(clock=lambda: 10.0)
    startup.begin("display", required=True)
    startup.begin("vision")
    assert startup.overall_state is StartupState.STARTING
    startup.set("display", StartupState.READY)
    startup.set("vision", StartupState.READY)
    assert startup.overall_state is StartupState.READY

    startup.set("vision", StartupState.UNAVAILABLE, "camera disabled")
    assert startup.overall_state is StartupState.DEGRADED
    assert startup.document()["degraded_reasons"] == ["camera disabled"]


def test_required_failure_remains_failed():
    startup = StartupReadiness()
    startup.begin("display", required=True)
    startup.set("display", StartupState.FAILED, "display unavailable")
    assert startup.overall_state is StartupState.FAILED


def test_startup_section_is_injected_for_existing_complete_documents(tmp_path):
    document = load_document()
    document.pop("startup")
    path = tmp_path / "phos.json"
    import json
    path.write_text(json.dumps(document))
    config = RuntimeConfig.from_file(path)
    assert config.startup_splash_enabled is True
    assert config.startup_ready_sound_enabled is False


def test_default_startup_assets_are_package_relative_and_overrides_are_config_relative(tmp_path):
    config = RuntimeConfig.from_file()
    assert config.resolve_startup_splash_image().name == "phos-startup-800x600.png"
    assert config.resolve_startup_ready_sound().name == "phos-startup.wav"

    document = load_document()
    document["startup"]["ready_sound"]["enabled"] = False
    document["startup"]["splash"]["image"] = "images/custom.png"
    document["startup"]["ready_sound"]["file"] = "audio/custom.wav"
    override = RuntimeConfig.from_dict(document, base_dir=tmp_path)
    assert override.resolve_startup_splash_image() == tmp_path / "images/custom.png"
    assert override.resolve_startup_ready_sound() == tmp_path / "audio/custom.wav"
