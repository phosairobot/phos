"""Bounded explicit voice-capture session; it owns no UI, LLM or playback."""
from __future__ import annotations
import asyncio
import audioop
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import logging
from pathlib import Path
import time
from typing import Optional
import wave
from robot.core import Event, RobotState
from .vad import VoiceActivityDetector

logger = logging.getLogger(__name__)

VOICE_LISTENING_STARTED = "voice_listening_started"
VOICE_SPEECH_STARTED = "voice_speech_started"
VOICE_SPEECH_ENDED = "voice_speech_ended"
VOICE_TRANSCRIPTION_STARTED = "voice_transcription_started"
VOICE_TRANSCRIPTION_COMPLETED = "voice_transcription_completed"
VOICE_EMPTY_UTTERANCE = "voice_empty_utterance"
VOICE_SESSION_CANCELLED = "voice_session_cancelled"
VOICE_ERROR = "voice_error"

@dataclass(frozen=True)
class VoiceStatus:
    enabled: bool; state: str = "idle"; listening: bool = False; speech_detected: bool = False
    stt_provider: Optional[str] = None; stt_available: bool = False; last_transcript: Optional[str] = None
    last_confidence: Optional[float] = None; language: Optional[str] = None
    last_transcription_at: Optional[str] = None; last_transcription_duration_ms: Optional[int] = None; last_error: Optional[str] = None
    def document(self): return self.__dict__.copy()

