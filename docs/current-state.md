# PHOS Current State

This file is an implementation handoff for coding agents. It records what is present in the checked-out repository and distinguishes implementation from hardware verification.

## Identity and baseline
- Project: PHOS.
- Python namespace: `robot`.
- Target: Raspberry Pi 3.
- Display target: 5-inch 800x600.
- Microphone available; exact interface remains TBD.
- Display/eyes and Pi Camera/tracking are documented as operational in `docs/hardware.md`; exact camera model remains unspecified.

## PHOS 1.4.0 — Voice & Interaction Foundation

PHOS 1.4.0 implements startup/readiness (splash, ready sound and hidden cursor),
touch/gesture input, provider-neutral native-rate microphone capture, canonical
16 kHz PCM16 processing, VAD, bounded pre-roll, local Vosk STT, and observable
`LISTENING → THINKING → IDLE` sessions validated on Raspberry Pi 3.

PHOS can capture and transcribe speech, but does not yet perform transcript →
intent/command interpretation → application command → behavior/action. TTS,
AudioOutputProvider, SPEAKING, wake words, continuous listening, conversational
LLM, and Home Assistant remain deferred. The small local Vosk model has limited
accuracy for longer phrases and non-native English accents. JSON remains the
canonical 1.4.0 configuration format.

## PHOS 1.3.0 release candidate

The source version is **1.3.0**. It adds stable Presence and Attention state,
transient presence LED reactions, observed facial-expression telemetry, and a
bounded expression-reaction policy to the existing Remote API and Web Admin.
Automated regression has passed. It is **not ready to tag** until the target-Pi
acceptance checklist is complete. See
[release-1.3.0.md](release-1.3.0.md) for current gates; older release sections
below are retained as implementation history, not current release status.

The recommended Raspberry Pi OS installation path is now
`./scripts/install-phos.sh`. It validates the supported platform, installs the
canonical `.[all]` runtime dependency aggregate, prepares the configured local
ONNX model, and performs software-only smoke checks. Physical hardware setup
and acceptance remain separate.

PHOS 1.4.0 Slice 1 adds a local startup splash owned by the existing eye display.
Runtime readiness records required display/core and optional Vision, sensors, and
LED outcomes as `ready`, `degraded`, or `failed`; normal eyes appear only for
ready/degraded startup. Optional local ready sound and face-display-only cursor
hiding are configured through the canonical `startup` section. Physical Pi
asset diagnostics now report the resolved splash/sound paths, readability,
splash loader result, and `aplay` outcome. Default PHOS assets are packaged
under `robot.assets`; explicit config overrides remain configuration-relative.
Physical Pi acceptance remains pending.

### Startup-experience Pi acceptance (pending)

On power-on, confirm the splash appears before normal eyes; it must remain while
components initialize. Confirm one ready sound when configured, normal eyes only
after ready/degraded state, and a persistent startup error for a critical failure.
Verify an unavailable optional sensor permits degraded startup. Move a mouse over
the face display to confirm its cursor is hidden, then open Web Admin and confirm
the browser cursor remains normal. Touch behavior is not implemented in this slice.

## Present in the repository

### Core and behavior
- Event bus and explicit `RobotState` state machine.
- `BehaviorEngine` under `src/robot/core/behavior_engine.py`.
- Environmental behavior is implemented through `EnvironmentalInterpreter`: confirmed, hysteretic temperature and CCS811 eCO2/TVOC context becomes `EnvironmentalState`, then a `BehaviorEngine` eye/LED accent. Missing, stale or unavailable data produces no alarm.
- Environmental overlays are additive display decoration: the interpreter also exposes independently confirmed temperature (`warm`/`cold`) and air-quality (`warning`/`bad`) intents, allowing sweat/snow and haze to coexist while the existing single priority `EnvironmentalState` continues to drive eyes and LEDs.
- Behavior produces `FaceState` and handles blink/idle gaze.
- Robot states include IDLE, LISTENING, THINKING, SPEAKING, SLEEPING and ERROR.
- Presence/Attention vertical slice is implemented and covered by hardware-free
  lifecycle tests: provider-neutral selected-face observations use confirmed
  Presence edges and Attention `IDLE → ACQUIRING → TRACKING → LOST → IDLE`.
  It supplies semantic gaze and optional configured LED enter/leave transients
  through `BehaviorEngine` and `FaceState`, without identity recognition.

