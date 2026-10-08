"""Hardware-free local Piper synthesis and ALSA playback tests."""
import subprocess
from pathlib import Path

import pytest

from robot.voice import (AplayAudioOutputProvider, AudioOutputError, PiperTTSProvider,
                         SynthesizedAudio, TTSConfigurationError, TTSSynthesisError)


def test_piper_rejects_empty_or_oversized_text(tmp_path):
    provider = PiperTTSProvider("piper", tmp_path / "voice.onnx")
    with pytest.raises(TTSConfigurationError): provider.synthesize("  ")
    with pytest.raises(TTSConfigurationError): provider.synthesize("x" * 1001)


def test_piper_requires_model_and_executable(tmp_path, monkeypatch):
    provider = PiperTTSProvider("piper", tmp_path / "missing.onnx")
    with pytest.raises(TTSConfigurationError, match="model"): provider.synthesize("Hello")
    model = tmp_path / "voice.onnx"; model.write_bytes(b"model")
    monkeypatch.setattr("robot.voice.tts.shutil.which", lambda command: None)
    with pytest.raises(TTSConfigurationError, match="executable"): PiperTTSProvider("piper", model).synthesize("Hello")


def test_piper_uses_safe_arguments_and_cleans_failed_output(tmp_path, monkeypatch):
    model = tmp_path / "voice.onnx"; model.write_bytes(b"model")
    seen = {}
    monkeypatch.setattr("robot.voice.tts.shutil.which", lambda command: "/usr/bin/piper")
    def run(command, **kwargs):
        seen.update(command=command, kwargs=kwargs)
        Path(command[command.index("--output_file") + 1]).write_bytes(b"RIFF")
        return subprocess.CompletedProcess(command, 0, "", "")
    monkeypatch.setattr("robot.voice.tts.subprocess.run", run)
    audio = PiperTTSProvider("piper", model, 2).synthesize("Hello; no shell")
    assert seen["command"][:3] == ["/usr/bin/piper", "--model", str(model)]
    assert seen["kwargs"]["input"] == "Hello; no shell"
    assert "shell" not in seen["kwargs"]
    assert audio.path.is_file(); audio.path.unlink()


def test_piper_nonzero_and_timeout_are_typed(tmp_path, monkeypatch):
    model = tmp_path / "voice.onnx"; model.write_bytes(b"model")
    monkeypatch.setattr("robot.voice.tts.shutil.which", lambda command: "/usr/bin/piper")
    monkeypatch.setattr("robot.voice.tts.subprocess.run", lambda *a, **k: subprocess.CompletedProcess(a[0], 1, "", "bad"))
    with pytest.raises(TTSSynthesisError): PiperTTSProvider("piper", model).synthesize("Hello")
    monkeypatch.setattr("robot.voice.tts.subprocess.run", lambda *a, **k: (_ for _ in ()).throw(subprocess.TimeoutExpired(a[0], 1)))
    with pytest.raises(TTSSynthesisError): PiperTTSProvider("piper", model).synthesize("Hello")


def test_aplay_uses_optional_device_and_reports_failures(tmp_path, monkeypatch):
    path = tmp_path / "audio.wav"; path.write_bytes(b"RIFF")
    seen = []
    monkeypatch.setattr("robot.voice.tts.shutil.which", lambda command: "/usr/bin/aplay")
    monkeypatch.setattr("robot.voice.tts.subprocess.run", lambda command, **kwargs: seen.append(command) or subprocess.CompletedProcess(command, 0, "", ""))
    AplayAudioOutputProvider(device="hw:1").play(SynthesizedAudio(path))
    assert seen == [["/usr/bin/aplay", "-q", "-D", "hw:1", str(path)]]
    monkeypatch.setattr("robot.voice.tts.subprocess.run", lambda *a, **k: subprocess.CompletedProcess(a[0], 1, "", ""))
    with pytest.raises(AudioOutputError): AplayAudioOutputProvider().play(SynthesizedAudio(path))
    monkeypatch.setattr("robot.voice.tts.shutil.which", lambda command: None)
    with pytest.raises(AudioOutputError): AplayAudioOutputProvider().play(SynthesizedAudio(path))
