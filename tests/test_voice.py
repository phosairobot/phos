import asyncio
import logging
import sys
import threading
import wave
from types import SimpleNamespace

from robot.core import EventBus, RobotState
from robot.voice import PCM16Resampler, PyAudioCaptureProvider, ResamplingAudioCaptureProvider, SpeechRecognitionResult, UnsupportedCaptureRate, VoiceCaptureSession, VoiceActivityDetector
from robot.voice.session import VOICE_TRANSCRIPTION_COMPLETED


def _pcm(amplitude, samples=480):
    return int(amplitude).to_bytes(2, "little", signed=True) * samples


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
        def get_device_info_by_index(self, index): return {"index": index, "name": "USB PnP Sound Device: Audio (hw:2,0)", "maxInputChannels": 1, "defaultSampleRate": 44100}
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
    asyncio.run(provider.stop())


def test_capture_reports_unsupported_rate_with_device_default(monkeypatch):
    _fake_pyaudio(monkeypatch, set())
    provider = PyAudioCaptureProvider(device_index=2, sample_rate=16000)
    try: asyncio.run(provider.start())
    except UnsupportedCaptureRate as error:
        assert error.requested_rate == 16000 and error.device_default_rate == 44100
    else: raise AssertionError("unsupported microphone format was accepted")


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
    with caplog.at_level(logging.INFO): frame = asyncio.run(run())
    assert len(frame) == 1323 * 2
    assert "VOICE AUDIO READ: waiting" in caplog.text
    assert "VOICE AUDIO READ: received bytes=2646" in caplog.text
    assert "VOICE AUDIO FRAME: bytes=2646 capture_rate=44100 processing_rate=44100" in caplog.text


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
    assert "VOICE AUDIO ERROR: stage=read exception_type=OSError exception_message=device disconnected" in caplog.text


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
    with caplog.at_level(logging.INFO): frame = asyncio.run(run())
    assert len(frame) % 2 == 0 and 940 <= len(frame) <= 970
    assert "VOICE AUDIO RESAMPLING: input_rate=44100 output_rate=16000" in caplog.text
    assert "VOICE AUDIO FRAME:" in caplog.text


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
    with caplog.at_level(logging.INFO):
        asyncio.run(run())
    assert events == ["voice_listening_started", "voice_speech_started", "voice_speech_ended", "voice_transcription_started", "voice_transcription_completed"]
    assert session.status()["last_transcript"] == "hello PHOS" and session.status()["state"] == "idle"
    assert states == [RobotState.LISTENING, RobotState.THINKING, RobotState.IDLE]
    levels = [record.message for record in caplog.records if record.message.startswith("VOICE VAD LEVEL:")]
    assert len(levels) == 1
    assert "rms=300 peak=300 threshold=100 speech_candidate_ms=30 speech_active=False" in levels[0]


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
    assert f"VOICE DEBUG WAV: path={dump} bytes={len(expected)} duration_ms=420" in caplog.text
    with wave.open(str(dump), "rb") as output:
        assert (output.getnchannels(), output.getsampwidth(), output.getframerate(), output.getnframes()) == (1, 2, 16000, 6720)
        assert output.readframes(output.getnframes()) == expected


def test_empty_stt_result_is_not_an_error_and_logs_raw_result(caplog):
    class Capture:
        async def start(self): pass
        async def stop(self): pass
    stt = _STT(SpeechRecognitionResult("", "fake", "fake-model", None, None, 30, "now"))
    async def transition(state, reason): pass
    session = VoiceCaptureSession(Capture(), stt, EventBus(), transition, enabled=True, sample_rate=16000, channels=1,
        chunk_ms=30, speech_start_ms=60, silence_end_ms=90, min_utterance_ms=30, max_utterance_ms=1000, threshold=100)
    with caplog.at_level(logging.INFO): asyncio.run(session._finalize(_pcm(300)))
    assert session.status()["last_transcript"] is None and session.status()["last_error"] is None
    assert "VOICE STT RAW RESULT: text='' confidence=null language=null" in caplog.text


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
