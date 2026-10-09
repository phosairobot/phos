"""Provider-neutral local synthesis and playback contracts."""
from __future__ import annotations

import audioop
from dataclasses import dataclass
import logging
import math
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import wave

logger = logging.getLogger(__name__)


class TTSError(RuntimeError): pass
class TTSConfigurationError(TTSError): pass
class TTSSynthesisError(TTSError): pass
class AudioOutputError(TTSError): pass
class TTSBusyError(TTSError): pass


@dataclass(frozen=True)
class SynthesizedAudio:
    """A temporary WAV artifact owned by the caller and removed after playback."""
    path: Path
    sample_rate: int | None = None
    channels: int | None = None
    encoding: str = "wav"


class TTSProvider:
    name: str
    def ready(self) -> bool: raise NotImplementedError
    def synthesize(self, text: str) -> SynthesizedAudio: raise NotImplementedError


class PiperTTSProvider(TTSProvider):
    """Offline Piper CLI adapter; it owns synthesis only, never playback."""
    name = "local"
    max_text_length = 1000

    def __init__(self, executable: str, model_path: Path | None, speaker_id: int | None = None, *, timeout=60) -> None:
        self.executable, self.model_path, self.speaker_id, self.timeout = executable, model_path, speaker_id, timeout

    def ready(self) -> bool:
        return bool(self.model_path and self.model_path.is_file() and shutil.which(self.executable))

    def synthesize(self, text: str) -> SynthesizedAudio:
        if not isinstance(text, str) or not text.strip():
            raise TTSConfigurationError("Speech text must not be empty.")
        if len(text) > self.max_text_length:
            raise TTSConfigurationError("Speech text exceeds the maximum length.")
        if self.model_path is None or not self.model_path.is_file():
            raise TTSConfigurationError("Piper model is unavailable.")
        executable = shutil.which(self.executable)
        if executable is None:
            raise TTSConfigurationError("Piper executable is unavailable.")
        output = tempfile.NamedTemporaryFile(prefix="phos-tts-", suffix=".wav", delete=False)
        output.close()
        path = Path(output.name)
        command = [executable, "--model", str(self.model_path), "--output_file", str(path)]
        if self.speaker_id is not None:
            command.extend(("--speaker", str(self.speaker_id)))
        try:
            result = subprocess.run(command, input=text, text=True, capture_output=True, timeout=self.timeout, check=False)
        except subprocess.TimeoutExpired as error:
            path.unlink(missing_ok=True)
            raise TTSSynthesisError("Piper synthesis timed out.") from error
        except OSError as error:
            path.unlink(missing_ok=True)
            raise TTSSynthesisError("Piper synthesis could not start.") from error
        if result.returncode or not path.is_file() or not path.stat().st_size:
            path.unlink(missing_ok=True)
            raise TTSSynthesisError("Piper synthesis failed.")
        return SynthesizedAudio(path)


class AudioOutputProvider:
    def play(self, audio: SynthesizedAudio) -> None: raise NotImplementedError


class AplayAudioOutputProvider(AudioOutputProvider):
    """ALSA aplay adapter for already-synthesized WAV files."""
    def __init__(self, player="aplay", device: str | None = None, *, sample_rate=48000,
                 channels=2, sample_width=2, preroll_ms=0, timeout=90) -> None:
        self.player, self.device, self.sample_rate, self.channels = player, device, sample_rate, channels
        self.sample_width, self.preroll_ms, self.timeout = sample_width, preroll_ms, timeout

    @staticmethod
    def _preroll_frames(params, duration_ms):
        samples = round(params.framerate * duration_ms / 1000)
        amplitude = max(1, (1 << (8 * params.sampwidth - 1)) // 10000)
        frames = bytearray()
        for index in range(samples):
            value = round(amplitude * math.sin(2 * math.pi * 440 * index / params.framerate))
            if params.sampwidth == 1:
                encoded = bytes((max(0, min(255, value + 128)),))
            elif params.sampwidth == 2:
                encoded = struct.pack("<h", value)
            elif params.sampwidth == 3:
                encoded = int(value).to_bytes(3, "little", signed=True)
            elif params.sampwidth == 4:
                encoded = struct.pack("<i", value)
            else:
                raise AudioOutputError("Unsupported WAV sample width for audio preroll.")
            frames.extend(encoded * params.nchannels)
        return bytes(frames)

    def _normalized_wav(self, source: Path) -> Path:
        try:
            with wave.open(str(source), "rb") as input_file:
                params, speech = input_file.getparams(), input_file.readframes(input_file.getnframes())
            if params.comptype != "NONE" or params.nchannels not in {1, 2}:
                raise AudioOutputError("Unsupported WAV format for audio playback.")
            if (not self.preroll_ms and params.sampwidth == self.sample_width
                    and params.framerate == self.sample_rate and params.nchannels == self.channels):
                return None
            if params.sampwidth != self.sample_width:
                speech = audioop.lin2lin(speech, params.sampwidth, self.sample_width)
            if params.framerate != self.sample_rate:
                speech, _state = audioop.ratecv(speech, self.sample_width, params.nchannels,
                                                 params.framerate, self.sample_rate, None)
            if params.nchannels == 1 and self.channels == 2:
                speech = audioop.tostereo(speech, self.sample_width, 1, 1)
            elif params.nchannels != self.channels:
                raise AudioOutputError("Unsupported WAV channel conversion for audio playback.")
            target = wave._wave_params(self.channels, self.sample_width, self.sample_rate, 0, "NONE", "not compressed")
            preroll = self._preroll_frames(target, self.preroll_ms)
            output = tempfile.NamedTemporaryFile(prefix="phos-tts-playback-", suffix=".wav", delete=False)
            output.close()
            normalized = Path(output.name)
            with wave.open(str(normalized), "wb") as output_file:
                output_file.setparams(target)
                output_file.writeframes(preroll + speech)
            return normalized
        except (OSError, EOFError, wave.Error) as error:
            raise AudioOutputError("Audio playback could not be prepared.") from error

    def play(self, audio: SynthesizedAudio) -> None:
        executable = shutil.which(self.player)
        if executable is None:
            raise AudioOutputError("Audio player is unavailable.")
        normalized = self._normalized_wav(audio.path)
        playback_path = normalized or audio.path
        if self.preroll_ms:
            logger.debug("TTS audio playback preroll_ms=%s", self.preroll_ms)
        command = [executable, "-q"]
        if self.device is not None:
            command.extend(("-D", self.device))
        command.append(str(playback_path))
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=self.timeout, check=False)
            if result.returncode:
                raise AudioOutputError("Audio playback failed.")
        except subprocess.TimeoutExpired as error:
            raise AudioOutputError("Audio playback timed out.") from error
        except OSError as error:
            raise AudioOutputError("Audio playback could not start.") from error
        finally:
            if normalized is not None:
                normalized.unlink(missing_ok=True)