class VoiceCaptureSession:
    def __init__(self, capture, stt, events, transition, *, enabled, sample_rate, channels, chunk_ms,
                 speech_start_ms, silence_end_ms, min_utterance_ms, max_utterance_ms, threshold,
                 pre_roll_ms=300, debug_dump_utterance_wav=False,
                 debug_utterance_wav_path=Path("/tmp/phos-last-utterance.wav")) -> None:
        self._capture, self._stt, self._events, self._transition = capture, stt, events, transition
        self._sample_rate, self._channels, self._chunk_ms = sample_rate, channels, chunk_ms
        self._min_ms, self._max_ms = min_utterance_ms, max_utterance_ms
        self._vad = VoiceActivityDetector(threshold=threshold, speech_start_ms=speech_start_ms, silence_end_ms=silence_end_ms, chunk_ms=chunk_ms)
        self._status = VoiceStatus(enabled, stt_provider=getattr(stt, "name", None), stt_available=stt.available())
        self._task = None; self._cancel = False; self._last_vad_level_log = float("-inf")
        self._pre_roll_ms = pre_roll_ms
        self._pre_roll_limit_bytes = sample_rate * channels * 2 * pre_roll_ms // 1000
        self._pre_roll = bytearray()
        self._debug_dump_utterance_wav = debug_dump_utterance_wav
        self._debug_utterance_wav_path = Path(debug_utterance_wav_path)
    def status(self): return self._status.document()
    async def start(self):
        logger.info("VOICE SESSION: start requested")
        stage = "config"
        try:
            logger.info("VOICE CONFIG: enabled=%s", self._status.enabled)
            if not self._status.enabled: raise RuntimeError("Voice is disabled")
            if self._task and not self._task.done(): return self.status()
            self._pre_roll.clear()
            stage = "vad_ready"
            logger.debug("VOICE VAD: ready")
            stage = "stt_availability"
            logger.info("VOICE STT: provider=%s", self._status.stt_provider)
            logger.info("VOICE STT: available=%s", self._status.stt_available)
            if not self._status.stt_available: raise RuntimeError("Selected STT provider is unavailable")
            self._cancel = False
            stage = "audio_open"
            await self._capture.start()
        except Exception as error:
            logger.exception("VOICE SESSION ERROR: stage=%s exception_type=%s exception_message=%s",
                             stage, type(error).__name__, error)
            raise
        logger.debug("VOICE AUDIO: capture started")
        self._status = replace(self._status, state="listening", listening=True, last_error=None)
        try:
            stage = "listening_transition"
            await self._transition(RobotState.LISTENING, "voice_listening")
            stage = "listening_event"
            await self._emit(VOICE_LISTENING_STARTED, {})
            stage = "capture_task"
            self._task = asyncio.create_task(self._run())
        except Exception as error:
            logger.exception("VOICE SESSION ERROR: stage=%s exception_type=%s exception_message=%s",
                             stage, type(error).__name__, error)
            raise
        return self.status()
    async def stop(self, *, cancelled=False):
        self._cancel = cancelled
        if self._task and self._task is not asyncio.current_task(): self._task.cancel(); await asyncio.gather(self._task, return_exceptions=True)
        self._pre_roll.clear()
        await self._capture.stop(); self._status = replace(self._status, state="idle", listening=False, speech_detected=False)
        if cancelled: await self._emit(VOICE_SESSION_CANCELLED, {})
        if self._transition: await self._transition(RobotState.IDLE, "voice_stopped")
        return self.status()
    async def _run(self):
        frames = bytearray(); speaking = False
        try:
            async for frame in self._capture.read_frames():
                marker = self._vad.observe(frame)
                now = time.monotonic()
                if now - self._last_vad_level_log >= .5:
                    level = self._vad.level()
                    logger.debug("VOICE VAD LEVEL: rms=%s peak=%s threshold=%s speech_candidate_ms=%s speech_active=%s",
                                level["rms"], level["peak"], level["threshold"],
                                level["speech_candidate_ms"], level["speech_active"])
                    self._last_vad_level_log = now
                if marker == "started":
                    pre_roll_bytes = len(self._pre_roll)
                    speaking = True; frames.extend(self._pre_roll); self._pre_roll.clear()
                    self._status = replace(self._status, speech_detected=True)
                    logger.info("VOICE SPEECH: started pre_roll_ms=%s pre_roll_bytes=%s",
                                self._pre_roll_ms, pre_roll_bytes)
                    await self._emit(VOICE_SPEECH_STARTED, {})
                if speaking: frames.extend(frame)
                elif self._pre_roll_limit_bytes:
                    self._pre_roll.extend(frame)
                    if len(self._pre_roll) > self._pre_roll_limit_bytes:
                        del self._pre_roll[:-self._pre_roll_limit_bytes]
                if speaking and (marker == "ended" or len(frames) * 1000 >= self._max_ms * self._sample_rate * self._channels * 2):
                    await self._finalize(bytes(frames)); return
        except asyncio.CancelledError: raise
        except Exception as error: await self._failure(error)
        finally: self._pre_roll.clear()
    async def _finalize(self, pcm):
        duration = round(len(pcm) * 1000 / (self._sample_rate * self._channels * 2))
        await self._emit(VOICE_SPEECH_ENDED, {"duration_ms": duration})
        logger.info("VOICE SPEECH: ended duration_ms=%s", duration)
        if duration < self._min_ms: await self._emit(VOICE_EMPTY_UTTERANCE, {"duration_ms": duration}); await self.stop(); return
        self._status = replace(self._status, state="thinking", listening=False, speech_detected=False); await self._transition(RobotState.THINKING, "voice_transcription")
        await self._emit(VOICE_TRANSCRIPTION_STARTED, {"provider": self._status.stt_provider})
        logger.info("VOICE STT: started provider=%s audio_duration_ms=%s", self._status.stt_provider, duration)
        logger.debug("VOICE UTTERANCE: duration_ms=%s bytes=%s sample_rate=%s channels=%s rms=%s peak=%s",
                    duration, len(pcm), self._sample_rate, self._channels,
                    audioop.rms(pcm, 2) if pcm else 0, audioop.max(pcm, 2) if pcm else 0)
        if self._debug_dump_utterance_wav:
            try: await asyncio.to_thread(self._write_debug_wav, pcm)
            except Exception as error:
                logger.exception("VOICE DEBUG WAV ERROR: exception_type=%s exception_message=%s",
                                 type(error).__name__, error)
        started_at = time.monotonic()
        try: result = await self._stt.transcribe(pcm, sample_rate=self._sample_rate, channels=self._channels)
        except Exception as error: await self._failure(error); return
        elapsed = round((time.monotonic() - started_at) * 1000)
        logger.info("VOICE STT: completed provider=%s duration_ms=%s", self._status.stt_provider, elapsed)
        logger.info("VOICE TRANSCRIPT: text=%r", result.text) if result.text else logger.info("VOICE TRANSCRIPT: <empty>")
        if result.confidence is not None or result.language is not None:
            logger.info("VOICE STT RESULT: confidence=%s language=%s", result.confidence, result.language)
        self._status = replace(self._status, last_transcript=result.text or None, last_confidence=result.confidence,
                               language=result.language, last_transcription_at=result.completed_at or datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                               last_transcription_duration_ms=elapsed)
        await self._emit(VOICE_TRANSCRIPTION_COMPLETED, result.document()); await self.stop()
    def _write_debug_wav(self, pcm: bytes) -> None:
        with self._debug_utterance_wav_path.open("wb") as stream:
            with wave.open(stream, "wb") as output:
                output.setnchannels(self._channels)
                output.setsampwidth(2)
                output.setframerate(self._sample_rate)
                output.writeframes(pcm)
        duration = round(len(pcm) * 1000 / (self._sample_rate * self._channels * 2))
        logger.debug("VOICE DEBUG WAV: path=%s bytes=%s duration_ms=%s",
                    self._debug_utterance_wav_path, len(pcm), duration)
    async def _failure(self, error):
        self._status = replace(self._status, last_error=type(error).__name__)
        await self._emit(VOICE_ERROR, {"reason": type(error).__name__}); await self.stop()
    async def _emit(self, name, payload): await self._events.publish(Event(name, payload))
