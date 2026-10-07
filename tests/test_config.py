"""Configuration tests use temporary files and never construct a real AWS client."""
import asyncio
import json
from pathlib import Path

import pytest

from robot.config import ConfigurationError, RuntimeConfig, load_document
from robot.runtime import build_runtime
from robot.ui import MemoryEyeDisplay


@pytest.fixture
def document():
    return load_document()


def write_config(tmp_path, document):
    path = tmp_path / "phos.json"
    path.write_text(json.dumps(document))
    return path


def test_canonical_config_is_complete_safe_and_round_trips(document):
    config = RuntimeConfig.from_file()
    assert config.to_dict() == document
    assert not config.vision_enabled
    assert config.display_width == 800 and config.display_height == 600
    assert config.expression_provider == "local"
    assert config == RuntimeConfig()
    assert "_base_dir" not in repr(config)


def test_loaded_values_reach_subsystems(document, tmp_path):
    document["display"].update(width=640, height=480, fps=17, fullscreen=False,
                               transition_seconds=.3, iris_color="violet")
    document["behavior"].update(face_gaze_smoothing=.7, reaction_decay_per_second=.2)
    document["vision"]["face_tracking_enabled"] = True
    document["vision"]["capture_fps"] = 12
    document["vision"]["detector"].update(scale_factor=1.2, min_neighbors=7)
    runtime = build_runtime(config=RuntimeConfig.from_file(write_config(tmp_path, document)), eye_display=MemoryEyeDisplay())
    assert runtime._behavior_engine._face_gaze_smoothing == .7
    assert runtime._behavior_engine._reaction_decay_per_second == .2
    assert runtime._vision_pipeline._capture_interval == pytest.approx(1 / 12)
    assert runtime._vision_pipeline._face_detector._scale_factor == 1.2
    assert runtime._vision_pipeline._face_detector._min_neighbors == 7
    assert runtime._eye_render_loop._renderer._width == 640
    assert runtime._eye_render_loop._renderer.iris_color == "violet"


def test_loaded_configuration_runs_headless(document, tmp_path):
    document["display"].update(width=640, height=480, fps=17, fullscreen=False)
    display = MemoryEyeDisplay()
    runtime = build_runtime(config=RuntimeConfig.from_file(write_config(tmp_path, document)), eye_display=display)
    async def exercise():
        stop = asyncio.Event()
        stop.set()
        await runtime.run(stop)
    asyncio.run(exercise())
    assert display.frames[0].width == 640 and display.frames[0].height == 480


@pytest.mark.parametrize("content", ['{', '[]', 'null', '{"display":{},"display":{}}', '{}'])
def test_malformed_or_incomplete_document_fails(content, tmp_path):
    path = tmp_path / "bad.json"
    path.write_text(content)
    with pytest.raises(ConfigurationError):
        RuntimeConfig.from_file(path)


@pytest.mark.parametrize("route", ["display", "vision.detector.min_size", "expression.provider",
                                   "expression.local.labels", "expression.aws.region", "logging.level"])
def test_missing_required_fields_fail_with_path(document, tmp_path, route):
    keys, value = route.split("."), document
    for key in keys[:-1]:
        value = value[key]
    del value[keys[-1]]
    with pytest.raises(ConfigurationError, match="missing fields"):
        RuntimeConfig.from_file(write_config(tmp_path, document))


@pytest.mark.parametrize("route,value", [
    ("expression.provider", "invalid"), ("expression.provider", []),
    ("expression.enabled", "true"), ("display.fps", True), ("display.fps", 1.5),
    ("display.width", 0), ("display.fullscreen", 1), ("behavior.blink_interval_seconds", [3, 2]),
    ("behavior.face_gaze_smoothing", 2), ("vision.camera_resolution", [640]),
    ("vision.detector.min_size", [900, 900]), ("vision.detector.scale_factor", 1),
    ("vision.detection_fps", float("inf")), ("expression.crop_margin", -1),
    ("expression.local.labels", ["happy", "happy"]), ("expression.local.labels", "happy"),
    ("expression.local.mean", [1, 2]), ("expression.local.scale", float("nan")),
    ("expression.local.model_path", ""), ("expression.local.model_path", 42),
    ("expression.smoothing.minimum_observations", 0),
    ("expression.aws.cooldown_seconds", 0), ("expression.aws.cache_ttl_seconds", 10),
    ("expression.aws.max_requests_per_session", -1), ("logging.level", "TRACE"),
    ("display.iris_color", "chartreuse"), ("display.iris_color", "#00FFFF"),
    ("behavior.imu_shake_reaction_strength", 1.0),
    ("behavior.imu_impact_reaction_strength", .88),
    ("behavior.imu_tilt_eye_asymmetry_strength", .51),
    ("led_ring.brightness", 1.1), ("led_ring.update_rate_hz", 0),
    ("led_ring.directional_animation_speed", 0),
    ("led_ring.imu_animation_color", "purple"),
    ("led_ring.bottom_led_index", -1),
    ("vision.camera_preview.scale", 0.05), ("vision.camera_preview.scale", 0.5),
    ("vision.camera_preview.max_fps", 11), ("vision.camera_preview.position", "center"),
    ("vision.camera_preview.enabled", "yes"),
])
def test_invalid_values_fail_before_start(document, tmp_path, route, value):
    keys, section = route.split("."), document
    for key in keys[:-1]:
        section = section[key]
    section[keys[-1]] = value
    with pytest.raises(ConfigurationError):
        RuntimeConfig.from_file(write_config(tmp_path, document))


