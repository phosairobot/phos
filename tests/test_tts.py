"""Hardware-free local Piper synthesis and ALSA playback tests."""
import asyncio
import hashlib
import os
import subprocess
from pathlib import Path
import wave

import pytest

from robot.voice import (AplayAudioOutputProvider, AudioOutputError, PiperTTSProvider,
                         SynthesizedAudio, TTSConfigurationError, TTSSynthesisError,
                         TTSProviderFactory, TTSProviderUnavailableError)
from robot.config import RuntimeConfig, load_document
from robot.runtime import PhosRuntime
from robot.voice import TTSBusyError
from robot.voice.tts import speech_request_id


def test_runtime_accepts_one_speech_task_before_playback_completes():
    runtime = object.__new__(PhosRuntime)
    runtime._tts_provider = object()
    runtime._audio_output = object()
    runtime._speech_task = None
    runtime._next_speech_request_id = 0
    started, release = asyncio.Event(), asyncio.Event()

    async def slow_speak(text, *, request_id=None):
        assert text == "Hello"
        assert request_id == 1
        started.set()
        await release.wait()

    runtime.speak = slow_speak

    async def exercise():
        await runtime.accept_speech("Hello")
        assert runtime._speech_task is not None
        await started.wait()
        with pytest.raises(TTSBusyError):
            await runtime.accept_speech("Again")
        release.set()
        await runtime._speech_task
        assert runtime._speech_task is None

    asyncio.run(exercise())


def test_runtime_clears_speech_task_after_each_of_five_sequential_requests():
    runtime = object.__new__(PhosRuntime)
    runtime._tts_provider = object()
    runtime._audio_output = object()
    runtime._speech_task = None
    runtime._next_speech_request_id = 0
    requests = []

    async def speak(text, *, request_id=None):
        requests.append((text, request_id))

    runtime.speak = speak

    async def exercise():
        for number in range(5):
            await runtime.accept_speech(f"Request {number}")
            task = runtime._speech_task
            assert task is not None
            await task
            assert runtime._speech_task is None

    asyncio.run(exercise())
    assert requests == [(f"Request {number}", number + 1) for number in range(5)]


def test_piper_rejects_empty_or_oversized_text(tmp_path):
    provider = PiperTTSProvider("piper", tmp_path / "voice.onnx")
    with pytest.raises(TTSConfigurationError): provider.synthesize("  ")
    with pytest.raises(TTSConfigurationError): provider.synthesize("x" * 1001)


def test_tts_provider_factory_selects_local_and_never_falls_back_for_cloud(tmp_path):
    model = tmp_path / "voice.onnx"; model.write_bytes(b"model")
    document = load_document()
    document["tts"].update(enabled=True, provider="local")
    document["tts"]["local"]["model_path"] = str(model)
    config = RuntimeConfig.from_dict(document, base_dir=tmp_path)
    assert isinstance(TTSProviderFactory.create(config), PiperTTSProvider)

    document["tts"]["provider"] = "elevenlabs"
    cloud = RuntimeConfig.from_dict(document, base_dir=tmp_path)
    with pytest.raises(TTSProviderUnavailableError, match="not implemented"):
        TTSProviderFactory.create(cloud)


def test_unknown_tts_provider_is_rejected_by_configuration(tmp_path):
    document = load_document(); document["tts"]["provider"] = "unknown"
    with pytest.raises(Exception, match="tts.provider"):
        RuntimeConfig.from_dict(document, base_dir=tmp_path)


def test_piper_requires_model(tmp_path):
    provider = PiperTTSProvider("piper", tmp_path / "missing.onnx")
    with pytest.raises(TTSConfigurationError, match="model"): provider.synthesize("Hello")


def test_piper_lazily_loads_one_voice_and_reuses_it_for_sequential_utterances(tmp_path, caplog):
    model = tmp_path / "voice.onnx"; model.write_bytes(b"model")
    loads, utterances = [], []

    class Voice:
        def synthesize_wav(self, text, wav_file):
            utterances.append(text)
            wav_file.setparams((1, 2, 22050, 0, "NONE", "not compressed"))
            wav_file.writeframes(b"\x01\x00" * 8)

    voice = Voice()
    provider = PiperTTSProvider("piper", model, voice_loader=lambda path: loads.append(path) or voice)
    with caplog.at_level("INFO"):
        first = provider.synthesize("Hello")
        second = provider.synthesize("Again")

    assert loads == [str(model)]
    assert utterances == ["Hello", "Again"]
    assert caplog.text.count("PHOS SPEECH: piper_model_load_start") == 1
    assert caplog.text.count("PHOS SPEECH: piper_model_load_complete") == 1
    assert first.path.is_file() and second.path.is_file()
    first.path.unlink(); second.path.unlink()


