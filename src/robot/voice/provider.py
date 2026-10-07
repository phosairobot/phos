"""Provider-neutral speech-to-text contracts and optional lightweight adapters."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import json
from typing import Optional


@dataclass(frozen=True)
class SpeechRecognitionResult:
    text: str
    provider: str
    model: Optional[str] = None
    confidence: Optional[float] = None
    language: Optional[str] = None
    duration_ms: Optional[int] = None
    completed_at: Optional[str] = None

    def document(self) -> dict:
        return {"text": self.text, "provider": self.provider, "model": self.model,
                "confidence": self.confidence, "language": self.language,
                "duration_ms": self.duration_ms, "completed_at": self.completed_at}


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
        import asyncio
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


class UnavailableSTTProvider(STTProvider):
    def __init__(self, name: str) -> None: self.name, self.model = name, None
    def available(self) -> bool: return False
    async def transcribe(self, pcm: bytes, *, sample_rate: int, channels: int) -> SpeechRecognitionResult:
        raise RuntimeError(f"{self.name} STT provider is unavailable")
