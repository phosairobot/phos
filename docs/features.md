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

PHOS 1.4.0 is the current source version. The capabilities below are implemented
in the repository; optional hardware paths still require target-Pi acceptance.
See the [current implementation handoff](current-state.md) and
[roadmap](roadmap.md) for scope and physical-validation limits.

</div>

## Implemented

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
Optional BME280/BMP280 and CCS811 services feed `EnvironmentalInterpreter`.
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
Completed taps, long presses and horizontal swipes are recognized locally as
semantic input. The last valid gesture is observable through the shared runtime
snapshot, Remote API and read-only Web Admin diagnostics; raw pointer events
are never exposed.
</article>

<article class="phos-card" markdown>
<span class="phos-status phos-status--optional">OPTIONAL HARDWARE</span>
### WS2812B visual feedback
An optional LED ring consumes provider-neutral `FaceState` intent through a
low-rate controller, including semantic colors and directional motion fills.
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

Transcript-to-intent/command handling, conversational LLM operation, wake-word
and continuous listening, Home Assistant, and MCP are explicitly deferred.
Existing interfaces or scaffolding are not evidence of an end-to-end delivered
feature. [Read the roadmap](roadmap.md).

</div>
