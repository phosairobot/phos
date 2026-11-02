# PHOS

![PHOS robot](img/phos.png)

**An open-source expressive robot platform for Raspberry Pi.**

PHOS combines an animated face, camera-based perception, environmental and motion sensing, touch, voice, and LAN administration into a modular physical robot platform. It targets Raspberry Pi 3 today and is developed as a real hardware project: capabilities are designed behind explicit provider boundaries, covered by hardware-free tests where possible, and distinguished from physical Pi validation.

**Current release:** PHOS 1.8.0 — MCP & Runtime Administration Stabilization · **Target:** Raspberry Pi 3 · **Documentation:** [phosairobot.github.io/phos](https://phosairobot.github.io/phos/)

## What is PHOS?

PHOS is built around expressive, observable robot behavior rather than a single cloud service or a browser-only demo. Sensors and providers produce observations; the central `BehaviorEngine` turns those into semantic robot state and visual intent. The renderer, LEDs, audio, Web Admin, and API consume those boundaries without reaching into hardware internals.

The project is local-first where practical. Its core display, local vision, local Vosk speech recognition, and local Piper speech synthesis can run on the Pi. Cloud providers are optional, selected explicitly, and kept behind the same provider-neutral interfaces.

## What PHOS can do today

### Eyes & display

- Render animated expressive eyes with blink, idle gaze, semantic accents, and runtime face states.
- Present a startup/readiness experience with a splash, optional ready sound, and display-session safeguards for the physical face display.

### Vision & presence

- Use the Raspberry Pi Camera for face detection, selected-face tracking, and bounded pupil gaze.
- Derive confirmed presence and attention states, including optional LED arrival and departure reactions.
- Run local OpenCV/ONNX expression observations with cautious reaction policy; PHOS does not perform identity recognition by default.

### Voice input & speech

- Capture bounded utterances from a USB microphone with resampling, VAD, and pre-roll.
- Transcribe through provider-neutral `STTProvider`: Local/Vosk is the default, with opt-in Cartesia Ink batch STT.
- Synthesize through `TTSProvider`: Local/Piper, Cartesia, and ElevenLabs integration feed a separate `AudioOutputProvider` playback boundary.
- Accept speech through `/api/v1/speak`, reflecting playback through the `SPEAKING` state.
- Apply supported STT, TTS, and audio-output changes live: active operations retain their original provider, while subsequent work uses the replacement.
- Resolve the supported local English/Italian command catalog; see [Voice Commands](docs/voice-commands.md) for the exact accepted phrases and required interaction settings.

### Sensors & physical interaction

- Recognize local touchscreen taps, long presses, and horizontal swipes as semantic gestures.
- Integrate optional BME280/BMP280 environmental sensing, CCS811 air-quality estimates, MPU-6050/GY-521 motion sensing, and WS2812B LED-ring feedback.
- Translate confirmed environmental and motion observations into behavioral visual intent rather than letting drivers control the display directly.

### Behavior, Web Admin & API

- Use a central `BehaviorEngine` and explicit robot states for presence, attention, environmental context, motion, listening, thinking, and speaking.
- Provide authenticated Web Admin for configuration, runtime status, encrypted credentials, and Voice & Speech settings.
- Expose a provider-neutral Remote API and runtime status without giving web clients direct access to GPIO, camera frames, or renderer internals.
- Persist validated configuration atomically. YAML is the canonical editable format; existing legacy JSON configuration remains supported as a fallback.

## Local first, cloud optional

| Capability | Local default | Optional cloud |
| --- | --- | --- |
| Speech to Text | Vosk | Cartesia Ink batch STT |
| Text to Speech | Piper | Cartesia, ElevenLabs |
| Expression observation | OpenCV / ONNX | AWS Rekognition |

Cloud selection is opt-in: PHOS does not silently substitute a local provider when an explicitly selected cloud provider is unavailable. Credentials live in encrypted external storage through `SecretsService`, not in runtime YAML or Web responses. ElevenLabs integration is implemented, while its end-to-end physical availability depends on the provider account and selected voice/plan.

## Architecture at a glance

```text
Camera / microphone / sensors / touch
                 |
                 v
         Providers and adapters
                 |
                 v
           BehaviorEngine
                 |
                 v
      Semantic robot state / FaceState
                 |
                 v
       Face / LEDs / audio / Web & API
```

Providers observe or perform focused capabilities; `BehaviorEngine` owns behavioral decisions; the renderer consumes semantic `FaceState`; and Web/API adapters use application services rather than manipulating hardware directly. This keeps local and cloud provider choices independently replaceable. Read the [architecture reference](https://phosairobot.github.io/phos/architecture/) for the complete design.

## Reference hardware

The reference platform is a Raspberry Pi 3 with a 5-inch 800×600 display, Raspberry Pi Camera, and USB microphone. Optional integrations include BME280/BMP280, CCS811, MPU-6050/GY-521, and a WS2812B RGB LED ring. Software support and physical acceptance are tracked separately; consult the [hardware guide](https://phosairobot.github.io/phos/hardware/) before wiring or assuming a module is validated on a particular build.

## Web Admin & Remote API

PHOS can be administered from a trusted LAN through authenticated Web Admin. It provides runtime/system status, validated configuration, a write-only credentials page, and a Voice & Speech area split into Speech to Text, Text to Speech, and Audio Output. The page intentionally shows configured and active provider/device state separately, with Unknown status when a runtime snapshot is unavailable. Supported live changes are activated before they are persisted.

The Remote API exposes semantic status and control through the shared application-service boundary. See the [Web Admin manual](https://phosairobot.github.io/phos/web-administration/) and [Remote API reference](https://phosairobot.github.io/phos/remote-api/) for deployment, authentication, and endpoint details.

## MCP: semantic context and current perception

PHOS can expose semantic tools and read-only MCP Resources to an external AI
agent through Streamable HTTP. Resources read PHOS state/context; tools request
supported semantic actions. The external agent never accesses hardware or the
Raspberry Pi camera directly:

```text
External Agent → MCP semantic interface → AgentApplicationService
               → PhosApplicationService → PHOS runtime services
```

Available Resources are `phos://status`, `phos://physical-context`,
`phos://environment`, and `phos://vision/snapshot`. The vision resource is one
current on-demand JPEG frame, not a video stream or continuous subscription.
It is protected separately from generic physical-context reads because the
image may contain people visible to the camera. See the [MCP guide](docs/mcp.md).
The currently exposed tools are `phos_get_status`, `phos_get_physical_context`,
`phos_get_presence`, `phos_get_attention`, `phos_get_observed_expression`,
`phos_set_expression`, `phos_speak`, `phos_sleep`, `phos_wake`, and
`phos_signal_user`.

Modern MCP clients may use `subscriptions/listen` for semantic Resource change
cues, then re-read the notified Resource. Notifications contain no PHOS event
payloads and never announce camera frames. Numeric sensor samples are read on
demand; semantic condition and sensor availability/freshness transitions emit
Resource update cues. This is a change-cue protocol, not an event log: it has
no replay, history, or raw sensor-telemetry push; after reconnecting, clients
open a new subscription and re-read current Resources. The separately owned
`phos-mcp-bridge` has externally validated long-lived stdio subscription
forwarding for consecutive `phos://status` updates.

## Project status & roadmap

### PHOS 1.8.0 — MCP & Runtime Administration Stabilization

This release adds the permission-checked MCP semantic adapter, Streamable HTTP
transport, stdio bridge Resource forwarding, and the on-demand camera snapshot
Resource. It also stabilizes Web Admin Runtime settings with immediate Camera
Preview and logging-level application through the PHOS lifecycle boundary.

PHOS continues to distinguish source implementation from physical validation.
Direct HTTP semantic subscription and sensor freshness validation is recorded;
MCP Stage 2 is complete. Raspberry Pi validation confirmed that, with Camera
Preview disabled, `phos://vision/snapshot` remained available and returned a
non-empty `image/jpeg` blob. This validates resource availability and JPEG
output, not image-content quality. Optional sensor, provider, and deployment
acceptance remains component-specific.

Next, PHOS is deliberately not jumping straight to a generic assistant. The major deferred capabilities are conversational LLM interaction, wake-word and continuous listening, Home Assistant, and MCP credential policy/UI hardening. Cartesia STT remains batch/utterance-based; provider-side streaming and turn detection are not implemented.

MCP agent configuration is canonical PHOS configuration: `agents.default_agent`
selects one entry from `agents.entries`, whose `id`, optional `display_name`,
and `permission_profile` resolve through the existing permission boundary.
When the whole section is absent, legacy deployments use `local-agent` with
`standard`; an explicit invalid section fails startup rather than falling back.
An entry may reference a bearer secret with
`credential: {type: bearer, secret_ref: mcp.agent.token}`. `SecretsService`
holds the token; runtime configuration and `AgentIdentity` never do. If any
such reference exists, each MCP request must present `Authorization: Bearer …`;
invalid credentials fail closed and separate MCP sessions retain separate,
request-scoped identities.

Configured MCP credentials have a programmatic lifecycle boundary for future
administration: create, rotate, and revoke use the encrypted `SecretsService`
store while retaining the same non-secret reference in configuration. A newly
created or rotated bearer is shown exactly once; credential status never
returns stored bearer values. Rotation and revocation take effect immediately
for subsequent requests without changing the agent's permission profile.
Web Admin **Settings → Agents** provides the same create/rotate/revoke workflow
for existing configured agents, with confirmed destructive actions and one-time
bearer disclosure only.

See the concise [roadmap](https://phosairobot.github.io/phos/roadmap/) and the detailed [current implementation handoff](https://phosairobot.github.io/phos/current-state/) for scope, release history, and physical acceptance limits.

## Quick start

On a Raspberry Pi OS system with a graphical desktop:

```bash
git clone https://github.com/phosairobot/phos.git
cd phos
./scripts/install-phos.sh
```

The installer is the supported Raspberry Pi OS/Debian ARM path. It does not replace the setup guide: review the [installation documentation](https://phosairobot.github.io/phos/installation/) for display, camera, optional hardware, configuration, and managed user-service startup requirements.

## Documentation

Detailed documentation lives at **[phosairobot.github.io/phos](https://phosairobot.github.io/phos/)**. It covers [installation](https://phosairobot.github.io/phos/installation/), [hardware](https://phosairobot.github.io/phos/hardware/), [architecture](https://phosairobot.github.io/phos/architecture/), [configuration and development](https://phosairobot.github.io/phos/development/), [vision](https://phosairobot.github.io/phos/vision/), [voice](https://phosairobot.github.io/phos/voice/), [Web Admin](https://phosairobot.github.io/phos/web-administration/), and the [Remote API](https://phosairobot.github.io/phos/remote-api/).

## Contributing

Contributions are welcome from Python developers, Raspberry Pi and hardware makers, provider/integration developers, and documentation contributors. Start with [the architecture](docs/architecture.md), [accepted decisions](docs/decisions.md), and the repository's [contributor guidance](AGENTS.md). Keep hardware-specific work behind adapters, preserve provider-neutral boundaries, and retain hardware-free test coverage where possible.

Project links: [GitHub](https://github.com/phosairobot/phos) · [X](https://x.com/phosairobot) · [Instagram](https://www.instagram.com/phosairobot/)

## License

No license file is currently included in this repository. Contact the project maintainers before redistributing or reusing the code.
