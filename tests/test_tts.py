"""Hardware-free local Piper synthesis and ALSA playback tests."""
import asyncio
import hashlib
import io
import os
import subprocess
import sys
import types
from pathlib import Path
import wave

import pytest

from robot.voice import (AplayAudioOutputProvider, AudioOutputError, AudioOutputProviderFactory, PiperTTSProvider,
                         PipeWireAudioOutputProvider,
                         SynthesizedAudio, TTSConfigurationError, TTSSynthesisError,
                         CartesiaTTSProvider, ElevenLabsTTSProvider, STTProviderFactory,
                         TTSProviderError, TTSProviderFactory, TTSUnavailableError)
from robot.config import RuntimeConfig, load_document
from robot.core import RobotCore, RobotState
from robot.runtime import PhosRuntime, build_runtime
from robot.secrets import SecretsService
from robot.ui import MemoryEyeDisplay
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


def test_runtime_unavailable_tts_is_typed_without_provider_reconstruction():
    runtime = object.__new__(PhosRuntime)
    runtime._tts_provider = None
    runtime._audio_output = None

    async def exercise():
        with pytest.raises(TTSUnavailableError, match="currently unavailable"):
            await runtime.speak("Hello")
        with pytest.raises(TTSUnavailableError, match="currently unavailable"):
            await runtime.accept_speech("Hello")

    asyncio.run(exercise())


def test_audio_output_status_uses_live_audio_output_player(tmp_path):
    document = load_document()
    document["tts"]["audio_output"]["player"] = "pipewire"
    config = RuntimeConfig.from_dict(document, base_dir=tmp_path)
    runtime = build_runtime(config=config, eye_display=MemoryEyeDisplay())
    runtime.replace_audio_output(types.SimpleNamespace(name="pipewire", device=None, play=lambda audio: None), config=config)
    status = runtime.application_status()["speech"]["audio_output"]
    assert status["configured_provider"] == "pipewire"
    assert status["active_provider"] == "pipewire"


@pytest.mark.parametrize("retain_final_wav", [False, True])
def test_runtime_final_wav_cleanup_and_pipewire_retention(tmp_path, monkeypatch, retain_final_wav, caplog):
    source = tmp_path / "speech.wav"
    with wave.open(str(source), "wb") as wav:
        wav.setparams((1, 2, 16000, 0, "NONE", "not compressed")); wav.writeframes(b"\x11\0" * 16)
    expected_bytes = source.read_bytes()
    request_id = os.getpid() * 1000 + (62 if retain_final_wav else 61)
    retained_glob = f"phos-speech-{os.getpid()}-{request_id}-*.wav"
    for path in Path("/tmp").glob(retained_glob): path.unlink()
    monkeypatch.setattr("robot.voice.tts.shutil.which", lambda command: "/usr/bin/pw-play")
    played = []
    monkeypatch.setattr("robot.voice.tts.subprocess.run", lambda command, **kwargs: played.append(Path(command[-1]).read_bytes()) or subprocess.CompletedProcess(command, 0, "", ""))
    runtime = object.__new__(PhosRuntime)
    runtime.core = RobotCore(); runtime._tts_lock = asyncio.Lock()
    runtime._tts_provider = types.SimpleNamespace(synthesize=lambda text: SynthesizedAudio(source))
    runtime._audio_output = PipeWireAudioOutputProvider(debug_retain_final_wav=retain_final_wav)
    runtime._tts_enabled = True
    try:
        with caplog.at_level("INFO"):
            asyncio.run(runtime.speak("Hello", request_id=request_id))
        retained = list(Path("/tmp").glob(retained_glob))
        assert played == [expected_bytes]
        assert not source.exists()
        assert f"PHOS SPEECH: audio_output provider=pipewire retain_final_wav={str(retain_final_wav).lower()}" in caplog.text
        if retain_final_wav:
            assert len(retained) == 1
            assert retained[0].read_bytes() == played[0]
            assert f"PHOS SPEECH: retained_wav path={retained[0]} bytes={len(played[0])}" in caplog.text
        else:
            assert retained == []
    finally:
        for path in Path("/tmp").glob(retained_glob): path.unlink(missing_ok=True)


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
    document["tts"]["elevenlabs"]["voice_id"] = "voice-id"
    cloud = RuntimeConfig.from_dict(document, base_dir=tmp_path)
    with pytest.raises(TTSConfigurationError, match="credential"):
        TTSProviderFactory.create(cloud)