### UI / eyes
- UI-neutral `FaceState` and expression/blink enums.
- Eye renderer/display runtime with smooth frame updates.
- Standalone eye demo under `src/robot/ui/demo.py`.
- Current README documents an 800x600/fullscreen runtime path and keyboard-driven eye demo.

### Vision
- Provider-neutral camera, face-detector and expression-provider contracts.
- Picamera2 camera adapter and OpenCV face detection/expression implementation are present.
- `VisionPipeline` rate-limits detection and expression inference independently and publishes stable-expression / face-lost events.
- Temporal expression smoothing is present. `BehaviorEngine` maps only stable
  observations to subtle `FaceState` reactions, refreshes sustained reactions,
  and decays them to baseline. UNKNOWN is explicit; negative classes never
  select a PHOS expression. Surprise is temporary with rearming/cooldown.
  Neutral perception defaults to disabled pending calibration; `RobotState`
  takes priority. Semantic thresholds and diagnostics are in `docs/vision.md`.
- `FaceState` includes a provider-neutral `VisualAccent`; the eye renderer
  smoothly applies its semantic neutral/warm/curious/alert/sleepy/error colors
  to eye and pupil appearance without depending on Vision labels.
- OpenCV Zoo MobileFaceNet is the selected expression replacement candidate;
  FER+ remains the rollback/comparison baseline. The existing provider supports
  both, with presets and inference timing. An in-memory paired camera benchmark
  is available as `python -m robot.vision.benchmark`. Pi CPU inference is
  measured; two camera comparisons had poor face detection coverage and
  showed no recognition improvement. A rotation probe did not resolve detection; see `docs/vision-model-evaluation.md` for
  model details, evidence, channel-order inconsistency and acceptance steps.
- Face-position tracking is implemented: Vision initially acquires the largest
  valid face, then prefers geometric continuity and publishes normalized selected-face
  position events, and `BehaviorEngine` smoothly maps them into bounded
  `FaceState` pupil targets. Hardware operation is recorded in `docs/hardware.md`;
  fresh release regression remains required.
- Expression crops are bounded squares with configurable margin and smoothed
  scale; two matching detections are required before inference. Debug logs expose
  all detections, selections and rejections. See `docs/vision.md` for geometric
  gates, reacquisition rules and physical crop verification.

### AI
- Provider-neutral `LLMProvider` contract and `RobotAgent` skeleton exist.
- OpenAI, Anthropic and local/LAN provider adapter modules exist.
- Provider maturity should be judged from code/tests rather than filename presence alone.

### Runtime
- An application runtime/coordinator already exists and can supervise Core/UI and optional Vision.
- This code predates the newer vertical-milestone discipline. Preserve it, but do not use its existence as a reason to expand unfinished subsystems automatically.

### Voice / TTS
- Slice 3 provides explicit microphone capture, energy VAD and provider-neutral local STT through an optional Vosk model. It ends at an observable transcript; audio is not served. Temporary physical STT debugging is opt-in through `voice.debug.dump_utterance_wav` and overwrites one configured WAV file.
- LLM, TTS and playback remain outside this slice.
- TTS architecture remains provider-neutral with Piper as the preferred first local engine to benchmark when that milestone begins.

## Current development priority

Complete PHOS 1.3.0 release acceptance on the target Pi: Presence/Attention,
expression observation/reaction, optional LED feedback, and existing
sensor/overlay paths must be verified together. Automated regression is passed.
See [the 1.3.0 release record](release-1.3.0.md) for the remaining manual gates.
Other scope remains deferred.
The expression-reaction implementation needs target-hardware verification with
a selected lightweight ONNX model. Confirm the model's labels, dimensions and
preprocessing, then verify semantic happy/surprised confirmation and UNKNOWN
abstention on the Pi Camera/display while checking CPU use and clean shutdown.
Neither current model has demonstrated reliable recognition; neutral activation
requires calibration, not just a high softmax score.

### Presence / Attention Pi acceptance (pending)

