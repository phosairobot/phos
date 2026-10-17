"""PyAudio input negotiation and the one PCM16 resampling boundary."""
from __future__ import annotations

import asyncio
import audioop
import logging
import threading
import time
from pathlib import Path
import wave

logger = logging.getLogger(__name__)

# A diagnostic capture retains the beginning of one capture session only.  This
# is deliberately bounded: even a microphone left open while waiting for speech
# must not grow an in-memory debug buffer without limit on a Pi.
NATIVE_DEBUG_MAX_DURATION_SECONDS = 30
NATIVE_CAPTURE_QUEUE_CHUNKS = 16
_CAPTURE_STOP = object()


class UnsupportedCaptureRate(RuntimeError):
    def __init__(self, requested_rate, device_default_rate):
        super().__init__("unsupported_capture_rate")
        self.requested_rate, self.device_default_rate = requested_rate, device_default_rate


class CaptureDeviceUnavailable(RuntimeError):
    """A configured microphone could not be selected safely."""
    def __init__(self, reason="capture_device_unavailable"):
        super().__init__(reason)


def _pcm_stats(pcm: bytes) -> dict:
    if not pcm:
        return {"peak": 0, "rms": 0, "clipping": 0, "nonzero_ratio": 0.0}
    samples = len(pcm) // 2
    values = memoryview(pcm).cast("h")
    return {"peak": audioop.max(pcm, 2), "rms": audioop.rms(pcm, 2),
            "clipping": sum(abs(value) >= 32767 for value in values),
            "nonzero_ratio": round(sum(value != 0 for value in values) / samples, 4)}


class PCM16Resampler:
    """Streaming mono PCM16 conversion with phase retained between chunks."""
    def __init__(self, input_rate: int, output_rate: int) -> None:
        self.input_rate, self.output_rate, self._state = input_rate, output_rate, None
    def convert(self, pcm: bytes) -> bytes:
        if self.input_rate == self.output_rate: return pcm
        converted, self._state = audioop.ratecv(pcm, 2, 1, self.input_rate, self.output_rate, self._state)
        return converted


