# Architecture Decisions

## ADR-001 — Python as primary language

**Status:** Accepted

Python is the primary application language.

**Why**
- Strong Raspberry Pi ecosystem
- Excellent AI/audio/computer-vision ecosystem
- Fast iteration
- Good maintainability for a personal robotics project

C/C++ may be introduced for a hardware driver, microcontroller firmware or a performance-critical component when justified.

## ADR-002 — Hardware abstraction

**Status:** Accepted

Hardware access is isolated behind explicit interfaces/adapters, with physical-device implementation under `src/robot/hardware/` where applicable.

**Why**
- Testing without physical hardware
- Easier hardware replacement
- Cleaner AI/core layers

## ADR-003 — Raspberry Pi 3 is the baseline

**Status:** Accepted

Solutions should remain practical on Raspberry Pi 3 unless a later hardware upgrade is explicitly documented.

Expensive AI inference may use a remote machine/API or an optimized local model.

## ADR-004 — Provider-independent LLM interface

**Status:** Accepted

All language-model access uses `LLMProvider`.

**Why**
- Switch between OpenAI, Anthropic and local/self-hosted models without rewriting robot behavior.
- Prevent vendor SDK types from leaking through the architecture.
- Make AI behavior unit-testable using fake providers.
- Allow future routing between fast, powerful and offline models.

## ADR-005 — Primary LLM is not assumed to run on Raspberry Pi 3

**Status:** Accepted

The Pi 3 runs robot orchestration, UI, hardware and integration logic. The primary LLM may run through a cloud API or on a LAN server. Small local models may be explored as offline fallbacks.

## ADR-006 — Provider-neutral TTS and separate playback

**Status:** Accepted

Speech synthesis uses `TTSProvider`. Audio playback/device management is a distinct responsibility. Piper is the preferred first local TTS implementation to benchmark on Raspberry Pi 3.

## ADR-007 — Lightweight Raspberry Pi Vision

**Status:** Accepted

Use Picamera2 for Raspberry Pi Camera capture, lightweight OpenCV face detection, and a configurable lightweight ONNX expression classifier through OpenCV DNN. Expression classification is an uncertain visual observation, not mind/emotion inference. Identity recognition and image persistence are out of scope by default.

## ADR-008 — Behavior mediates Vision and UI

**Status:** Accepted

Vision does not drive the renderer directly. Stable observations plus `RobotState` feed `BehaviorEngine`, which produces UI-neutral `FaceState`. `EyeRenderer` consumes `FaceState`. PHOS does not mechanically mirror negative human facial-expression observations.

## ADR-009 — Architecture-first multi-agent workflow

**Status:** Accepted

Architecture/docs own WHAT and WHY. Coding agents such as Codex own HOW and CODE. Git/repository files are the source of truth.

Coding agents may propose architectural changes but must not silently implement changes that conflict with accepted decisions. If implementation reveals a conflict, report it and document an approved resolution before redesigning the system.

## ADR-010 — Project identity: PHOS

**Status:** Accepted

The project name is **PHOS**. Documentation, agent instructions and user-facing project references use PHOS.

The existing Python package namespace `robot` remains unchanged. Renaming the package is a separate implementation migration requiring explicit approval.

## ADR-011 — Hierarchical AGENTS.md owns PHOS-specific agent guidance

**Status:** Accepted

Durable PHOS-specific instructions live in the root `AGENTS.md`, nested subsystem `AGENTS.md` files and `docs/`. More specific instructions apply to the directory they govern.

Avoid duplicating detailed architecture across root instructions, nested instructions and skills.

## ADR-012 — Runnable vertical milestones

**Status:** Accepted

Complete and verify one runnable capability before deliberately introducing or expanding the next major subsystem. Preferred progression is eyes -> face tracking -> expression reactions -> voice -> integrations.

Code that already exists ahead of the currently verified milestone is preserved, but it should not drive automatic scope expansion.

## ADR-013 — General coding skill and PHOS guidance are separate

**Status:** Accepted

The reusable coding-agent skill is project-neutral and contains only implementation workflow. PHOS-specific facts, hardware constraints, architecture, milestone order and subsystem rules live in this repository through hierarchical `AGENTS.md` files and `docs/`.

The project does not require a PHOS-specific skill.

## ADR-014 — Optional cloud facial-expression adapter

**Status:** Accepted (explicitly requested extension of ADR-007).

Keep local ONNX as default and add selectable AWS Rekognition behind the existing
ExpressionProvider. Capture, tracking, selection and cropping stay local; only
selected stable crops leave the Pi when AWS is explicitly selected. Cloud work
is single-flight, asynchronous, rate-limited and cached with expiry/backoff;
there is no implicit fallback. Cached reads are not independent temporal evidence.
The existing semantic/behavior/UI boundaries remain unchanged. This extends
ADR-007's local baseline without replacing it. See Vision for policy and privacy.

