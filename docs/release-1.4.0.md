# PHOS 1.4.0 — Voice & Interaction Foundation

The authoritative version is `robot.__version__` in `src/robot/__init__.py`.

## Highlights

- **Startup/readiness experience:** render-loop splash presentation, readiness
  handling, optional startup-ready sound, and a hidden cursor on the face display.
- **Touch interaction foundation:** provider-neutral touch input, semantic touch
  events, and tap, long-press, left-swipe, and right-swipe gesture detection.
- **Provider-neutral voice capture:** native microphone-rate negotiation and one
  resampling boundary to canonical 16 kHz mono PCM16 processing.
- **Speech capture:** energy VAD and bounded 300 ms pre-roll preserve utterance
  onset before local transcription.
- **Local STT baseline:** provider-neutral STT contracts with an optional local
  Vosk adapter, observable session events, and `LISTENING → THINKING → IDLE`.
- **Observability:** voice status/API visibility, semantic lifecycle logging,
  bounded diagnostic telemetry, and an opt-in one-file debug WAV dump.
- **Physical validation:** Raspberry Pi 3 validation with a USB microphone,
  44.1 kHz native capture, 16 kHz processing, VAD, pre-roll, and local Vosk.

## Known limitations

- Voice transcripts are not connected to PHOS actions or voice commands.
- TTS and a `SPEAKING` lifecycle are not included.
- PHOS has no conversational LLM layer, wake word, continuous listening, Home
  Assistant voice integration, or cloud STT adapter in this release.
- The small Vosk English model is a local/offline baseline; longer phrases and
  non-native English accents can have limited recognition accuracy.
- JSON remains the canonical configuration format in 1.4.0. YAML migration is
  intentionally deferred.
