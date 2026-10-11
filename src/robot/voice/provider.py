"""Provider-neutral speech-to-text contracts and optional lightweight adapters."""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timezone
import asyncio
import io
import logging
from pathlib import Path
import json
import time
from typing import Optional
import wave

from robot.interaction.models import TranscriptResult

logger = logging.getLogger(__name__)


class STTError(RuntimeError): pass
class STTConfigurationError(STTError): pass
class STTProviderError(STTError):
    def __init__(self, code: str): super().__init__(code); self.code = code


# Public compatibility name for the existing STT boundary.  New interaction
# code uses TranscriptResult directly; both names identify the same plain type.
SpeechRecognitionResult = TranscriptResult


class STTProvider(ABC):
    name: str
    model: Optional[str] = None

    @abstractmethod
    def available(self) -> bool: ...

    @abstractmethod
    async def transcribe(self, pcm: bytes, *, sample_rate: int, channels: int) -> SpeechRecognitionResult: ...


class VoskSTTProvider(STTProvider):
    """Optional offline Vosk adapter; importing it never downloads a model."""
    name = "local"

    def __init__(self, model_path: Optional[Path], language: Optional[str] = None) -> None:
        self._path, self._language = model_path, language
        self.model = None if model_path is None else model_path.name

    def available(self) -> bool:
        if self._path is None or not self._path.is_dir():
            return False
        try:
            import vosk  # noqa: F401
        except ImportError:
            return False
        return True

    async def transcribe(self, pcm: bytes, *, sample_rate: int, channels: int) -> SpeechRecognitionResult:
        if not self.available():
            raise RuntimeError("Local STT model or optional vosk dependency is unavailable")
        return await asyncio.to_thread(self._transcribe, pcm, sample_rate, channels)

    def _transcribe(self, pcm: bytes, sample_rate: int, channels: int) -> SpeechRecognitionResult:
        import vosk
        recognizer = vosk.KaldiRecognizer(vosk.Model(str(self._path)), sample_rate)
        recognizer.AcceptWaveform(pcm)
        response = json.loads(recognizer.FinalResult())
        words = response.get("result") or []
        confidence = sum(item.get("conf", 0) for item in words) / len(words) if words else None
        return SpeechRecognitionResult(response.get("text", "").strip(), self.name, self.model, confidence,
                                       self._language, round(len(pcm) * 1000 / (sample_rate * channels * 2)),
                                       datetime.now(timezone.utc).isoformat(timespec="milliseconds"))


class CartesiaSTTProvider(STTProvider):
    """Batch Ink adapter. VAD remains responsible for utterance boundaries."""
    name = "cartesia"

    def __init__(self, api_key: str, model_id: str, language: str, sample_rate: int, *, client_factory=None) -> None:
        if not isinstance(api_key, str) or not api_key.strip():
            raise STTConfigurationError("Cartesia credential is not configured.")
        if (not isinstance(model_id, str) or not model_id.strip() or not isinstance(language, str)
                or not language.strip() or sample_rate != 16000):
            raise STTConfigurationError("Cartesia STT configuration is invalid.")
        try:
            if client_factory is None:
                from cartesia import Cartesia
                client_factory = Cartesia
            self._client = client_factory(api_key=api_key)
        except Exception as error:
            raise STTConfigurationError("Cartesia STT client could not be initialized.") from error
        self.model, self._language, self._sample_rate = model_id, language, sample_rate

    def available(self) -> bool: return True

    @staticmethod
    def _error_code(error: Exception) -> str:
        status = getattr(error, "status_code", getattr(error, "status", None))
        if status in {401, 403}: return "authentication_error"
        if status == 402: return "provider_plan_or_quota_error"
        if status == 429: return "rate_limited"
        if isinstance(status, int) and status >= 500 or isinstance(error, (OSError, TimeoutError, ConnectionError)):
            return "provider_unavailable"
        return "transcription_failed"

    @staticmethod
    def _wav(pcm: bytes, sample_rate: int, channels: int) -> io.BytesIO:
        if sample_rate != 16000 or channels != 1 or not pcm or len(pcm) % 2:
            raise STTConfigurationError("Cartesia STT requires canonical PCM16 mono 16 kHz audio.")
        output = io.BytesIO()
        with wave.open(output, "wb") as wav_file:
            wav_file.setparams((channels, 2, sample_rate, 0, "NONE", "not compressed"))
            wav_file.writeframes(pcm)
        output.seek(0)
        return output

    async def transcribe(self, pcm: bytes, *, sample_rate: int, channels: int) -> SpeechRecognitionResult:
        return await asyncio.to_thread(self._transcribe, pcm, sample_rate, channels)

    def _transcribe(self, pcm: bytes, sample_rate: int, channels: int) -> SpeechRecognitionResult:
        started = time.monotonic()
        logger.info("PHOS STT: provider=cartesia transcription_start")
        try:
            audio = self._wav(pcm, sample_rate, channels)
            response = self._client.stt.transcribe(
                file=("phos-utterance.wav", audio, "audio/wav"), model=self.model,
                encoding="pcm_s16le", sample_rate=sample_rate, language=self._language,
            )
            text = getattr(response, "text", None)
            if not isinstance(text, str):
                text = response.get("text", "") if isinstance(response, dict) else ""
            logger.info("PHOS STT: provider=cartesia transcription_complete duration_ms=%s",
                        round((time.monotonic() - started) * 1000))
            return SpeechRecognitionResult(text.strip(), self.name, self.model, None, self._language,
                                           round(len(pcm) * 1000 / (sample_rate * channels * 2)),
                                           datetime.now(timezone.utc).isoformat(timespec="milliseconds"))
        except STTError:
            raise
        except Exception as error:
            code = self._error_code(error)
            logger.warning("PHOS STT: provider=cartesia error=%s", code)
            raise STTProviderError(code) from error


class STTProviderFactory:
    """Runtime-only construction; explicit cloud selection never falls back."""
    @staticmethod
    def create(config, secrets_service=None) -> STTProvider:
        if config.voice_stt_provider == "local":
            return VoskSTTProvider(config.resolve_path(config.voice_stt_model_path), config.voice_stt_language)
        if config.voice_stt_provider == "cartesia":
            secret = secrets_service.get_secret("tts.cartesia.api_key") if secrets_service else None
            if not secret:
                raise STTConfigurationError("Cartesia credential is not configured.")
            return CartesiaSTTProvider(secret, config.voice_stt_cartesia_model_id,
                                       config.voice_stt_cartesia_language,
                                       config.voice_stt_cartesia_sample_rate)
        raise STTConfigurationError("Unknown STT provider.")


class UnavailableSTTProvider(STTProvider):
    def __init__(self, name: str) -> None: self.name, self.model = name, None
    def available(self) -> bool: return False
    async def transcribe(self, pcm: bytes, *, sample_rate: int, channels: int) -> SpeechRecognitionResult:
        raise RuntimeError(f"{self.name} STT provider is unavailable")
