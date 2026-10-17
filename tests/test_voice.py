import asyncio
import logging
import sys
import threading
import time
import wave
from types import SimpleNamespace
import pytest

from robot.core import EventBus, RobotState
import robot.voice.capture as capture_module
from robot.voice import (CartesiaSTTProvider, PCM16Resampler, PyAudioCaptureProvider,
                         ResamplingAudioCaptureProvider, SpeechRecognitionResult, STTConfigurationError,
                         STTProviderError, STTProviderFactory, UnsupportedCaptureRate, CaptureDeviceUnavailable,
                         VoiceCaptureSession, VoiceActivityDetector, VoskSTTProvider)
from robot.voice.session import VOICE_TRANSCRIPTION_COMPLETED


def _pcm(amplitude, samples=480):
    return int(amplitude).to_bytes(2, "little", signed=True) * samples


def test_stt_factory_defaults_to_local_vosk_and_cartesia_is_explicit(monkeypatch):
    local = SimpleNamespace(voice_stt_provider="local", voice_stt_model_path=None,
                            voice_stt_language=None, resolve_path=lambda path: path)
    assert isinstance(STTProviderFactory.create(local), VoskSTTProvider)
    cartesia = SimpleNamespace(voice_stt_provider="cartesia", voice_stt_cartesia_model_id="ink-whisper-2025-06-04",
                               voice_stt_cartesia_language="en", voice_stt_cartesia_sample_rate=16000)
    with pytest.raises(STTConfigurationError, match="credential"):
        STTProviderFactory.create(cartesia, SimpleNamespace(get_secret=lambda name: None))
    class Client:
        def __init__(self, *, api_key): self.api_key = api_key
    monkeypatch.setitem(sys.modules, "cartesia", SimpleNamespace(Cartesia=Client))
    assert isinstance(STTProviderFactory.create(cartesia, SimpleNamespace(get_secret=lambda name: "secret")),
                      CartesiaSTTProvider)


def test_cartesia_stt_wraps_canonical_pcm_as_wav_and_returns_provider_neutral_result():
    received = {}

    class Client:
        class stt:
            @staticmethod
            def transcribe(**kwargs):
                received.update(kwargs)
                return SimpleNamespace(text="hello PHOS")

    provider = CartesiaSTTProvider("secret", "ink-whisper-2025-06-04", "en", 16000,
                                   client_factory=lambda **kwargs: Client())
    result = asyncio.run(provider.transcribe(_pcm(300), sample_rate=16000, channels=1))
    filename, audio, content_type = received["file"]
    with wave.open(audio, "rb") as wav_file:
        assert (wav_file.getframerate(), wav_file.getnchannels(), wav_file.getsampwidth()) == (16000, 1, 2)
    assert (filename, content_type, received["model"], received["language"], result.text, result.provider) == (
        "phos-utterance.wav", "audio/wav", "ink-whisper-2025-06-04", "en", "hello PHOS", "cartesia")
    assert "secret" not in repr(received)


def test_cartesia_stt_failures_are_typed_and_do_not_select_vosk(caplog):
    class Client:
        class stt:
            @staticmethod
            def transcribe(**kwargs):
                error = RuntimeError("network failed"); raise error
    provider = CartesiaSTTProvider("secret", "ink-whisper-2025-06-04", "en", 16000,
                                   client_factory=lambda **kwargs: Client())
    with caplog.at_level(logging.WARNING), pytest.raises(STTProviderError, match="transcription_failed"):
        asyncio.run(provider.transcribe(_pcm(300), sample_rate=16000, channels=1))
    assert "PHOS STT: provider=cartesia error=transcription_failed" in caplog.text
    assert "secret" not in caplog.text


def test_energy_vad_emits_only_speech_edges():
    vad = VoiceActivityDetector(threshold=100, speech_start_ms=60, silence_end_ms=90, chunk_ms=30)
    assert [vad.observe(_pcm(0)) for _ in range(2)] == [None, None]
    assert [vad.observe(_pcm(300)) for _ in range(2)] == [None, "started"]
    assert [vad.observe(_pcm(0)) for _ in range(3)] == [None, None, "ended"]