def test_unknown_tts_provider_is_rejected_by_configuration(tmp_path):
    document = load_document(); document["tts"]["provider"] = "unknown"
    with pytest.raises(Exception, match="tts.provider"):
        RuntimeConfig.from_dict(document, base_dir=tmp_path)


def test_elevenlabs_factory_uses_runtime_secret_and_provider_reuses_client(tmp_path, monkeypatch):
    document = load_document()
    document["tts"].update(enabled=True, provider="elevenlabs")
    document["tts"]["elevenlabs"]["voice_id"] = "voice-id"
    config = RuntimeConfig.from_dict(document, base_dir=tmp_path)
    created = []

    class Client:
        def __init__(self, *, api_key): created.append(api_key)
        class text_to_speech:
            @staticmethod
            def convert(**kwargs): return iter((b"\x01\0", b"\x02\0"))

    monkeypatch.setitem(sys.modules, "elevenlabs", types.SimpleNamespace(ElevenLabs=Client))
    provider = TTSProviderFactory.create(config, types.SimpleNamespace(get_secret=lambda name: "secret-value"))
    assert isinstance(provider, ElevenLabsTTSProvider) and created == ["secret-value"]
    first, second = provider.synthesize("Hello"), provider.synthesize("Again")
    assert created == ["secret-value"]
    assert first.path.is_file() and second.path.is_file()
    first.path.unlink(); second.path.unlink()


def test_elevenlabs_failure_is_typed_and_cleans_output():
    class Client:
        class text_to_speech:
            @staticmethod
            def convert(**kwargs): raise RuntimeError("authentication header")
    provider = ElevenLabsTTSProvider("secret", "voice", "model", "pcm_24000", client_factory=lambda **kwargs: Client())
    with pytest.raises(TTSProviderError, match="synthesis_failed"):
        provider.synthesize("Hello")


@pytest.mark.parametrize(("status", "code", "expected"), [
    (402, "paid_plan_required", "provider_plan_required"), (404, "voice_not_found", "voice_not_found"),
    (401, "unauthorized", "authentication_error"), (403, "forbidden", "authentication_error"),
    (429, "rate_limited", "rate_limited"),
    (503, "service_error", "provider_unavailable"), (400, "bad_request", "synthesis_failed"),
])
def test_elevenlabs_api_errors_are_sanitized(status, code, expected, caplog):
    class ApiError(Exception):
        def __init__(self): self.status_code, self.body = status, {"code": code, "authorization": "secret"}
    class Client:
        class text_to_speech:
            @staticmethod
            def convert(**kwargs): raise ApiError()
    provider = ElevenLabsTTSProvider("secret", "voice", "model", "pcm_24000", client_factory=lambda **kwargs: Client())
    with caplog.at_level("WARNING"), pytest.raises(TTSProviderError) as error:
        provider.synthesize("Hello")
    assert error.value.code == expected
    assert f"PHOS TTS: provider=elevenlabs error={expected}" in caplog.text
    assert "secret" not in caplog.text and "authorization" not in caplog.text


def test_elevenlabs_network_error_is_provider_unavailable():
    class Client:
        class text_to_speech:
            @staticmethod
            def convert(**kwargs):
                raise ConnectionError("endpoint with secret")

    provider = ElevenLabsTTSProvider("secret", "voice", "model", "pcm_24000",
                                     client_factory=lambda **kwargs: Client())
    with pytest.raises(TTSProviderError, match="provider_unavailable"):
        provider.synthesize("Hello")


def _cartesia_wav(*, rate=44100):
    output = io.BytesIO()
    with wave.open(output, "wb") as wav_file:
        wav_file.setparams((1, 2, rate, 0, "NONE", "not compressed"))
        wav_file.writeframes(b"\x01\0\x02\0")
    return output.getvalue()


