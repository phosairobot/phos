# PHOS 1.1.0 release record

**Status: NOT READY — source release candidate; complete integration tests before tagging.**

The authoritative version is `robot.__version__` in `src/robot/__init__.py`,
set to `1.1.0`. Packaging, startup logging and Web Admin status derive from that
single value. No commit, tag, deployment or publication is implied by this
record.

## Scope actually implemented

PHOS 1.1.0 retains the 1.0 animated-eyes, tracking, optional expression and
authenticated Web Admin baseline, and includes these implemented optional paths:

- MPU-6050 provider/service, freshness/retry handling and pure, confirmed
  `STILL`, `MOVING`, directional tilt, `SHAKE` and `IMPACT` interpretation.
- BehaviorEngine mapping of semantic IMU events to temporary eye intent and
  semantic LED intent; raw IMU readings never reach renderers or GPIO.
- WS2812B provider behind the root-owned helper/local socket and a low-rate
  `LEDRingController` with canonical palette, directional fills and restoration
  of the currently resolved persistent state after an IMU effect.
- BME280/BMP280 temperature/pressure service and CCS811 eCO2 (estimated
  equivalent CO2, not direct NDIR CO2)/TVOC service, each with explicit
  availability, freshness and degraded mode.
- `EnvironmentalInterpreter`: confirmation, hysteresis, recovery and
  per-source validity. Its priority contract is BAD > WARNING > WARM/COLD >
  NORMAL; CCS811 warm-up excludes only air-quality rules.
- Additive confirmed temperature and air-quality overlay intents. The display
  renders sweat, snow/ice and haze in a shared, eye-relative safe band; these
  overlays do not change eye geometry, iris color or LED intent.
- Persistent visual-source selection (`manual`, `environment`, `state`) with
  temporary robot/IMU overrides restored after expiration.
- Canonical configuration and Web Admin controls/status for the optional
  sensors, environmental behavior, IMU, LED ring, visual source and overlays.

Voice, conversation/LLM operation, Home Assistant, remote API, MCP and OS
reboot remain outside this release.

## Architecture and reliability audit

The release-path audit found the intended boundaries intact: hardware libraries
stay in adapters/providers; sensor workers publish typed snapshots; interpreters
publish semantic events; BehaviorEngine is the behavior/arbitration boundary;
EyeRenderer and LEDRingController consume `FaceState` semantics; and Web Admin
uses lifecycle/configuration services rather than hardware. `STILL` means no IMU
animation, not a base-color write, so a WARM state is restored after a tilt.

The canonical full `config/phos.json`/`RuntimeConfig` model remains the only
non-secret runtime configuration. Existing 1.0-era partial configurations are
not silently accepted: required 1.1 sections/fields fail validation with a clear
field error. Merge the complete canonical file or follow the migration guidance
in [development](development.md#environmental-configuration-migration); this is
the documented compatibility policy.

## Automated evidence

On this development host and its checked-in virtual environment:

- Focused configuration, sensor-provider/service, air-quality, IMU,
  EnvironmentalInterpreter, BehaviorEngine, eye/overlay and LED tests:
  **276 passed**.
- The system Python lacks Flask, but the project `.venv` has the pinned Flask
  dependency and collects all 56 Web Admin tests.
- A complete suite and full Web worker execution could not complete in this
  sandbox: the runner does not permit the real local worker/socket path to
  finish within its execution window. This is a release gate, not a skipped or
  weakened test.

Tests use fakes/mocks and require no Pi hardware. They cover sensor absence and
staleness, CCS811 warm-up, interpreter priority/partial availability, semantic
state restoration, directional LED frames, overlay geometry and canonical Web
configuration/status paths. They do not prove physical timing, wiring or visual
appearance.

## Release blockers

- Run the complete automated suite, including real Web-worker tests, in an
  environment that permits the local worker/socket test.
- Complete the following Raspberry Pi 3 physical checklist and record hardware,
  OS, commit, logs and observations.
- Fresh-install and exercise the optional hardware dependencies/helper on the
  target Pi. No target performance measurement is yet release evidence.

Known non-blocking limitations: eCO2 is estimated/equivalent CO2; BMP280 has no
humidity; CCS811 baseline persistence is not implemented; expression recognition
quality/neutral calibration remains unestablished; Web Admin is trusted-LAN HTTP
and not an Internet-facing multi-user service.

## Upgrade notes

1. Back up the deployed complete configuration and administrator data.
2. Merge every required field from `config/phos.json`; do not retain an old
   partial document. Validate with `RuntimeConfig.from_file` before restart.
3. Leave optional sensors, ring and environmental behavior disabled until their
   dependencies, wiring and physical checks are complete.
4. For a ring, configure matching helper-unit and JSON GPIO/count values, then
   restart the helper before PHOS. For sensors, restart PHOS after hardware
   configuration changes; interpretation/display controls can use Reload where
   documented.

## Release checklist

### Startup, Vision and Web Admin

- [ ] On a clean Pi OS desktop install, validate configuration and start PHOS;
  confirm 800×600 eyes and `1.1.0` in logs/Web Admin.
- [ ] Enable Web Admin on a trusted LAN, complete bootstrap password rotation,
  save/reload a valid setting and reject an invalid one.
- [ ] Enable face tracking; acquire, lose and reacquire a face while eyes remain
  responsive.

### IMU and LED ring

- [ ] Verify MPU-6050 address/WHO_AM_I and stable raw acceleration/gyro values.
- [ ] Verify STILL, MOVING, LEFT/RIGHT/FORWARD/BACK tilt, SHAKE and IMPACT with
  configured mounting axes, confirmation and cooldown.
- [ ] Confirm directional pupils/asymmetry and that robot-state priority still
  overrides temporary IMU effects.
- [ ] Verify ring base color, Web Admin color reload, all directional fills,
  shake/impact, and return to the resolved persistent state after each effect.

### Environment and overlays

- [ ] Verify BMP280/BME280 temperature, pressure, freshness and stale/unavailable
  presentation; confirm BMP280 humidity is not fabricated.
- [ ] Verify CCS811 address, warm-up withholding, eCO2/TVOC freshness and failure
  recovery; label eCO2 as estimated/equivalent CO2.
- [ ] Verify WARM/COLD confirmation/recovery and that CCS811 warm-up does not
  suppress fresh BMP280 temperature behavior.
- [ ] Safely verify warning/bad thresholds or controlled test data, including
  priority and restoration after IMU tilt/shake.
- [ ] Verify centered near-eye sweat, snow and haze; verify combined temperature
  plus air-quality overlays remain readable and do not cover pupils.

### Visual source, resilience and shutdown

- [ ] Verify manual, environment and state persistent sources and restoration
  after temporary IMU/robot-state events.
- [ ] Start with each optional peripheral missing or unavailable; eyes, Vision
  where enabled and Web Admin must continue with a clear status.
- [ ] Measure sustained eye responsiveness/CPU/RSS while optional paths run.
- [ ] Restart through systemd, then stop/start during active optional services;
  confirm camera, I2C workers, web worker and LED helper clean up as expected.

## Suggested commands after all gates pass

```bash
git switch -c release/1.1.0
git add -A
git commit -m "Prepare PHOS 1.1.0 release"
git tag -a v1.1.0 -m "PHOS 1.1.0"
```

Review the diff and perform the completed release checklist before executing
these commands. This task did not execute them.