def test_vad_level_reports_pcm16_amplitudes_and_candidate_duration():
    vad = VoiceActivityDetector(threshold=100, speech_start_ms=60, silence_end_ms=90, chunk_ms=30)
    assert vad.observe(_pcm(300)) is None
    assert vad.level() == {"rms": 300, "peak": 300, "threshold": 100,
                           "speech_candidate_ms": 30, "speech_active": False}
    assert vad.observe(_pcm(300)) == "started"
    assert vad.level()["speech_active"] is True


def _fake_pyaudio(monkeypatch, supported, stream=None):
    class Stream:
        def read(self, frames, exception_on_overflow=False): return _pcm(200, frames)
        def stop_stream(self): pass
        def close(self): pass
    class Audio:
        def get_device_count(self): return 3
        def get_device_info_by_index(self, index): return {"index": index, "name": "USB PnP Sound Device: Audio (hw:2,0)" if index == 0 else f"Other input {index}", "maxInputChannels": 1, "defaultSampleRate": 44100}
        def get_default_input_device_info(self): return self.get_device_info_by_index(2)
        def is_format_supported(self, rate, **kwargs): return rate in supported
        def open(self, **kwargs): self.opened = kwargs; return stream or Stream()
        def terminate(self): pass
    module = SimpleNamespace(paInt16=8, PyAudio=Audio)
    monkeypatch.setitem(sys.modules, "pyaudio", module)


def test_capture_negotiates_native_rate_when_requested_16k_is_unsupported(monkeypatch):
    _fake_pyaudio(monkeypatch, {44100})
    provider = PyAudioCaptureProvider(device_name="USB PnP", sample_rate=16000)
    asyncio.run(provider.start())
    assert provider.capture_rate == 44100 and provider.resolved_device_index == 0
    assert provider._audio.opened == {"format": 8, "channels": 1, "rate": 44100, "input": True,
                                      "input_device_index": 0, "frames_per_buffer": 1323}
    assert provider._native_frames_per_read == 1323
    asyncio.run(provider.stop())


def test_stale_numeric_capture_index_is_rejected_instead_of_selecting_another_input(monkeypatch):
    _fake_pyaudio(monkeypatch, set())
    provider = PyAudioCaptureProvider(device_index=2, sample_rate=16000)
    try: asyncio.run(provider.start())
    except CaptureDeviceUnavailable as error:
        assert str(error) == "capture_device_name_required"
    else: raise AssertionError("stale numeric input index was accepted")


def test_configured_device_name_selects_the_same_microphone_after_enumeration_reorder(monkeypatch):
    class Audio:
        def get_device_count(self): return 3
        def get_device_info_by_index(self, index):
            names = ["HDMI input", "Other USB input", "USB PnP Sound Device"]
            return {"index": index, "name": names[index], "maxInputChannels": 1, "defaultSampleRate": 48000}
        def get_default_input_device_info(self): return self.get_device_info_by_index(0)
        def is_format_supported(self, rate, **_kwargs): return rate == 48000
        def open(self, **kwargs): self.opened = kwargs; return SimpleNamespace(stop_stream=lambda: None, close=lambda: None)
        def terminate(self): pass
    monkeypatch.setitem(sys.modules, "pyaudio", SimpleNamespace(paInt16=8, PyAudio=Audio))
    provider = PyAudioCaptureProvider(device_name="USB PnP")
    asyncio.run(provider.start())
    assert (provider.resolved_device_index, provider.capture_rate) == (2, 48000)
    asyncio.run(provider.stop())


def test_pcm16_resampler_preserves_mono_framing_and_session_receives_16k():
    pcm = _pcm(300, 4410)
    converted = PCM16Resampler(44100, 16000).convert(pcm)
    assert len(converted) % 2 == 0 and 3000 <= len(converted) <= 3400
    class Native:
        capture_rate, channels, resolved_device_name, resolved_device_index = 44100, 1, "USB", 2
        def available(self): return True
        async def start(self): pass
        async def stop(self): pass
        async def close(self): pass
        async def read_frames(self): yield pcm
    wrapped = ResamplingAudioCaptureProvider(Native(), 16000)
    async def run():
        await wrapped.start()
        async for frame in wrapped.read_frames(): return frame
    assert len(asyncio.run(run())) % 2 == 0