def test_cartesia_factory_uses_runtime_secret_and_reuses_client(tmp_path, monkeypatch, caplog):
    document = load_document()
    document["tts"].update(enabled=True, provider="cartesia")
    document["tts"]["cartesia"].update(voice_id="voice-id", model_id="configured-model",
                                          sample_rate=24000, speed=1.2, volume=.8)
    config = RuntimeConfig.from_dict(document, base_dir=tmp_path)
    created, requests = [], []

    class Client:
        def __init__(self, *, api_key, default_headers): created.append((api_key, default_headers))
        class tts:
            @staticmethod
            def generate(**kwargs):
                requests.append(kwargs)
                payload = _cartesia_wav(rate=kwargs["output_format"]["sample_rate"])
                class Response:
                    def write_to_file(self, path):
                        with Path(path).open("wb") as output:
                            for chunk in (payload[:9], payload[9:29], payload[29:]):
                                output.write(chunk)
                return Response()

    monkeypatch.setitem(sys.modules, "cartesia", types.SimpleNamespace(Cartesia=Client))
    with caplog.at_level("INFO"):
        provider = TTSProviderFactory.create(config, types.SimpleNamespace(get_secret=lambda name: "secret-value"))
    assert isinstance(provider, CartesiaTTSProvider) and created == [("secret-value", {"cartesia-version": "2026-08-14"})]
    assert "PHOS TTS: provider=cartesia credential_source=secrets_service credential_present=True" in caplog.text
    with caplog.at_level("INFO"):
        first, second = provider.synthesize("Hello"), provider.synthesize("Again")
    assert len(created) == 1 and len(requests) == 2
    assert requests[0]["model_id"] == "configured-model"
    assert requests[0]["voice"] == {"mode": "id", "id": "voice-id"}
    assert requests[0]["output_format"] == {"container": "wav", "encoding": "pcm_s16le", "sample_rate": 24000}
    assert requests[0]["generation_config"] == {"speed": 1.2, "volume": .8}
    assert ("PHOS SPEECH: input_wav provider=cartesia" in caplog.text
            and "file_size_bytes=" in caplog.text and "declared_frames=" in caplog.text)
    assert "secret-value" not in caplog.text
    with wave.open(str(first.path), "rb") as wav_file:
        assert (wav_file.getframerate(), wav_file.getnchannels(), wav_file.getsampwidth()) == (24000, 1, 2)
    first.path.unlink(); second.path.unlink()


def test_cartesia_stt_and_tts_use_one_shared_secret_slot(tmp_path, monkeypatch):
    document = load_document()
    document["voice"]["stt"]["provider"] = "cartesia"
    document["tts"].update(enabled=True, provider="cartesia")
    document["tts"]["cartesia"]["voice_id"] = "voice-id"
    config = RuntimeConfig.from_dict(document, base_dir=tmp_path)
    requested = []

    class Secrets:
        def get_secret(self, name):
            requested.append(name)
            return "shared-secret"

    class Client:
        def __init__(self, **kwargs): pass

    monkeypatch.setitem(sys.modules, "cartesia", types.SimpleNamespace(Cartesia=Client))
    assert STTProviderFactory.create(config, Secrets()).name == "cartesia"
    assert TTSProviderFactory.create(config, Secrets()).name == "cartesia"
    assert requested == ["tts.cartesia.api_key", "tts.cartesia.api_key"]


def test_runtime_retains_cartesia_stt_secret_service_for_live_tts_configuration(tmp_path, monkeypatch):
    document = load_document()
    document["voice"]["stt"]["provider"] = "cartesia"
    config = RuntimeConfig.from_dict(document, base_dir=tmp_path)
    expected = SecretsService(tmp_path / ".phos-secrets")
    expected.set_secret("tts.cartesia.api_key", "shared-secret")
    received = []

    class STT:
        name = "cartesia"
        def available(self): return True

    def create_stt(received_config, secrets):
        received.append(secrets)
        return STT()

    monkeypatch.setattr("robot.runtime.STTProviderFactory.create", create_stt)
    runtime = build_runtime(config=config, eye_display=MemoryEyeDisplay())
    assert received == [runtime._secrets_service]
    assert runtime._secrets_service.get_secret("tts.cartesia.api_key") == "shared-secret"


def test_cartesia_missing_credential_and_required_fields_are_typed(tmp_path):
    document = load_document()
    document["tts"].update(enabled=True, provider="cartesia")
    document["tts"]["cartesia"]["voice_id"] = "voice-id"
    config = RuntimeConfig.from_dict(document, base_dir=tmp_path)
    with pytest.raises(TTSConfigurationError, match="credential"):
        TTSProviderFactory.create(config, types.SimpleNamespace(get_secret=lambda name: None))
    for field in ("voice_id", "model_id"):
        incomplete = load_document()
        incomplete["tts"].update(enabled=True, provider="cartesia")
        incomplete["tts"]["cartesia"]["voice_id"] = "voice-id"
        incomplete["tts"]["cartesia"][field] = None
        with pytest.raises(Exception, match=field):
            RuntimeConfig.from_dict(incomplete, base_dir=tmp_path)


