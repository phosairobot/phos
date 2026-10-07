"""PyAudio input negotiation and the one PCM16 resampling boundary."""
from __future__ import annotations

import asyncio
import audioop
import logging
import time

logger = logging.getLogger(__name__)


class UnsupportedCaptureRate(RuntimeError):
    def __init__(self, requested_rate, device_default_rate):
        super().__init__("unsupported_capture_rate")
        self.requested_rate, self.device_default_rate = requested_rate, device_default_rate


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
    def __init__(self, device_name=None, device_index=None, sample_rate=None, channels=1, chunk_ms=30) -> None:
        self.device_name, self.device_index, self.requested_rate = device_name, device_index, sample_rate
        self.channels, self.chunk_ms, self.capture_rate, self._audio, self._stream = channels, chunk_ms, None, None, None
        self.resolved_device_name, self.resolved_device_index = None, None
        self._last_frame_log = float("-inf")
    def available(self):
        try: import pyaudio  # noqa: F401
        except ImportError: return False
        return True
    def _resolve(self, audio):
        if self.device_name:
            for index in range(audio.get_device_count()):
                info = audio.get_device_info_by_index(index)
                if info.get("maxInputChannels", 0) and self.device_name.lower() in info.get("name", "").lower(): return index, info
            raise RuntimeError("audio_device_unavailable")
        if self.device_index is not None:
            info = audio.get_device_info_by_index(self.device_index)
            if not info.get("maxInputChannels", 0): raise RuntimeError("audio_device_unavailable")
            return self.device_index, info
        info = audio.get_default_input_device_info(); return info["index"], info
    async def start(self):
        logger.debug("VOICE AUDIO: start requested")
        if not self.available(): raise RuntimeError("pyaudio_unavailable")
        import pyaudio
        self._audio = pyaudio.PyAudio()
        try:
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
        self.resolved_device_index, self.resolved_device_name = index, info["name"]
        frames_per_buffer = max(1, round(self.capture_rate * self.chunk_ms / 1000))
        logger.info("VOICE AUDIO: device resolved name=%r index=%s rate=%s channels=%s format=paInt16",
                    self.resolved_device_name, self.resolved_device_index, self.capture_rate, self.channels)
        try:
            logger.debug("VOICE AUDIO: stream opening input=True input_device_index=%s channels=%s format=paInt16 rate=%s frames_per_buffer=%s",
                        index, self.channels, self.capture_rate, frames_per_buffer)
            self._stream = self._audio.open(format=pyaudio.paInt16, channels=self.channels, rate=self.capture_rate,
                                            input=True, input_device_index=index, frames_per_buffer=frames_per_buffer)
            logger.info("VOICE AUDIO: stream opened")
        except Exception as error:
            logger.exception("VOICE SESSION ERROR: stage=audio_open exception_type=%s exception_message=%s",
                             type(error).__name__, error)
            await self.stop()
            raise
    async def read_frames(self):
        frames = max(1, round(self.capture_rate * self.chunk_ms / 1000))
        first_read = True
        logger.debug("VOICE AUDIO: read loop started")
        while self._stream is not None:
            stream = self._stream
            try:
                if first_read: logger.debug("VOICE AUDIO READ: waiting")
                # One awaited worker read at a time preserves capture backpressure and
                # avoids blocking the event loop or accumulating per-frame tasks.
                pcm = await asyncio.to_thread(stream.read, frames, exception_on_overflow=False)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.exception("VOICE AUDIO ERROR: stage=read exception_type=%s exception_message=%s",
                                 type(error).__name__, error)
                raise
            if first_read:
                logger.debug("VOICE AUDIO READ: received bytes=%s", len(pcm))
                first_read = False
            now = time.monotonic()
            if now - self._last_frame_log >= .5:
                logger.debug("VOICE AUDIO FRAME: bytes=%s capture_rate=%s processing_rate=%s",
                            len(pcm), self.capture_rate, self.capture_rate)
                self._last_frame_log = now
            if self._stream is stream: yield pcm
    async def stop(self):
        if self._stream is not None: self._stream.stop_stream(); self._stream.close(); self._stream = None
        if self._audio is not None: self._audio.terminate(); self._audio = None
    async def close(self): await self.stop()


class ResamplingAudioCaptureProvider:
    def __init__(self, capture, processing_rate: int) -> None:
        self._capture, self.processing_rate, self._resampler = capture, processing_rate, None
        self._last_frame_log = float("-inf")
    def available(self): return self._capture.available()
    async def start(self):
        await self._capture.start(); self._resampler = PCM16Resampler(self._capture.capture_rate, self.processing_rate)
        logger.debug("VOICE AUDIO: device=%r device_index=%s capture_rate=%s processing_rate=%s channels=%s format=PCM16", self._capture.resolved_device_name, self._capture.resolved_device_index, self._capture.capture_rate, self.processing_rate, self._capture.channels)
        if self._capture.capture_rate != self.processing_rate:
            logger.info("VOICE AUDIO RESAMPLING: input_rate=%s output_rate=%s",
                        self._capture.capture_rate, self.processing_rate)
    async def read_frames(self):
        async for pcm in self._capture.read_frames():
            pcm = self._resampler.convert(pcm)
            if pcm:
                now = time.monotonic()
                if now - self._last_frame_log >= .5:
                    logger.debug("VOICE AUDIO FRAME: bytes=%s capture_rate=%s processing_rate=%s",
                                len(pcm), self._capture.capture_rate, self.processing_rate)
                    self._last_frame_log = now
                yield pcm
    async def stop(self): await self._capture.stop()
    async def close(self): await self._capture.close()
