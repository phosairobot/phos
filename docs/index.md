---
hide:
  - toc
classes:
  - phos-home
---

<div class="phos-landing-hero phos-purple-hero phos-entrance" markdown>

![PHOS logo](assets/images/phos.png){ .phos-landing-logo width="128" height="128" }

# Build a <span class="phos-gradient-accent">physical presence</span> for your AI.

<p class="phos-hero-lede">PHOS is an open-source Raspberry Pi robot with expressive eyes, optional vision, voice, and sensors. Build a local companion or give an external AI agent permission-controlled physical context through MCP.</p>

<div class="phos-actions" markdown>

[Get Started](build-phos.md){ .md-button .md-button--primary }
[Explore Architecture](architecture.md){ .md-button }

</div>

</div>

Start with the Raspberry Pi 3 and 5-inch 800×600 display baseline. Follow the
[User Guide](build-phos.md) to install PHOS, configure your hardware, and try
the capabilities you need. Camera, voice, sensors, external controllers, and
Web Admin are opt-in; each guide explains its prerequisites.

## How PHOS works

```mermaid
flowchart TD
    standalone["Standalone runtime<br/>Local touch and configured behaviors"]
    agent["AI agent or remote client"]
    mcp["MCP<br/>Identity and permission profiles"]
    rest["REST API<br/>Authenticated semantic requests"]
    services["PHOS application services<br/>Validation and bounded actions"]
    runtime["Local runtime<br/>State and behavior"]
    hardware["Display, voice, vision and sensors<br/>Optional ESP32 controller"]
    standalone --> runtime
    agent <-->|Structured context and requests| mcp
    agent <-->|Telemetry and controls| rest
    mcp <--> services
    rest <--> services
    services <--> runtime
    runtime <--> hardware
    style standalone fill:#2e1065,stroke:#a855f7,color:#fff
    style agent fill:#2e1065,stroke:#a855f7,color:#fff
    style services fill:#2e1065,stroke:#a855f7,color:#fff
    style runtime fill:#1e293b,stroke:#8b5cf6,color:#fff
    style hardware fill:#1e293b,stroke:#8b5cf6,color:#fff
```

**Standalone:** run the animated face, supported local touch reactions, and
configured sensor behaviors on the Pi. Add voice capture, speech output, or
local administration when ready. These paths do not require an external agent.

**AI agent-connected:** connect a compatible client through Streamable HTTP
MCP. The agent reads structured observations and requests permitted expression,
speech, state, or signal actions. PHOS authenticates requests, applies its
permission profiles, and owns hardware execution. Reasoning stays with the
external agent; end-to-end conversational LLM operation remains future work.

Both modes use the same runtime and application services. You can start locally
and [connect an agent later](developer-quickstart.md).

## Core capabilities

<div class="grid cards phos-purple-cards phos-entrance" markdown>

-   :material-connection: **MCP integration**

    ---

    Share semantic physical context through permission-checked tools and
    Resources. Read bounded RAM-only history or request an authorized camera
    snapshot, without continuous video streaming.

    [Connect an MCP client](mcp-overview.md)

-   :material-api: **REST API**

    ---

    Read status, health, and available sensor data; request supported semantic
    actions through the authenticated API. Explore the supplied OpenAPI and
    Swagger UI documentation.

    [Use the REST API](remote-api.md)

-   :material-cog-outline: **Web administration**

    ---

    Inspect the live status dashboard, configure skins and settings, and manage
    agent access. Apply supported changes through validated live activation,
    reload, or restart paths.

    [Set up Web Admin](web-admin-overview.md)

-   :material-eye-outline: **Vision & presence**

    ---

    Enable Picamera2 face tracking, presence, attention, and expression
    observations. Optional on-demand NanoDet supplies object labels and
    confidence; it requires a separately installed model.

    [Explore vision and expressions](vision-expressions.md)

-   :material-account-voice: **Voice: STT & TTS**

    ---

    Capture bounded speech sessions with Vosk or Cartesia STT. Speak through
    Piper, Cartesia, or ElevenLabs TTS and the separate audio playback path.
    Configure explicit voice command sessions with the supported English/Italian
    vocabulary. Audio hardware, models, and provider setup are required.

    [Configure voice and speech](voice.md)