def test_capture_read_frames_yields_audio_and_logs_first_read(monkeypatch, caplog):
    _fake_pyaudio(monkeypatch, {44100})
    provider = PyAudioCaptureProvider(device_name="USB PnP", sample_rate=16000)
    async def run():
        await provider.start()
        async for frame in provider.read_frames():
            await provider.stop()
            return frame
    with caplog.at_level(logging.DEBUG): frame = asyncio.run(run())
    assert len(frame) == 1323 * 2
    assert "PHOS VOICE CAPTURE: native_read_seq=1 native_frames=1323 native_bytes=2646" in caplog.text


def test_dedicated_capture_executor_yields_multiple_sequential_chunks(monkeypatch):
    chunks = [_pcm(100, 1323), _pcm(200, 1323), _pcm(300, 1323)]
    class Stream:
        def __init__(self): self.index = 0
        def read(self, _frames, exception_on_overflow=False):
            value = chunks[self.index]; self.index += 1; return value
        def stop_stream(self): pass
        def close(self): pass
    _fake_pyaudio(monkeypatch, {44100}, Stream())
    provider = PyAudioCaptureProvider(device_name="USB PnP")
    async def run():
        await provider.start()
        iterator = provider.read_frames()
        result = [await anext(iterator) for _ in chunks]
        await provider.stop()
        return result, provider.native_chunks_read
    result, native_chunks = asyncio.run(run())
    assert result == chunks and native_chunks == 3


def test_every_native_chunk_reaches_resampling_processing(monkeypatch):
    chunks = [_pcm(100, 1323), _pcm(200, 1323), _pcm(300, 1323)]
    class Stream:
        def __init__(self): self.index = 0
        def read(self, _frames, exception_on_overflow=False):
            value = chunks[self.index]; self.index += 1; return value
        def stop_stream(self): pass
        def close(self): pass
    _fake_pyaudio(monkeypatch, {44100}, Stream())
    raw = PyAudioCaptureProvider(device_name="USB PnP")
    capture = ResamplingAudioCaptureProvider(raw, 16000)
    async def run():
        await capture.start()
        iterator = capture.read_frames()
        output = [await anext(iterator) for _ in chunks]
        await capture.stop()
        return output
    output = asyncio.run(run())
    assert len(output) == 3
    assert raw.native_chunks_read == raw.processed_chunks == 3


def test_capture_producer_continues_while_consumer_is_delayed_and_queue_is_bounded(monkeypatch):
    class RealtimeStream:
        def __init__(self): self.read_count = 0; self.stopped = False
        def read(self, frames, exception_on_overflow=False):
            time.sleep(.01)
            self.read_count += 1
            return _pcm(self.read_count, frames)
        def stop_stream(self): self.stopped = True
        def close(self): self.stopped = True
    stream = RealtimeStream()
    _fake_pyaudio(monkeypatch, {44100}, stream)
    provider = PyAudioCaptureProvider(device_name="USB PnP", native_queue_chunks=2)
    async def run():
        await provider.start()
        iterator = provider.read_frames()
        first = await anext(iterator)
        # Processing takes much longer than one 30ms audio chunk. The worker
        # must nevertheless continue consuming the native stream.
        await asyncio.sleep(.08)
        second = await anext(iterator)
        await provider.stop()
        return first, second
    first, second = asyncio.run(run())
    assert int.from_bytes(first[:2], "little", signed=True) == 1
    assert int.from_bytes(second[:2], "little", signed=True) == 2
    assert provider.native_chunks_read >= 6
    assert provider.queue_high_watermark <= 2
    assert provider.dropped_chunks > 0
    assert stream.stopped and provider._capture_thread is None


def test_blocking_capture_read_does_not_freeze_event_loop_and_cancellation_closes_stream(monkeypatch):
    entered, released = threading.Event(), threading.Event()
    class BlockingStream:
        stopped = closed = False
        def read(self, frames, exception_on_overflow=False):
            entered.set(); released.wait(); return _pcm(200, frames)
        def stop_stream(self): self.stopped = True; released.set()
        def close(self): self.closed = True; released.set()
    stream = BlockingStream()
    _fake_pyaudio(monkeypatch, {44100}, stream)
    provider = PyAudioCaptureProvider(device_name="USB PnP", sample_rate=16000)
    async def run():
        await provider.start()
        iterator = provider.read_frames()
        read = asyncio.create_task(anext(iterator))
        while not entered.is_set(): await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert not read.done()
        read.cancel()
        await asyncio.gather(read, return_exceptions=True)
        await provider.stop()
    asyncio.run(run())
    assert stream.stopped and stream.closed


