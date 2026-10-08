"""Provider-neutral local synthesis and playback contracts."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil
import subprocess
import tempfile


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
    def __init__(self, player="aplay", device: str | None = None, *, timeout=90) -> None:
        self.player, self.device, self.timeout = player, device, timeout

    def play(self, audio: SynthesizedAudio) -> None:
        executable = shutil.which(self.player)
        if executable is None:
            raise AudioOutputError("Audio player is unavailable.")
        command = [executable, "-q"]
        if self.device is not None:
            command.extend(("-D", self.device))
        command.append(str(audio.path))
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=self.timeout, check=False)
        except subprocess.TimeoutExpired as error:
            raise AudioOutputError("Audio playback timed out.") from error
        except OSError as error:
            raise AudioOutputError("Audio playback could not start.") from error
        if result.returncode:
            raise AudioOutputError("Audio playback failed.")
