"""Hardware-free local Piper synthesis and ALSA playback tests."""
import subprocess
from pathlib import Path
import wave

import pytest

from robot.voice import (AplayAudioOutputProvider, AudioOutputError, PiperTTSProvider,
                         SynthesizedAudio, TTSConfigurationError, TTSSynthesisError)
from robot.config import RuntimeConfig, load_document


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
    path = tmp_path / "audio.wav"
    with wave.open(str(path), "wb") as wav:
        wav.setparams((2, 2, 48000, 0, "NONE", "not compressed")); wav.writeframes(b"\0\0" * 4)
    seen = []
    monkeypatch.setattr("robot.voice.tts.shutil.which", lambda command: "/usr/bin/aplay")
    monkeypatch.setattr("robot.voice.tts.subprocess.run", lambda command, **kwargs: seen.append(command) or subprocess.CompletedProcess(command, 0, "", ""))
    AplayAudioOutputProvider(device="hw:1").play(SynthesizedAudio(path))
    assert seen == [["/usr/bin/aplay", "-q", "-D", "hw:1", str(path)]]
    monkeypatch.setattr("robot.voice.tts.subprocess.run", lambda *a, **k: subprocess.CompletedProcess(a[0], 1, "", ""))
    with pytest.raises(AudioOutputError): AplayAudioOutputProvider().play(SynthesizedAudio(path))
    monkeypatch.setattr("robot.voice.tts.shutil.which", lambda command: None)
    with pytest.raises(AudioOutputError): AplayAudioOutputProvider().play(SynthesizedAudio(path))


def test_aplay_preroll_preserves_speech_in_one_wav_and_cleans_up(tmp_path, monkeypatch):
    source = tmp_path / "speech.wav"
    speech = b"\x01\x00\x02\x00" * 40
    with wave.open(str(source), "wb") as wav:
        wav.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        wav.writeframes(speech)
    seen = {}
    monkeypatch.setattr("robot.voice.tts.shutil.which", lambda command: "/usr/bin/aplay")
    def run(command, **kwargs):
        warmed = Path(command[-1])
        seen["path"] = warmed
        with wave.open(str(warmed), "rb") as wav:
            seen["params"] = wav.getparams(); seen["frames"] = wav.readframes(wav.getnframes())
        return subprocess.CompletedProcess(command, 0, "", "")
    monkeypatch.setattr("robot.voice.tts.subprocess.run", run)
    AplayAudioOutputProvider(preroll_ms=1000).play(SynthesizedAudio(source))
    assert (seen["params"].framerate, seen["params"].nchannels, seen["params"].sampwidth) == (48000, 2, 2)
    preroll_bytes = 48000 * 2 * 2
    assert any(seen["frames"][:preroll_bytes])
    assert seen["frames"][preroll_bytes:preroll_bytes + 2] == seen["frames"][preroll_bytes + 2:preroll_bytes + 4]
    assert not seen["path"].exists()


def test_aplay_normalizes_piper_pcm_to_hdmi_format_without_preroll(tmp_path, monkeypatch):
    source = tmp_path / "piper.wav"
    speech = b"\x10\x00\xf0\xff" * 2205
    with wave.open(str(source), "wb") as wav:
        wav.setparams((1, 2, 22050, 0, "NONE", "not compressed")); wav.writeframes(speech)
    seen = {}
    monkeypatch.setattr("robot.voice.tts.shutil.which", lambda command: "/usr/bin/aplay")
    def run(command, **kwargs):
        with wave.open(command[-1], "rb") as wav:
            seen["params"] = wav.getparams(); seen["frames"] = wav.readframes(wav.getnframes())
        return subprocess.CompletedProcess(command, 0, "", "")
    monkeypatch.setattr("robot.voice.tts.subprocess.run", run)
    AplayAudioOutputProvider(preroll_ms=0).play(SynthesizedAudio(source))
    assert (seen["params"].framerate, seen["params"].nchannels, seen["params"].sampwidth) == (48000, 2, 2)
    assert len(seen["frames"]) / (48000 * 2 * 2) == pytest.approx(.2, abs=.002)
    assert seen["frames"][:2] == seen["frames"][2:4]


def test_tts_preroll_configuration_rejects_negative_value(tmp_path):
    document = load_document()
    document["tts"]["audio_output"]["preroll_ms"] = -1
    with pytest.raises(Exception, match="preroll"):
        RuntimeConfig.from_dict(document, base_dir=tmp_path)


@pytest.mark.parametrize(("field", "value"), [("sample_rate", 0), ("channels", 3), ("sample_width", 1)])
def test_tts_output_format_configuration_is_validated(tmp_path, field, value):
    document = load_document(); document["tts"]["audio_output"][field] = value
    with pytest.raises(Exception, match="tts.audio_output"):
        RuntimeConfig.from_dict(document, base_dir=tmp_path)