def test_local_provider_paths_are_relative_to_config_not_cwd(document, tmp_path, monkeypatch):
    model = tmp_path / "model.onnx"
    model.write_bytes(b"test model")
    document["expression"]["enabled"] = True
    document["expression"]["local"]["model_path"] = "model.onnx"
    path = write_config(tmp_path, document)
    monkeypatch.chdir(tmp_path.parent)
    config = RuntimeConfig.from_file(path)
    runtime = build_runtime(config=config, eye_display=MemoryEyeDisplay())
    provider = runtime._vision_pipeline._expression_provider
    assert provider._model_path == model
    assert provider._labels == tuple(document["expression"]["local"]["labels"])
    assert provider._mean == tuple(document["expression"]["local"]["mean"])
    assert runtime._vision_pipeline._smoother._minimum_observations == 3


@pytest.mark.parametrize("model,labels", [(None, ["happy"]), ("missing.onnx", []), ("missing.onnx", ["happy"])])
def test_enabled_local_requires_model_and_labels(document, tmp_path, model, labels):
    document["expression"]["enabled"] = True
    document["expression"]["local"].update(model_path=model, labels=labels)
    with pytest.raises(ConfigurationError):
        RuntimeConfig.from_file(write_config(tmp_path, document))


def test_aws_config_does_not_require_model_or_credentials(document, tmp_path, monkeypatch):
    for name in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "AWS_REGION", "AWS_DEFAULT_REGION"):
        monkeypatch.delenv(name, raising=False)
    document["expression"].update(provider="aws", enabled=True)
    document["expression"]["local"].update(model_path=None, labels=[])
    config = RuntimeConfig.from_file(write_config(tmp_path, document))
    runtime = build_runtime(config=config, eye_display=MemoryEyeDisplay())
    assert runtime._vision_pipeline._expression_provider.config == config.cloud_expression
    assert runtime._vision_pipeline._smoother._maximum_gap_seconds == config.cloud_expression.cache_ttl_seconds


@pytest.mark.parametrize("section,key", [(None, "AWS_SECRET_ACCESS_KEY"), ("expression", "secret"),
                                        ("aws", "access_key"), ("logging", "unexpected")])
def test_unknown_fields_and_secrets_rejected(document, tmp_path, section, key):
    target = document if section is None else document["expression"]["aws"] if section == "aws" else document[section]
    target[key] = "placeholder-only"
    with pytest.raises(ConfigurationError, match="unknown fields"):
        RuntimeConfig.from_file(write_config(tmp_path, document))


def test_reusable_edit_validate_save_rebases_paths(document, tmp_path):
    model = tmp_path / "model.onnx"
    model.write_bytes(b"test model")
    document["expression"]["enabled"] = True
    document["expression"]["local"]["model_path"] = "model.onnx"
    document["logging"]["file"] = None
    original = RuntimeConfig.from_dict(document, base_dir=tmp_path)
    destination = tmp_path / "subdirectory"
    destination.mkdir()
    path = destination / "settings.json"
    original.save(path)
    reloaded = RuntimeConfig.from_file(path)
    assert reloaded.resolve_path(reloaded.expression_model_path) == model
    assert reloaded.to_dict()["expression"]["local"]["model_path"] == "../model.onnx"
    edited = reloaded.to_dict()
    edited["expression"]["provider"] = "aws"
    updated = RuntimeConfig.from_dict(edited, base_dir=path.parent)
    updated.save(path)
    assert RuntimeConfig.from_file(path).expression_provider == "aws"
    assert not list(destination.glob(".settings.json.*"))


