# Voice capture and speech recognition

PHOS 1.4.0 Slice 3 provides an explicit, finite microphone session: PyAudio
negotiates a supported native 16-bit mono device rate, then one stateful PCM16
resampler converts it to the canonical 16 kHz stream consumed by VAD and Vosk.
An `STTProvider` transcribes the bounded utterance. Raw audio is never persisted,
exposed through the API, or sent through SSE. During temporary physical STT
debugging, opt in to `voice.debug.dump_utterance_wav`; the most recent submitted
utterance is overwritten at `voice.debug.utterance_wav_path` (default
`/tmp/phos-last-utterance.wav`). It is a mono PCM16 WAV at the canonical
processing rate and must not be treated as permanent storage.

Voice is disabled by default. Enable `voice.enabled`, select an ALSA device,
and configure a local Vosk model directory under `voice.stt.model_path`. Vosk
is optional (`pip install vosk`); PHOS never downloads a model at startup.
Without the dependency or model, status reports the provider unavailable.

`voice.input.sample_rate` is the optional requested hardware rate; if it is
unsupported, PHOS tries the selected device's default rate, then 44.1/48 kHz,
before reporting `unsupported_capture_rate`. `voice.processing.sample_rate` is
strictly 16 kHz. Set `voice.input.device` to a stable substring such as
`USB PnP Sound Device: Audio`; `device_index` is optional diagnostics/fallback,
not a stable hardware identity.

The explicit API session transitions `IDLE → LISTENING → THINKING → IDLE`.
SSE emits only lifecycle edges such as `voice_speech_started` and
`voice_transcription_completed`. TTS, LLMs, wake words, continuous listening,
speaker identification and playback remain out of scope.

During an active capture session, PHOS writes a rate-limited `VOICE VAD LEVEL`
diagnostic at most once per 500 ms. It reports only calculated RMS/peak levels
from the canonical 16 kHz PCM16 frame, never audio samples. `voice.vad.threshold`
uses the same signed 16-bit PCM amplitude scale as `audioop.rms` (roughly
0–32768), not decibels; the VAD regards a frame as speech when its RMS is at
least the configured threshold.

For physical microphone validation, capture also logs its start/device/stream
lifecycle and a rate-limited `VOICE AUDIO FRAME` line at most once per 500 ms.
The line contains only delivered byte length plus capture and processing rates;
it never logs raw PCM. The first blocking hardware read additionally logs
`VOICE AUDIO READ: waiting` and its received byte count. Read failures are
logged with `VOICE AUDIO ERROR: stage=read`.

Before VAD speech start, PHOS retains the bounded `voice.vad.pre_roll_ms`
processed-PCM pre-roll (300 ms by default) to preserve the beginning of an
utterance. Immediately before local STT it logs whole-utterance duration, byte
length, RMS and peak, conditionally writes the temporary WAV debug dump, and
logs the provider's raw text, confidence and language result.

## Raspberry Pi 3 physical baseline and benchmark

Slice 3 is physically validated with a USB microphone on Raspberry Pi 3:
44.1 kHz mono PCM16 PyAudio capture is resampled once to 16 kHz mono PCM16,
then passes through energy VAD (threshold 50), 300 ms pre-roll and the local
`models/vosk/vosk-model-small-en-us-0.15` English model. Model files are not
committed. This small Vosk model validates the offline, provider-independent
STT architecture; longer phrases and non-native English accents can be poor.

For a manual benchmark, restart PHOS and test: `Hello`, `How are you`, `Hello
PHOS`, `This is a test`, `What time is it`, and `Turn on the light`. Record the
expected phrase, transcript, `VOICE STT: completed` duration, and a success,
partial or poor rating. Confirm silence does not activate VAD, speech onset is
not clipped, and inspect `/api/v1/voice` (or optionally the one debug WAV).
