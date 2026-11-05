<div class="phos-use-cases-hero" markdown>

# Use cases

These examples describe implemented interfaces and optional capabilities. A
particular workflow still requires its listed hardware, provider, and
configuration to be available.

</div>

<div class="phos-flow phos-use-case-flow" aria-label="PHOS agent integration flow">
  <span>AI Agent or person</span><b>→</b><span>PHOS</span><b>→</b><span>Safe physical interaction</span>
</div>

<div class="phos-use-case-grid" markdown>

## AI Agent with a physical presence

**Goal:** let an external AI Agent speak, express itself, and read semantic
physical context.

**What PHOS does:** exposes MCP tools/resources while retaining permission and
hardware ownership. **AI Agent required:** yes. **Requires:** enabled MCP and
an authenticated configured agent.

Example: an agent reads `phos://status`, asks PHOS to speak a concise response,
then requests a supported expression. Start with
[MCP & AI Agent Integration](mcp-overview.md).

## Interactive desktop companion

**Goal:** run an expressive local robot without external-agent orchestration.

**What PHOS does:** renders visual state, accepts supported touch input, and
can use configured voice/sensor services. **AI Agent required:** no.
**Requires:** display; other capabilities are optional. See [Visual system](visual-system.md)
and [Voice](voice.md).

## Environmental awareness

**Goal:** inspect temperature, humidity where available, pressure, and
air-quality estimates.

**What PHOS does:** publishes fresh measurements and bounded semantic context.
**AI Agent required:** no. **Requires:** configured supported sensors.

Example: use the dashboard or REST environment endpoint; a connected agent may
read the same semantic context through MCP. See [Hardware](hardware.md).

## Presence-aware interaction

**Goal:** respond when a person is in the camera view.

**What PHOS does:** derives presence and attention from optional face detection.
**AI Agent required:** no, but an agent can consume the observation.
**Requires:** camera and enabled vision pipeline. See [Vision](vision.md).

## Developer integration

**Goal:** integrate an application without direct hardware access.

**What PHOS does:** provides authenticated semantic REST controls and MCP.
**AI Agent required:** no for REST; optional for MCP. **Requires:** trusted LAN
deployment and configured administration access. See [Remote API](remote-api.md).

</div>
