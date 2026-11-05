<div class="phos-discover-hero" markdown>

# Discover PHOS

PHOS is a physical companion platform for AI Agents and people. It combines a
small expressive display with optional sensing, voice, vision, touch, and
network integration on a Raspberry Pi.

</div>

## A companion, not only an MCP server

In standalone use, PHOS can render eyes and expressions, maintain robot state,
respond to supported touch and sensor observations, and expose local Web Admin
and REST controls. These capabilities do not depend on an external AI Agent.

When connected to an AI Agent, MCP gives the agent a constrained semantic view
of PHOS and a controlled action surface. The agent reasons; PHOS owns the
hardware and applies its permission and runtime boundaries.

## Capabilities by outcome

<div class="phos-discover-grid" markdown>

### Visual expression

PHOS renders animated eyes, blink and gaze behavior, supported expressions,
robot-state visuals, and transient local overlays. See [Visual system](visual-system.md).

### Speech and interaction

With configured hardware/providers, PHOS captures bounded voice sessions and
can synthesize short speech. Local Vosk/Piper and optional Cartesia/ElevenLabs
providers are documented in [Voice](voice.md). Touch gestures are local input,
including top-left voice control and top-right Admin Access double-tap.

### Physical awareness

Optional BME280/BMP280, CCS811, and MPU-6050 adapters provide temperature,
humidity where supported, pressure, air-quality estimates, and motion data.
PHOS distinguishes measurements from semantic interpretations; CCS811 eCO2 is
an estimate, not a direct CO2 measurement. See [Hardware & sensors](hardware-overview.md).

### Vision and human presence

The optional camera pipeline can detect faces, derive presence/attention, and
produce a bounded visible-expression observation. It does not identify people
or claim to know their emotions. See [Vision](vision.md).

### Agent and application integration

MCP clients can discover semantic tools/resources; REST clients can read status
and request supported actions. Read [MCP & AI Agent Integration](mcp-overview.md)
or the [Remote API](remote-api.md).

</div>

## Watch PHOS in Action

<div class="phos-media-callout" markdown>

:fontawesome-brands-youtube:{ .phos-media-icon }

Explore videos, demonstrations and development updates from the PHOS project.

[Visit the PHOS YouTube Channel](https://www.youtube.com/@phosairobot){ .md-button .md-button--primary }

</div>

## Next steps

- [Install PHOS](installation.md)
- [Explore use cases](use-cases.md)
- [Configure Web Admin](web-administration.md)
- [Understand the architecture](architecture.md)