def test_cartesia_missing_sdk_leaves_runtime_healthy(tmp_path, monkeypatch):
    document = load_document()
    document["tts"].update(enabled=True, provider="cartesia")
    document["tts"]["cartesia"]["voice_id"] = "voice-id"
    config = RuntimeConfig.from_dict(document, base_dir=tmp_path)
    monkeypatch.setitem(sys.modules, "cartesia", None)
    runtime = build_runtime(config=config, eye_display=MemoryEyeDisplay(),
                            secrets_service=types.SimpleNamespace(get_secret=lambda name: "secret-value"))
    assert runtime._tts_provider is None
    assert runtime.application_status()["tts"] == {"enabled": True, "configured_provider": "cartesia",
                                                    "available": False, "reason": "missing_dependency"}


@pytest.mark.parametrize(("status", "code", "expected"), [
    (401, "unauthorized", "authentication_error"), (429, "rate_limit_exceeded", "rate_limited"),
    (402, "quota_exceeded", "provider_plan_required"), (404, "voice_not_found", "voice_not_found"),
    (400, "missing_version", "provider_configuration_error"), (400, "invalid_model", "invalid_model"),
    (503, "service_error", "provider_unavailable"),
])
def test_cartesia_api_errors_are_sanitized(status, code, expected, caplog):
    class ApiError(Exception):
        def __init__(self): self.status_code, self.body = status, {"code": code, "authorization": "secret"}
    class Client:
        class tts:
            @staticmethod
            def generate(**kwargs): raise ApiError()
    provider = CartesiaTTSProvider("secret", "voice", "sonic-3.6", 44100, 1, 1,
                                   client_factory=lambda **kwargs: Client())
    with caplog.at_level("WARNING"), pytest.raises(TTSProviderError) as error:
        provider.synthesize("Hello")
    assert error.value.code == expected
    assert f"PHOS TTS: provider=cartesia error={expected}" in caplog.text
    assert f"provider_code={code}" in caplog.text
    assert "secret" not in caplog.text and "authorization" not in caplog.text


def test_cartesia_400_logs_safe_message_and_effective_parameters(caplog):
    transcript = "Never log this transcript"

    class ApiError(Exception):
        status_code = 400
        body = {"code": "missing_version", "message": "Cartesia-Version is required.",
                "authorization": "secret-value"}
    class Client:
        class tts:
            @staticmethod
            def generate(**kwargs): raise ApiError()

    provider = CartesiaTTSProvider("secret-value", "voice-id", "sonic-3.6", 44100, 1.0, 1.0,
                                   client_factory=lambda **kwargs: Client())
    with caplog.at_level("INFO"), pytest.raises(TTSProviderError, match="provider_configuration_error"):
        provider.synthesize(transcript)
    assert ("provider=cartesia request model_id=sonic-3.6 voice_id=voice-id sample_rate=44100 "
            "speed=1.0 volume=1.0 api_version=2026-08-14 container=wav encoding=pcm_s16le" in caplog.text)
    assert ("error=provider_configuration_error status=400 provider_code=missing_version "
            "provider_message='Cartesia-Version is required.'" in caplog.text)
    assert "secret-value" not in caplog.text and transcript not in caplog.text and "authorization" not in caplog.text


@pytest.mark.parametrize(("field", "value"), [("speed", .5), ("speed", 1.6), ("volume", .4), ("volume", 2.1)])
def test_cartesia_invalid_generation_config_fails_before_remote_call(tmp_path, field, value):
    document = load_document()
    document["tts"].update(enabled=True, provider="cartesia")
    document["tts"]["cartesia"].update(voice_id="voice-id", **{field: value})
    with pytest.raises(Exception, match=field):
        RuntimeConfig.from_dict(document, base_dir=tmp_path)


def test_runtime_returns_to_idle_after_expected_elevenlabs_failure(caplog):
    runtime = object.__new__(PhosRuntime)
    runtime.core = RobotCore()
    runtime._tts_lock = asyncio.Lock()
    runtime._tts_provider = types.SimpleNamespace(
        name="elevenlabs",
        synthesize=lambda text: (_ for _ in ()).throw(TTSProviderError("provider_plan_required")),
    )
    runtime._audio_output = object()

    async def exercise():
        with pytest.raises(TTSProviderError, match="provider_plan_required"):
            await runtime.speak("Hello")
        assert runtime.core.state is RobotState.IDLE

    with caplog.at_level("WARNING"):
        asyncio.run(exercise())
    assert "TTS provider/runtime error" not in caplog.text


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


