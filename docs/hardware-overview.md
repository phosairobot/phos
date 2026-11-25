---
hide:
  - navigation
  - toc
classes:
  - phos-home
---

# Hardware & sensors

## Bring your own hardware through defined interfaces

PHOS separates its Raspberry Pi runtime from physical-device adapters and
external controller firmware. Agents read shared physical context through MCP;
they do not need a BME280 driver, an ESP32 SDK, or serial access. Supported
measurements, interpreted conditions, and availability retain the same
application-service boundary regardless of the configured provider.

The Pi 3 and 800×600 display are the reference runtime platform. An external
microcontroller is optional. The implemented `phos-hw` v1 protocol uses serial
UTF-8 JSON Lines with capability discovery, heartbeat, reconnect handling,
sensor observations, and resolved semantic LED commands. ESP32 is its reference
controller implementation, not a dependency of every PHOS build.

See [the controller architecture and protocol](hardware-controller.md) for
sensor mapping and porting requirements. Support for a protocol does not imply
support for every microcontroller or sensor. The table below identifies the
existing hardware paths; [wiring details](hardware.md) remain the setup reference.

<div class="phos-page-intro" markdown>

<span class="phos-kicker">PHYSICAL PLATFORM</span>

PHOS targets a **Raspberry Pi 3** and keeps hardware-specific access behind
providers/adapters. Optional devices are disabled by default; enable them after
wiring and configuration.

Start with the [Hardware BOM](hardware-bom.md) to choose a core build, audio
add-ons, and optional sensors or an ESP32 controller.

For the controller build, flash the official
[phos-esp32 firmware](https://github.com/phosairobot/phos-esp32) following its
build/upload instructions. It provides ENS160 + AHT21 telemetry and WS2812B
semantic LED control; complete the [Pi-side setup](hardware-controller.md).

</div>

| Component              | What PHOS uses it for                                                              |
| ---------------------- | ---------------------------------------------------------------------------------- |
| Raspberry Pi 3         | Main computer, runtime and hardware controller.                                    |
| 5-inch 800×600 display / touchscreen | Animated face and eye output, plus local touch gestures where available.                  |
| Raspberry Pi Camera    | Face detection, tracking and optional expression observations.                     |
| USB microphone         | Voice capture, VAD, and provider-neutral speech-to-text input.                      |
| HDMI/display audio path | Reference playback path for PHOS text-to-speech output.                            |
| BME280 / BMP280        | Temperature and pressure; BME280 also supplies humidity for environmental context. |
| ENS160 + AHT21         | Controller-backed eCO2/TVOC/AQI-UBA plus temperature/humidity; eCO2 is not direct CO2. |
| GY-521 / MPU-6050      | Acceleration and angular velocity for interpreted motion/tilt events.              |
| WS2812B RGB LED ring   | Connected to the ESP32; driven via `phos-hw` v1 semantic LED commands.            |

<div class="phos-callout" markdown>

Find wiring, voltage limits, and I²C addresses in the [hardware guide](hardware.md).

</div>
