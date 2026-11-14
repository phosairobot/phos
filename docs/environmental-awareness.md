---
hide:
  - navigation
  - toc
classes:
  - phos-home
---

# Environmental awareness

<div class="phos-page-intro" markdown>

<span class="phos-kicker">CONTEXT, NOT RAW CONTROL</span>

Environmental readings are confirmed and interpreted before they influence the
face, ring or overlays.

</div>

<div class="phos-pipeline" markdown>

<span>BME280 / BMP280 · controller ENS160/AHT21</span> → <span>EnvironmentalInterpreter</span> → <span>Semantic state &amp; overlay intents</span> → <span>BehaviorEngine / FaceState</span> → <span>Eyes · LEDs · overlays</span>

</div>

PHOS interprets environmental context rather than mapping raw values straight
to a renderer. Optional BME280/BMP280 readings provide temperature (plus
pressure, and humidity only on BME280). Controller-backed ENS160 provides TVOC,
AQI-UBA and **estimated / equivalent CO2 (eCO2)**, not direct NDIR CO2; AHT21
provides separately fresh controller temperature and humidity.

`EnvironmentalInterpreter` confirms and recovers semantic states with
hysteresis: **NORMAL**, **COLD**, **WARM**, **AIR_QUALITY_WARNING** and
**AIR_QUALITY_BAD**. Missing, stale or unavailable readings do not create an
alarm. Partial sensor availability remains useful, while ENS160 warm-up withholds
only air-quality rules—not fresh temperature context.

<div class="phos-callout" markdown>

The single priority state drives persistent eye/LED behavior. Independently
confirmed overlay intents are additive: warm conditions can add sweat, cold can
add snow/ice, and air quality can add haze. Temperature and air-quality overlays
can coexist in a shared safe display band.

</div>

Thresholds and physical setup remain in the [canonical architecture](architecture.md#environmental-sensor-service), [hardware](hardware.md#environmental-sensors) and [installation](installation.md#optional-environmental-sensor) guides.
