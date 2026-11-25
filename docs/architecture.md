# PHOS Architecture

## High-level model

### Repository ownership

- **[phos](https://github.com/phosairobot/phos)** — owns the Raspberry Pi runtime,
  application services, MCP Streamable HTTP server, REST API, Web Admin, Vision and
  Voice.
- **[phos-mcp-bridge](https://github.com/phosairobot/phos-mcp-bridge)** — is the Node.js
  20+ transport adapter between local MCP `stdio` and PHOS Streamable HTTP. It reads
  bearer auth from `PHOS_MCP_TOKEN`, performs no identity or permission decisions, and
  never automatically retries `tools/call`.
- **[phos-esp32](https://github.com/phosairobot/phos-esp32)** — owns the companion
  `phos-hw` v1 firmware: ESP32 sensor acquisition and WS2812B physical execution.

## Remote application boundary

Remote control is mediated by **`robot.services.PhosApplicationService`**.

- **REST, Web Admin, event-stream, MCP, and Voice adapters** — Use semantic commands and
  read models from this boundary; they do not access camera, GPIO, I2C sensors, LED
  providers, or renderer internals.
- **`RobotCore` / `BehaviorEngine`** — Receive state changes delegated by the service.
- **Lifecycle** — Receives configuration work delegated by the service.

The following diagram shows the Core, subsystem, and provider boundaries.

```mermaid
flowchart TD
  core["Robot Core<br/>Lifecycle, state and events"]
  core --> voice[Voice]
  core --> ai["AI interfaces"]
  core --> ui[UI]
  voice --> mic[Microphone] --> speech["STT / TTS"]
  ai --> agent["Agent / tools"] --> llm[LLMProvider] --> adapters[Provider adapters]
  ui --> display["Display / eyes"]
  vision[Vision] -->|Observations and events| core
  hardware[Hardware adapters] -->|Typed observations| core
  style core fill:#2e1065,stroke:#a855f7,color:#fff
```

## Core principles

### External Hardware Controller Protocol v1

PHOS defines a hardware-agnostic USB/serial controller boundary in
`robot.hardware.controller_protocol`; it is not an ESP32 protocol.

#### Framing and envelope

It uses UTF-8 JSON Lines, one bounded object per newline, with the envelope:

- `protocol: "phos-hw"`
- `version: 1`
- `type`
- Optional `id`
- Optional `reply_to`
- Object `payload`

Controllers push semantic sensor/motion events and advertise capabilities; PHOS does not
poll them.

#### Inbound messages and sensor mapping

The v1 source currently specifies the payload only where PHOS consumes it. Strict
payload validation applies to:

- `motion_update`
- `orientation_update`
- `set_led_animation`

The serial adapter also maps `sensor_update` values into existing semantic services:

- Temperature (`c`).
- Pressure (`hpa`).
- Humidity (`%`).
- eCO2 (`ppm`).
- TVOC (`ppb`).
- The push proof (`{"sensor":"push","event":"pressed"}`).

`capabilities` remains controller-defined metadata; LED output is available only for the
active serial session when that metadata advertises LED support. Other recognized v1
event types are retained as diagnostics but have no Raspberry semantic mapping yet.

#### Outbound commands

PHOS currently transmits only:

- `request_capabilities`
- `ping`
- Resolved `set_led_animation` commands.

The remaining accepted command names are reserved protocol vocabulary, not Web or API
controls.

**Bounded LED animations**

- `off`
- `solid`
- `blink`
- `pulse`
- `breathe`
- `chase`
- `spin`
- `wipe`

**`breathe`** accepts color, brightness, period/duration and min/max intensity.
Controllers execute timing, but never invent PHOS semantics.

#### Connection and validation boundaries

Heartbeats use reconnectable transports; a stale peer is unhealthy. PHOS treats LED
output as unavailable for that session; any controller-local LED fallback is
firmware-owned. Unknown top-level fields are ignored, while unknown types and versions
receive bounded protocol errors. Sensor events must still enter existing semantic
services and `BehaviorEngine` ownership. The Raspberry Pi serial adapter is
reconnectable and configured with a manual device path.

#### Directional LED semantics

Directional LED fields are protocol semantics, not physical LED indexes.

- **`spin`** — Is circular and defaults to clockwise; it accepts only clockwise or
  counterclockwise direction.
- **`chase` and `wipe`** — Default to circular/clockwise; their circular form accepts
  clockwise/counterclockwise direction (`wipe` may also name a top/bottom/left/right
  origin). Their symmetric form requires an origin plus outward or inward direction.
- **`off`** — Rejects all directional fields.
- **`solid`** — Rejects all directional fields.
- **`blink`** — Rejects all directional fields.
- **`pulse`** — Rejects all directional fields.
- **`breathe`** — Rejects all directional fields.

Top, bottom, left and right are logical positions: each controller implementation owns
its physical index mapping and must preserve these v1 defaults for payloads that omit
the new optional fields.

#### Touch input mapping

For the initial inbound proof, a controller pushes `sensor_update` with
`{"sensor":"push","event":"pressed"}`. PHOS maps this to its existing completed
touch/tap semantic boundary; it does not poll the controller or add controller-specific
behavior logic.

#### Controller-backed provider configuration

For controller-backed sensors:

1. Set `hardware_controller.enabled: true`.
2. Set the corresponding canonical provider to `controller`:

    - `sensors.environmental.provider`
    - `sensors.ens160.provider`
    - `sensors.imu.provider`

3. Retain `enabled: true`.

This prevents local I2C workers from starting while controller `sensor_update` messages
update the same semantic snapshots used by the application services. The controller is a
generic provider, not a hardware vendor identity. The committed default remains `local`
and does not enable optional controller hardware.

#### IMU message contracts

Controller IMU telemetry uses two validated v1 messages:

**`motion_update`** has exactly:

```json
{"state": "stationary|moving|tilt_left|tilt_right|tilt_forward|tilt_backward|shake|impact", "intensity": "low|medium|high"}
```

Intensity is semantic movement intensity, never linear velocity.

**`orientation_update`** has exactly:

- `acceleration_m_s2` — Vector with finite numeric `x`, `y`, and `z` axes in m/s².
- `angular_velocity_dps` — Vector with finite numeric `x`, `y`, and `z` axes in
  degrees/second.

Both update the existing IMU service; semantic motion and telemetry remain independent
snapshot fields.

### Transient semantic overlays

**`SemanticOverlayService`** owns bounded, source-scoped presentation requests:

- `success`
- `info`
- `attention`
- `warning`
- `error`
- `sensor_alert`

Each source owns one request; replacement affects only that source. Requests are
plain-text (maximum 120 characters), expire on monotonic time, and resolve by priority
from highest to lowest as shown below. Equal-priority requests resolve to the newest
request.

```mermaid
flowchart LR
  error[error] --> warning[warning] --> sensor[sensor_alert]
  sensor --> attention[attention] --> success[success] --> info[info]
  style error fill:#2e1065,stroke:#a855f7,color:#fff
```

The renderer receives only an immutable visible intent/message view, never
caller-supplied drawing details.

The existing interpreted air-quality warning/bad event is the only automatic v1 sensor
source; it requests `sensor_alert` without inspecting raw providers.

#### Overlay boundaries and display order

This system is internal to the application-service boundary in v1: it adds no REST
endpoint. It is independent of `RobotState`, `FaceState`, environmental markers, and
state effects. The compact semantic card renders after camera preview but before the
final local administrative layer. Recovery and Admin QR overlays therefore remain
secret-bearing system overlays above it, retain their existing touch behavior, and are
not dismissible by semantic requests.

### Semantic Physical Context

The physical-context read model combines canonical semantics through the shared
application-service boundary:

```mermaid
flowchart TD
  n0["Sensors / Vision / RobotState"]
  n1["Canonical semantic services"]
  n2["Semantic Physical Context"]
  n3["PhosApplicationService"]
  n4["Agent / MCP / REST adapters"]
  n0 --> n1 --> n2 --> n3 --> n4
  style n0 fill:#2e1065,stroke:#a855f7,color:#fff
```

`SemanticPhysicalContext` is a read-only, build-on-read perception model. It combines
canonical robot, environmental, air-quality, motion, presence, attention, and
observed-expression semantics. Each section preserves its own availability and freshness
state, so partial context remains useful. It exposes no provider/device identity, raw
camera data, identity, renderer state, LED state, or controller health. It adds no REST
endpoint or MCP adapter; `/api/v1/status` remains the broader operational status
contract.

### Minimal Agent Permission Model

Agent operations pass through permission checks before application services:

```mermaid
flowchart TD
  n0["External agent"]
  n1["Agent permission boundary"]
  n2["PhosApplicationService"]
  n3["Behavior / Voice / Physical Context / Semantic Signals"]
  n0 --> n1 --> n2 --> n3
  style n0 fill:#2e1065,stroke:#a855f7,color:#fff
```

`AgentApplicationService` is a small, provider-independent facade for future trusted
agent adapters.  It checks an immutable `AgentAccess` set before delegating to the
existing `PhosApplicationService`; it never grants access to providers, renderers,
controller transports, or configuration internals.  The canonical operation mapping is:

| Operation | Permission |
| --- | --- |
| `status` | `READ_STATUS` |
| `get_physical_context` | `READ_PHYSICAL_CONTEXT` |
| `presence` | `READ_PERCEPTION` |
| `attention` | `READ_PERCEPTION` |
| `observed_expression` | `READ_PERCEPTION` |
| `set_expression` | `CONTROL_EXPRESSION` |
| `speak` | `CONTROL_VOICE` |
| `sleep` | `CONTROL_SLEEP` |
| `wake` | `CONTROL_SLEEP` |
| `signal_user` | `SIGNAL_USER` |
| `set_visual_state` | `CONTROL_VISUAL_STATE` |

#### State transitions and visual leases

Sleep and wake remain normal state transitions; a signal remains subject to existing
semantic LED arbitration.

An agent visual-state request is an identity-scoped, expiring presentation lease, not a
lifecycle transition: only `thinking` may be acquired and `idle` releases the caller's
lease. The effective visual state preserves Core priority for error, sleeping, speaking,
and listening, and never mutates `RobotCore.state`.

#### Permission profiles and excluded controls

The in-code `standard` profile contains that explicit complete v1 set; the `observer`
profile contains only the three read permissions. Configured agent identities select a
profile through the canonical agent registry. Raw LED animation, renderer internals,
continuous camera-frame access, serial/controller transport, hardware/configuration
mutation, and administrative/recovery controls are intentionally absent from this
facade.

#### Permission, capability, and authentication

- **Permission** — Answers whether an identity may request an operation.
- **Capability** — Answers whether the delegated application service can currently
  perform it; validation, availability, lifecycle, and arbitration errors still come
  from that service after a permission check.
- **Authentication** — Answers who is calling.

MCP resolves one immutable provider-neutral `AgentIdentity` per inbound request before
constructing the identity-bound facade. Configured bearer credentials select independent
agents; with no credential references, the compatibility path selects the configured
default identity without credential verification. Authentication modes and the
credential lifecycle are documented in the [MCP reference](mcp.md). Network access, TLS,
OS permissions, and deployment isolation remain separate concerns.

### MCP Semantic Adapter — Stage 1

The MCP adapter delegates through the existing agent and application boundaries:

```mermaid
flowchart TD
  n0["MCP client"]
  n1["MCP semantic adapter"]
  n2["Agent permission boundary"]
  n3["PhosApplicationService"]
  n4["PHOS runtime / services"]
  n0 --> n1 --> n2 --> n3 --> n4
  style n0 fill:#2e1065,stroke:#a855f7,color:#fff
```

Stage 1 uses the official Python `mcp` SDK (the bounded 2.x line) over Streamable HTTP.
PHOS owns the listener lifecycle: normal runtime startup creates one listener over the
existing application services, and normal runtime shutdown closes it.

#### Request/response tools

It exposes seventeen request/response tools.

**Status and physical context**

- `phos_get_status`
- `phos_get_physical_context`
- `phos_get_physical_assessment`

**Perception and camera**

- `phos_get_presence`
- `phos_get_attention`
- `phos_get_observed_expression`
- `phos_get_camera_snapshot`
- `phos_capture_camera_snapshot`
- `phos_get_visible_objects`

**Expressions, state, speech, and signals**

- `phos_set_expression`
- `phos_speak`
- `phos_sleep`
- `phos_wake`
- `phos_signal_user`
- `phos_set_state`

**Retained physical history**

- `phos_query_measurements`
- `phos_list_physical_transitions`

Every one delegates through **`AgentApplicationService`**; MCP owns no permissions,
runtime state, provider, or hardware logic.

#### Client sessions and deployment boundaries

MCP clients connect to the already-running PHOS instance. Client sessions do not create
runtimes, application services, or permission boundaries. The default listener is
loopback-only; a non-loopback bind requires explicit configuration and emits a warning.
PHOS bearer authentication and operation permissions do not replace network restrictions
or TLS deployment policy.

This is for trusted local/integrated execution. Configure credential references for
distinct bearer-authenticated agents; the credential-free compatibility path is not
transport authentication. Do not expose the endpoint on the public Internet. Raw
hardware, controller transport, renderer internals, continuous video, audio-device
management, configuration, and administrative/recovery controls are absent. Protected
on-demand camera snapshots are available through the semantic facade.

#### Semantic events and resource updates

Stage 2 begins with a transport-neutral semantic event model. The application service
projects meaningful interpreted transitions (robot state, environmental and air-quality
conditions, motion, presence, attention, and observed expression) into events with
stable fields:

- `type`
- `timestamp`
- `source`
- `payload`

It does not emit raw telemetry, provider/device details, or confidence jitter.

##### Event filtering and status projection

Consumers may request a validated allow-list of stable event types; each subscription
defensively suppresses only adjacent identical semantic events during a short internal
window, so a real transition from A to B and back to A remain visible. The model is an
internal observer boundary—not the Core event bus and not an MCP feature. It projects
canonical agent-status availability as one compact `capability_changed` event per
changed capability, and canonical degraded state as `system_health_changed`. The first
ready-status snapshot establishes a baseline and emits neither startup availability nor
health events; later comparisons use the same availability/degraded projection as
`phos_get_status`, with health reasons sorted before comparison.

##### Subscription ownership and cleanup

Each observer owns an explicit `SemanticSubscription` handle with an opaque
process-local identifier, active state, idempotent `close()`, and optional
context-manager cleanup. The publisher holds the handler strongly only while the handle
is active; explicit close is the required ownership boundary. A dispatch snapshot
already in progress may complete when a handle is closed, but no later delivery starts
after `close()` returns.

##### MCP notification delivery

The MCP adapter may consume this model through its bounded `McpSemanticEventBridge`:
standard MCP `subscriptions/listen` clients receive URI-only
`notifications/resources/updated` cues and re-read the relevant Resource. This remains
separate from the Core event bus; REST/WebSocket delivery and an optional future MCP
Events webhook adapter are not Core responsibilities. Camera resources remain excluded
from semantic push cues.

#### Agent status projection

`phos_get_status` is intentionally narrower than `/api/v1/status`. Its agent status
document contains only:

- `running`
- `state`
- Semantic subsystem `availability`.
- `degraded`
- `degraded_reasons`

It contains no sensor or perception values (those belong to physical context),
provider/device/model details, controller session data, LED animation resolution,
display state, or administrative diagnostics. The projection is owned by
`AgentApplicationService`, not the MCP adapter, so future trusted agent adapters share
the same contract.

### 1. Separation of concerns

Core coordinates lifecycle, state, events and behavior. Subsystems implement
capabilities.

### 2. Hardware abstraction

Business logic depends on interfaces, not GPIO/device-specific code. Ordinary unit tests
must not require physical hardware.

### 3. Events where they reduce coupling

Use events for meaningful asynchronous state changes such as speech recognition,
robot-state changes or stable Vision observations. Do not create an event merely to
avoid a normal function call.

### 4. Explicit robot state

The existing state model includes `IDLE`, `LISTENING`, `THINKING`, `SPEAKING`,
`SLEEPING` and `ERROR`. Keep transitions explicit and testable.

### 5. Incremental verification

PHOS is developed through runnable vertical milestones. Existing code may be ahead of
the current verified milestone, but new work should prove one capability before
expanding the next major subsystem.

## Visual / behavior path

The visual path separates observation, behavior, rendering, and display composition:

```mermaid
flowchart TD
  camera[Camera] --> vision[Vision] --> events[Stable observations and events]
  events --> core[Robot Core] --> behavior[BehaviorEngine] --> face[FaceState]
  face --> renderer[EyeRenderer] --> display[EyeDisplay composition]
  vision --> preview[Latest preview snapshot] --> runtime[PhosRuntime] --> display
  style behavior fill:#2e1065,stroke:#a855f7,color:#fff
```

- **Vision** — Observes.
- **Behavior** — Interprets observations in the context of PHOS's own `RobotState`.
- **`EyeRenderer`** — Consumes only `FaceState` and handles rendering/animation details.
- **Display adapter** — May compose an optional local diagnostic preview from a
  UI-neutral snapshot supplied by `PhosRuntime`; it never accesses camera or Vision
  providers.

The face-rendering frequency is independent from Vision timing so the display can remain
smooth while detection/inference runs at a lower rate.

`BehaviorEngine` currently lives under `src/robot/core/`, because behavior is part of
Core orchestration in the checked-out implementation.

### Visual reaction semantics

Vision classifies an uncertain **visible facial expression**, not a person's internal
emotional state. `BehaviorEngine` gives PHOS its own response and produces the
UI-neutral `FaceState`; Vision never selects renderer geometry or colors directly.

| Stable visible-expression observation | PHOS behavior | `FaceExpression` | `VisualAccent` |
| --- | --- | --- | --- |
| neutral | calm acknowledgement | neutral | neutral |
| happiness / happy | warm, friendly response | happy | warm |
| surprise / surprised | alert, open response | surprised | alert |
| unknown (including rejected/negative FER classes) | preserve and decay to baseline | unchanged, then neutral | unchanged, then neutral |

Vision publishes semantic evidence, not individual negative FER labels. UNKNOWN is an
explicit absence of accepted evidence. It is distinct from a positively confirmed
neutral observation; current uncalibrated models abstain on neutral. See
`docs/vision.md` for thresholds and temporal rules.

#### Face intent and renderer mapping

**`FaceState`** carries these independently:

- Expression.
- Semantic `VisualAccent`.
- `reaction_strength`.

Its optional signed `eye_asymmetry` is provider-neutral visual intent:

- **Positive** — Opens the left eye by that amount and closes the right by the same
  amount.
- **Negative** — Mirrors it.
- **Zero** — Requests equal eyes.
- **Absent** — The renderer uses the expression profile's ordinary shape.

The renderer maps accents to its own color palette:

| Accent | Color |
| --- | --- |
| Neutral | Light cyan/white |
| Warm | Turquoise |
| Curious | Cyan/blue |
| Alert | Amber |
| Sleepy | Muted violet |
| Error | Red |

Strength blends the neutral and accent colors, as well as the existing eye-shape
profile.

Eye, pupil, and background color transitions interpolate at render cadence rather than
jumping at Vision inference cadence.

#### Environmental overlays

Environmental interpretation also supplies independent, confirmed temperature and
air-quality overlay intents on `FaceState`. They are composable renderer inputs only:
the single priority-selected `EnvironmentalState` remains the contract for existing eye
and LED behavior, while the display can show, for example, both a warm-temperature
marker and an air-quality haze without reinterpreting sensor readings or thresholds.

#### Robot-state priority and reaction timing

Robot state remains higher priority than Vision:

- **Listening/thinking** — Use a curious accent.
- **Speaking** — Uses warm.
- **Sleeping** — Uses sleepy.
- **Error** — Uses red.

**Expression reaction timing**

- **Happy reactions** — Refresh only from confirmed observations and decay smoothly back
  to neutral when evidence stops.

- **Surprise** — Is temporary, cannot refresh from a sustained pose, and needs confirmed
  alternative evidence plus a cooldown to rearm.

- **UNKNOWN** — Allows normal decay.

- **Face tracking and blink timing** — Remain independent from expression inference.

`VisualAccent` is provider-neutral visual intent for the face. The WS2812B ring in the
PHOS hardware build connects to the ESP32; its output uses the semantic controller path
below rather than a per-pixel renderer feed.

```mermaid
flowchart LR
  behavior[BehaviorEngine] --> face[FaceState] --> eyes[EyeRenderer] --> display[Display]
  intent[Robot state / environment / user signal] --> arbiter[SemanticLedService]
  arbiter --> controller[ControllerService]
  controller -->|phos-hw v1 serial JSONL| esp[ESP32 controller]
  esp --> ring[WS2812B ring]
  style arbiter fill:#2e1065,stroke:#a855f7,color:#fff
  style esp fill:#1e293b,stroke:#8b5cf6,color:#fff
```

### External-controller semantic LED arbitration

External controller LEDs use a separate semantic path: `SemanticLedService` owns
source-scoped requests, priority arbitration, expiry, and command deduplication; the
controller executes only the resolved realtime animation. The intent sources are:

- **`RobotState`** — Contributes one persistent intent.
- **Environmental context** — Contributes a lower-priority ambient intent.
- **`signal_user`** — Contributes the bounded semantic signals:

    - `success`
    - `warning`
    - `error`
    - `attention`

#### Transient signals and persistent errors

All user signals, including `signal_user("error")`, are transient. In contrast,
`RobotState.ERROR` contributes the persistent `robot_state/error` intent until the robot
state changes. Error wins over warning, transient user signals, robot-state intents, and
ambient context.

#### Animation ownership and capability changes

Neither `BehaviorEngine`, Web/API adapters, nor firmware choose animation parameters. On
a controller capability report the arbiter reapplies its current winner; when LED
capability is absent it retains logical intent and emits no hardware command.

```mermaid
flowchart LR
  n0["RobotState / environment / signal_user"]
  n1["SemanticLedService"]
  n2["ControllerService"]
  n3["ESP32 controller LED executor"]
  n4["WS2812B ring"]
  n0 --> n1 --> n2 --> n3 --> n4
  style n0 fill:#2e1065,stroke:#a855f7,color:#fff
```

## Runtime

`PhosRuntime` is the current application coordinator. It supervises Core/UI and optional
Vision tasks and handles lifecycle/failure boundaries.

Keep orchestration thin. Do not move expression classification, rendering geometry or
model-provider logic into the runtime. Camera/inference/render/audio work must not
collapse into a single blocking loop.

The existence of the integrated runtime does not override milestone discipline: each
capability still needs independent runnable verification.

## AI provider architecture

Model access stays behind the provider-neutral interface:

```mermaid
flowchart TD
  agent["RobotAgent / behaviors / tools"] --> llm[LLMProvider]
  llm --> openai[OpenAI adapter]
  llm --> anthropic[Anthropic adapter]
  llm --> local[Local / LAN adapter]
  style llm fill:#2e1065,stroke:#a855f7,color:#fff
```

- **`LLMProvider`** — Is the only model interface visible to the rest of the robot.
- **Provider adapters** — Translate neutral messages/tools/tool calls to and from
  vendor-specific formats.

The primary conversational model is not assumed to run on Raspberry Pi 3. Cloud API or
LAN-hosted models are valid; a deliberately small local fallback may be explored later.

## Voice architecture

The voice path separates capture, transcription, intent resolution, authorization, and
application execution:

```mermaid
flowchart TD
  n0["USB microphone"]
  n1["AudioCaptureProvider<br/>Native device rate"]
  n2["ResamplingAudioCaptureProvider"]
  n3["PCM16 mono, 16 kHz"]
  n4["VAD"]
  n5["Bounded pre-roll"]
  n6["VoiceCaptureSession"]
  n7["STTProvider"]
  n8["TranscriptResult"]
  n9["InteractionInput"]
  n10["IntentResolver"]
  n11["ResolvedIntent or UnresolvedIntent"]
  n12["CommandRouter"]
  n13["Semantic command route"]
  n14["Explicit source-aware authorization"]
  n15["CommandExecutor"]
  n16["PhosApplicationService"]
  n17["InteractionOutcome"]
  n18["InteractionResponseRenderer"]
  n19["InteractionResponse"]
  n0 --> n1 --> n2 --> n3 --> n4 --> n5 --> n6 --> n7 --> n8 --> n9 --> n10 --> n11 --> n12 --> n13 --> n14 --> n15 --> n16 --> n17 --> n18 --> n19
  style n0 fill:#2e1065,stroke:#a855f7,color:#fff
```

### Capture and processing rates

Capture rate and processing rate are deliberately separate. Raspberry Pi 3 validation
uses a USB microphone at 44.1 kHz, followed by the one canonical resampling boundary to
the 16 kHz stream consumed by VAD and STT; microphones need not support 16 kHz natively.

The current explicit capture session stops at transcription:

```mermaid
stateDiagram-v2
  IDLE --> LISTENING
  LISTENING --> THINKING
  THINKING --> IDLE
```

### VAD baseline and pre-roll

VAD confirmation takes time, so its bounded 300 ms pre-roll preserves audio preceding
speech-start confirmation. The validated baseline is:

| Parameter | Baseline value |
| --- | --- |
| Threshold | 50 |
| Speech start | 120 ms |
| Silence end | 700 ms |
| Minimum utterance | 300 ms |
| Maximum utterance | 12000 ms |
| Pre-roll | 300 ms |

These are validated baseline values, not universal calibration values.

### Transcription providers

`STTProvider` keeps transcription provider-neutral; `VoskSTTProvider` is the first
local/offline implementation, not PHOS's architectural identity. The small English Vosk
model validates the local architecture but can have limited accuracy for longer phrases
and non-native English accents.

### Intent resolution, routing, and execution

Voice input publishes status/events and uses centralized runtime state transitions. It
does not control GPIO, renderers, LEDs, or `BehaviorEngine`.

- **`robot.interaction`** — Provides plain `TranscriptResult`, `InteractionInput`, and
  canonical resolved/unresolved intent contracts.

- **Async `IntentResolver`** — Its boundary interprets language only; it has no
  runtime/hardware access and does not route or execute commands.

- **Selectable deterministic resolver** — Has a small exact English/Italian catalog for
  state, expression, temperature, pressure, and air-quality commands; see [Voice
  Commands](voice-commands.md) for the user-facing accepted phrases.

- **Main runtime** — When interaction is enabled, observes completed voice transcripts
  and publishes resolution events/status.

- **`CommandRouter`** — Separately validates a resolved canonical intent and maps it to
  a semantic command.

- **Voice execution** — Is opt-in through `interaction.execution_enabled` (false by
  default): the coordinator creates a voice-scoped allowlist, then `CommandExecutor`
  invokes sleep, wake, expression, and authorized sensor commands through
  `PhosApplicationService`.

### Query outcomes and spoken responses

Sensor queries reuse that service's read models and produce an `InteractionOutcome` with
canonical numeric fields; unavailable snapshots are normal outcomes.

The pure local `InteractionResponseRenderer` produces deterministic English or Italian
user-facing text from query outcomes.

With `interaction.response_tts_enabled` (false by default), query responses pass through
the application speech boundary and the existing synthesis/playback path:

```mermaid
flowchart LR
  synthesis[TTSProvider] --> playback[AudioOutputProvider]
  style synthesis fill:#2e1065,stroke:#a855f7,color:#fff
```

This includes `SPEAKING`; actions and unresolved input remain silent.

The current provider-neutral TTS contract accepts text only, so response language is
retained as interaction metadata rather than passed as a provider-specific argument.

TTS adapters are implemented for:

- Local Piper.
- Cartesia.
- ElevenLabs.

Phrase resolution, command routing/execution, wake words, continuous listening, LLM
conversation, and Home Assistant remain future work.

### Physical voice validation

Physical validation on Raspberry Pi 3 with a USB microphone confirmed native capture,
resampling from 44.1 kHz to 16 kHz, VAD, preserved speech onset through pre-roll, local
Vosk transcription, and return to `IDLE`.

## Vision architecture

Vision keeps capture, detection, expression classification, and smoothing separate:

```mermaid
flowchart TD
  n0["Picamera2CameraProvider"]
  n1["FaceDetector"]
  n2["ExpressionProvider"]
  n3["ExpressionSmoother"]
  n4["Provider-neutral Vision result / event"]
  n0 --> n1 --> n2 --> n3 --> n4
  style n0 fill:#2e1065,stroke:#a855f7,color:#fff
```

Use lightweight processing appropriate for Raspberry Pi 3. Expression classification
describes uncertain visible facial-expression patterns, not a person's true
mental/emotional state. No identity recognition or frame persistence by default.

## Home Assistant relationship

Home Assistant is an integration/tool surface, not PHOS's brain. Commands may use REST,
state/events may use WebSocket, and MQTT may later expose PHOS entities. AI access must
be mediated by explicit tools/services and allowlists; sensitive actions require
explicit policy.

### Local/cloud expression boundary

Local and optional cloud providers share the expression-smoothing and behavior path:

```mermaid
flowchart LR
  camera[Camera] --> crop[Local detector / selector / square crop]
  crop --> provider[ExpressionProvider]
  provider --> local[OpenCVExpressionProvider]
  provider --> cloud[Optional AWSExpressionProvider]
  local --> smoother[ExpressionSmoother]
  cloud --> smoother
  smoother --> events[Vision events] --> behavior[BehaviorEngine] --> face[FaceState]
  style provider fill:#2e1065,stroke:#a855f7,color:#fff
```

#### Provider execution and observation provenance

AWS SDK objects remain in its adapter. Cloud requests use a single background asyncio
task plus `to_thread`, independent of capture/tracking/render cadence. Provider
lifecycle invalidation discards evidence across tracking discontinuity. Cached
`ExpressionObservation.sampled_at` preserves provenance so reads cannot manufacture
temporal confirmation. Local observations retain their existing cadence and semantics.
See [Vision](vision.md#selectable-local-aws-expressions).

## Canonical configuration and secrets

`config/phos.yaml` is the sole supported source of normal runtime settings.

- The provider-neutral `robot.config` module validates its required sections and maps
  them to `RuntimeConfig` and `CloudExpressionConfig`, with no hardware, SDK or argparse
  dependencies.

- The runtime passes only relevant typed values to each subsystem.

- Startup validates before constructing any camera/display components; paths are
  relative to the configuration file.

- Existing constructor fallback values are not consulted by application composition.

The same file/dict validation, serialization and atomic persistence must be used by the
optional web configuration interface. Credentials are never fields in application
settings.

`SecretsService` keeps runtime-only provider credentials in an authenticated encrypted
local store; its web facade reports slot status and permits replacement/removal but
deliberately cannot read values.

New ordinary settings must extend this central model/file, not standalone CLI arguments.

Existing CLI settings are deprecated explicit overrides during migration.

### Reload, live activation, and restart

- **Reload through the lifecycle service** — Logging level, display iris appearance, and
  camera-preview settings.
- **Runtime-aware live activation and persistence** — Supported Voice & Speech
  STT/TTS/audio changes.
- **Restart** — Other subsystem changes require restart. See [configuration
  reference](development.md#configuration).

## Administration adapter

`robot.web` wraps the canonical configuration service with structured settings forms and
an advanced YAML editor, both backed by the same `ConfigRepository`.

- **Credentials page** — Uses the write-only `WebSecretsService` facade, never
  `SecretsService.get_secret`, so secrets cannot enter page rendering or an
  administrative response.

Provider integration remains separate from this storage foundation.

### Web worker and authentication boundaries

Waitress runs in a separate process owned by the main startup lifecycle, independent of
rendering/Vision. It receives startup settings for a saved-versus-startup comparison;
runtime-backed pages request semantic status through the bounded application/lifecycle
channel rather than reading subsystem internals directly.

Its password-storage service is separate from runtime JSON; Flask sessions and Flask-WTF
protect administration actions. Core, providers and the renderer have no web-framework
dependency. See the [web manual](web-administration.md).

### Settings domains and persistence

Administration domains select canonical field paths for presentation only. Each page
merges its submitted fields into the complete document and calls the same
validation/persistence boundary. System / Status is read-only; password changes use the
separate credential service. See the manual for the domain-to-field map.

## Shared application services for control surfaces

Web, future remote API, MCP and Voice are adapters around PHOS application services.
They reuse the same validation, authorization, configuration and behavior-command
services; they must not reach into GPIO, hardware adapters, provider objects, renderer
state or other subsystem internals. Add an application service when an approved
capability needs one, rather than duplicating behavior in each surface.

The configuration, Remote API, supported Voice & Speech, and MCP semantic application
services are implemented examples.

## Application lifecycle boundary

**`robot.lifecycle.LifecycleService`** owns the active-configuration snapshot and
allowed operations:

- **Status** — Allowed status operation.
- **Reload** — Validates the full canonical file before applying logging level, iris
  appearance, and camera preview through their application-service boundaries.
- **Restart** — Allowed restart operation.

Supported Voice & Speech changes use a separate candidate-activation/persistence
transaction; other changes remain pending.

The parent process serves a bounded local process channel to the web worker; adapters
cannot submit shell commands, paths or arbitrary configuration payloads. Future API/MCP
surfaces must use the same policy service.

### Restart ownership and failure recovery

The supplied user systemd service owns process replacement. A confirmed restart follows
this sequence:

1. Request graceful runtime shutdown and exit code 75.
2. systemd restarts the same entry point.

The web adapter neither runs OS commands nor spawns replacements. Runtime subsystem and
web-worker failures propagate as nonzero process exits for systemd recovery.

### Preview reload and runtime status

Preview reload is acknowledged after camera start/stop acceptance; failed starts release
partially acquired camera resources, and shutdown drains preview tasks and supervisor
waiters. Manual launches reject browser restart. OS reboot and general remote control
remain out of scope. See installation for display-session environment and service
permissions. Status distinguishes configured values from runtime active/availability
state where that snapshot is available; it does not invent a health-monitoring layer.

## Eye appearance

The dedicated [Skin Architecture](skins/architecture.md) explains catalog loading,
active-agent selection, and the shared appearance lifecycle. See the
[Skins reference](skins/configuration.md) for supported presentation fields.

- **Display & Appearance configuration** — Supplies the validated canonical iris
  color to `EyeRenderer` at startup.
- **`EyeRenderer`** — Still consumes only `FaceState` and renderer style configuration;
  expression/provider labels never reach the renderer.
- **Semantic visual accents** — Continue to pass through `BehaviorEngine` and `FaceState`.
- **Tk** — Draws layered eye-body, iris, pupil and highlight primitives without a
  raster-processing pipeline.
- **Live iris appearance** — Can be applied through the configuration lifecycle and
  render-loop queue; display geometry/backend changes still require PHOS restart.

## Environmental sensor service

The separately approved BME280/BMP280 addition follows:

```mermaid
flowchart TD
  n0["BME280 / BMP280"]
  n1["Selected hardware provider"]
  n2["EnvironmentalSensorService"]
  n3["PhosRuntime.sensor_status"]
  n4["LifecycleService status snapshot"]
  n5["Existing local process channel"]
  n6["Authenticated Web Admin Sensors"]
  n0 --> n1 --> n2 --> n3 --> n4 --> n5 --> n6
  style n0 fill:#2e1065,stroke:#a855f7,color:#fff
```

- **`robot.sensors`** — Owns the provider-neutral contract, immutable measurements and
  latest-state service.

- **`robot.hardware` adapters** — Alone know the SMBus/driver APIs.

- **`hardware.environmental.environmental_provider_type`** — Centralizes selection and
  capabilities.

### Measurement contracts and interpretation

Both return `EnvironmentalReading` with temperature/pressure and nullable
`humidity_percent`; BMP280 must return null, BME280 must supply humidity. The service
validates that invariant using declared available measurements.

The runtime constructs and starts/stops the service and passes only its typed, fresh
snapshots to `EnvironmentalInterpreter`.

The interpreter publishes confirmed semantic state and independent overlay intents to
`BehaviorEngine`; raw readings never reach `EyeRenderer`, `LEDRingController` or Vision.

Future compatible environmental providers reuse this boundary; other kinds of sensors
need their own typed measurements rather than forcing them into
temperature/humidity/pressure fields.

### Worker lifecycle and freshness

One daemon worker serializes all provider calls; disabled means no worker, provider
construction, vendor imports or I2C open. It retries initialization after failures,
retains no history and exposes only finite valid current measurements.

Snapshot metadata includes:

- Active sensor type.
- Available measurements.
- Availability/status.
- UTC last success.
- Monotonic age.
- Sanitized error type.

Failed or stale snapshots contain no current values.

A blocked native call cannot stop the render loop or grow a work queue; stop signals the
worker and awaits at most one second.

The owner closes the bus when I/O returns; process exit releases it if stuck. No
concurrent bus close is used.

### Configuration and administration access

All settings are restart-only under existing lifecycle classification. Save and Reload
do not restart any subsystem. Only an explicitly requested PHOS restart reinitializes
hardware.

The Sensors page reads in-memory state via shared services, never imports a driver or
infers availability from saved enabled/address values. The existing authenticated
process channel carries the snapshot; no new API or IPC operation is exposed. See
ADR-022, [hardware](hardware.md#bme280) and [configuration
migration](development.md#environmental-configuration-migration).

### Controller-backed ENS160 and AHT21

Controller telemetry enters the shared status and interpretation services:

```mermaid
flowchart LR
  esp[ESP32 telemetry] --> controller[ControllerService]
  controller --> callbacks[Runtime lifecycle callbacks] --> air[AirQualitySensorService]
  air --> status[Runtime / application status] --> clients[WebUI / MCP]
  style air fill:#2e1065,stroke:#a855f7,color:#fff
```

The controller acquires measurements and publishes phos-hw v1 records; Pi services own
freshness, validity, controller-generation isolation and environmental interpretation.
ENS160 provides estimated eCO2, TVOC and AQI-UBA; AHT21 temperature and humidity are
independently fresh. Retiring a session immediately makes controller readings unusable
until fresh telemetry arrives.

### MPU-6050 IMU extension

The IMU provider feeds the existing sensor-service and runtime status boundary:

```mermaid
flowchart LR
  provider[hardware.MPU6050Provider] --> service[IMUSensorService]
  service --> status[PhosRuntime.sensor_status]
  style service fill:#2e1065,stroke:#a855f7,color:#fff
```

This path uses the same worker, freshness, retry and authenticated status boundary as
the other sensors.

- `IMUReading` holds acceleration X/Y/Z in m/s² and angular velocity X/Y/Z in °/s.

- The hardware adapter alone knows MPU-6050 registers and smbus2; Web Admin only reads
  snapshots and edits canonical `sensors.imu` settings.

- No `BehaviorEngine`, `EyeRenderer` or Vision component receives IMU readings.

The adapter applies fixed ±2 g / ±250 °/s scale factors only; offset calibration, fusion
and orientation are outside this capability.

### Motion interpretation extension

Motion interpretation converts readings into provider-neutral semantic observations:

```mermaid
flowchart LR
  reading[IMUReading] --> interpreter[MotionInterpreter] --> motion[MotionState / MotionEvent]
  style interpreter fill:#2e1065,stroke:#a855f7,color:#fff
```

Motion interpretation is a pure, provider-neutral layer between IMU state and its
semantic consumers. It never accesses the MPU-6050, GPIO, Web handlers, Vision,
`EyeRenderer` or `BehaviorEngine`. The IMU worker supplies timestamped samples and
exposes only the current interpreted state and last event through the existing status
boundary. `STILL`, `MOVING`, four tilt directions, `SHAKE` and `IMPACT` are semantic
observations consumed by `BehaviorEngine` through the event bus.

Motion settings reload into the interpreter without reopening the I2C provider.

### IMU visual behavior extension

The IMU service publishes stable semantic state changes to Core's local event bus.
`BehaviorEngine` consumes them and overlays visual intent onto `FaceState`; the renderer
still receives no IMU object or event.

#### Motion-specific visual intent

In `IDLE`:

- **`MOVING`** — Recenters the pupils, opens the eyes to 1.16 and gives it 0.63 reaction
  strength.

**Tilt reactions** move pupils in the corresponding visual direction at 86% of the
normalized safe range. All have 0.56 strength and the curious accent.

| Tilt | Eye opening and asymmetry |
| --- | --- |
| Horizontal | Opening 1.12; mirrored 0.18 signed eye asymmetry. Left tilt opens the left eye and closes the right; right tilt reverses it. |
| Forward | Equal eyes, opening to 1.23. |
| Back | Equal eyes, closing to 0.82. |

- **`SHAKE`** — Is a 1.18-open, 0.88-strength surprised alert.
- **`IMPACT`** — Is the stronger 1.25-open, 1.0-strength alert.

Both hold for 55% of their configured duration then decay smoothly.

#### State priority and resumption

`ERROR`, `SLEEPING`, `LISTENING`, `THINKING` and `SPEAKING` override IMU intent; motion
then resumes only when Core returns to IDLE. Motion overlays take priority over
lower-priority Vision expression reactions in IDLE, without changing the user-selected
iris theme.

### Local Admin Access

**`NetworkInfoService`** is parent-process runtime state, not configuration: it selects
the first non-loopback, non-link-local IPv4 address and refreshes its bounded cached
result every 30 seconds.

Web Admin obtains that read model only through `RemoteApplicationService` and lifecycle.

#### Physical Admin overlay

The physical Admin overlay is a separate top display layer. It receives only the
effective LAN Admin URL and a cached `qrcode` matrix; Tk renders individual square
canvas modules, so Pillow and image resampling are unnecessary. A top-right double-tap
20% of the display opens it, and a tap closes it. This never changes `FaceState`,
`RobotState`, or `BehaviorEngine` state.
