<div class="phos-use-cases-hero" markdown>

# What you can build with PHOS

Use PHOS as the physical interface for your agent or application. These examples
combine implemented PHOS capabilities with external logic you provide; they are
not bundled coding-agent, personal-assistant, or automation workflows.

</div>

<div class="phos-use-case-grid" markdown>

## Agent activity at a glance

An agent requests `phos_set_state` with a bounded `thinking` duration while it
works and releases its own visual lease with `idle`. Supported expressions add
non-verbal feedback. People can notice activity without keeping chat open.
Local robot states retain priority over the thinking visual.

**Requires:** a running display and an action-capable agent profile.
[MCP visual-state contract](mcp.md).

## Coding-agent feedback

Your integration maps a completed task or request for attention to
`phos_set_expression`, a supported `phos_signal_user` intent, or a short
`phos_speak` request. The coding tool detects task progress; PHOS executes the
permitted physical request.

**Requires:** `standard` access; signals require available controller-backed
LEDs, and speech requires configured TTS and playback.
[Connect an MCP client](developer-quickstart.md).

## Presence-aware interactions

A compatible client subscribes to `phos://physical-context`, receives an update
cue after a confirmed presence transition, and reads current presence and
attention. Your host decides whether to invoke agent logic and request a reply.
PHOS supplies anonymous context, not personal identity or proof of engagement.

**Requires:** enabled camera-based presence, read access, and a client supporting
`subscriptions/listen` for automatic change cues. On-demand reads work without
subscriptions. [Resource update flow](mcp-overview.md#react-to-changes).

## Environmental explanations

An agent reads available temperature, humidity, pressure, or air-quality
indicators and explains relevant observations. For a time-window question it
can assess evidence and query bounded retained measurements. The external
agent interprets the evidence; PHOS does not diagnose causes or guarantee
measurement accuracy. ENS160 eCO2 is an estimated equivalent, not direct CO2.

**Requires:** configured supported sensors. Historical assessment and queries
require `read_physical_history`, included in `standard`; retained data is
RAM-only, bounded, and lost on restart. [Physical evidence contract](mcp.md#physical-context-intelligence-v01-agent-composition-contract).

## New hardware behind a stable agent interface

A maker implements `phos-hw` v1 on a controller, advertises capabilities, and
pushes accepted observations to the Pi. PHOS maps them into shared services so
agents consume physical context without controller-specific drivers. New
measurement types still require PHOS mappings; arbitrary controller fields do
not automatically become MCP resources.

**Requires:** protocol-compatible firmware, serial setup, and supported PHOS
sensor mappings. ESP32 is the reference implementation.
[Hardware integration guide](hardware-controller.md).

## Multiple agents, one physical interface

Give a context-reading application an `observer` identity and a feedback agent a
separate `standard` identity. Their requests use the same PHOS runtime with
independent credentials and permission checks. Your applications coordinate
which activity to present; credentials do not reserve separate physical outputs.

**Requires:** registered agents with issued credentials and compatible clients.
[Multi-agent access and output limits](mcp-overview.md#multiple-agents-and-controlled-access).

## Local operation and personalization

Run the eyes, supported touch reactions, configured sensors, and finite voice
sessions without an external agent. Use Web Admin or REST for local operation;
choose skins and colors to personalize the interface.

**Requires:** the local runtime and hardware/providers for each enabled feature.
[Standalone operation](discover.md#standalone-operation) · [Visual system](visual-system.md).

</div>