Normal settings belong to RuntimeConfig and must be reusable by a future web
configuration layer. Secrets stay outside application settings in standard AWS
credential resolution. No web interface is part of this change.

## ADR-015 — One canonical runtime configuration

**Status:** Accepted (explicit user-requested configuration centralization).

Use `config/phos.yaml` for all current non-secret application settings. Required
sections map to the existing typed RuntimeConfig surface in `robot.config`;
CloudExpressionConfig remains a typed provider subset. Validate types, ranges,
relationships and active paths before building subsystems. CLI defaults and
provider-specific JSON files are replaced by this file. Legacy `phos.json` is
supported as a fallback or explicit path. Existing setting flags
remain deprecated explicit overrides for a separately announced migration.

The committed file safely starts only eyes; camera/expression processing is
opt-in. Keep geometric/semantic safeguards and renderer implementation constants
in their subsystems. New normal runtime settings extend the central schema/file.
Reusable loading, validation, serialization and atomic persistence are independent
of CLI and available for the future web milestone. Secrets remain external;
no web UI or live reload is part of this change.

## ADR-016 — Optional local web administration

**Status:** Accepted (explicit user-requested web administration milestone).

Add a lightweight Flask/Waitress administration adapter in a separate process
owned by application startup. Reuse RuntimeConfig schema, validation and atomic
persistence; expose saved settings separately from the startup snapshot. No hot
reload. Canonical web settings opt into LAN binding; default disabled/loopback
preserves the existing runtime. The optional web dependencies stay outside Core,
Vision and rendering.

Use one administrator, an external owner-only salted password-hash file, mandatory
bootstrap password change, expiring signed sessions with server-side revocation,
CSRF protection and bounded password attempts. AWS secrets remain external. HTTP
is trusted-LAN-only; Internet/TLS/proxy deployment is outside this milestone.
Details and local recovery belong in the [web manual](web-administration.md).

## ADR-017 — PHOS 1.0.0 scope and shared control services

**Status:** Accepted (explicit release-finalization request).

Freeze 1.0.0 at the existing runtime, eyes, tracking, local/AWS expression and
canonical configuration/web administration scope. Hardware availability does
not bring sensors, LEDs, conversation, remote API or MCP into this release.
Keep one authoritative version in `robot.__version__`, consumed by packaging
and runtime/status reporting. Separate automated release checks from physical
acceptance and expression accuracy.

Future Web/API/MCP/Voice control surfaces must reuse application services and
must not access hardware or subsystem internals. Shared validation, authorization
and behavior semantics belong in those services, not in duplicated adapters.
See the release record and roadmap for scope and deferred capabilities.


## ADR-018 — Validated reload and supervisor-owned restart

**Status:** Accepted (explicit requested addition to 1.0.0 finalization).

Extend ADR-016/017 only with shared lifecycle operations. Saving and active state
are separate. Validate the complete canonical file before reload; logging level
has a safe live apply boundary. Other settings require restart unless separately
approved.
A user systemd service owns application replacement after graceful exit status
75. No adapter executes arbitrary OS commands or self-spawns a replacement.
Manual launches do not offer browser restart. Confirmation and CSRF protect
restart, and status/reload/restart require completed administrator bootstrap.
Web/API/MCP/Voice must reuse application services; OS reboot is deferred.


## ADR-019 — Configurable lightweight eye appearance

**Status:** Accepted (explicit eye-system improvement request).

Keep rendering within BehaviorEngine → FaceState → EyeRenderer → display.
A canonical, named `display.iris_color` selects the base iris theme; semantic
accent tint remains derived from FaceState. The Tk display uses inexpensive
layered Canvas primitives for eye-body depth, iris/pupil separation and
reflections. No image pipeline, graphics dependency or provider-specific visual
logic is introduced. Unsafe display geometry/backend changes require PHOS restart.


## ADR-020 — Live application of named eye appearance

**Status:** Accepted (explicit runtime reload requirement).

This decision extends ADR-018's reloadability classification: canonical
`display.iris_color` joins logging level in the safe reloadable field allowlist.
The lifecycle service validates the entire file and sends typed settings through
the runtime service to the render-loop queue. The queue applies the theme on the
display event loop, where EyeRenderer smoothly interpolates its current iris
color. No subsystem restarts. Mixed reloads apply logging and appearance while
retaining pending restart-required fields. Web adapters cannot access renderer
state directly.

## ADR-021 — Local diagnostic camera preview composition