This workspace does not establish physical Pi acceptance. On the Pi, start with
no person (`no_one` / `idle`), enter and remain in frame (one confirmed
`person_entered`, `idle → acquiring → tracking`, configured entered sweep and
gaze), then move in each direction while checking environmental/expression
visuals remain independent. A brief departure must not emit `person_left`; a
confirmed departure must emit it once, run the configured reverse sweep and
hold Attention in `lost` for `attention.lost_hold_ms` before `idle`. Re-entry
must repeat one complete cycle. Finally verify Dashboard/Status update target
details and show unavailable—not fake 100%—confidence when the detector has no
confidence value.

## Selectable expression providers

Local ONNX remains the default; optional AWS Rekognition is implemented behind
ExpressionProvider, with local tracking/cropping, bounded background requests,
cache expiry, change gating, stability gating and failure backoff. Settings
now come from the complete canonical `config/phos.yaml` and can be overridden
by deprecated CLI settings. Cloud cached samples retain
capture timestamps and do not advance semantic confirmation. See Vision and
installation for policy, privacy, exact commands and external credentials.
Mocked tests cover cloud behavior without credentials/network/cost. Physical
Pi/AWS validation, expression accuracy and cloud threshold calibration remain
outstanding. No automatic provider fallback is implemented.

## Canonical configuration

`robot.config` owns RuntimeConfig/CloudExpressionConfig, strict full-file schema
validation, config-relative paths and reusable atomic save. Runtime composition
passes typed configuration values for display, behavior, detector, Vision, expression smoothing,
local/AWS providers and logging. The default file enables only eyes. The old
expression-specific partial JSON files are removed; migrate existing deployments
using `docs/development.md`. No secrets are part of the model. `run_pi.sh` seeds
configuration without overwriting existing Pi settings. The optional web interface
reuses this model for structured settings and an advanced YAML editor that saves
back to the active source format; see below. Startup flags remain deprecated
overrides pending a separately announced removal after consumers migrate.

## Web administration

Implemented: optional Flask/Waitress process, required canonical `web` section,
responsive domain pages with shared navigation, per-page merges through full
configuration validation/atomic saves, provider
selection, bootstrap password rotation, hashed local credentials, expiring
revocable sessions, CSRF and global password attempt throttling. Startup owns
worker cleanup and exposes parent-owned active configuration metadata on a
separate read-only Status page. General provides navigation; Network, Display & Appearance, Vision,
Expression Recognition, Logging and Web Administration / Security expose only
implemented fields. Password management stays separate.
Settings → Configuration presents the complete validated configuration as YAML,
uses revision protection against stale saves, and requires an explicit restart
after any advanced-editor save; it never applies or converts settings live.
Logging level, iris appearance and camera-preview settings are live-reloadable after full canonical validation. Confirmed
PHOS restart uses the supplied user systemd service; manual launches reject
browser restart. No OS reboot, AWS credential probe, automatic backup or Internet
deployment.
The General, System / Status and Sensors pages additionally use the authenticated
Remote API for an initial semantic runtime snapshot and live SSE updates. The
Waitress-compatible event stream reconnects with bounded backoff and retains
last-known values; the Sensors dashboard presents optional sensor states
independently and capabilities populate runtime controls. Server-rendered status
remains available when JavaScript is unavailable.
See [user manual](web-administration.md) for installation and recovery. Tests are
hardware-free; Pi resource usage and LAN browser verification remain required.

## Remote API application boundary

`robot.services.PhosApplicationService` is the remote-control boundary. It
returns provider-neutral JSON models and accepts semantic operations only; it
never exposes GPIO, I2C registers, pixels, camera frames, or renderer objects.
The Flask adapter exposes `/api/v1/status`, `/state`, `/environment`, `/motion`,
`/health`, `/capabilities`, `/overlay`, `/config`, `/expression`, `/visual-source` and `/events`. Events use
`{type, timestamp, payload}` and suppress consecutive duplicate payloads.

When composed into the optional local web process, the API uses canonical
`web.host` and `web.port`; the committed default is disabled/loopback. Do not
publish it to the Internet. For LAN/remote use, retain administrator
authentication and terminate TLS/additional authentication at a trusted reverse
proxy. Example after authenticating locally:
`curl http://127.0.0.1:8080/api/v1/status`. WSGI uses a low-rate SSE-compatible
event stream; a future WebSocket adapter must reuse this same service boundary.
See [Remote API](remote-api.md) for the endpoint contract, authentication, and
LAN deployment guidance.

