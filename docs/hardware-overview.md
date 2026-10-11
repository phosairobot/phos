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
providers/adapters. Optional devices are disabled by default and still need
physical acceptance on the target robot.

</div>

| Component              | What PHOS uses it for                                                              |
| ---------------------- | ---------------------------------------------------------------------------------- |
| Raspberry Pi 3         | Main computer, runtime and hardware controller.                                    |
| 5-inch 800×600 display / touchscreen | Animated face and eye output, plus local touch gestures where available.                  |
| Raspberry Pi Camera    | Face detection, tracking and optional expression observations.                     |
| USB microphone         | Voice capture, VAD, and provider-neutral speech-to-text input.                      |
| HDMI/display audio path | Reference playback path for PHOS text-to-speech output.                            |
| BME280 / BMP280        | Temperature and pressure; BME280 also supplies humidity for environmental context. |
| CCS811                 | Air-quality estimates: eCO2 and TVOC; eCO2 is not direct CO2 measurement.          |
| GY-521 / MPU-6050      | Acceleration and angular velocity for interpreted motion/tilt events.              |
| WS2812B RGB LED ring   | Optional semantic visual feedback and temporary directional animations.            |

<div class="phos-callout" markdown>

Wiring, voltage, I²C addresses and physical acceptance are deliberately kept in
the [canonical hardware guide](hardware.md). Do not assume a documented adapter
means the physical module has been verified on every Pi build.

</div>