def test_piper_reuses_one_voice_for_five_distinct_non_silent_wavs(tmp_path, monkeypatch):
    model = tmp_path / "voice.onnx"; model.write_bytes(b"model")
    loads, output_paths, playback_paths = [], [], []

    class Voice:
        def synthesize_wav(self, text, wav_file):
            wav_file.setparams((1, 2, 22050, 0, "NONE", "not compressed"))
            sample = len(output_paths) + 1
            wav_file.writeframes(sample.to_bytes(2, "little", signed=True) * 40)

    provider = PiperTTSProvider("piper", model, voice_loader=lambda path: loads.append(path) or Voice())
    monkeypatch.setattr("robot.voice.tts.shutil.which", lambda command: "/usr/bin/aplay")

    def run(command, **kwargs):
        playback_path = Path(command[-1])
        with wave.open(str(playback_path), "rb") as wav_file:
            params = wav_file.getparams()
            frames = wav_file.readframes(params.nframes)
        assert (params.framerate, params.nchannels, params.sampwidth) == (48000, 2, 2)
        assert any(frames)
        playback_paths.append(playback_path)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("robot.voice.tts.subprocess.run", run)
    output = AplayAudioOutputProvider(preroll_ms=1000)
    for number in range(5):
        audio = provider.synthesize(f"Request {number}")
        output_paths.append(audio.path)
        output.play(audio)
        audio.path.unlink()

    assert loads == [str(model)]
    assert len(set(output_paths)) == 5
    assert len(playback_paths) == 5
    assert len(set(playback_paths)) == 5
    assert all(not path.exists() for path in playback_paths)


def test_piper_rejects_a_silent_generated_wav(tmp_path):
    model = tmp_path / "voice.onnx"; model.write_bytes(b"model")

    class SilentVoice:
        def synthesize_wav(self, text, wav_file):
            wav_file.setparams((1, 2, 22050, 0, "NONE", "not compressed"))
            wav_file.writeframes(b"\0\0" * 8)

    provider = PiperTTSProvider("piper", model, voice_loader=lambda path: SilentVoice())
    with pytest.raises(TTSSynthesisError, match="silent"):
        provider.synthesize("Hello")


def test_piper_load_or_synthesis_failure_is_typed_and_cleans_temp_wav(tmp_path):
    model = tmp_path / "voice.onnx"; model.write_bytes(b"model")
    with pytest.raises(TTSConfigurationError, match="could not be loaded"):
        PiperTTSProvider("piper", model, voice_loader=lambda path: (_ for _ in ()).throw(RuntimeError())).synthesize("Hello")

    class Voice:
        def synthesize_wav(self, text, wav_file): raise RuntimeError("failed")
    with pytest.raises(TTSSynthesisError):
        PiperTTSProvider("piper", model, voice_loader=lambda path: Voice()).synthesize("Hello")


def test_aplay_uses_optional_device_and_reports_failures(tmp_path, monkeypatch):
    path = tmp_path / "audio.wav"
    with wave.open(str(path), "wb") as wav:
        wav.setparams((2, 2, 48000, 0, "NONE", "not compressed")); wav.writeframes(b"\x01\0" * 4)
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


def test_aplay_logs_normalized_final_wav_and_one_playback_timing(tmp_path, monkeypatch, caplog):
    source = tmp_path / "speech.wav"
    with wave.open(str(source), "wb") as wav:
        wav.setparams((1, 2, 16000, 0, "NONE", "not compressed")); wav.writeframes(b"\x01\x00" * 1600)
    calls = []
    monkeypatch.setattr("robot.voice.tts.shutil.which", lambda command: "/usr/bin/aplay")
    monkeypatch.setattr("robot.voice.tts.subprocess.run", lambda command, **kwargs: calls.append(command) or subprocess.CompletedProcess(command, 0, "", ""))

    with caplog.at_level("INFO"):
        AplayAudioOutputProvider(preroll_ms=1000).play(SynthesizedAudio(source))

    assert len(calls) == 1
    assert "PHOS SPEECH: normalization_start" in caplog.text
    assert "PHOS SPEECH: normalization_complete" in caplog.text
    assert "rate_hz=48000 channels=2 sample_width_bytes=2" in caplog.text
    assert "PHOS SPEECH: playback_start" in caplog.text
    assert "PHOS SPEECH: playback_complete" in caplog.text