def test_capture_read_error_is_logged_with_read_stage(monkeypatch, caplog):
    class FailingStream:
        def read(self, frames, exception_on_overflow=False): raise OSError("device disconnected")
        def stop_stream(self): pass
        def close(self): pass
    _fake_pyaudio(monkeypatch, {44100}, FailingStream())
    provider = PyAudioCaptureProvider(device_name="USB PnP", sample_rate=16000)
    async def run():
        await provider.start()
        async for _ in provider.read_frames(): pass
    with caplog.at_level(logging.ERROR):
        try: asyncio.run(run())
        except OSError: pass
        else: raise AssertionError("read failure was swallowed")
    assert "VOICE AUDIO ERROR: stage=capture_worker exception_type=OSError exception_message=device disconnected" in caplog.text


def test_resampling_provider_yields_16k_pcm_and_logs_rates(caplog):
    class Native:
        capture_rate, channels, resolved_device_name, resolved_device_index = 44100, 1, "USB", 2
        def available(self): return True
        async def start(self): pass
        async def stop(self): pass
        async def close(self): pass
        async def read_frames(self): yield _pcm(300, 1323)
    wrapped = ResamplingAudioCaptureProvider(Native(), 16000)
    async def run():
        await wrapped.start()
        async for frame in wrapped.read_frames(): return frame
    with caplog.at_level(logging.DEBUG): frame = asyncio.run(run())
    assert len(frame) % 2 == 0 and 940 <= len(frame) <= 970
    assert "VOICE AUDIO RESAMPLING: input_rate=44100 output_rate=16000" in caplog.text
    assert "VOICE AUDIO FRAME:" in caplog.text


def test_chunk_geometry_keeps_native_and_processing_rate_domains_separate(monkeypatch, caplog):
    _fake_pyaudio(monkeypatch, {44100})
    raw = PyAudioCaptureProvider(device_name="USB PnP", sample_rate=16000, chunk_ms=30)
    capture = ResamplingAudioCaptureProvider(raw, 16000)
    async def run():
        await capture.start()
        result = raw._native_frames_per_read, capture.processing_frames_per_chunk
        await capture.stop()
        return result
    with caplog.at_level(logging.INFO): native_frames, processing_frames = asyncio.run(run())
    assert (native_frames, processing_frames) == (1323, 480)
    assert "native_frames_per_read=1323 processing_frames_per_chunk=480" in caplog.text


