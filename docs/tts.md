# Text-to-Speech

## Decision

The robot uses a provider-independent `TTSProvider` interface.

The application must not depend directly on Piper, an OpenAI TTS SDK, Home Assistant TTS or another vendor implementation outside the corresponding provider adapter.

## Implemented local provider: Piper

Piper is the preferred first local TTS engine because the project targets a Raspberry Pi 3 and should retain useful offline speech capability.

`PiperTTSProvider` lazily loads the installed `piper-tts` Python voice API once
in the main PHOS runtime process, then reuses that in-memory model for each secure
temporary WAV. `AplayAudioOutputProvider` plays that artifact through ALSA and the
runtime removes it afterwards. Piper never selects a speaker device directly.

Models are local deployment assets, normally under `models/tts/piper/`, and are
not committed. Configure `tts.local.model_path` (plus an optional Piper
`speaker_id`) and install the `piper-tts` package on the Pi. The canonical `tts` section is
disabled by default, uses `provider: local`, `engine: piper`, and `aplay` output.
When enabled, a failed model/Python API is reported when speech is requested;
disabled TTS does not affect startup.

On the reference Raspberry Pi HDMI deployment, PHOS normalizes Piper output at
the playback boundary to 48000 Hz, stereo, PCM16 (`sample_rate: 48000`,
`channels: 2`, `sample_width: 2`). Piper synthesis itself is unchanged. A
1000 ms low-amplitude non-zero 440 Hz warm-up signal is then prepended in that
same target format and the resulting temporary WAV is passed to `aplay` once.
Set `preroll_ms: 0` to disable only the warm-up; other outputs may configure a
different target format. This is an HDMI output workaround, not a Piper
synthesis change.

For diagnostics, each accepted utterance receives a monotonically increasing request
identifier; its logs use `PHOS SPEECH[n]` for acceptance, synthesis, WAV validation,
normalization, playback, and terminal task cleanup. PHOS logs monotonic durations for synthesis, normalization,
`aplay` playback and total completion, along with final WAV technical metadata
(rate, channels, PCM sample width, frames and duration). Source Piper WAVs must
also be non-empty, uncompressed, and non-silent before they can reach playback.
It never logs speech
text or audio content. REST-triggered speech uses this exact same runtime-owned
Piper → normalized/prerolled single WAV → one `aplay` path. The first utterance
includes lazy model loading and logs `piper_model_load_*`; later utterances reuse
the loaded voice. PHOS does not run a persistent Piper server.

### Diagnosing intermittent HDMI playback

`tts.audio_output.debug.retain_final_wav` is disabled by default. Set it to
`true` only while diagnosing playback. For a REST request, PHOS first creates its
normal unique, fully closed and fsynced final WAV, then makes a byte-identical
diagnostic copy under `/tmp/phos-speech-<pid>-<request-id>-<unique>.wav`. The
normal temporary final WAV remains the artifact passed to `aplay` and is removed
only after `aplay` exits. Retained diagnostics are not removed automatically, do
not overwrite prior samples, and can accumulate under `/tmp`. Retention is
best-effort: collisions, permission failures, and filesystem errors are logged as
warnings but never prevent speech playback.

Every request logs sanitized final-WAV metadata (format, frames, duration, peak,
RMS, byte size, and SHA-256 prefix), plus the selected `aplay` executable/device,
return code, and duration. DEBUG logging also emits the selected ALSA target.
PHOS invokes the deterministic command `aplay -q [-D <configured-device>]
<final-wav>` using a file path, never stdin. When `device` is configured, that
same device is supplied on every attempt; otherwise ALSA's default-device
resolution is necessarily used and is logged as `default`.

`tts.audio_output.retry.max_attempts` defaults to `2` (and is capped at two),
with a 200 ms `retry.delay_ms`. A nonzero `aplay` result, timeout, or execution
error retries playback of the exact same finalized WAV once. Piper synthesis is
never retried. A zero exit status means only that `aplay` consumed the WAV; it
does not prove that HDMI rendered intelligible audio, so PHOS does not retry a
successful process merely because physical output cannot be observed in software.
The selected user, runtime directory, ALSA/Pulse/PipeWire variables, working
directory, and process group are emitted only at DEBUG level to compare service
and manual-shell execution safely.

After a noisy request, replay its retained artifact without regenerating speech:

```sh
aplay /tmp/phos-speech-<pid>-<request-id>-<unique>.wav
```

Use `aplay -D <configured-device>` when `tts.audio_output.device` is configured.
If that replay is clean, the artifact was valid and the remaining issue is HDMI or
ALSA sink state. If it is noisy too, compare its metadata/checksum to a good
request: the problem is in final-artifact generation. A zero `aplay` exit status
alone is not evidence that HDMI rendered intelligible audio.

HDMI audio can also be affected when the display enters standby. The reference
deployment keeps its X11 face display active while PHOS runs; see [face display
session guidance](hardware.md#face-display-session).

## Architecture

```text
Robot / behavior
      |
      v
 TTSProvider
      |
      +--> PiperTTSProvider      local/offline
      +--> future ElevenLabs provider
      +--> future Google Cloud TTS provider
      +--> future Cartesia provider
```

`TTSProviderFactory` selects the configured provider only in the main PHOS
runtime process. The Web Admin edits canonical configuration and never creates
a provider. `local` (Piper) is the sole implemented selection. `elevenlabs`,
`google`, and `cartesia` are stable reserved identifiers for future providers;
the factory rejects them explicitly rather than falling back to Piper. Their API
credentials remain write-only secrets managed through Web Admin's Credentials
page, never YAML. Provider changes are saved but require a PHOS restart.

## Contract

`TTSProvider.synthesize()` receives a neutral `TTSRequest` and returns a neutral `TTSResult`.

Provider-specific SDK objects must never escape the adapter.

## Responsibilities

### TTSProvider
- define the stable synthesis contract
- carry text, language and optional voice
- return the generated audio path and neutral metadata

### PiperTTSProvider
- translate the neutral request into a Piper Python API synthesis call
- write audio to the requested output path
- convert Piper failures into explicit application errors

### Audio playback
Playback is a separate responsibility. `TTSProvider` generates audio; it does not own speaker selection, volume, ALSA configuration or playback lifecycle.

## Raspberry Pi 3 constraints

- Prefer local TTS for low-latency/offline system phrases when practical.
- Benchmark voice/model quality versus synthesis latency on the Pi 3.
- Do not assume every Piper model performs acceptably on the Pi 3.
- Keep model path and executable path configurable.
- Do not bundle large voice models in the source repository.

## Future providers

A new provider must implement `TTSProvider` without changing callers.
Examples may include cloud TTS, Home Assistant TTS or a LAN-hosted synthesis service.
Cloud providers are not implemented.

## Raspberry Pi validation

1. Install the `piper-tts` Python package and place a small voice model and metadata beside each
   other under `models/tts/piper/`.
2. Set `tts.enabled: true` and `tts.local.model_path` in the active config.
3. Start PHOS and invoke the application `speak()` path with a short phrase.
4. Confirm audible ALSA output, `IDLE → SPEAKING → IDLE`, and temporary WAV
   cleanup.
5. Exercise microphone/Vosk listening afterwards to confirm no STT regression.