def test_invalid_update_leaves_saved_configuration_unchanged(document, tmp_path):
    path = write_config(tmp_path, document)
    previous = path.read_text()
    document["expression"]["provider"] = "invalid"
    with pytest.raises(ConfigurationError):
        RuntimeConfig.from_dict(document, base_dir=tmp_path).save(path)
    assert path.read_text() == previous


def test_missing_log_parent_and_detector_file_fail_early(document, tmp_path):
    document["logging"]["file"] = "missing-dir/phos.log"
    with pytest.raises(ConfigurationError, match="logging.file"):
        RuntimeConfig.from_file(write_config(tmp_path, document))
    document["logging"]["file"] = None
    document["vision"]["face_tracking_enabled"] = True
    document["vision"]["detector"]["cascade_path"] = "missing.xml"
    with pytest.raises(ConfigurationError, match="cascade_path"):
        RuntimeConfig.from_file(write_config(tmp_path, document))


def test_invalid_startup_never_constructs_application(tmp_path, monkeypatch):
    from robot import main
    path = tmp_path / "bad.json"
    path.write_text('{}')
    monkeypatch.setattr(main, "build_application", lambda **kwargs: pytest.fail("Application constructed before validation"))
    monkeypatch.setattr(main.logging, "FileHandler", lambda *a, **kw: pytest.fail("Log opened before validation"))
    monkeypatch.setattr("sys.argv", ["phos", "--config", str(path)])
    with pytest.raises(SystemExit) as error:
        main.main()
    assert error.value.code == 2


def test_cli_config_only_and_overrides_use_same_validation(document, tmp_path, monkeypatch, caplog):
    from robot import main
    document["logging"]["file"] = None
    document["display"]["fps"] = 19
    path = write_config(tmp_path, document)
    seen = []
    async def run(*, config, lifecycle=None):
        seen.append(config)
    monkeypatch.setattr(main, "async_main", run)
    monkeypatch.setattr(main.logging, "basicConfig", lambda **kw: None)
    monkeypatch.setattr("sys.argv", ["phos", "--config", str(path)])
    main.main()
    assert seen[-1].display_fps == 19 and not seen[-1].vision_enabled
    monkeypatch.setattr("sys.argv", ["phos", "--config", str(path), "--expression-provider", "aws", "--expression-debug"])
    main.main()
    assert seen[-1].expression_enabled and seen[-1].expression_provider == "aws"
    assert seen[-1].expression_diagnostics and seen[-1].display_fps == 19
    assert "deprecated" in caplog.text
    assert json.loads(path.read_text()) == document


def test_explicit_file_loading_does_not_consult_another_default_file(document, tmp_path, monkeypatch):
    import robot.config as module
    path = write_config(tmp_path, document)
    monkeypatch.setattr(module, "DEFAULT_CONFIG_PATH", tmp_path / "missing-default.json")
    assert RuntimeConfig.from_file(path).display_fps == document["display"]["fps"]


def test_legacy_voice_document_receives_pre_roll_default(document, tmp_path):
    del document["voice"]["vad"]["pre_roll_ms"]
    config = RuntimeConfig.from_file(write_config(tmp_path, document))
    assert config.voice_pre_roll_ms == 300


def test_legacy_voice_document_receives_debug_defaults(document, tmp_path):
    del document["voice"]["debug"]
    config = RuntimeConfig.from_file(write_config(tmp_path, document))
    assert not config.voice_debug_dump_utterance_wav
    assert config.voice_debug_utterance_wav_path == Path("/tmp/phos-last-utterance.wav")


@pytest.mark.parametrize("value", [-1, 1001, 1.5, "300"])
def test_voice_pre_roll_requires_integer_milliseconds_in_range(document, tmp_path, value):
    document["voice"]["vad"]["pre_roll_ms"] = value
    with pytest.raises(ConfigurationError): RuntimeConfig.from_file(write_config(tmp_path, document))


@pytest.mark.parametrize("key,value", [("dump_utterance_wav", "false"), ("utterance_wav_path", None), ("utterance_wav_path", "")])
def test_voice_debug_configuration_is_strict(document, tmp_path, key, value):
    document["voice"]["debug"][key] = value
    with pytest.raises(ConfigurationError): RuntimeConfig.from_file(write_config(tmp_path, document))