def test_native_capture_reports_realtime_ratio_from_wall_and_audio_duration(monkeypatch, tmp_path, caplog):
    provider = PyAudioCaptureProvider(debug_native_wav=True, debug_native_wav_path=tmp_path / "native.wav")
    provider.capture_rate, provider.channels, provider._capture_started_at = 44100, 1, 100.0
    monkeypatch.setattr(capture_module.time, "monotonic", lambda: 106.3)
    # 6.3 seconds at 44.1 kHz, mono PCM16.
    pcm = _pcm(100, 44100 * 63 // 10)
    with caplog.at_level(logging.INFO):
        provider._write_native_wav(pcm)
    assert "capture_wall_ms=6300 native_audio_ms=6300 realtime_ratio=1.0" in caplog.text


def test_stereo_capture_is_downmixed_before_the_single_16k_resample():
    left, right = _pcm(1000, 4410), _pcm(-1000, 4410)
    stereo = b"".join(left[index:index + 2] + right[index:index + 2] for index in range(0, len(left), 2))
    class Native:
        capture_rate, channels, resolved_device_name, resolved_device_index = 44100, 2, "USB", 2
        def available(self): return True
        async def start(self): pass
        async def stop(self): pass
        async def close(self): pass
        async def read_frames(self): yield stereo
    wrapped = ResamplingAudioCaptureProvider(Native(), 16000)
    async def run():
        await wrapped.start()
        async for frame in wrapped.read_frames(): return frame
    frame = asyncio.run(run())
    assert len(frame) % 2 == 0 and 3000 <= len(frame) <= 3400
    assert max(abs(value) for value in memoryview(frame).cast("h")) <= 2


def test_debug_native_capture_wav_is_written_before_processing(monkeypatch, tmp_path):
    chunks = [_pcm(100, 3), _pcm(-200, 5), _pcm(300, 2)]
    class Stream:
        def __init__(self): self.index = 0
        def read(self, _frames, exception_on_overflow=False):
            value = chunks[self.index]; self.index += 1; return value
        def stop_stream(self): pass
        def close(self): pass
    _fake_pyaudio(monkeypatch, {44100}, Stream())
    native = tmp_path / "phos-debug-native-capture.wav"
    provider = PyAudioCaptureProvider(device_name="USB PnP", sample_rate=16000,
                                      debug_native_wav=True, debug_native_wav_path=native)
    async def run():
        await provider.start()
        iterator = provider.read_frames()
        for _ in chunks: await anext(iterator)
        await provider.stop()
    asyncio.run(run())
    with wave.open(str(native), "rb") as captured:
        assert (captured.getframerate(), captured.getnchannels(), captured.getsampwidth()) == (44100, 1, 2)
        expected = b"".join(chunks)
        assert captured.getnframes() == len(expected) // 2
        assert captured.readframes(captured.getnframes()) == expected


def test_native_debug_duration_uses_all_raw_frames_not_vad_sized(monkeypatch, tmp_path, caplog):
    # Silence is included deliberately: VAD would exclude it from an utterance,
    # but native diagnostics must preserve every stream.read() byte.
    chunks = [_pcm(0, 441), _pcm(0, 441), _pcm(500, 441), _pcm(0, 441)]
    class Stream:
        def __init__(self): self.index = 0
        def read(self, _frames, exception_on_overflow=False):
            value = chunks[self.index]; self.index += 1; return value
        def stop_stream(self): pass
        def close(self): pass
    _fake_pyaudio(monkeypatch, {44100}, Stream())
    native = tmp_path / "native.wav"
    provider = PyAudioCaptureProvider(device_name="USB PnP", debug_native_wav=True,
                                      debug_native_wav_path=native)
    async def run():
        await provider.start()
        iterator = provider.read_frames()
        for _ in chunks: await anext(iterator)
        await provider.stop()
    with caplog.at_level(logging.INFO): asyncio.run(run())
    expected = b"".join(chunks)
    with wave.open(str(native), "rb") as captured:
        assert captured.readframes(captured.getnframes()) == expected
        assert captured.getnframes() == len(expected) // 2
        assert captured.getnframes() / captured.getframerate() == pytest.approx(.04)
    assert "PHOS VOICE NATIVE: frames=1764 bytes=3528 duration_ms=40" in caplog.text


def test_native_debug_dump_is_independent_from_vad_selected_utterance(monkeypatch, tmp_path):
    # Six native 30ms reads: one leading silence is intentionally not part of
    # the VAD utterance (pre-roll is disabled), but must stay in the diagnostic.
    chunks = [_pcm(0, 1323), _pcm(500, 1323), _pcm(500, 1323),
              _pcm(0, 1323), _pcm(0, 1323), _pcm(0, 1323)]
    class Stream:
        def __init__(self): self.index = 0
        def read(self, _frames, exception_on_overflow=False):
            value = chunks[self.index]; self.index += 1; return value
        def stop_stream(self): pass
        def close(self): pass
    _fake_pyaudio(monkeypatch, {44100}, Stream())
    native, utterance = tmp_path / "native.wav", tmp_path / "utterance.wav"
    raw = PyAudioCaptureProvider(device_name="USB PnP", debug_native_wav=True,
                                 debug_native_wav_path=native)
    capture = ResamplingAudioCaptureProvider(raw, 16000)
    async def transition(_state, _reason): pass
    session = VoiceCaptureSession(capture, _STT(), EventBus(), transition, enabled=True,
        sample_rate=16000, channels=1, chunk_ms=30, speech_start_ms=60, silence_end_ms=90,
        min_utterance_ms=30, max_utterance_ms=1000, threshold=100, pre_roll_ms=0,
        debug_dump_utterance_wav=True, debug_utterance_wav_path=utterance)
    async def run():
        await session.start()
        await session._task
    asyncio.run(run())
    with wave.open(str(native), "rb") as native_wav:
        native_payload = native_wav.readframes(native_wav.getnframes())
        assert native_payload == b"".join(chunks)
        assert (native_wav.getframerate(), native_wav.getnchannels()) == (44100, 1)
    with wave.open(str(utterance), "rb") as utterance_wav:
        assert (utterance_wav.getframerate(), utterance_wav.getnchannels(), utterance_wav.getsampwidth()) == (16000, 1, 2)
        assert utterance_wav.getnframes() < len(native_payload) // 2


class _Capture:
    async def start(self): pass
    async def stop(self): pass
    async def read_frames(self):
        for frame in (_pcm(300), _pcm(300), _pcm(0), _pcm(0), _pcm(0)):
            yield frame


class _STT:
    name, model = "fake", "fake-model"
    def __init__(self, result=None): self.result, self.pcm = result, None
    def available(self): return True
    async def transcribe(self, pcm, *, sample_rate, channels):
        self.pcm = pcm
        return self.result or SpeechRecognitionResult("hello PHOS", "fake", "fake-model", .9, "en", 150, "now")


def test_capture_session_transcribes_bounded_utterance_and_returns_idle(caplog):
    events, states = [], []
    bus = EventBus()
    for name in ("voice_listening_started", "voice_speech_started", "voice_speech_ended", "voice_transcription_started", VOICE_TRANSCRIPTION_COMPLETED):
        bus.subscribe(name, lambda event: events.append(event.name))
    async def transition(state, reason): states.append(state)
    session = VoiceCaptureSession(_Capture(), _STT(), bus, transition, enabled=True, sample_rate=16000, channels=1,
        chunk_ms=30, speech_start_ms=60, silence_end_ms=90, min_utterance_ms=30, max_utterance_ms=1000, threshold=100)
    async def run():
        await session.start(); await session._task
    with caplog.at_level(logging.DEBUG):
        asyncio.run(run())
    assert events == ["voice_listening_started", "voice_speech_started", "voice_speech_ended", "voice_transcription_started", "voice_transcription_completed"]
    assert session.status()["last_transcript"] == "hello PHOS" and session.status()["state"] == "idle"
    assert states == [RobotState.LISTENING, RobotState.THINKING, RobotState.IDLE]
    levels = [record.message for record in caplog.records if record.message.startswith("VOICE VAD LEVEL:")]
    assert len(levels) == 1
    assert "rms=300 peak=300 threshold=100 speech_candidate_ms=30 speech_active=False" in levels[0]


def test_capture_processing_stall_ends_session_cleanly():
    class StalledCapture:
        def __init__(self): self.stopped = False
        async def start(self): pass
        async def stop(self): self.stopped = True
        async def read_frames(self):
            await asyncio.Event().wait()
            yield b""
    capture, states = StalledCapture(), []
    async def transition(state, _reason): states.append(state)
    session = VoiceCaptureSession(capture, _STT(), EventBus(), transition, enabled=True, sample_rate=16000,
        channels=1, chunk_ms=30, speech_start_ms=60, silence_end_ms=90, min_utterance_ms=30,
        max_utterance_ms=1000, threshold=100, capture_stall_timeout_s=.01)
    async def run():
        await session.start()
        await session._task
    asyncio.run(run())
    assert capture.stopped
    assert session.status()["state"] == "idle"
    assert session.status()["last_error"] == "capture_processing_stalled"
    assert states == [RobotState.LISTENING, RobotState.IDLE]


def test_active_voice_session_keeps_pinned_stt_when_default_is_replaced():
    old, new = _STT(), _STT()
    old.name, new.name = "old", "new"
    async def transition(state, reason): pass
    session = VoiceCaptureSession(_Capture(), old, EventBus(), transition, enabled=True, sample_rate=16000, channels=1,
        chunk_ms=30, speech_start_ms=60, silence_end_ms=90, min_utterance_ms=30, max_utterance_ms=1000, threshold=100)
    async def run():
        await session.start()
        session.replace_stt_provider(new)
        await session._task
    asyncio.run(run())
    assert old.pcm is not None and new.pcm is None
    assert session.status()["stt_provider"] == "old"


def test_session_prepends_bounded_pre_roll_and_writes_exact_vosk_wav(tmp_path, caplog):
    dump = tmp_path / "utterance.wav"
    before_start = [_pcm(10 + index) for index in range(10)] + [_pcm(300)]
    trigger, silence = _pcm(400), _pcm(0)
    class Capture:
        async def start(self): pass
        async def stop(self): pass
        async def read_frames(self):
            for frame in (*before_start, trigger, silence, silence, silence): yield frame
    stt, events = _STT(), []
    bus = EventBus()
    bus.subscribe("voice_speech_ended", lambda event: events.append(event.data))
    async def transition(state, reason): pass
    session = VoiceCaptureSession(Capture(), stt, bus, transition, enabled=True, sample_rate=16000, channels=1,
        chunk_ms=30, speech_start_ms=60, silence_end_ms=90, min_utterance_ms=30, max_utterance_ms=1000, threshold=100,
        debug_dump_utterance_wav=True, debug_utterance_wav_path=dump)
    async def run(): await session.start(); await session._task
    with caplog.at_level(logging.DEBUG): asyncio.run(run())
    expected = b"".join(before_start[-10:] + [trigger, silence, silence, silence])
    assert stt.pcm == expected
    assert events == [{"duration_ms": 420}]
    assert "VOICE SPEECH: started pre_roll_ms=300 pre_roll_bytes=9600" in caplog.text
    assert f"VOICE AUDIO: accumulated_bytes={len(expected)}" in caplog.text
    assert f"VOICE AUDIO: finalized_bytes={len(expected)}" in caplog.text
    assert f"VOICE AUDIO: finalized_bytes_after_close={len(expected)}" in caplog.text
    assert f"VOICE AUDIO: submitted_bytes={len(expected)}" in caplog.text
    assert f"VOICE DEBUG WAV: path={dump} bytes={len(expected)} duration_ms=420" in caplog.text
    with wave.open(str(dump), "rb") as output:
        assert (output.getnchannels(), output.getsampwidth(), output.getframerate(), output.getnframes()) == (1, 2, 16000, 6720)
        assert output.readframes(output.getnframes()) == expected


def test_finalized_pcm_is_immutable_and_survives_capture_close_before_stt(tmp_path, caplog):
    source = bytearray(_pcm(300))
    expected = bytes(source)

    class ClearingCapture:
        def __init__(self, buffer):
            self.buffer, self.stop_calls = buffer, 0
        async def start(self): pass
        async def stop(self):
            self.stop_calls += 1
            self.buffer.clear()

    capture, stt = ClearingCapture(source), _STT()
    async def transition(state, reason): pass
    dump = tmp_path / "finalized.wav"
    session = VoiceCaptureSession(capture, stt, EventBus(), transition, enabled=True, sample_rate=16000,
        channels=1, chunk_ms=30, speech_start_ms=60, silence_end_ms=90, min_utterance_ms=30,
        max_utterance_ms=1000, threshold=100, debug_dump_utterance_wav=True,
        debug_utterance_wav_path=dump)

    with caplog.at_level(logging.INFO):
        asyncio.run(session._finalize(source))

    assert source == bytearray()
    assert capture.stop_calls >= 1
    assert isinstance(stt.pcm, bytes)
    assert stt.pcm == expected
    assert "VOICE AUDIO: finalized_bytes=%s" % len(expected) in caplog.text
    assert "VOICE AUDIO: finalized_bytes_after_close=%s" % len(expected) in caplog.text
    assert "VOICE AUDIO: submitted_bytes=%s" % len(expected) in caplog.text
    with wave.open(str(dump), "rb") as output:
        assert (output.getnchannels(), output.getsampwidth(), output.getframerate()) == (1, 2, 16000)
        assert output.readframes(output.getnframes()) == stt.pcm


def test_empty_stt_result_is_not_an_error_and_logs_empty_transcript(caplog):
    class Capture:
        async def start(self): pass
        async def stop(self): pass
    stt = _STT(SpeechRecognitionResult("", "fake", "fake-model", None, None, 30, "now"))
    async def transition(state, reason): pass
    session = VoiceCaptureSession(Capture(), stt, EventBus(), transition, enabled=True, sample_rate=16000, channels=1,
        chunk_ms=30, speech_start_ms=60, silence_end_ms=90, min_utterance_ms=30, max_utterance_ms=1000, threshold=100)
    with caplog.at_level(logging.INFO): asyncio.run(session._finalize(_pcm(300)))
    assert (session.status()["last_transcript"] is None and session.status()["language"] is None
            and session.status()["last_error"] is None)
    assert "VOICE TRANSCRIPT: <empty>" in caplog.text
    assert session.status()["last_transcription_duration_ms"] is not None


def test_debug_wav_write_failure_does_not_skip_stt(tmp_path, caplog):
    class Capture:
        async def start(self): pass
        async def stop(self): pass
    stt = _STT()
    async def transition(state, reason): pass
    session = VoiceCaptureSession(Capture(), stt, EventBus(), transition, enabled=True, sample_rate=16000, channels=1,
        chunk_ms=30, speech_start_ms=60, silence_end_ms=90, min_utterance_ms=30, max_utterance_ms=1000,
        threshold=100, debug_dump_utterance_wav=True, debug_utterance_wav_path=tmp_path / "missing" / "utterance.wav")
    with caplog.at_level(logging.ERROR): asyncio.run(session._finalize(_pcm(300)))
    assert stt.pcm == _pcm(300)
    assert "VOICE DEBUG WAV ERROR: exception_type=FileNotFoundError" in caplog.text


def test_pre_roll_discards_old_bytes_and_zero_pre_roll_is_supported():
    class Capture:
        async def start(self): pass
        async def stop(self): pass
    async def transition(state, reason): pass
    session = VoiceCaptureSession(Capture(), _STT(), EventBus(), transition, enabled=True, sample_rate=16000, channels=1,
        chunk_ms=30, speech_start_ms=60, silence_end_ms=90, min_utterance_ms=30, max_utterance_ms=1000,
        threshold=100, pre_roll_ms=300)
    assert session._pre_roll_limit_bytes == 9600
    session._pre_roll.extend(b"x" * 12000)
    del session._pre_roll[:-session._pre_roll_limit_bytes]
    assert len(session._pre_roll) == 9600 and session._pre_roll == bytearray(b"x" * 9600)
    zero = VoiceCaptureSession(Capture(), _STT(), EventBus(), transition, enabled=True, sample_rate=16000, channels=1,
        chunk_ms=30, speech_start_ms=60, silence_end_ms=90, min_utterance_ms=30, max_utterance_ms=1000,
        threshold=100, pre_roll_ms=0)
    assert zero._pre_roll_limit_bytes == 0


def test_pre_roll_is_cleared_when_session_is_cancelled():
    class Capture:
        async def start(self): pass
        async def stop(self): pass
    async def transition(state, reason): pass
    session = VoiceCaptureSession(Capture(), _STT(), EventBus(), transition, enabled=True, sample_rate=16000, channels=1,
        chunk_ms=30, speech_start_ms=60, silence_end_ms=90, min_utterance_ms=30, max_utterance_ms=1000,
        threshold=100, pre_roll_ms=300)
    session._pre_roll.extend(_pcm(20))
    asyncio.run(session.stop(cancelled=True))
    assert not session._pre_roll


def test_pre_roll_is_reset_before_each_listening_session():
    class Capture:
        async def start(self): pass
        async def stop(self): pass
        async def read_frames(self):
            if False: yield b""
    async def transition(state, reason): pass
    session = VoiceCaptureSession(Capture(), _STT(), EventBus(), transition, enabled=True, sample_rate=16000, channels=1,
        chunk_ms=30, speech_start_ms=60, silence_end_ms=90, min_utterance_ms=30, max_utterance_ms=1000,
        threshold=100, pre_roll_ms=300)
    session._pre_roll.extend(_pcm(20))
    async def run(): await session.start(); await session._task
    asyncio.run(run())
    assert not session._pre_roll
