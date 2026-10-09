# Text-to-Speech

## Decision

The robot uses a provider-independent `TTSProvider` interface.

The application must not depend directly on Piper, an OpenAI TTS SDK, Home Assistant TTS or another vendor implementation outside the corresponding provider adapter.

## Implemented local provider: Piper

Piper is the preferred first local TTS engine because the project targets a Raspberry Pi 3 and should retain useful offline speech capability.

`PiperTTSProvider` invokes an installed `piper` CLI without a shell, supplies text
on standard input, and writes a secure temporary WAV file. `AplayAudioOutputProvider`
plays that artifact through ALSA and the runtime removes it afterwards. Piper never
selects a speaker device directly.

Models are local deployment assets, normally under `models/tts/piper/`, and are
not committed. Configure `tts.local.model_path` (plus an optional Piper
`speaker_id`) and install `piper` on the Pi. The canonical `tts` section is
disabled by default, uses `provider: local`, `engine: piper`, and `aplay` output.
When enabled, a failed model/executable is reported when speech is requested;
disabled TTS does not affect startup.

On the reference Raspberry Pi HDMI deployment, short playback can lose its
opening while the sink wakes. `tts.audio_output.preroll_ms` defaults to `1000`:
PHOS creates one temporary WAV containing a very low-amplitude non-zero 440 Hz
warm-up signal followed unchanged by the synthesized speech, then calls `aplay`
once. Set `preroll_ms: 0` to restore direct playback. This is an HDMI output
workaround, not a Piper synthesis change.

## Architecture

```text
Robot / behavior
      |
      v
 TTSProvider
      |
      +--> PiperTTSProvider      local/offline
      +--> future cloud provider
      +--> future HA provider
```

## Contract

`TTSProvider.synthesize()` receives a neutral `TTSRequest` and returns a neutral `TTSResult`.

Provider-specific SDK objects must never escape the adapter.

## Responsibilities

### TTSProvider
- define the stable synthesis contract
- carry text, language and optional voice
- return the generated audio path and neutral metadata

### PiperTTSProvider
- translate the neutral request into a Piper invocation
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

1. Install the Piper CLI and place a small voice model and metadata beside each
   other under `models/tts/piper/`.
2. Set `tts.enabled: true` and `tts.local.model_path` in the active config.
3. Start PHOS and invoke the application `speak()` path with a short phrase.
4. Confirm audible ALSA output, `IDLE → SPEAKING → IDLE`, and temporary WAV
   cleanup.
5. Exercise microphone/Vosk listening afterwards to confirm no STT regression.