## PHOS 1.0.0 finalization

The established 1.0.0 scope is frozen; the separately approved BME280/BMP280 addition is
recorded below. See [release record](release-1.0.0.md)
and [deferred roadmap](roadmap.md). Version is authoritative in
`src/robot/__init__.py`, with package metadata, startup logs and Status using it.
Camera failure/partial-start and cancellation cleanup are hardened. Source sync
ships metadata, docs and the pinned web dependency snapshot while preserving
deployed settings/credentials. Packaged installs include the canonical JSON.

## Validated reload and managed restart

`robot.lifecycle.LifecycleService` owns the active snapshot, load timestamp and
fixed status/reload/restart policy. The web worker uses a local process channel;
it cannot submit commands or paths. Reload validates the entire saved file and
applies logging level, iris appearance and camera preview through their application-service
boundaries, preserving AWS SDK log suppression. Other changed fields are listed
as restart-required. Authenticated restart requires CSRF and
one-use explicit confirmation, then graceful runtime shutdown and exit 75.
`deploy/phos.service` performs process replacement under the desktop user with
no sudo/polkit or shell endpoint. Source sync includes the unit but never enables
it. System actions remain separate from ordinary config forms. OS reboot and all
previously deferred roadmap capabilities remain deferred.

## Eye appearance refinement

Display & Appearance now validates and exposes `display.iris_color` in the
canonical JSON. EyeRenderer receives this theme at startup and layers lightweight
Canvas eye-body, iris, pupil and highlight geometry. FaceState continues to
supply semantic reaction intent; expression labels/providers do not control
rendering. Theme changes apply after configuration reload without restarting
Vision or other runtime services. Raspberry Pi frame-rate impact awaits
target hardware verification.


## Local camera picture-in-picture

An optional diagnostic preview is disabled by default. When active it uses the
existing Vision camera owner, retains only a latest-frame reference in memory,
and passes a UI-neutral image/diagnostic value through PhosRuntime to the display
compositor. Web Admin → Vision settings apply through validated configuration
reload. Frames are neither persisted nor exposed through Web Admin. Enabling the
preview starts the dormant pipeline if no other Vision feature is running;
disabling it releases the camera when no other Vision feature needs it.

## Live eye appearance reload verification

Only `display.iris_color` is currently a configurable eye-style setting and is
live-reloadable. Semantic accents remain BehaviorEngine-produced FaceState and
need no configuration reload. The lifecycle service validates the whole
canonical document, then gives the typed RuntimeConfig to PhosRuntime. Its
display-loop boundary safely queues the iris theme onto the asyncio render
thread; EyeRenderer interpolates the color on subsequent frames. Reload does not
rebuild Core, Vision, camera, BehaviorEngine, providers or the renderer object.
Mixed changes apply logging, iris appearance and preview while retaining other changed
fields in `restart_required`. Invalid configuration applies nothing.

## Final release hardening

