---
hide:
  - toc
classes:
  - phos-home
---

<div class="phos-landing-hero" markdown>

![PHOS logo](assets/images/phos.png){ .phos-landing-logo width="128" height="128" }

# PHOS — A Physical Companion for AI Agents

<p class="phos-hero-lede">An open-source Raspberry Pi robot platform that can perceive selected aspects of its surroundings, express visual state, speak, and carry out controlled actions in the real world.</p>

<div class="phos-actions" markdown>

[Discover PHOS](discover.md){ .md-button .md-button--primary }
[Getting started](installation-overview.md){ .md-button }
[Connect with MCP](mcp-overview.md){ .md-button }

</div>

</div>

PHOS combines a local display, sensors, voice, vision, touch, Web
Administration, REST APIs, and an MCP interface. It is useful in two
complementary ways.

<div class="phos-mode-grid" markdown>

<section class="phos-mode-card" markdown>

## Standalone PHOS

PHOS does not require an external AI Agent to run its implemented capabilities.
Its built-in runtime manages animated eyes, robot state, visual behavior,
optional sensors, touch interactions, supported voice sessions, and local Web
Administration. It is not documented as a built-in general-purpose
conversational LLM.

[Explore standalone capabilities](discover.md){ .md-button }

</section>

<section class="phos-mode-card" markdown>

## AI Agent connected

An external AI Agent can connect through MCP to read semantic physical context
and request safe actions such as speech, expression changes, sleep/wake, and a
user signal. PHOS retains ownership of hardware, permission checks, behavior,
and physical execution.

[Read the MCP guide](mcp-overview.md){ .md-button }

</section>

</div>

<div class="phos-flow phos-home-flow" aria-label="Agent integration flow">
  <span>AI Agent</span><b>→</b><span>MCP / REST</span><b>→</b><span>PHOS services</span><b>→</b><span>Physical runtime</span>
</div>

## What PHOS can do

<div class="phos-capability-grid" markdown>

<div class="phos-capability-card" markdown>

:material-eye-outline:{ .phos-capability-icon }

### Animated eyes & expressions

Visual state, gaze, blink behavior, expressions, and local overlays.

</div>

<div class="phos-capability-card" markdown>

:material-microphone-outline:{ .phos-capability-icon }

### Voice

Bounded voice sessions with configured TTS/STT providers and hardware.

</div>

<div class="phos-capability-card" markdown>

:material-camera-outline:{ .phos-capability-icon }

### Vision & presence

Optional camera-based presence and attention observations.

</div>

<div class="phos-capability-card" markdown>

:material-thermometer:{ .phos-capability-icon }

### Environmental sensors

Optional environmental, air-quality, motion, and touch input.

</div>

<div class="phos-capability-card" markdown>

:material-memory:{ .phos-capability-icon }

### External Hardware Controller

Connect PHOS to ESP32 or other compatible microcontrollers using the `phos-hw`
v1 protocol for sensor data, motion events and semantic hardware control.

[Explore the Hardware Protocol](hardware-controller.md){ .md-button }

</div>

<div class="phos-capability-card" markdown>

:material-connection:{ .phos-capability-icon }

### MCP integration

Authenticated semantic tools and resources for external AI Agents.

</div>

<div class="phos-capability-card" markdown>

:material-api:{ .phos-capability-icon }

### REST API

Authenticated application controls without direct hardware access.

</div>

<div class="phos-capability-card" markdown>

:material-monitor-dashboard:{ .phos-capability-icon }

### Web Administration

Local configuration and operational controls for a running robot.

</div>

</div>

Hardware and providers are optional: a software capability being implemented
does not imply every physical installation has that device enabled or accepted.

## Watch PHOS in Action

<div class="phos-media-callout" markdown>

:fontawesome-brands-youtube:{ .phos-media-icon }

Explore videos, demonstrations and development updates from the PHOS project.

[Visit the PHOS YouTube Channel](https://www.youtube.com/@phosairobot){ .md-button .md-button--primary }

</div>

## Start here

<div class="phos-actions" markdown>

[Discover PHOS](discover.md){ .md-button .md-button--primary }
[Getting started](installation-overview.md){ .md-button }
[Connect an AI Agent with MCP](mcp-overview.md){ .md-button }
[REST API](remote-api.md){ .md-button }

</div>

## Practical paths

| If you want to… | Start with… |
| --- | --- |
| Meet PHOS and its capabilities | [Discover PHOS](discover.md) |
| Build a physical companion | [Hardware & sensors](hardware-overview.md) and [installation](installation.md) |
| Connect an external agent | [MCP & AI Agent Integration](mcp-overview.md) |
| Automate PHOS from software | [Remote API](remote-api.md) |
| Configure a running robot | [Web Administration](web-administration.md) |
| Contribute or extend PHOS | [Architecture](architecture.md) and [development](development.md) |

See the [GitHub repository](https://github.com/phosairobot/phos) for source,
issues, and contribution context. The [roadmap](roadmap.md) separates current
implementation from future work and physical acceptance.