**Status:** Accepted (explicit optional preview request).

Keep one Picamera2/Vision pipeline owner. When enabled, retain only the latest
captured frame and existing selected-face/expression diagnostics in memory, then
pass a UI-neutral value through PhosRuntime to a bounded, low-rate display
composition layer. EyeRenderer remains Vision-agnostic. Preview is disabled by
default, local to the PHOS display, never persisted or served over the network,
and is live-reloadable without restarting PHOS or an already-running camera and
Vision pipeline. Enabling preview may start the dormant existing pipeline;
disabling it releases the camera only when no other Vision feature uses it.

### 1.0.0 hardening clarification (ADR-018/021)

Release hardening preserves the existing scope and boundaries. Runtime/worker
failures must exit nonzero so the documented supervisor can recover. Preview
reload acknowledges camera lifecycle acceptance before recording active settings;
failed/cancelled starts release resources. Previously successful appearance
changes remain recorded if a later hardware application fails. Invalid canonical
configuration still applies nothing. The committed preview default remains off.
Deprecated, functional CLI overrides remain during the ADR-015 migration; they
are not a second persisted configuration or the production launch path.


## ADR-022 — Optional BME280 environmental service

**Status:** Accepted (explicit BME280 integration request, separately approved
follow-on to the frozen 1.0.0 baseline).

Use a small synchronous `EnvironmentalSensorProvider` contract and immutable
`EnvironmentalReading` (temperature °C, relative humidity %, pressure hPa).
The BME280 adapter owns I2C bus 1 via optional RPi.bme280/smbus2; vendor imports
are lazy. A dedicated single worker owns device initialization/read/close and
publishes a lock-protected, in-memory latest snapshot through the sensor service.
No hardware call runs in the asyncio renderer or a web request. Failures retry
with bounded backoff and rate-limited warnings; stale/failed readings are never
presented as current. Native calls cannot be safely interrupted, so shutdown
bounds its wait without concurrent close or replacement workers.

Canonical `sensors.bme280` is disabled by default; every field requires restart.
The new required section follows the existing explicit config-merge migration
policy. Runtime exposes a read-only snapshot via the existing lifecycle status
channel to the authenticated Sensors editor. It adds no network control API,
behavior input, event stream or general dashboard. CCS811, GY-521, LED ring,
voice, MCP and Home Assistant remain deferred. Real Pi acceptance is separate
from fake-provider tests; the 1.0.0 release history/version is not rewritten.


### ADR-022 extension — selectable BME280 / BMP280

**Status:** Accepted (explicit environmental sensor extension request).

Replace the BME280-specific configuration block with `sensors.environmental`
plus a required `type` selector. Preserve common settings and the explicit
configuration migration policy. Every sensor change still requires restart.
The provider-neutral reading has nullable `humidity_percent`; BME280 provides
humidity, BMP280 cannot. Runtime status includes type and explicit measurement
capabilities even when disabled/unavailable; Web Admin renders unsupported
humidity distinctly from unavailable readings.

Keep RPi.bme280 for BME280; its sampling API requires humidity registers. Add
Pimoroni bmp280 for BMP280, reusing worker-owned smbus2 bus 1 and the existing
service, retry, freshness and lifecycle boundaries. Strict chip IDs prevent
silent reinterpretation. No behavior integration or other sensor scope is added.


## ADR-023 — CCS811 through the existing sensor service pattern

**Status:** Accepted (explicit CCS811 integration request).

Add `AirQualitySensorProvider` / immutable `AirQualityReading` for eCO2 ppm and
TVOC ppb alongside the environmental contract. eCO2 is estimated equivalent
CO2, never direct NDIR CO2. Reuse the established worker/retry/freshness/lifecycle
implementation, without coupling sensors to behavior, Vision or rendering.
Canonical `sensors.ccs811` defaults disabled, uses bus 1 and configurable
0x5a/0x5b, and requires restart for all settings. Web Admin uses the existing
service snapshot/configuration boundaries.

Select the existing lightweight smbus2 dependency with a local register adapter
based on ams DS000459, avoiding a new GPIO/I2C framework. Fixed mode 1 and a
20-minute conditioning gate remain hardware policy. Expected no-data/warm-up
polls retain initialization; physical failures retry with bounded backoff.
Automatic compensation accepts fresh service-level temperature plus humidity,
never assumes BMP280 humidity and restores device defaults on loss of source.
No baseline persistence, firmware updating, GPIO wake control, GY-521, WS2812B
or additional control surface is included. Exact breakout electrical validation,
first-use burn-in and sustained Pi operation remain separate physical acceptance.

## ADR-024 — Optional MPU-6050 IMU service