The source version remains exactly **1.0.0**. Release status is **not ready to tag**
until the remaining [release record verification](release-1.0.0.md#verification-and-limits) gates
are verified. Earlier test counts are superseded by this audit; git history retains
those historical records.

Implemented fixes: preview defaults off; reload waits for camera lifecycle
acceptance and preserves configuration-relative paths; failed/cancelled startup
releases resources; shutdown drains reload tasks and Vision supervisor waiters;
intentional preview-only camera stop does not trigger a subsystem failure; stopped
pipelines clear snapshots; rapid toggling cannot build up encoder jobs; preview converts BGR to RGB for Tk
without altering Vision/model preprocessing. Runtime
and web-worker failures propagate a nonzero exit so systemd can recover.
Invalid configuration applies nothing. A later hardware apply failure leaves
previously successful appearance changes recorded and reports the failure.

Verification (macOS, Python 3.11.6):

- Focused runtime/main/lifecycle/configuration/AWS-mock/worker-cleanup checks:
  **129 passed**.
- Full suite rechecked on 2026-09-23: **285 passed, 1 skipped, 2 failed** in 219.08 seconds
  (`.venv/bin/python -m pytest -q --tb=short -rs`). Both failures are sandbox
  `PermissionError` binding localhost in `test_real_web_worker_serves_and_releases_port`
  and `test_real_worker_reload_reaches_parent_application`; permission for an
  unsandboxed rerun was declined. The skip is `tests/vision/test_expression.py:105`
  because `cv2` is unavailable. No test was weakened/skipped to hide these failures.
  Installing OpenCV and wheel and rerunning the localhost tests outside the
  sandbox were requested again during this verification and declined. These
  acceptance checks remain blocked; no additional implementation change was needed.
- Final acknowledgement-path runtime/main/lifecycle/UI checks: **58 passed**
  after retaining completion acknowledgement across an IPC timeout.
- No real AWS calls or Pi/display/systemd operations were performed. Mobile/tablet
  review covered responsive CSS/templates and form tests, not a physical browser.
- `pip check`, canonical version/config validation, CLI help, shell syntax and
  diff whitespace checks pass. Source archive built with 1.0.0 metadata and
  required canonical config, unit, web assets and dependency snapshot verified.
- Wheel/editable installation unavailable locally (`bdist_wheel` missing).
  The local venv has an old installed 0.1.0 package; source launch and tests select
  `src` explicitly. This is not the authoritative release version.
- Targeted tracked-file scans found no AWS key/private-key patterns or tracked
  generated artifacts. Functional deprecated CLI overrides, eye demo and paired
  Vision benchmark are retained; no roadmap scaffolding was removed.

The fresh-install guide now provides one complete path through dependencies,
configuration, model/SDK selection, credentials and the recommended user service.
Release documentation owns exact acceptance steps and performance limitations;
no new Pi performance or recognition-quality claim is made.


## BME280 / BMP280 follow-on integration

The explicitly requested BME280/BMP280 addition is implemented separately from the
frozen 1.0.0 baseline (ADR-022); the source version remains unchanged. It is
disabled by default. `robot.sensors` provides immutable environmental values,
a synchronous provider contract and one bounded worker with current-state
snapshots. `robot.hardware.bme280` owns lazy RPi.bme280/smbus2 access to bus 1,
address 0x76/0x77, chip-ID validation, calibration, sampling and cleanup.
`robot.hardware.bmp280` adds Pimoroni BMP280 with the same ownership boundary;
`hardware.environmental` centralizes type selection. Nullable humidity and
explicit capabilities distinguish unsupported humidity from unavailable samples.

Runtime starts/stops the service; sensor absence, invalid samples and I/O errors
stay local to sensor availability with retry/backoff. Native slow calls cannot
block eyes/Vision or accumulate jobs. Monotonic freshness and UTC update time
prevent old readings being shown as current. The lifecycle status snapshot sends
read-only state to authenticated Web Admin → Sensors over the existing channel.
All five new canonical settings require restart; no CLI fields or behavior
coupling were added. Existing deployment JSON needs the explicit `sensors.environmental` migration
and type field; Web Admin offers both types and shows the active sensor.

See [installation](installation.md#optional-environmental-sensor) for exact
Pi commands and [hardware](hardware.md#bme280) for expected signal wiring and
unverified breakout details. Physical I2C/accuracy/combined-runtime acceptance
remains pending. The CCS811, MPU-6050 and LED-ring extensions are recorded below.

Verification on 2026-09-23 (macOS, Python 3.11.6):

- Final complete suite: **325 passed, 1 skipped** in 151.16 seconds
  (`.venv/bin/python -m pytest -q --tb=short -rs`, outside the sandbox with
  permission for localhost binding). Both real web-worker tests passed.
- The sole skip is the existing Vision preprocessing test at
  `tests/vision/test_expression.py:105`, because local `cv2` is unavailable.
- Sensor coverage uses fake providers/vendor modules: disabled/no access,
  both addresses and chip ID, units, invalid values, polling/freshness,
  backoff/recovery, render continuity, bounded shutdown during read/start,
  clean restart, config migration/validation, lifecycle IPC and web display/save.
- `pip check`, Python compilation, startup `--help`, shell syntax and
  `git diff --check` passed. Optional BME280 libraries were not installed locally;
  real I2C and Raspberry Pi resource measurements were not performed.

These BME280-only results are historical; the extension verification is recorded below. No commit, deployment or physical hardware acceptance was performed.


### Selectable environmental sensor extension (2026-09-24)

Changed implementation files: `config/phos.json`, `pyproject.toml`,
`src/robot/config.py`, `src/robot/runtime.py`, `src/robot/sensors.py`,
`src/robot/hardware/bme280.py`, `src/robot/hardware/bmp280.py`,
`src/robot/hardware/environmental.py`, `src/robot/web/configuration.py`,
`src/robot/web/domains.py`, `src/robot/web/templates/configuration.html`,
`tests/test_sensors.py`, and `tests/test_web.py`.
Documentation updated: README, hardware, installation, development/configuration,
web administration, architecture, decisions, current state and roadmap.
The pre-existing uncommitted BME280 work is extended, not discarded; existing
lifecycle/main/web-app integration is retained without further changes.

The shared provider/service now supports both chips. Runtime snapshots carry
active type, explicit available measurements, values, freshness and health.
BMP280 humidity is null and displayed as Not supported. Wrong chip IDs and
capability mismatches are unavailable, with bounded retries and clear logs.
All five `sensors.environmental` settings require restart; no hot swapping.
The required config migration is documented in development. RPi.bme280 remains
unchanged; BMP280 uses the optional bmp280 1.x / i2cdevice library and the same
adapter-owned smbus2 bus. Pi setup and runnable physical checks remain in
installation; no deployment or actual wiring/accuracy acceptance was performed.

Verification (macOS, Python 3.11): sensor suite **50 passed**; final complete
suite **340 passed, 1 skipped** in 223.37 seconds
(`.venv/bin/python -m pytest -q --tb=short -rs`, with permission for localhost
binding). The single skip is the existing Vision preprocessing check because
`cv2` is unavailable. Both real web-worker checks passed. The sandbox-only run
had two localhost-binding PermissionErrors; these disappeared in the authorized
run. Canonical JSON validation, Python compilation, `pip check` and
`git diff --check` passed. Driver distributions were inspected without installing
sensor dependencies locally. No unresolved architectural conflicts were found.


### CCS811 air-quality follow-on (2026-09-24)

The explicitly requested CCS811 capability extends the sensor services (ADR-023).
`hardware.ccs811.CCS811Provider` uses smbus2, validates chip/application status,
initializes mode 1, handles readiness and returns typed eCO2 ppm / TVOC ppb.
`AirQualitySensorService` shares the existing bounded worker with the environmental
service; neither failure nor conditioning blocks rendering/Vision. eCO2 is
estimated equivalent CO2, not direct NDIR CO2.

Canonical `sensors.ccs811` has enabled, I2C address, polling and stale timeout;
all require restart and the committed default is disabled. Existing deployment
JSON must merge this required block. The Sensors page includes configuration,
measurements, timestamp/age, health and compensation input over the same lifecycle
channel. Fresh temperature/humidity may pass through the environmental service;
BMP280 or unavailable sources restore device defaults. Conditioning withholds
values for 20 minutes after initialization; first-use/physical stability remains
unverified. No baseline storage, firmware loader or GPIO wake controller is added.

Implementation files changed for this task: `src/robot/hardware/ccs811.py`,
`src/robot/sensors.py`, `src/robot/runtime.py`, `src/robot/config.py`,
`config/phos.json`, `pyproject.toml`, `src/robot/web/configuration.py`,
`src/robot/web/domains.py`, `src/robot/web/templates/configuration.html`,
`tests/test_air_quality.py`, `tests/test_web.py`. README and hardware, installation,
development, web administration, architecture, decisions, roadmap and this handoff
were updated. Prior uncommitted environmental work is preserved.

Physical TODO: identify the precise Keyestudio/SEN breakout, verify supply and
Pi-side logic/wake wiring, sustained bus transfers, conditioning/stability,
compensation, failure recovery and concurrent eyes/Vision/Web Admin operation.
Use the concrete commands/checks in installation. No hardware acceptance,
deployment or commit is claimed. WS2812B remains out of scope; the MPU-6050 follow-on is recorded below.

Verification for CCS811: **88 sensor tests passed** (environmental regression
plus air quality); **3 targeted Web Admin tests passed**. Final full suite:
**379 passed, 1 skipped** in 220.95 seconds with
`.venv/bin/python -m pytest -q --tb=short -rs` and localhost socket permission.
The sole skip is the existing Vision preprocessing test because local `cv2` is
unavailable. Canonical JSON validation, Python compilation, `pip check` and
`git diff --check` passed. No physical I2C test was run. No architectural conflict
was found; the electrical identity of the actual SEN/Keyestudio board remains a
physical acceptance prerequisite, explicitly distinguished in hardware notes.

### CCS811 initialization diagnosis follow-up

User reports HW_ID=0x81 after sensor power recovery, then all registers=0xff
once PHOS starts, persisting after PHOS stops with BMP280 disconnected. This
localizes the trigger to startup interaction but does not establish the exact
physical cause. The unconditional SW_RESET was removed from normal startup;
APP_START is now conditional on boot mode, and mode 1 is verified after writing.
Initialization errors include address, phase and actual values; all-ones STATUS
is rejected before interpreting flags. No config or architecture changes.

Changed `src/robot/hardware/ccs811.py`, `tests/test_air_quality.py`, installation
troubleshooting and this handoff. Mock tests cover boot/application startup,
reconnect without reset, and invalid reads at each initialization checkpoint.
The physical fix remains pending a test using the updated file on the Pi after
power recovery. No deployment or commit was performed.

Verification of this follow-up: **94 sensor tests passed**; full suite **385
passed, 1 skipped** in 222.07 seconds (`.venv/bin/python -m pytest -q --tb=short
-rs`, with localhost socket permission). The skip remains missing local `cv2`.
Compilation and `git diff --check` passed. These are software checks, not
confirmation that removing SW_RESET resolves the reported physical failure.

### MPU-6050 IMU follow-on (2026-09-25)

The requested GY-521 / MPU-6050 extension is implemented as a separate typed
sensor path: `MPU6050Provider` owns smbus2/register access and `IMUSensorService`
owns polling, freshness, retry and status. `IMUReading` exposes acceleration XYZ
in m/s² and angular velocity XYZ in °/s. Canonical `sensors.imu` is disabled by
default and supports `0x68`/`0x69`; all changes require restart. Web Admin edits
that block and reads a service snapshot only. The adapter uses ±2 g / ±250 °/s
factory scale factors, without automatic offset calibration, sensor fusion,
orientation or behavior integration. Physical wiring and concurrent Pi testing
remain pending.

The follow-on motion interpreter derives stable STILL, MOVING, tilt, SHAKE and
IMPACT observations from IMU service samples only. Its configurable thresholds
are live-reloadable; hardware configuration remains restart-only. Their raw
status is visible in Web Admin.

The approved follow-on now routes stable IMU motion state changes through Core
to BehaviorEngine. It produces visible FaceState-only tilt, movement, shake and
impact reactions; no IMU-to-renderer, GPIO or LED connection exists. Behavior
parameters reload live, while Core states retain priority.

Tilt interpretation now uses normalized filtered gravity ahead of MOVING, with
exit hysteresis, explicit signed mounting axes and temporal confirmation.
Canonical polling is 20 Hz; deployment migration and Pi verification are in the
MPU-6050 installation guide. Hardware acceptance remains pending.

IMU visual intent is deliberately amplified for the 800×600 display: persistent
movement and tilts stay active while observed, while shake and impact use alert
overlays with hold then decay. Impact has the strongest allowed FaceState eye
opening and reaction strength. All visual tuning remains live-reloadable.

Horizontal IMU tilt now has explicit mirrored eye asymmetry: TILT_LEFT opens the
left eye and TILT_RIGHT opens the right. Forward/back and transient IMU alerts
keep equal eye sizes. The renderer receives only signed FaceState intent and
smoothly interpolates left/right openings independently.

The optional WS2812B ring is now implemented as a separate `FaceState` semantic
consumer. Its provider is lazy-loaded and disabled by default; it never receives
raw IMU, Vision or sensor input. Physical electrical and Pi acceptance remain
pending.
