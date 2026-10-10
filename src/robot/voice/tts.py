"""Provider-neutral local synthesis and playback contracts."""
from __future__ import annotations

import audioop
from contextvars import ContextVar
from dataclasses import dataclass
import hashlib
import logging
import math
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import time
from threading import Lock
import wave

logger = logging.getLogger(__name__)
speech_request_id: ContextVar[int | None] = ContextVar("speech_request_id", default=None)


def speech_log(stage: str, level=logging.INFO, **fields) -> None:
    """Emit request-correlated, metadata-only speech diagnostics."""
    request_id = speech_request_id.get()
    prefix = f"PHOS SPEECH[{request_id}]" if request_id is not None else "PHOS SPEECH"
    details = " ".join(f"{name}={value}" for name, value in fields.items())
    logger.log(level, "%s: %s%s", prefix, stage, f" {details}" if details else "")


def _fsync_path(path: Path) -> None:
    """Durably finalize an artifact before handing it to another process."""
    with path.open("rb") as artifact:
        os.fsync(artifact.fileno())


def _sha256_prefix(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as artifact:
        for block in iter(lambda: artifact.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()[:16]


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
    """Offline Piper Python adapter with one lazily loaded voice model."""
    name = "local"
    max_text_length = 1000

    def __init__(self, executable: str, model_path: Path | None, speaker_id: int | None = None, *, timeout=60,
                 voice_loader=None) -> None:
        # ``executable`` remains accepted during the configuration migration;
        # synthesis uses the package API so the ONNX voice stays resident.
        self.executable, self.model_path, self.speaker_id, self.timeout = executable, model_path, speaker_id, timeout
        self._voice_loader = voice_loader
        self._voice = None
        self._voice_lock = Lock()

    def ready(self) -> bool:
        return bool(self.model_path and self.model_path.is_file())

    def _load_voice(self):
        if self._voice is not None:
            return self._voice
        with self._voice_lock:
            if self._voice is not None:
                return self._voice
            speech_log("piper_model_load_start")
            started = time.monotonic()
            try:
                if self._voice_loader is None:
                    from piper import PiperVoice
                    self._voice_loader = PiperVoice.load
                self._voice = self._voice_loader(str(self.model_path))
            except Exception as error:
                speech_log("piper_model_load_failed", logging.WARNING, exception_type=type(error).__name__)
                raise TTSConfigurationError("Piper voice could not be loaded.") from error
            speech_log("piper_model_load_complete", duration_ms=round((time.monotonic() - started) * 1000))
            return self._voice

    def _synthesize_wav(self, voice, text: str, output) -> None:
        if self.speaker_id is None:
            voice.synthesize_wav(text, output)
            return
        try:
            from piper import SynthesisConfig
            voice.synthesize_wav(text, output, syn_config=SynthesisConfig(speaker_id=self.speaker_id))
        except Exception as error:
            raise TTSSynthesisError("Piper synthesis failed.") from error

    def synthesize(self, text: str) -> SynthesizedAudio:
        if not isinstance(text, str) or not text.strip():
            raise TTSConfigurationError("Speech text must not be empty.")
        if len(text) > self.max_text_length:
            raise TTSConfigurationError("Speech text exceeds the maximum length.")
        if self.model_path is None or not self.model_path.is_file():
            raise TTSConfigurationError("Piper model is unavailable.")
        output = tempfile.NamedTemporaryFile(prefix="phos-tts-", suffix=".wav", delete=False)
        output.close()
        path = Path(output.name)
        try:
            voice = self._load_voice()
            with wave.open(str(path), "wb") as wav_file:
                self._synthesize_wav(voice, text, wav_file)
            _fsync_path(path)
        except TTSConfigurationError:
            path.unlink(missing_ok=True)
            raise
        except Exception as error:
            path.unlink(missing_ok=True)
            raise TTSSynthesisError("Piper synthesis failed.") from error
        if not path.is_file() or not path.stat().st_size:
            path.unlink(missing_ok=True)
            raise TTSSynthesisError("Piper synthesis failed.")
        try:
            with wave.open(str(path), "rb") as wav_file:
                params = wav_file.getparams()
                frames = wav_file.readframes(params.nframes)
            if (params.comptype != "NONE" or params.nframes <= 0 or not frames
                    or audioop.max(frames, params.sampwidth) == 0):
                raise TTSSynthesisError("Piper synthesis produced empty or silent audio.")
            duration_ms = round(params.nframes * 1000 / params.framerate)
            speech_log("synthesis_wav", rate_hz=params.framerate, channels=params.nchannels,
                       sample_width_bytes=params.sampwidth, frames=params.nframes,
                       audio_duration_ms=duration_ms, peak=audioop.max(frames, params.sampwidth),
                       rms=audioop.rms(frames, params.sampwidth))
        except (OSError, EOFError, wave.Error, audioop.error) as error:
            path.unlink(missing_ok=True)
            raise TTSSynthesisError("Piper synthesis produced an invalid WAV.") from error
        except TTSSynthesisError:
            path.unlink(missing_ok=True)
            raise
        return SynthesizedAudio(path)


class AudioOutputProvider:
    def play(self, audio: SynthesizedAudio) -> None: raise NotImplementedError


class AplayAudioOutputProvider(AudioOutputProvider):
    """ALSA aplay adapter for already-synthesized WAV files."""
    def __init__(self, player="aplay", device: str | None = None, *, sample_rate=48000,
                 channels=2, sample_width=2, preroll_ms=0, timeout=90,
                 debug_retain_final_wav=False, retry_max_attempts=2, retry_delay_ms=200) -> None:
        self.player, self.device, self.sample_rate, self.channels = player, device, sample_rate, channels
        self.sample_width, self.preroll_ms, self.timeout = sample_width, preroll_ms, timeout
        self.debug_retain_final_wav = debug_retain_final_wav
        self.retry_max_attempts, self.retry_delay_ms = retry_max_attempts, retry_delay_ms

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

    def _retain_final_wav(self, playback_path: Path, checksum: str) -> Path | None:
        """Best-effort diagnostic copy; never make optional retention authoritative."""
        request_id = speech_request_id.get()
        if not self.debug_retain_final_wav or request_id is None:
            return None
        retained_path = None
        try:
            descriptor, generated_path = tempfile.mkstemp(
                prefix=f"phos-speech-{os.getpid()}-{request_id}-", suffix=".wav", dir="/tmp")
            os.close(descriptor)
            retained_path = Path(generated_path)
            shutil.copyfile(playback_path, retained_path)
            _fsync_path(retained_path)
            if _sha256_prefix(retained_path) != checksum:
                raise OSError("retained WAV checksum mismatch")
            speech_log("retained_wav", path=str(retained_path), sha256=checksum)
            return retained_path
        except OSError as error:
            if retained_path is not None:
                retained_path.unlink(missing_ok=True)
            speech_log("retained_wav_failed", logging.WARNING, exception_type=type(error).__name__)
            return None

    def _normalized_wav(self, source: Path) -> Path | None:
        normalized = None
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
            descriptor, generated_path = tempfile.mkstemp(prefix="phos-tts-playback-", suffix=".wav")
            normalized = Path(generated_path)
            raw_file = os.fdopen(descriptor, "wb")
            with raw_file:
                with wave.open(raw_file, "wb") as output_file:
                    output_file.setparams(target)
                    output_file.writeframes(preroll + speech)
                raw_file.flush()
                os.fsync(raw_file.fileno())
            return normalized
        except (OSError, EOFError, wave.Error, audioop.error) as error:
            if normalized is not None:
                normalized.unlink(missing_ok=True)
            raise AudioOutputError("Audio playback could not be prepared.") from error

    def play(self, audio: SynthesizedAudio) -> None:
        executable = shutil.which(self.player)
        if executable is None:
            raise AudioOutputError("Audio player is unavailable.")
        normalization_started = time.monotonic()
        speech_log("normalization_start")
        normalized = self._normalized_wav(audio.path)
        playback_path = normalized or audio.path
        normalization_ms = round((time.monotonic() - normalization_started) * 1000)
        try:
            with wave.open(str(playback_path), "rb") as playback_file:
                params = playback_file.getparams()
                frames = playback_file.readframes(params.nframes)
        except (OSError, EOFError, wave.Error) as error:
            if normalized is not None:
                normalized.unlink(missing_ok=True)
            raise AudioOutputError("Audio playback WAV could not be inspected.") from error
        if (params.comptype != "NONE" or params.framerate != self.sample_rate
                or params.nchannels != self.channels or params.sampwidth != self.sample_width):
            if normalized is not None:
                normalized.unlink(missing_ok=True)
            raise AudioOutputError("Audio playback WAV does not match the configured output format.")
        if params.nframes <= 0 or not frames or audioop.max(frames, params.sampwidth) == 0:
            if normalized is not None:
                normalized.unlink(missing_ok=True)
            raise AudioOutputError("Audio playback WAV is empty or silent.")
        duration_ms = round(params.nframes * 1000 / params.framerate)
        checksum = _sha256_prefix(playback_path)
        size_bytes = playback_path.stat().st_size
        speech_log("normalization_complete", duration_ms=normalization_ms, rate_hz=params.framerate,
                   channels=params.nchannels, sample_width_bytes=params.sampwidth,
                   frames=params.nframes, audio_duration_ms=duration_ms,
                   peak=audioop.max(frames, params.sampwidth), rms=audioop.rms(frames, params.sampwidth))
        speech_log("final_wav", sha256=checksum, size_bytes=size_bytes)
        retained_path = self._retain_final_wav(playback_path, checksum)
        if self.preroll_ms:
            logger.debug("TTS audio playback preroll_ms=%s", self.preroll_ms)
        command = [executable, "-q"]
        if self.device is not None:
            command.extend(("-D", self.device))
        command.append(str(playback_path))
        try:
            playback_started = time.monotonic()
            speech_log("alsa_target", logging.DEBUG, executable=executable, device=self.device or "default")
            speech_log("playback_environment", logging.DEBUG, user=os.environ.get("USER", "unknown"),
                       xdg_runtime_dir=os.environ.get("XDG_RUNTIME_DIR", "unset"),
                       alsa_config_path=os.environ.get("ALSA_CONFIG_PATH", "unset"),
                       pulse_server=os.environ.get("PULSE_SERVER", "unset"),
                       pipewire_remote=os.environ.get("PIPEWIRE_REMOTE", "unset"),
                       cwd=os.getcwd(), process_group=os.getpgrp())
            speech_log("playback_command", executable=executable, quiet=True,
                       device=self.device or "default", final_wav=str(playback_path))
            speech_log("playback_start", executable=executable, device=self.device or "default",
                       final_wav=str(playback_path))
            result = None
            for attempt in range(1, self.retry_max_attempts + 1):
                speech_log("playback_attempt", attempt=attempt, playback_device=self.device or "default")
                try:
                    result = subprocess.run(command, capture_output=True, text=True, timeout=self.timeout, check=False)
                except (subprocess.TimeoutExpired, OSError) as error:
                    failure = type(error).__name__
                    speech_log("playback_failed", logging.WARNING, attempt=attempt, exception_type=failure)
                else:
                    if not result.returncode:
                        break
                    stderr = " ".join(result.stderr.splitlines())[:160]
                    failure = f"returncode={result.returncode} stderr={stderr or 'none'}"
                    speech_log("playback_failed", logging.WARNING, returncode=result.returncode,
                               stderr=stderr or "none", attempt=attempt)
                if attempt == self.retry_max_attempts:
                    raise AudioOutputError("Audio playback failed after retry.")
                speech_log("playback_retry", logging.WARNING, next_attempt=attempt + 1,
                           delay_ms=self.retry_delay_ms, reason=failure)
                time.sleep(self.retry_delay_ms / 1000)
            if result is None or result.returncode:
                raise AudioOutputError("Audio playback failed after retry.")
            if retained_path is not None:
                if not playback_path.is_file() or _sha256_prefix(playback_path) != checksum:
                    raise AudioOutputError("Playback WAV changed during playback.")
                if _sha256_prefix(retained_path) != checksum:
                    speech_log("retained_wav_failed", logging.WARNING, exception_type="ChecksumMismatch")
                else:
                    speech_log("retained_wav_verified", path=str(retained_path), sha256=checksum)
            speech_log("playback_complete", duration_ms=round((time.monotonic() - playback_started) * 1000),
                       attempt=attempt, returncode=result.returncode)
        finally:
            if normalized is not None:
                normalized.unlink(missing_ok=True)