def test_aplay_failure_logs_sanitized_return_code(tmp_path, monkeypatch, caplog):
    source = tmp_path / "speech.wav"
    with wave.open(str(source), "wb") as wav:
        wav.setparams((2, 2, 48000, 0, "NONE", "not compressed")); wav.writeframes(b"\x01\0" * 4)
    monkeypatch.setattr("robot.voice.tts.shutil.which", lambda command: "/usr/bin/aplay")
    monkeypatch.setattr("robot.voice.tts.subprocess.run", lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 7, "", "sink unavailable\n"))

    with caplog.at_level("WARNING"), pytest.raises(AudioOutputError):
        AplayAudioOutputProvider().play(SynthesizedAudio(source))

    assert "PHOS SPEECH: playback_failed returncode=7 stderr=sink unavailable" in caplog.text


def test_aplay_retries_the_same_final_wav_once_then_succeeds(tmp_path, monkeypatch, caplog):
    source = tmp_path / "speech.wav"
    with wave.open(str(source), "wb") as wav:
        wav.setparams((1, 2, 16000, 0, "NONE", "not compressed")); wav.writeframes(b"\x01\0" * 1600)
    calls = []
    monkeypatch.setattr("robot.voice.tts.shutil.which", lambda command: "/usr/bin/aplay")

    def run(command, **kwargs):
        playback_path = Path(command[-1])
        assert playback_path.is_file()
        calls.append(playback_path)
        return subprocess.CompletedProcess(command, 7 if len(calls) == 1 else 0, "", "sink unavailable" if len(calls) == 1 else "")

    monkeypatch.setattr("robot.voice.tts.subprocess.run", run)
    with caplog.at_level("INFO"):
        AplayAudioOutputProvider(device="hw:1", retry_delay_ms=0).play(SynthesizedAudio(source))

    assert len(calls) == 2
    assert calls[0] == calls[1]
    assert not calls[0].exists()
    assert "playback_retry next_attempt=2 delay_ms=0" in caplog.text
    assert "playback_complete" in caplog.text and "attempt=2 returncode=0" in caplog.text


def test_aplay_stops_after_two_failed_attempts_without_resynthesizing(tmp_path, monkeypatch):
    model = tmp_path / "voice.onnx"; model.write_bytes(b"model")
    syntheses, calls = [], []

    class Voice:
        def synthesize_wav(self, text, wav_file):
            syntheses.append(text)
            wav_file.setparams((1, 2, 22050, 0, "NONE", "not compressed"))
            wav_file.writeframes(b"\x01\0" * 40)

    audio = PiperTTSProvider("piper", model, voice_loader=lambda path: Voice()).synthesize("Only once")
    monkeypatch.setattr("robot.voice.tts.shutil.which", lambda command: "/usr/bin/aplay")
    monkeypatch.setattr("robot.voice.tts.subprocess.run", lambda command, **kwargs: calls.append(Path(command[-1])) or subprocess.CompletedProcess(command, 5, "", "device unavailable"))
    try:
        with pytest.raises(AudioOutputError, match="after retry"):
            AplayAudioOutputProvider(retry_delay_ms=0).play(audio)
    finally:
        audio.path.unlink(missing_ok=True)

    assert syntheses == ["Only once"]
    assert len(calls) == 2
    assert calls[0] == calls[1]
    assert not calls[0].exists()


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


