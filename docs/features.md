---
hide:
  - navigation
  - toc
classes:
  - phos-home
---

# Features

<div class="phos-page-intro" markdown>

<span class="phos-kicker">CAPABILITY MAP</span>

Perceive physical context, react through compatible external clients, and
express agent activity through a local interface. Capabilities depend on the
hardware and providers enabled in your build. Start with
[connecting an agent](developer-quickstart.md), then use the subsystem guides
below for setup and limits.

</div>

## Agent-facing capabilities

| Capability | Practical value | Details |
| --- | --- | --- |
| Physical awareness | Read measurements and semantic context without managing device drivers. | [MCP resources and tools](mcp-overview.md#ask-phos-or-request-an-action) |
| Event-driven interaction | Receive selected semantic-change cues, then read current context. | [Subscriptions and lifecycle](mcp-overview.md#react-to-changes) |
| Physical expression | Request supported expressions, temporary thinking visuals, speech, and available LED signals. | [MCP tool catalog](mcp-overview.md#tool-and-resource-catalog) |
| Hardware extensibility | Connect supported Pi peripherals or protocol-compatible controllers. | [Hardware architecture](hardware-overview.md) |
| Agent independence and multiple identities | Give separate agents credentials and permission profiles on a shared runtime. | [Controlled access](mcp-overview.md#multiple-agents-and-controlled-access) |

## Local runtime capabilities


<div class="phos-grid" markdown>

<article class="phos-card" markdown>
<span class="phos-status">IMPLEMENTED</span>
### Expressive animated eyes
An independent renderer provides smooth eye animation, blink and idle gaze,
semantic visual accents, selectable iris themes and optional local camera
picture-in-picture.
</article>

<article class="phos-card" markdown>
<span class="phos-status">IMPLEMENTED</span>
### Face tracking and expression observations
The Pi Camera pipeline selects and tracks a face, maps its position to bounded
pupil gaze, and can use local ONNX or optional AWS expression providers. Stable
observations—not raw labels—reach behavior.
</article>

<article class="phos-card" markdown>
<span class="phos-status">IMPLEMENTED</span>
### Presence and attention
Confirmed face presence produces edge-triggered enter/leave semantics. A
provider-neutral attention state acquires, tracks, holds loss and returns to
idle, driving semantic gaze and optional configured LED sweeps. It is not face
recognition, biometric identity, eye-contact, multi-person arbitration, or
voice engagement.
</article>

<article class="phos-card" markdown>
<span class="phos-status phos-status--optional">OPTIONAL HARDWARE</span>
### Motion awareness
Optional MPU-6050/GY-521 support turns six-axis readings into confirmed STILL,
MOVING, directional tilt, SHAKE and IMPACT observations with temporary,
directional eye and LED intent.
</article>

<article class="phos-card" markdown>
<span class="phos-status phos-status--optional">OPTIONAL HARDWARE</span>
### Environmental awareness
Optional BME280/BMP280 and controller-backed ENS160/AHT21 services feed `EnvironmentalInterpreter`.
Confirmed temperature and air-quality context can drive semantic eye/LED
behavior and additive sweat, snow/ice and haze overlays.
</article>

<article class="phos-card" markdown>
<span class="phos-status">IMPLEMENTED</span>
### Visual source arbitration
Manual, environmental or PHOS-state persistent visual sources establish a
baseline. IMU and higher-priority robot events are transient overrides; the
resolved persistent source returns afterward.
</article>

<article class="phos-card" markdown>
<span class="phos-status">IMPLEMENTED</span>
### Voice & Speech
An explicit microphone session uses energy VAD and a provider-neutral STT
boundary: Local/Vosk is the default and Cartesia Ink is optional batch STT.
Local/Piper, Cartesia, and ElevenLabs TTS integration use `AudioOutputProvider`;
supported Voice & Speech provider/output changes activate live and persist.
</article>

<article class="phos-card" markdown>
<span class="phos-status">IMPLEMENTED</span>
### Face-display touch telemetry
Completed taps, double-taps, long presses and horizontal swipes are recognized locally as
semantic input. The last valid gesture is observable through the shared runtime
snapshot, Remote API and read-only Web Admin diagnostics; raw pointer events
are never exposed.
</article>

<article class="phos-card" markdown>
<span class="phos-status phos-status--optional">OPTIONAL HARDWARE</span>
### WS2812B visual feedback
An optional WS2812B ring connects to the ESP32 controller. PHOS sends resolved
`phos-hw` v1 semantic LED commands over an active serial connection; firmware
drives the ring after advertising LED capability.
</article>

<article class="phos-card" markdown>
<span class="phos-status">IMPLEMENTED</span>
### Web Admin and canonical configuration
An authenticated trusted-LAN editor saves the active validated configuration
source, normally `config/phos.yaml`. Voice & Speech shows configured and active
provider state and applies supported STT/TTS/audio changes live; other subsystem
settings retain their documented reload or restart policy.
</article>

<article class="phos-card" markdown>
<span class="phos-status">IMPLEMENTED</span>
### Remote API & OpenAPI
[Explore the Remote API](remote-api.md)

PHOS exposes a versioned local REST API for robot status, health, environmental and motion state, capabilities, semantic expressions, visual source control and face overlays. The API includes OpenAPI 3.x documentation, Swagger UI and structured JSON errors, and reuses the same application-service boundary as the Web Admin.

</article>

</div>

## Explicitly planned, not delivered

<div class="phos-callout" markdown>

Conversational LLM operation, wake-word and continuous listening, and Home
Assistant are deferred. The supported deterministic English/Italian
[voice commands](voice-commands.md) are available. MCP
tools, resources, and resource-change subscriptions are implemented; see
[MCP & AI Agent Integration](mcp-overview.md) for their bounded semantics.
Existing interfaces or scaffolding are not evidence of an end-to-end delivered
feature. [Read the roadmap](roadmap.md).

</div>
