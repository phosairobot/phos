---
hide:
  - navigation
  - toc
classes:
  - phos-home
---

# Hardware & sensors

<div class="phos-page-intro" markdown>

<span class="phos-kicker">PHYSICAL PLATFORM</span>

PHOS targets a **Raspberry Pi 3** and keeps hardware-specific access behind
providers/adapters. Optional devices are disabled by default; enable them after
wiring and configuration.

Start with the [Hardware BOM](hardware-bom.md) to choose a core build, audio
add-ons, and optional sensors or an ESP32 controller.

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
| WS2812B RGB LED ring   | Optional semantic visual feedback and temporary directional animations.            |

<div class="phos-callout" markdown>

Find wiring, voltage limits, and I²C addresses in the [hardware guide](hardware.md).

</div>
