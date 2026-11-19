# PHOS

**An open-source Raspberry Pi robot that gives AI agents safe, structured physical context.**

PHOS combines an expressive display, camera perception, sensors, and controlled
robot actions into a lightweight Raspberry Pi robot platform. It keeps hardware
ownership and physical observations on the robot while external AI agents can
reason through its MCP interface. PHOS is not a standalone LLM or an autonomous
agent: agents interpret observations and make decisions; PHOS provides bounded
robot capabilities and enforces permissions.

![PHOS robot](docs/assets/images/phos.png)

## Key features

- Animated eyes, semantic robot states, and optional LED/touch reactions.
- Camera-based face, presence, attention, and local expression observations.
- On-demand NanoDet object categories through MCP; disabled by default and not
  a continuous detector.
- Optional BME280/BMP280 environment sensing, MPU6050 motion sensing, and
  controller-backed ENS160/AHT21 air-quality telemetry.
- ESP32-compatible `phos-hw` v1 JSON Lines controller protocol over wired serial.
- Semantic physical transitions plus bounded RAM-only temporal measurement history.
- Streamable HTTP MCP server with identity-aware permissions and Resources.
- Authenticated Web Admin and a semantic Remote API.

Software support and physical acceptance are deliberately separate. See the
[hardware guide](docs/hardware.md) before assuming a component is validated on
your particular build.

## Supported hardware

| Hardware | PHOS role | Current support |
| --- | --- | --- |
| Raspberry Pi 3 | Primary lightweight deployment target | Reference target; deployment acceptance remains build-specific |
| Raspberry Pi-compatible camera | VisionPipeline input | Implemented through Picamera2 |
| ESP32 | External controller | `phos-hw` v1 serial JSONL protocol; firmware is not included here |
| BME280 / BMP280 | Temperature, pressure, humidity where available | Optional Pi I2C providers |
| ENS160 + AHT21 | Air quality plus controller temperature/humidity | Controller-backed ESP32 telemetry, not Pi I2C polling |
| MPU6050 / GY-521 | Motion/orientation | Optional provider, disabled by default |
| 5-inch 800×600 display | Animated face | Reference display configuration |

CCS811 appears in older project history, but the current air-quality baseline is
controller-backed ENS160/AHT21; consult the current [hardware documentation](docs/hardware.md).

## Repository contents

| Path | Purpose |
| --- | --- |
| `src/robot/` | Core runtime, providers, services, vision, hardware, UI, web, and MCP code |
| `src/robot/vision/` | VisionPipeline, face/expression perception, NanoDet, and object-awareness internals |
| `src/robot/hardware/` | Pi I2C/GPIO adapters and controller protocol/serial integration |
| `src/robot/mcp/` | MCP semantic server and transport lifecycle |
| `src/robot/web/` | Web Admin and Remote API implementation |
| `config/` | Canonical YAML configuration and compatibility JSON examples |
| `scripts/` | Pi installer, diagnostics, and standalone benchmark utilities |
| `tests/` | Hardware-free automated tests |
| `docs/` | MkDocs source documentation |

## MCP — Model Context Protocol

MCP is PHOS's agent boundary:

```text
External AI Agent → MCP → PHOS application services → sensors, vision, state, permitted controls
```

Rather than embedding a large model on a Raspberry Pi 3, PHOS exposes
permission-controlled structured reads and actions to an authorized external
agent. Useful examples include physical context, bounded measurement history,
recent physical transitions, and on-demand visible object categories through
`phos_get_visible_objects`.

Object awareness requires `read_perception`, is on-demand only, and is disabled
by default. It returns labels/confidence and frame freshness metadata—not raw
images, bounding boxes, identities, or tracking data. See [MCP](docs/mcp.md)
for the complete 15-tool catalog, authentication, permissions, transport, and
error behavior.

## Getting started

PHOS targets Raspberry Pi OS with a graphical desktop. The supported Raspberry
Pi installation path is:

```bash
git clone https://github.com/phosairobot/phos.git
cd phos
./scripts/install-phos.sh
```

Review the [installation guide](docs/installation.md) before installation for
display, camera, controller, optional sensor, and managed-service requirements.
Configuration is in [config/phos.yaml](config/phos.yaml); optional features,
including object awareness, must be explicitly enabled with locally installed
model assets.

## Documentation

- [Documentation home](docs/index.md)
- [Architecture](docs/architecture.md)
- [Hardware and controller protocol](docs/hardware.md) · [controller details](docs/hardware-controller.md)
- [Vision](docs/vision.md) · [expression model evaluation](docs/vision-model-evaluation.md)
- [MCP](docs/mcp.md)
- [Installation](docs/installation.md) · [development and testing](docs/development.md)

## Contributing and license

Contributions from developers, makers, documentation contributors, and hardware
vendors are welcome. Start with [architecture](docs/architecture.md),
[accepted decisions](docs/decisions.md), and [repository guidance](AGENTS.md).

No repository license file or contribution guide is currently included. Contact
the maintainers before redistributing or reusing the code.