-   :material-chip: **Sensors & ESP32 hardware**

    ---

    Add BME280/BMP280 environmental readings, MPU6050 motion, or controller-backed
    ENS160/AHT21 telemetry. ESP32 integration uses the serial `phos-hw` v1 JSONL
    protocol and requires matching external firmware.

    [Plan your Hardware BOM](hardware-bom.md) · [Check wiring](hardware.md)

</div>

## Choose your path

| Journey | Start here | Next steps |
| --- | --- | --- |
| **Makers & users** | Choose parts from the [Hardware BOM](hardware-bom.md), [build PHOS](build-phos.md), and [install the runtime](installation.md). | Check [hardware](hardware-overview.md), configure [Web Admin](web-administration.md), then connect through [MCP](mcp-overview.md) or [REST](remote-api.md). |
| **Developers & contributors** | Read the [architecture](architecture.md), [decisions](decisions.md), and [testing guide](development.md). | Explore [MCP specifications](mcp.md), the [controller protocol](hardware-controller.md), [provider boundaries](architecture.md#core-principles), and [skin extensions](appearance/creating-skins.md); follow the [contribution guide](contributing.md). |

## Watch PHOS in Action

See PHOS working with external AI agents, real sensor history, browser controls,
and expressive vision. These demonstrations come from the official PHOS channel.

<div class="video-grid">
  <figure class="video-card">
    <div class="video-wrapper">
      <iframe src="https://www.youtube-nocookie.com/embed/KMd93sneYoE?rel=0" title="Giving AI Agents Access to Real Sensor History | Raspberry Pi + ESP32 + MCP" loading="lazy" referrerpolicy="strict-origin-when-cross-origin" allow="encrypted-media; picture-in-picture; fullscreen" allowfullscreen></iframe>
    </div>
    <figcaption>Giving AI Agents Access to Real Sensor History | Raspberry Pi + ESP32 + MCP</figcaption>
  </figure>
  <figure class="video-card">
    <div class="video-wrapper">
      <iframe src="https://www.youtube-nocookie.com/embed/29Qx_1gh-ZM?rel=0" title="What If Every AI Agent Had Its Own Physical Identity?" loading="lazy" referrerpolicy="strict-origin-when-cross-origin" allow="encrypted-media; picture-in-picture; fullscreen" allowfullscreen></iframe>
    </div>
    <figcaption>What If Every AI Agent Had Its Own Physical Identity?</figcaption>
  </figure>
  <figure class="video-card">
    <div class="video-wrapper">
      <iframe src="https://www.youtube-nocookie.com/embed/KeopCfsvkS4?rel=0" title="PHOS admin UI" loading="lazy" referrerpolicy="strict-origin-when-cross-origin" allow="encrypted-media; picture-in-picture; fullscreen" allowfullscreen></iframe>
    </div>
    <figcaption>PHOS admin UI</figcaption>
  </figure>
  <figure class="video-card">
    <div class="video-wrapper">
      <iframe src="https://www.youtube-nocookie.com/embed/sYE029codNQ?rel=0" title="The eyes are part of how PHOS sees, reacts and communicates: Vision, expressions and visual feedback" loading="lazy" referrerpolicy="strict-origin-when-cross-origin" allow="encrypted-media; picture-in-picture; fullscreen" allowfullscreen></iframe>
    </div>
    <figcaption>The eyes are part of how PHOS sees, reacts and communicates: Vision, expressions and visual feedback</figcaption>
  </figure>
</div>

[Subscribe on YouTube (@phosairobot)](https://www.youtube.com/@phosairobot){ .md-button }

## Follow PHOS and share your build

Find source code, report issues, and share hardware experiences on
[GitHub](https://github.com/phosairobot/phos). A star helps other makers discover
the project. For more development updates, follow
[Instagram](https://www.instagram.com/phosairobot/) and
[X](https://x.com/phosairobot).

For implementation details and planned work, see the
[current implementation state](current-state.md), [roadmap](roadmap.md), and
[release records](releases.md). Verify wiring, power, device compatibility, and
calibration for your own build before enabling optional hardware.
