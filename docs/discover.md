<div class="phos-discover-hero" markdown>

# Discover PHOS

**Bringing AI agents into the physical world.**

**Eyes, voice and senses for AI agents.** PHOS is an open-source, bidirectional
physical I/O layer for AI agents, powered by the Model Context Protocol.

</div>

## Give your agent a physical interface

An agent can read selected physical context and communicate through a local
expressive face, configured speech, and supported LED signals. People can notice
activity or a request for attention beyond a chat window. PHOS handles devices,
context, permissions, and execution; your agent supplies reasoning and decisions.

**Bring your own agent. Bring your own hardware.** Connect through a compatible
MCP client. Use supported Raspberry Pi peripherals or an external controller
implementing the `phos-hw` v1 protocol. The runtime targets Raspberry Pi 3;
controller extensibility does not imply support for every board or sensor.

## Perceive, react, express

<div class="phos-discover-grid" markdown>

### Perceive physical context

Read available temperature, humidity, pressure, air-quality indicators, motion,
anonymous presence, attention, and observed expressions. Measurements are
accompanied by availability and semantic context where implemented. Authorized
camera snapshots and optional on-demand object labels are separate interfaces.
See [core capabilities](features.md).

### React to meaningful changes

Subscription-capable clients receive resource change cues and read updated
context. PHOS reports selected semantic transitions rather than pushing every
sensor sample. The external host or agent decides whether to reason or act;
notifications are best-effort and have no replay. See [MCP integration](mcp-overview.md).

### Express through a physical presence

Animated eyes combine blink, local gaze behavior, expressions, and state
visuals. Agents can request supported expressions and temporary thinking
visuals, speak through configured TTS, or signal through available controller
LEDs. There is no direct MCP gaze or raw LED control. See [how PHOS works](how-phos-works.md).

### Share controlled access

Register distinct agent identities and credentials with `observer` or `standard`
profiles. PHOS checks resource reads and tool operations through application
services. Agents share the physical outputs; external applications coordinate
their own tasks. See [agent access](mcp-overview.md#multiple-agents-and-controlled-access).

</div>

## Standalone operation

PHOS can also operate locally without an external agent. Its runtime renders
eyes, responds to supported touch gestures, manages robot state, and applies
configured sensor reactions. Optional voice services provide bounded microphone
sessions, STT, TTS/playback, and the deterministic English/Italian
[voice command catalog](voice-commands.md). End-to-end conversational LLM
operation, wake words, and continuous listening remain outside the delivered
local voice path.

Use this mode for a local expressive display, hardware checks, or
personalization. Both standalone and agent-connected operation require the local
runtime. [Installation](installation.md) explains setup;
[Web Admin](web-administration.md) provides configuration and operational controls,
and [skins](appearance/creating-skins.md) personalize the face.

## See PHOS and choose a path

Watch the [official demonstrations on the homepage](index.md#watch-phos-in-action)
or visit the [PHOS YouTube channel](https://www.youtube.com/@phosairobot).

- [Connect your first MCP client](developer-quickstart.md)
- [Explore use cases](use-cases.md)
- [Build PHOS](build-phos.md)
- [Understand hardware integration](hardware-overview.md)
- [Build integrations and contribute](contributing.md)