**Status:** Accepted (explicit GY-521 integration request).

Add typed `IMUSensorProvider` / immutable `IMUReading` and reuse the existing
bounded sensor worker for an optional bus-1 MPU-6050 adapter. Canonical
`sensors.imu` defaults disabled, permits `0x68`/`0x69`, and requires restart for
all fields. Report acceleration in m/s² and angular velocity in °/s through the
existing runtime status and Web Admin boundaries. Use smbus2 directly and fixed
±2 g / ±250 °/s scales; do not add automatic offset calibration, orientation
fusion, interrupts, gestures or behavior integration. Physical wiring and board
acceptance remain separate from this implementation.

### ADR-024 extension — Motion interpretation

Add a lightweight, pure `MotionInterpreter` after raw IMU readings. It produces
debounced STILL, MOVING, four tilt, SHAKE and IMPACT observations with a bounded
low-pass filter, temporal confirmation and event cooldown. Thresholds are
canonical `sensors.imu.motion` settings and reload through the runtime service
without touching I2C. No behavior, rendering, orientation fusion or hardware
interrupt contract is introduced.

### ADR-024 extension — Motion visual intent

Permit stable motion state changes to enter Core as provider-neutral local
events. BehaviorEngine maps them to FaceState only: persistent tilt/movement in
IDLE and bounded shake/impact alert overlays. Core states retain priority and
EyeRenderer remains unaware of IMU concepts. Eight live-reloadable `behavior.imu_*`
settings provide visible intensity, tilt gaze range and eye asymmetry, separate transient strengths,
durations and
cooldown without exposing renderer geometry or changing the base iris theme.

### ADR-024 extension — Reliable sustained tilt

The user-approved classifier correction removes the movement gate on tilt.
Filtered normalized gravity, signed mounting axes, enter/exit hysteresis and
temporal confirmation give tilt priority below impact/shake and above movement.
Preserve the public tilt threshold key with normalized-component semantics; add
an exit threshold and explicit mounting mapping to canonical live configuration.
Use a fixed time-based low-pass filter and 20 Hz default sampling, without AHRS
or changes to BehaviorEngine/EyeRenderer. See installation for conventions,
limitations, diagnostics and physical verification.

### ADR-024 extension — Amplified IMU visual intent

Keep the established IMU event → BehaviorEngine → FaceState boundary. Increase
persistent motion and tilt intent for visibility on the 800×600 display, while
using separately configurable shake and impact strengths so impact is always
stronger. FaceState retains its existing geometry clamps; alert accents remain
temporary semantic intent, preserving the configured base iris. The seven
`behavior.imu_*` settings reload live and do not alter the motion interpreter.

### ADR-024 extension — Directional IMU eye asymmetry

Add an optional signed `FaceState.eye_asymmetry` intent. It requests mirrored
left/right eye openness without naming an IMU state in the renderer. Behavior
sets it from stable horizontal tilt: left tilt opens the left eye and right tilt
opens the right eye by the same configured amount; forward/back and non-tilt IMU
states request equal eyes. This explicit value overrides the expression
profile's ordinary asymmetry, while EyeRenderer continues to clamp and
independently interpolate received eye targets. The one live-reloadable behavior
setting remains architecture-neutral and preserves robot-state priority.

### ADR-025 — Optional semantic WS2812B output

Add a lazy `rpi-ws281x` hardware provider behind a root-owned helper and a
separate low-rate unprivileged LED controller.
The controller reads only the existing immutable `FaceState` semantic accent and
reaction strength, mapping it to a small uniform steady/pulse/fade vocabulary.
It must not receive raw Vision, IMU or sensor input, and provider failures must
not affect Core or eye rendering. Canonical `led_ring` settings keep pin/count
restart-only; enabled state and visual settings reload through the shared
lifecycle service. The ring is disabled and count-unconfigured by default until
physical wiring is accepted.

## ADR-026 — YAML-first configuration with legacy JSON compatibility

**Status:** Accepted.

YAML is the canonical format for new PHOS installations because it is easier to
edit by hand while retaining the complete, strictly validated RuntimeConfig
schema. `ConfigRepository` owns discovery, parsing, serialization and atomic
persistence so every adapter uses one fixed active source for its process:
`phos.yaml`, then `phos.yml`, then legacy `phos.json`.

Saving to the active source avoids surprise format conversion and preserves a
user's chosen deployment boundary. Automatic JSON-to-YAML conversion is
rejected because configuration changes must remain deliberate, reviewable and
recoverable. JSON remains supported for backward compatibility, with a concise
one-process warning when it is active. YAML is preferred for new installations;
removal of JSON support is not currently scheduled.