def test_aplay_canonicalizes_streaming_wav_with_absurd_declared_frames(tmp_path, monkeypatch, caplog):
    """A streaming RIFF data size must never turn into an unbounded allocation."""
    source = tmp_path / "cartesia-streaming.wav"
    with wave.open(str(source), "wb") as wav:
        wav.setparams((1, 2, 44100, 0, "NONE", "not compressed"))
        wav.writeframes(b"\x10\0" * 4000)
    # The data chunk length is at byte 40 in this ordinary PCM WAV.  Simulate a
    # streaming response which left it at an implausibly large value.
    payload = bytearray(source.read_bytes())
    payload[40:44] = (0xFFFFFFF0).to_bytes(4, "little")
    source.write_bytes(payload)
    seen = {}
    monkeypatch.setattr("robot.voice.tts.shutil.which", lambda command: "/usr/bin/aplay")

    def run(command, **kwargs):
        playback_path = Path(command[-1])
        with wave.open(str(playback_path), "rb") as wav:
            seen["params"] = wav.getparams()
            seen["frames"] = wav.readframes(wav.getnframes())
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("robot.voice.tts.subprocess.run", run)
    with caplog.at_level("WARNING"):
        AplayAudioOutputProvider().play(SynthesizedAudio(source))

    assert (seen["params"].framerate, seen["params"].nchannels, seen["params"].sampwidth) == (48000, 2, 2)
    assert seen["frames"]
    assert "PHOS AUDIO: inconsistent WAV frame metadata; using bounded canonicalization" in caplog.text


def test_aplay_reads_large_wavs_in_bounded_chunks(tmp_path, monkeypatch):
    source = tmp_path / "large.wav"
    with wave.open(str(source), "wb") as wav:
        wav.setparams((1, 2, 22050, 0, "NONE", "not compressed"))
        wav.writeframes(b"\x11\0" * 50_000)
    calls = []
    original_readframes = wave.Wave_read.readframes

    def bounded_readframes(wav, frame_count):
        calls.append(frame_count)
        assert frame_count <= 4096
        return original_readframes(wav, frame_count)

    monkeypatch.setattr(wave.Wave_read, "readframes", bounded_readframes)
    monkeypatch.setattr("robot.voice.tts.shutil.which", lambda command: "/usr/bin/aplay")
    monkeypatch.setattr("robot.voice.tts.subprocess.run",
                        lambda command, **kwargs: subprocess.CompletedProcess(command, 0, "", ""))
    AplayAudioOutputProvider().play(SynthesizedAudio(source))
    assert calls


def test_aplay_rejects_truncated_wav_as_typed_audio_error(tmp_path):
    source = tmp_path / "truncated.wav"
    source.write_bytes(b"RIFF\x24\0\0\0WAVEfmt ")
    with pytest.raises(AudioOutputError, match="could not be prepared"):
        AplayAudioOutputProvider()._normalized_wav(source)


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
def test_pipewire_output_uses_pw_play_without_shell_and_bounds_failures(tmp_path, monkeypatch, caplog):
    artifact = tmp_path / "speech.wav"
    artifact.write_bytes(b"RIFF test")
    calls = []
    monkeypatch.setattr("robot.voice.tts.shutil.which", lambda command: "/usr/bin/pw-play" if command == "pw-play" else None)

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 7, "", "sink unavailable\n" + "x" * 400)

    monkeypatch.setattr("robot.voice.tts.subprocess.run", run)
    with caplog.at_level("INFO"), pytest.raises(AudioOutputError, match="PipeWire audio playback failed"):
        PipeWireAudioOutputProvider(retry_max_attempts=1).play(SynthesizedAudio(artifact))

    assert calls == [(["/usr/bin/pw-play", str(artifact)], {"capture_output": True, "text": True,
                                                               "timeout": 90, "check": False})]
    assert "shell" not in calls[0][1]
    assert "PHOS AUDIO: provider=pipewire playback_started" in caplog.text
    assert "PHOS AUDIO: provider=pipewire playback_failed" in caplog.text
    assert len(caplog.text) < 1000


def test_pipewire_factory_requires_pw_play_and_keeps_aplay_available(tmp_path, monkeypatch):
    document = load_document()
    document["tts"]["audio_output"]["player"] = "pipewire"
    config = RuntimeConfig.from_dict(document, base_dir=tmp_path)
    monkeypatch.setattr("robot.voice.tts.shutil.which", lambda command: "/usr/bin/pw-play" if command == "pw-play" else None)
    assert isinstance(AudioOutputProviderFactory.create(config), PipeWireAudioOutputProvider)
    monkeypatch.setattr("robot.voice.tts.shutil.which", lambda command: None)
    with pytest.raises(AudioOutputError, match="unavailable"):
        AudioOutputProviderFactory.create(config)

    document["tts"]["audio_output"]["player"] = "aplay"
    assert isinstance(AudioOutputProviderFactory.create(RuntimeConfig.from_dict(document, base_dir=tmp_path)),
                      AplayAudioOutputProvider)