class PyAudioCaptureProvider:
    """Native PCM16 capture, selecting stable name before transient index."""
    def __init__(self, device_name=None, device_index=None, sample_rate=None, channels=1, chunk_ms=30,
                 *, debug_native_wav=False, debug_native_wav_path=Path("/tmp/phos-debug-native-capture.wav"),
                 debug_native_max_duration_seconds=NATIVE_DEBUG_MAX_DURATION_SECONDS,
                 native_queue_chunks=NATIVE_CAPTURE_QUEUE_CHUNKS) -> None:
        self.device_name, self.device_index, self.requested_rate = device_name, device_index, sample_rate
        self.channels, self.chunk_ms, self.capture_rate, self._audio, self._stream = channels, chunk_ms, None, None, None
        self.resolved_device_name, self.resolved_device_index = None, None
        self._last_frame_log = float("-inf")
        self._debug_native_wav, self._debug_native_wav_path = debug_native_wav, Path(debug_native_wav_path)
        self._debug_native_max_duration_seconds = debug_native_max_duration_seconds
        self._native_capture_pcm = bytearray()
        self._native_capture_max_bytes = 0
        self._native_capture_truncated = False
        self._native_frames_per_read = None
        self._capture_started_at = None
        self._capture_thread = None
        self._capture_stop = threading.Event()
        self._loop = None
        self._native_queue = None
        self._queue_slots = None
        self._native_queue_chunks = native_queue_chunks
        self._worker_error = None
        self._capture_finished = False
        self._stats_lock = threading.Lock()
        self._input_overflow_count = 0
        self._last_overflow_log = float("-inf")
        self.native_chunks_read = 0
        self.processed_chunks = 0
        self.queue_high_watermark = 0
        self.dropped_chunks = 0
        self._last_flow_log = float("-inf")
    def available(self):
        try: import pyaudio  # noqa: F401
        except ImportError: return False
        return True
    def _resolve(self, audio):
        if self.device_name:
            matches = [(index, info) for index in range(audio.get_device_count())
                       if (info := audio.get_device_info_by_index(index)).get("maxInputChannels", 0)
                       and self.device_name.lower() in info.get("name", "").lower()]
            if len(matches) != 1: raise CaptureDeviceUnavailable()
            return matches[0]
        if self.device_index is not None:
            # Numeric USB indexes are transient.  Never reinterpret a stale
            # index as an unrelated microphone after a port/reboot change.
            raise CaptureDeviceUnavailable("capture_device_name_required")
        info = audio.get_default_input_device_info(); return info["index"], info

    @staticmethod
    def _log_input_devices(audio):
        devices = []
        for index in range(audio.get_device_count()):
            info = audio.get_device_info_by_index(index)
            if info.get("maxInputChannels", 0):
                devices.append((index, info.get("name", ""), round(info.get("defaultSampleRate", 0)), info["maxInputChannels"]))
        logger.info("PHOS VOICE: input_devices=%s", devices)
    async def start(self):
        logger.debug("VOICE AUDIO: start requested")
        if not self.available(): raise RuntimeError("pyaudio_unavailable")
        import pyaudio
        self._audio = pyaudio.PyAudio()
        try:
            self._log_input_devices(self._audio)
            logger.debug("VOICE AUDIO: resolving input device")
            index, info = self._resolve(self._audio)
        except Exception as error:
            logger.exception("VOICE SESSION ERROR: stage=audio_resolve exception_type=%s exception_message=%s",
                             type(error).__name__, error)
            await self.stop()
            raise
        default_rate = round(info["defaultSampleRate"])
        candidates = [rate for rate in (self.requested_rate, default_rate, 44100, 48000, 16000) if rate]
        def supports(rate):
            try: return self._audio.is_format_supported(rate, input_device=index, input_channels=self.channels, input_format=pyaudio.paInt16)
            except ValueError: return False
        self.capture_rate = next((rate for rate in dict.fromkeys(candidates) if supports(rate)), None)
        if self.capture_rate is None:
            self._audio.terminate(); self._audio = None; raise UnsupportedCaptureRate(self.requested_rate, default_rate)
        if self._debug_native_wav:
            self._native_capture_pcm.clear()
            self._native_capture_truncated = False
            self._native_capture_max_bytes = (
                self.capture_rate * self.channels * 2 * self._debug_native_max_duration_seconds
            )
        self.resolved_device_index, self.resolved_device_name = index, info["name"]
        self._native_frames_per_read = max(1, round(self.capture_rate * self.chunk_ms / 1000))
        frames_per_buffer = self._native_frames_per_read
        host_api = info.get("hostApi")
        try: host_api = self._audio.get_host_api_info_by_index(host_api).get("name", host_api)
        except Exception: pass
        logger.info("PHOS VOICE: capture_device configured=%r index=%s name=%r host_api=%r default_rate=%s "
                    "max_input_channels=%s opened_rate=%s opened_channels=%s sample_width=2",
                    self.device_name, index, info["name"], host_api, default_rate,
                    info.get("maxInputChannels"), self.capture_rate, self.channels)
        try:
            logger.debug("VOICE AUDIO: stream opening input=True input_device_index=%s channels=%s format=paInt16 rate=%s frames_per_buffer=%s",
                        index, self.channels, self.capture_rate, frames_per_buffer)
            self._stream = self._audio.open(format=pyaudio.paInt16, channels=self.channels, rate=self.capture_rate,
                                            input=True, input_device_index=index, frames_per_buffer=frames_per_buffer)
            # Capture must not compete with vision/STT/TTS work submitted to
            # asyncio's shared executor. The worker is a producer: it starts
            # the next native read without waiting for resampling or VAD.
            self._loop = asyncio.get_running_loop()
            self._native_queue = asyncio.Queue(maxsize=self._native_queue_chunks)
            self._queue_slots = threading.BoundedSemaphore(self._native_queue_chunks)
            self._capture_stop.clear()
            self._worker_error = None
            self._capture_finished = False
            self._capture_started_at = time.monotonic()
            self._input_overflow_count = 0
            self.native_chunks_read = self.processed_chunks = 0
            self._last_flow_log = float("-inf")
            self.queue_high_watermark = self.dropped_chunks = 0
            self._capture_thread = threading.Thread(target=self._capture_worker,
                                                    name="phos-capture", daemon=True)
            logger.info("VOICE AUDIO: stream opened")
            self._capture_thread.start()
        except Exception as error:
            logger.exception("VOICE SESSION ERROR: stage=audio_open exception_type=%s exception_message=%s",
                             type(error).__name__, error)
            await self.stop()
            raise
    async def read_frames(self):
        logger.debug("VOICE AUDIO: read loop started")
        while True:
            if self._capture_finished and self._native_queue.empty():
                if self._worker_error is not None:
                    raise self._worker_error
                return
            pcm = await self._native_queue.get()
            if pcm is _CAPTURE_STOP:
                if self._worker_error is not None:
                    raise self._worker_error
                return
            self._queue_slots.release()
            yield pcm
    async def stop(self):
        self._capture_stop.set()
        if self._stream is not None: self._stream.stop_stream(); self._stream.close(); self._stream = None
        if self._capture_thread is not None:
            worker, self._capture_thread = self._capture_thread, None
            await asyncio.to_thread(worker.join, 2)
            if worker.is_alive():
                logger.error("PHOS VOICE: capture_worker_shutdown_timeout")
        if self._debug_native_wav and self._native_capture_pcm and self.capture_rate:
            pcm = bytes(self._native_capture_pcm); self._native_capture_pcm.clear()
            await asyncio.to_thread(self._write_native_wav, pcm, self._native_capture_truncated)
        self._native_queue = self._queue_slots = self._loop = None
        if self._audio is not None: self._audio.terminate(); self._audio = None
    async def close(self): await self.stop()

    def _append_native_capture(self, pcm: bytes) -> None:
        """Retain original stream bytes, exactly once and before processing."""
        if not self._debug_native_wav or self._native_capture_truncated:
            return
        remaining = self._native_capture_max_bytes - len(self._native_capture_pcm)
        if remaining <= 0:
            self._native_capture_truncated = True
            return
        if len(pcm) <= remaining:
            self._native_capture_pcm.extend(pcm)
            return
        # PCM frames must stay whole even if the bounded diagnostic interval
        # ends in this read.
        frame_bytes = self.channels * 2
        self._native_capture_pcm.extend(pcm[:remaining - (remaining % frame_bytes)])
        self._native_capture_truncated = True

    def _write_native_wav(self, pcm, truncated=False):
        self._debug_native_wav_path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(self._debug_native_wav_path), "wb") as output:
            output.setnchannels(self.channels); output.setsampwidth(2); output.setframerate(self.capture_rate); output.writeframes(pcm)
        frames = len(pcm) // (self.channels * 2)
        duration_ms = round(frames * 1000 / self.capture_rate)
        capture_wall_ms = round((time.monotonic() - self._capture_started_at) * 1000) if self._capture_started_at else 0
        realtime_ratio = round(duration_ms / capture_wall_ms, 3) if capture_wall_ms else 0.0
        logger.info("PHOS VOICE NATIVE: frames=%s bytes=%s duration_ms=%s", frames, len(pcm), duration_ms)
        logger.info("PHOS VOICE: capture_wall_ms=%s native_audio_ms=%s realtime_ratio=%s input_overflows=%s",
                    capture_wall_ms, duration_ms, realtime_ratio, self._input_overflow_count)
        logger.info("PHOS VOICE: capture_queue captured_chunks=%s processed_chunks=%s queue_high_watermark=%s dropped_chunks=%s",
                    self.native_chunks_read, self.processed_chunks, self.queue_high_watermark, self.dropped_chunks)
        logger.info("PHOS VOICE: native_capture rate=%s channels=%s sample_width=2 truncated=%s %s",
                    self.capture_rate, self.channels, truncated, _pcm_stats(pcm))

    @staticmethod
    def _is_input_overflow(error: Exception) -> bool:
        return "overflow" in str(error).lower()

    def _capture_worker(self) -> None:
        """Continuously read native PCM; never wait for processing or VAD."""
        previous_read_end = None
        try:
            while not self._capture_stop.is_set() and self._stream is not None:
                started_at = time.monotonic()
                if previous_read_end is not None and self.native_chunks_read < 3:
                    logger.info("PHOS VOICE CAPTURE TIMING: read_end_to_next_read_start_ms=%s",
                                round((started_at - previous_read_end) * 1000, 3))
                self._record_input_backlog(self._stream, self._native_frames_per_read)
                pcm = self._stream.read(self._native_frames_per_read, exception_on_overflow=False)
                ended_at = time.monotonic()
                previous_read_end = ended_at
                self._append_native_capture(pcm)
                with self._stats_lock:
                    self.native_chunks_read += 1
                    sequence = self.native_chunks_read
                if sequence <= 3:
                    logger.info("PHOS VOICE CAPTURE: native_read_seq=%s native_frames=%s native_bytes=%s capture_timestamp_ms=%s",
                                sequence, len(pcm) // (self.channels * 2), len(pcm),
                                round((ended_at - self._capture_started_at) * 1000))
                self._offer_native_chunk(pcm)
                self._log_flow(ended_at)
        except Exception as error:
            if not self._capture_stop.is_set():
                self._worker_error = error
                logger.exception("VOICE AUDIO ERROR: stage=capture_worker exception_type=%s exception_message=%s",
                                 type(error).__name__, error)
        finally:
            self._schedule_on_loop(self._signal_capture_stopped)

    def _offer_native_chunk(self, pcm: bytes) -> None:
        # The semaphore bounds both queued chunks and callbacks waiting to put
        # them, so a blocked event loop cannot create an unbounded callback
        # backlog. Newest chunks are dropped under sustained consumer lag.
        if not self._queue_slots.acquire(blocking=False):
            with self._stats_lock:
                self.dropped_chunks += 1
            if self.dropped_chunks == 1 or self.dropped_chunks % 10 == 0:
                logger.warning("PHOS VOICE: capture_queue_drop dropped_chunks=%s", self.dropped_chunks)
            return
        if not self._schedule_on_loop(self._enqueue_native_chunk, pcm):
            self._queue_slots.release()

    def _enqueue_native_chunk(self, pcm: bytes) -> None:
        if self._native_queue is None:
            if self._queue_slots is not None:
                self._queue_slots.release()
            return
        try:
            self._native_queue.put_nowait(pcm)
            self.queue_high_watermark = max(self.queue_high_watermark, self._native_queue.qsize())
        except asyncio.QueueFull:
            self._queue_slots.release()
            with self._stats_lock:
                self.dropped_chunks += 1

    def _signal_capture_stopped(self) -> None:
        self._capture_finished = True
        if self._native_queue is not None and not self._native_queue.full():
            self._native_queue.put_nowait(_CAPTURE_STOP)

    def _schedule_on_loop(self, callback, *args) -> bool:
        try:
            if self._loop is None:
                return False
            self._loop.call_soon_threadsafe(callback, *args)
            return True
        except RuntimeError:  # event loop closed while test/runtime is stopping
            self._capture_stop.set()
            return False

    def _record_input_backlog(self, stream, requested_frames: int) -> None:
        """Observe a PortAudio backlog without converting it into a no-data loop."""
        try:
            available = stream.get_read_available()
        except (AttributeError, OSError):
            return
        if available > requested_frames:
            self._input_overflow_count += 1
            now = time.monotonic()
            if now - self._last_overflow_log >= 1:
                logger.warning("PHOS VOICE: input_backlog available_frames=%s requested_frames=%s count=%s",
                               available, requested_frames, self._input_overflow_count)
                self._last_overflow_log = now

    def mark_processed_chunk(self) -> None:
        self.processed_chunks += 1
        self._log_flow(time.monotonic())

    def _log_flow(self, now: float) -> None:
        if self._capture_started_at is None or now - self._last_flow_log < 2:
            return
        native_audio_ms = round(self.native_chunks_read * self._native_frames_per_read * 1000 / self.capture_rate)
        capture_wall_ms = round((now - self._capture_started_at) * 1000)
        realtime_ratio = round(native_audio_ms / capture_wall_ms, 3) if capture_wall_ms else 0.0
        queue_depth = self._native_queue.qsize() if self._native_queue is not None else 0
        logger.info("PHOS VOICE FLOW: capture_wall_ms=%s native_audio_ms=%s realtime_ratio=%s native_chunks_read=%s processed_chunks=%s queue_depth=%s queue_high_watermark=%s dropped_chunks=%s input_overflows=%s",
                    capture_wall_ms, native_audio_ms, realtime_ratio, self.native_chunks_read,
                    self.processed_chunks, queue_depth, self.queue_high_watermark, self.dropped_chunks,
                    self._input_overflow_count)
        self._last_flow_log = now


