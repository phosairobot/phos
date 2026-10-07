"""Small deterministic PCM energy VAD suitable for Raspberry Pi 3."""
from __future__ import annotations
import audioop


class VoiceActivityDetector:
    def __init__(self, *, threshold: int, speech_start_ms: int, silence_end_ms: int, chunk_ms: int) -> None:
        self.threshold, self.start_frames = threshold, max(1, -(-speech_start_ms // chunk_ms))
        self.chunk_ms = chunk_ms
        self.end_frames, self._speech, self._active, self._silent = max(1, -(-silence_end_ms // chunk_ms)), 0, 0, 0
        self.rms, self.peak = 0, 0

    def level(self) -> dict:
        """PCM16 amplitude diagnostics for the most recently observed frame."""
        return {"rms": self.rms, "peak": self.peak, "threshold": self.threshold,
                "speech_candidate_ms": self._speech * self.chunk_ms, "speech_active": bool(self._active)}

    def observe(self, pcm: bytes) -> str | None:
        # audioop reports signed PCM16 sample amplitudes: RMS and peak are
        # approximately in the inclusive 0..32768 range, never decibels.
        self.rms = audioop.rms(pcm, 2) if pcm else 0
        self.peak = audioop.max(pcm, 2) if pcm else 0
        active = self.rms >= self.threshold
        if not self._active:
            self._speech = self._speech + 1 if active else 0
            if self._speech >= self.start_frames:
                self._active, self._silent = True, 0
                return "started"
            return None
        self._silent = 0 if active else self._silent + 1
        if self._silent >= self.end_frames:
            self._active, self._speech, self._silent = False, 0, 0
            return "ended"
        return None
