---
hide:
  - toc
classes:
  - phos-home
---

<div class="phos-landing-hero" markdown>

![PHOS logo](assets/images/phos.png){ .phos-landing-logo width="128" height="128" }

# Build a physical presence for your AI.

<p class="phos-hero-lede">PHOS is an open-source Raspberry Pi robot with expressive eyes, optional vision, voice, and sensors. Build a local companion or give an external AI agent permission-controlled physical context through MCP.</p>

<div class="phos-actions" markdown>

[Get Started](build-phos.md){ .md-button .md-button--primary }
[Developer Architecture](architecture.md){ .md-button }

</div>

</div>

Start with the Raspberry Pi 3 and 5-inch 800×600 display baseline. Follow the
[User Guide](build-phos.md) to install PHOS, configure your hardware, and try
the capabilities you need. Camera, voice, sensors, external controllers, and
Web Admin are opt-in; each guide explains its prerequisites.

## Standalone or connected to an AI agent

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

<div class="grid cards" markdown>

-   :material-connection: **MCP for AI agents**

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

-   :material-cog-outline: **Web Admin**

    ---

    Configure PHOS in the browser, inspect runtime status, manage agent access,
    and apply supported changes through validated reload or restart paths.

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
    Audio hardware, models, and provider setup are required.

    [Configure voice and speech](voice.md)

-   :material-chip: **Sensors & ESP32 hardware**

    ---

    Add BME280/BMP280 environmental readings, MPU6050 motion, or controller-backed
    ENS160/AHT21 telemetry. ESP32 integration uses the serial `phos-hw` v1 JSONL
    protocol and requires matching external firmware.

    [Choose hardware and wiring](hardware.md)

</div>

## Choose your path

<div class="grid cards" markdown>

-   **Users & makers**

    ---

    1. Check the [hardware requirements](hardware-overview.md).
    2. Follow the [build guide](build-phos.md) and [installation steps](installation.md).
    3. Configure [Web Admin](web-administration.md) and enable optional capabilities.
    4. Try [MCP integration](mcp-overview.md) or the [REST API](remote-api.md).

-   **Developers & contributors**

    ---

    1. Understand the [architecture](architecture.md) and [accepted decisions](decisions.md).
    2. Set up [development and testing](development.md).
    3. Explore [MCP contracts](mcp.md), the [controller protocol](hardware-controller.md),
       or [appearance extensions](appearance/creating-skins.md).
    4. Read the [contribution guide](contributing.md) before proposing a change.

</div>

## Follow PHOS and share your build

Find source code, report issues, and share hardware experiences on
[GitHub](https://github.com/phosairobot/phos). A star helps other makers discover
the project. Visit the [PHOS YouTube channel](https://www.youtube.com/@phosairobot)
for published demonstrations and development updates, or follow
[Instagram](https://www.instagram.com/phosairobot/) and
[X](https://x.com/phosairobot).

For implementation details and planned work, see the
[current implementation state](current-state.md), [roadmap](roadmap.md), and
[release records](releases.md). Verify wiring, power, device compatibility, and
calibration for your own build before enabling optional hardware.