class ResamplingAudioCaptureProvider:
    def __init__(self, capture, processing_rate: int) -> None:
        self._capture, self.processing_rate, self._resampler = capture, processing_rate, None
        self._last_frame_log = float("-inf")
        self.processing_frames_per_chunk = None
        self._processing_sequence = 0
    def available(self): return self._capture.available()
    async def start(self):
        await self._capture.start(); self._resampler = PCM16Resampler(self._capture.capture_rate, self.processing_rate)
        chunk_ms = getattr(self._capture, "chunk_ms", None)
        self.processing_frames_per_chunk = (
            max(1, round(self.processing_rate * chunk_ms / 1000)) if chunk_ms is not None else None
        )
        logger.debug("VOICE AUDIO: device=%r device_index=%s capture_rate=%s processing_rate=%s channels=%s format=PCM16", self._capture.resolved_device_name, self._capture.resolved_device_index, self._capture.capture_rate, self.processing_rate, self._capture.channels)
        logger.info("PHOS VOICE: chunk_geometry native_rate=%s processing_rate=%s nominal_chunk_ms=%s native_frames_per_read=%s processing_frames_per_chunk=%s",
                    self._capture.capture_rate, self.processing_rate, chunk_ms,
                    getattr(self._capture, "_native_frames_per_read", None), self.processing_frames_per_chunk)
        if self._capture.capture_rate != self.processing_rate:
            logger.info("VOICE AUDIO RESAMPLING: input_rate=%s output_rate=%s",
                        self._capture.capture_rate, self.processing_rate)
    async def read_frames(self):
        async for pcm in self._capture.read_frames():
            native_bytes = len(pcm)
            if self._capture.channels == 2:
                pcm = audioop.tomono(pcm, 2, .5, .5)
            elif self._capture.channels != 1:
                raise RuntimeError("capture_channel_count_unsupported")
            pcm = self._resampler.convert(pcm)
            if pcm:
                self._processing_sequence += 1
                marker = getattr(self._capture, "mark_processed_chunk", None)
                if marker is not None:
                    marker()
                sequence = self._processing_sequence
                if sequence <= 3:
                    logger.info("PHOS VOICE PROCESSING: processing_seq=%s input_native_bytes=%s resampled_bytes=%s processing_frames=%s",
                                sequence, native_bytes, len(pcm), len(pcm) // 2)
                now = time.monotonic()
                if now - self._last_frame_log >= .5:
                    logger.debug("VOICE AUDIO FRAME: bytes=%s capture_rate=%s processing_rate=%s",
                                len(pcm), self._capture.capture_rate, self.processing_rate)
                    self._last_frame_log = now
                yield pcm
    async def stop(self): await self._capture.stop()
    async def close(self): await self._capture.close()