def test_debug_retention_passes_a_closed_final_wav_to_aplay_and_preserves_it(tmp_path, monkeypatch, caplog):
    source = tmp_path / "speech.wav"
    with wave.open(str(source), "wb") as wav:
        wav.setparams((1, 2, 16000, 0, "NONE", "not compressed")); wav.writeframes(b"\x11\0" * 1600)
    request_id = os.getpid() * 1000 + 41
    retained_glob = f"phos-speech-{os.getpid()}-{request_id}-*.wav"
    for path in Path("/tmp").glob(retained_glob):
        path.unlink()
    seen = {}
    monkeypatch.setattr("robot.voice.tts.shutil.which", lambda command: "/usr/bin/aplay")

    def run(command, **kwargs):
        playback_path = Path(command[-1])
        # aplay sees a complete, readable RIFF/WAV after the writer has closed.
        with wave.open(str(playback_path), "rb") as wav:
            seen["params"] = wav.getparams(); seen["frames"] = wav.readframes(wav.getnframes())
        seen["path"] = playback_path
        seen["checksum"] = hashlib.sha256(playback_path.read_bytes()).hexdigest()[:16]
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("robot.voice.tts.subprocess.run", run)
    token = speech_request_id.set(request_id)
    try:
        with caplog.at_level("INFO"):
            AplayAudioOutputProvider(preroll_ms=1000, debug_retain_final_wav=True).play(SynthesizedAudio(source))
    finally:
        speech_request_id.reset(token)

    retained_paths = list(Path("/tmp").glob(retained_glob))
    assert len(retained_paths) == 1
    retained_path = retained_paths[0]
    assert seen["path"] != retained_path
    assert retained_path.is_file()
    assert hashlib.sha256(retained_path.read_bytes()).hexdigest()[:16] == seen["checksum"]
    assert (seen["params"].framerate, seen["params"].nchannels, seen["params"].sampwidth) == (48000, 2, 2)
    assert f"final_wav sha256={seen['checksum']}" in caplog.text
    assert f"retained_wav path={retained_path}" in caplog.text
    retained_path.unlink()


def test_debug_retention_survives_a_restarted_request_id_collision(tmp_path, monkeypatch):
    source = tmp_path / "speech.wav"
    with wave.open(str(source), "wb") as wav:
        wav.setparams((2, 2, 48000, 0, "NONE", "not compressed")); wav.writeframes(b"\x11\0" * 16)
    request_id = os.getpid() * 1000 + 51
    retained_glob = f"phos-speech-{os.getpid()}-{request_id}-*.wav"
    for path in Path("/tmp").glob(retained_glob):
        path.unlink(missing_ok=True)
    seen = []
    monkeypatch.setattr("robot.voice.tts.shutil.which", lambda command: "/usr/bin/aplay")
    monkeypatch.setattr("robot.voice.tts.subprocess.run", lambda command, **kwargs: seen.append(Path(command[-1])) or subprocess.CompletedProcess(command, 0, "", ""))
    output = AplayAudioOutputProvider(debug_retain_final_wav=True)
    try:
        # Two PHOS process runs both begin their request sequence at 2. Existing
        # retained samples must not prevent the later run from playing.
        for _run in range(2):
            token = speech_request_id.set(request_id)
            try:
                output.play(SynthesizedAudio(source))
            finally:
                speech_request_id.reset(token)
        retained_paths = list(Path("/tmp").glob(retained_glob))
        assert len(seen) == 2
        assert len(retained_paths) == 2
        assert len(set(retained_paths)) == 2
        assert len({path.read_bytes() for path in retained_paths}) == 1
    finally:
        for path in Path("/tmp").glob(retained_glob):
            path.unlink(missing_ok=True)


def test_retention_failure_warns_but_never_blocks_playback(tmp_path, monkeypatch, caplog):
    source = tmp_path / "speech.wav"
    with wave.open(str(source), "wb") as wav:
        wav.setparams((2, 2, 48000, 0, "NONE", "not compressed")); wav.writeframes(b"\x11\0" * 16)
    monkeypatch.setattr("robot.voice.tts.shutil.which", lambda command: "/usr/bin/aplay")
    monkeypatch.setattr("robot.voice.tts.tempfile.mkstemp", lambda **kwargs: (_ for _ in ()).throw(PermissionError("denied")))
    calls = []
    monkeypatch.setattr("robot.voice.tts.subprocess.run", lambda command, **kwargs: calls.append(command) or subprocess.CompletedProcess(command, 0, "", ""))
    token = speech_request_id.set(2)
    try:
        with caplog.at_level("WARNING"):
            AplayAudioOutputProvider(debug_retain_final_wav=True).play(SynthesizedAudio(source))
    finally:
        speech_request_id.reset(token)
    assert len(calls) == 1
    assert "retained_wav_failed exception_type=PermissionError" in caplog.text


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
