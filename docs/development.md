# Development Guide

## Local development

The application should support a development mode that does not require Raspberry Pi hardware.

Use interfaces and fakes/mocks for hardware.

## Configuration

`config/phos.yaml` is the **canonical configuration and complete example**.
The complete YAML structure and authoritative default values are in
[the canonical file](https://github.com/phosairobot/phos/blob/main/config/phos.yaml); edit it directly and restart PHOS:

```bash
python3 src/robot/main.py
```

No separate example/provider files or hidden overlays are loaded. With no
argument, startup discovers `phos.yaml`, then `phos.yml`, then legacy `phos.json`
relative to the source checkout, not its working directory. A custom `--config`
selects one complete file instead.
To keep a separate deployment copy, copy the full canonical file, edit it and
pass its path explicitly. Keep deployment changes out of commits if inappropriate.
No file may contain credentials.

The eight required sections are `web`, `display`, `led_ring`, `behavior`, `vision`, `expression`
(with `smoothing`, `local`, `aws`), `sensors` (with `environmental`, `ccs811` and `imu`) and `logging`. `vision` includes `detector`.
Only the implemented environmental, CCS811 and MPU-6050 services have sensor configuration; there are no speculative runtime/voice settings. Every field in the
canonical file is required, including null values and inactive-provider settings;
a missing value is an error, not a second default hidden in code.

### Field reference

Values/defaults are maintained in `config/phos.yaml`. All numeric values must be
finite; booleans must be YAML booleans, not quoted strings or numbers.

| Section                  | Fields and purpose                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| ------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `sensors.environmental`  | `type`: `"bme280"` or `"bmp280"`; `enabled`: boolean; `i2c_address`: string `"0x76"` or `"0x77"` on bus 1; `poll_interval_seconds`: finite 1–3600 seconds; `stale_after_seconds`: finite, greater than poll interval and at most 86400 seconds. All five require PHOS restart.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| `sensors.ccs811`         | `enabled`: boolean; `i2c_address`: canonical lowercase `"0x5a"` or `"0x5b"`, bus 1; `poll_interval_seconds`: finite 1–3600 seconds; `stale_after_seconds`: finite, greater than poll interval and at most 86400 seconds. All four require PHOS restart.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| `sensors.imu`            | `enabled`: boolean; `i2c_address`: canonical lowercase `"0x68"` or `"0x69"`, bus 1; `poll_interval_seconds`: finite 0.05–3600 seconds; `stale_after_seconds`: finite, greater than poll interval and at most 86400 seconds. All four require PHOS restart.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| `sensors.imu.motion`     | `movement_threshold_m_s2`, `tilt_threshold_m_s2`, `shake_threshold_deg_s`, `impact_threshold_m_s2`, `confirmation_seconds`, `cooldown_seconds`: finite thresholds/timing. Impact must exceed movement. Also required: `tilt_exit_threshold_m_s2` (positive, below enter; both at most standard gravity), and distinct signed `lateral_axis` / `forward_axis` mounting axes. All motion fields are live-reloadable and do not reinitialize I2C. See installation for normalized threshold semantics.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| `led_ring`               | `enabled`: boolean; `led_count`: 0–1024 integer, positive when enabled; `gpio_pin`: GPIO 0–27; `brightness`: 0–1; `base_color` and `imu_animation_color`: named PHOS palette colors; `follow_visual_state`: boolean; `update_rate_hz`: 1–30. IMU effects add directional/shake/impact strengths (0–1; impact exceeds shake), fill speed, sector size, and physical `bottom_led_index` / `forward_led_index` / `clockwise` mapping (`forward_led_index` is the physical top). Left starts at the left quarter and fills clockwise; right mirrors it from the right quarter. Back grows in two fronts from bottom; forward grows in two fronts from top. Every fill replaces only reached underlying pixels; STILL resolves the current persistent environmental or base state rather than forcing base color. Changing animation color applies live and affects the next rendered frame. Pin/count require restart; all other fields are live-reloadable. Brightness uniformly scales logical RGB channels; WS2812B GRB ordering is handled only by the provider. |
| `behavior` IMU fields    | `imu_reaction_strength`, `imu_tilt_gaze_strength`, `imu_shake_reaction_strength`, and `imu_impact_reaction_strength`: 0–1; impact strength must exceed shake strength. `imu_tilt_eye_asymmetry_strength`: 0–0.5 signed-eye delta. Shake/impact duration: 0.1–30 seconds; cooldown: 0–3600 seconds. These eight fields are live-reloadable.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| `behavior.environmental` | Optional temperature/eCO2/TVOC interpretation. Temperature enter/exit values provide hysteresis; warning/bad eCO2 and TVOC thresholds ascend. Confirmation and recovery are positive seconds. All fields are live-reloadable and never reopen sensor hardware.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| `web`                    | `enabled`: start the administration worker; `host`: IPv4/IPv6 bind address; `port`: integer 1–65535. See the [web manual](web-administration.md).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| `display`                | `width`, `height`: positive integer pixel dimensions; `fps`: positive integer display cadence; `fullscreen`: fullscreen startup; `transition_seconds`: positive renderer interpolation duration; `iris_color`: one of cyan, blue, green, turquoise, amber, violet or white. `base_visual_source` is `manual`, `environment`, or `state`. `environment_overlays_enabled` controls semantic sweat/snow/haze decorations; they never change eye geometry, iris, or LED output. These display fields are live-reloadable.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| `behavior`               | `blink_interval_seconds`, `gaze_interval_seconds`: positive ascending `[minimum, maximum]` timing ranges; `face_gaze_smoothing`: gaze smoothing coefficient in (0,1]; `reaction_decay_per_second`: positive visual reaction decay.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| `vision`                 | `face_tracking_enabled`: camera/tracking without expression inference; `camera_resolution`: positive integer `[width,height]`; `capture_fps`, `detection_fps`: positive capture/detection cadences. `camera_preview` controls optional display-only picture-in-picture, disabled by default; all its fields (enabled, corner position, scale 0.1–0.4, maximum FPS 1–10 and three diagnostic toggles) are live-reloadable.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| `vision.detector`        | `cascade_path`: custom readable Haar file or null for existing platform discovery; `scale_factor`: pyramid scale greater than 1; `min_neighbors`: nonnegative integer detection support; `min_size`: positive pixel pair no larger than the camera resolution.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| `expression`             | `enabled`: expression processing, also enabling local tracking; `provider`: local/aws; `inference_fps`: positive local inference/cloud polling cadence; `crop_margin`: extra square-crop margin per side in [0,0.5].                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| `expression.smoothing`   | `minimum_confidence`: additional evidence floor in [0,1]; `minimum_observations`: positive integer count of distinct samples; `local_maximum_gap_seconds`: positive maximum gap for local evidence; `neutral_enabled`: enable neutral perception only after calibration. Cloud maximum gap is derived from its TTL.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| `expression.local`       | `model_path`: ONNX path or null; `labels`: unique, nonempty strings in output order (array may be empty only when local inference is inactive); `input_size`: positive `[width,height]`; `scale`: positive preprocessing multiplier; `mean`: three finite channel means; `swap_rb`: swap BGR/RGB; `grayscale`: existing grayscale preprocessing. When grayscale is true, provider behavior ignores swap_rb.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| `expression.aws`         | `region`: nonempty region string or null for external SDK/environment resolution; no credentials.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| `expression.aws`         | `cooldown_seconds`: minimum request interval; `stable_seconds`: eligible local continuity before requesting; `refresh_seconds`: refresh unchanged input; `cache_ttl_seconds`: maximum sample age. All positive; refresh must be less than TTL.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| `expression.aws`         | `max_requests_per_minute`: positive rate implemented as minimum spacing; `max_requests_per_session`: nonnegative integer cap, zero means unlimited; `change_threshold`: normalized crop difference threshold in [0,1]; `minimum_face_confidence`: AWS face-confidence floor in [0,1].                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| `expression.aws`         | `retry_initial_seconds`, `retry_max_seconds`: positive backoff limits, maximum at least initial; `connect_timeout_seconds`, `read_timeout_seconds`: positive SDK timeouts.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| `logging`                | `level`: DEBUG/INFO/WARNING/ERROR/CRITICAL; `file`: output path or null for console only; `expression_diagnostics`: detailed Vision/cloud diagnostics. SDK debug output is suppressed to avoid exposing request/credential metadata.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |

All configuration paths resolve relative to the selected configuration file's directory. The
canonical local path therefore starts with `../models/`, and its log path points
back to the checkout root. Active local models and explicit active cascades must
be readable files. Inactive models need not exist; AWS mode needs no ONNX file.
The log parent must already exist and be writable. PHOS does not create arbitrary
configuration/model/log directories. ONNX content/model-output compatibility and
physical device availability still require runtime verification.

### Provider examples

Edit these fields **inside the complete file**, preserving the other fields.
These are illustrative fragments, not additional partial configuration files:

```json
{ "expression": { "enabled": true, "provider": "local" } }
```

The canonical `expression.local` block already contains the MobileFaceNet input
contract. Download its model using [installation](installation.md). For FER+
rollback use the block in [Vision](vision.md), replacing every preprocessing
field rather than depending on former CLI defaults.

```json
{ "expression": { "enabled": true, "provider": "aws" } }
```

Keep the existing `expression.aws` block; optionally edit region and request
policy. No fallback is supported. All camera selection/cropping stays local;
AWS receives selected crops only. See [Vision](vision.md) for privacy and policy.
AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY and, for temporary credentials,
AWS_SESSION_TOKEN are external environment credentials. AWS_REGION /
AWS_DEFAULT_REGION, shared SDK profiles and roles are also supported.
A null JSON region defers resolution to the external environment/SDK. Parsing
never imports boto3, searches for credentials or contacts AWS.

### Validation, precedence and migration

Startup reads one JSON, applies explicit deprecated CLI overrides, validates
schema/types/ranges and active paths, then builds typed settings and constructs
subsystems. Errors name the field or JSON line/column and exit with status 2
before camera/display startup. Duplicate keys, unknown keys (including secrets),
missing sections/fields and incompatible refresh/TTL or detector/camera sizes
are rejected. Finite numeric validation also rejects NaN/infinity.

Precedence: explicit legacy CLI value > selected JSON value. CLI values have no
independent defaults and never overwrite the file. `--expression-provider` or
`--expression-model` also enables expression processing for compatibility;
`--face-tracking` enables tracking without changing expression enablement.
`--aws-region` overrides JSON region, followed by AWS_REGION, then the standard
SDK AWS_DEFAULT_REGION/profile fallback. CLI model paths retain their old
working-directory-relative interpretation; JSON paths are config-relative.

Retained but deprecated: `--face-tracking`, `--expression-provider`,
`--aws-region`, `--expression-model`, `--expression-labels`,
`--expression-input-size`, `--expression-scale`, `--expression-mean`,
`--expression-no-swap-rb`, `--expression-grayscale`, `--expression-debug`,
`--expression-crop-margin`. Help and startup warn about this transition.
`--config` and `--help` remain the supported primary interface. No runtime flags
were removed in this migration. Migrate launch scripts now; remove compatibility
flags only in a separately announced breaking change after consumers migrate.

The old partial flat JSON format and `config/expression-local.json` /
`config/expression-aws.json` are retired. To migrate, start from `config/phos.yaml`:

| Former field(s)                                                                                                                                            | Canonical destination                                      |
| ---------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------- |
| `display_fps`, `fullscreen`                                                                                                                                | `display.fps`, `display.fullscreen`                        |
| `face_tracking_enabled`, `camera_resolution`                                                                                                               | same names under `vision`                                  |
| `vision_capture_fps`, `face_detection_fps`                                                                                                                 | `vision.capture_fps`, `vision.detection_fps`               |
| `expression_provider`                                                                                                                                      | `expression.provider`; set `expression.enabled` explicitly |
| `expression_inference_fps`, `expression_crop_margin`                                                                                                       | `expression.inference_fps`, `expression.crop_margin`       |
| `expression_minimum_confidence`                                                                                                                            | `expression.smoothing.minimum_confidence`                  |
| `expression_model_path`, `expression_labels`, `expression_input_size`, `expression_scale`, `expression_mean`, `expression_swap_rb`, `expression_grayscale` | `expression.local` with the `expression_` prefix removed   |
| `cloud_expression`                                                                                                                                         | `expression.aws` (same member names)                       |
| `expression_diagnostics`                                                                                                                                   | `logging.expression_diagnostics`                           |

The current runtime previously combined flat dataclass defaults, CLI overrides,
provider configuration, renderer/behavior/detector constructor defaults and
hard-coded logging setup. Composition now passes all applicable settings
explicitly from the canonical file. Standalone library/demo/benchmark constructor
fallbacks remain for compatibility, but are not application configuration sources.
Geometric face association gates, class-specific semantic safeguards, visual
profiles and SDK JPEG implementation details remain implementation constants.
No AI/voice settings are added for subsystems outside this runtime milestone.

### Presence and attention configuration

`presence.led_reactions` controls optional LED-only semantic reactions. Its
`enabled` flag gates the feature; `entered` defaults to an 800 ms clockwise
sweep and `left` to a 900 ms counter-clockwise sweep. Each nested block has a
positive `duration_ms` and a `direction` of `clockwise` or
`counter_clockwise`. `attention.lost_hold_ms` is a non-negative millisecond
hold between a confirmed departure and `attention=idle`. Presence is below
error/sleep and IMU shake/impact transient priority; after a sweep completes,
the LED controller resolves the current persistent visual state rather than a
cached pre-sweep state.

Older full configuration documents that predate these two known blocks are
migrated in memory by explicit default injection before normal exact-key
validation. The file is not rewritten merely at startup; the migrated canonical
blocks are persisted by the next ordinary configuration save. This is a scoped
schema evolution rule: unknown/misspelled keys and unrelated missing fields
continue to fail strict validation.

### Expression reaction configuration

`expression_reactions` is a provider-neutral policy for the uncertain
`ObservedExpression` read model. `enabled`, `min_confidence`,
`confirmation_ms`, `cooldown_ms`, and `reaction_duration_ms` bound whether a
supported observation may produce a transient PHOS response. It is not an
emotion inference or a direct classifier-to-renderer mapping. Existing complete
documents missing this known block receive the scoped default injection above;
strict validation still rejects all other missing or unknown fields.

### Startup experience configuration

`startup.splash.enabled` keeps the local face display on a lightweight PHOS
startup screen until the runtime reaches `ready` or acceptable `degraded` state.
`startup.splash.image`, `title`, and `subtitle` configure the deep-navy branded
screen. A null image/file selects PHOS's installed `robot.assets` PNG/WAV;
non-null overrides are resolved relative to the loaded `phos.json`, never the
process working directory. The screen is deliberately unlike normal eyes and is
flushed once before synchronous subsystem startup continues.
`failed` remains on that screen. `startup.ready_sound` is an optional local WAV
file played once with the lightweight system `aplay` command after the transition;
when enabled its file must be readable. The cursor is hidden only on the local
Tk face canvas, without disabling touch/pointer events or changing Web Admin.
`player` is currently `aplay`; optional `device` is passed as `aplay -D DEVICE`.
At startup, `STARTUP ASSETS` logs the configured and resolved splash/sound paths
plus existence and readability. The Tk loader then logs the image format and
dimensions, or its loader exception. The ready-sound log includes the resolved
`aplay` binary, full command, return code, and stderr. For Pi troubleshooting,
run that logged command manually (for example `aplay -q -D default path/to/ready.wav`).
The `phos.service` unit runs `src/robot/main.py` with configuration discovery and
`WorkingDirectory=%h/phos`; it sets no
audio-session environment variables. Package defaults therefore do not depend
on either the working directory or a developer home directory; ALSA selection is
limited to the optional `startup.ready_sound.device` value.
The repository configuration selects the packaged
`robot.assets/images/phos-startup-800x600.png` and `robot.assets/audio/phos-startup.wav`.
`run_pi.sh` transfers them as part of `src/`, and a wheel includes them as package
data. The helper deliberately preserves an existing Pi configuration; merge
the `startup` block from the repository configuration into an already deployed
configuration before expecting these defaults to take effect.

### Reusable configuration API and web layer

`robot.config.RuntimeConfig` is the existing typed surface moved out of runtime;
`robot.runtime.RuntimeConfig` remains import-compatible. Cloud settings retain
`CloudExpressionConfig`, re-exported from the AWS adapter for existing callers.
JSON section names are mapped to typed fields at the boundary; raw dictionaries
never reach providers or behaviors.

- `load_document(path)` reads JSON for an editor without hardware/SDK imports.
- `RuntimeConfig.from_file(path)` loads and validates a full file.
- `RuntimeConfig.from_dict(document, base_dir=...)` validates edited settings
  with the same schema/rules, including config-relative paths.
- `config.to_dict()` returns the full serializable non-secret structure.
- `config.save(path)` validates and atomically replaces a file, rebasing paths
  if the file moves; a failed validation preserves the old file.

Existing `RuntimeConfig(**overrides)` and `CloudExpressionConfig(**overrides)`
Python calls overlay the canonical file for compatibility; they contain no
independent numeric defaults. Runtime construction revalidates before hardware
starts. New application code should use full-file/dict loading instead.

The optional [web administration adapter](web-administration.md) reuses this
read/edit/validate/save boundary. It never accepts/stores/displays AWS credentials
as normal settings. Its separate password store is not runtime configuration.
Save does not apply settings. The lifecycle service can reload logging level,
`display.iris_color` and all `vision.camera_preview` fields through runtime and
display boundaries; other persisted changes require restart. Preview reload
waits for runtime acceptance and preserves config-relative paths. The IPC client
bounds its response wait to five seconds; a slow native camera start can finish
after that timeout, so an unavailable response explicitly reports uncertainty
and retires the channel until PHOS restarts. A hardware apply failure is reported; earlier successful appearance
changes remain active and pending fields remain visible. This differs from an
invalid configuration, which applies nothing.
The editor may call `from_dict(..., check_paths=False)` to repair a removed model
path; schema/types/ranges are still validated. Startup and saves always validate
active paths.
`robot.web.domains` maps canonical field paths into navigation and presentation
only. Page saves merge the selected area's fields into the full document, reject
out-of-area fields and run normal full-document validation/atomic persistence.
The registry defines no defaults, types or validation rules. New implemented
settings must be assigned to a domain; coverage tests require every canonical
field to appear exactly once. Read-only General/Status pages cannot save settings.
New non-secret runtime options must extend this canonical model/file, not add
standalone CLI arguments or another configuration mechanism.

## Dependencies

Before adding a dependency, consider:

1. Is the standard library sufficient?
2. Is the dependency maintained?
3. Does it work on Raspberry Pi 3?
4. Does it materially simplify the implementation?

## Testing

Test:

- state transitions
- event handling
- AI tool contracts
- hardware adapters through fakes
- error handling

Do not make ordinary unit tests depend on physical GPIO/audio/display hardware.

For web development install `.[web]` plus pytest in a virtual environment and
run `python -m pytest -q`. Web tests exercise real password hashing, CSRF,
configuration persistence and an isolated local WSGI worker without hardware.

## Release version and installation

`robot.__version__` in `src/robot/__init__.py` is authoritative. Setuptools derives
metadata from that literal; do not add another independently maintained version.
Source checkouts use `config/phos.yaml`; wheels install it with the legacy JSON
asset under the environment's `share/phos/config/` data directory. Explicit `--config`
remains the supported deployment boundary; no alternate schema is introduced.
The pinned Python 3.11+ web snapshot is `requirements-web.txt`; platform camera
dependencies remain managed by Raspberry Pi OS. See the release record.

`robot.lifecycle` is the reusable lifecycle policy boundary. Its fixed operations
are status/reload/restart; it never accepts arbitrary commands or configuration
paths from callers. The web worker uses `robot.lifecycle_channel` to reach the
parent-owned service. Confirmation/authentication remain adapter responsibilities;
validation and restart capability policy are shared. Test both the service and
adapter guards. Infrastructure service markers are deployment metadata, not
ordinary JSON settings. No new runtime configuration section is needed.

### Environmental configuration migration

For an existing BME280 deployment, rename `sensors.bme280` to
`sensors.environmental`, retain its four existing values and add `"type": "bme280"`.
Do not retain both blocks. To use BMP280 choose `"type": "bmp280"`; humidity is
unsupported. The Python RuntimeConfig fields now use the `environmental_` prefix.

When updating a deployment without sensor settings, merge the complete `sensors` object from
`config/phos.yaml` into its existing configuration, preserving other settings. The new
section and all five fields are required even when disabled. As with previous
schema additions, missing fields fail validation; no silent overlay/migration or
second set of defaults is introduced. `run_pi.sh` preserves deployed JSON and
does not perform this merge. Validate locally before restarting the service.
No new CLI flags are added. Save/Reload classify every sensor difference as
restart-required; unrelated services are not restarted by either action.

Hardware-free sensor checks: `.venv/bin/python -m pytest -q tests/test_sensors.py`.
The adapter uses lazy optional imports; disabled operation and ordinary tests
need neither Linux I2C access nor the driver packages. Fake drivers test address,
chip ID, calibration and unit mapping; the service tests failure recovery,
staleness, polling and bounded shutdown independently of physical hardware.

### CCS811 configuration migration

Merge the complete `sensors.ccs811` block from the canonical YAML into an existing
deployment; preserve `sensors.environmental`. All four fields are required,
including when disabled. Default values live in `config/phos.yaml`.
The typed surface uses the `ccs811_` prefix; no CLI overrides were added. Save
and Reload do not reinitialize the device or change poll timing. Use Restart PHOS.

`AirQualityReading` carries integer `eco2_ppm` and `tvoc_ppb`; invalid values are
rejected before publication. Device-specific ranges live in the CCS811 adapter.
The shared worker handles lifecycle, stale values, retry/backoff and bounded
shutdown for both sensor services. `SensorNotReady` preserves initialization
and communicates conditioning/no new data; physical faults recreate the provider.
Compensation passes an immutable `EnvironmentalCompensation` from a fresh
service snapshot; it never invokes environmental hardware from the air-quality
worker. Temperature must be within -25–50 °C and humidity within 0–100% for this
compensation boundary. BMP280, disabled/failed/stale or out-of-range sources
supply no compensation, restoring device defaults. Device constants such as
conditioning/drive mode are adapter protocol policy, not duplicate JSON defaults.

Run `.venv/bin/python -m pytest -q tests/test_air_quality.py tests/test_sensors.py`
and the full suite. Tests use fake time, bus registers and providers, covering
conditioning without a 20-minute sleep. See the
[installation guide](installation.md#optional-ccs811-air-quality-sensor) for
physical verification and dependency commands.

### MPU-6050 configuration migration

Merge the complete `sensors.imu` block from canonical JSON into an existing
deployment, preserving the environmental and CCS811 blocks. The typed surface
uses the `imu_` prefix. All four fields are required even when disabled and are
restart-only. `IMUReading` contains finite acceleration values in m/s² and
angular velocity values in °/s. It has no persisted calibration state: the
adapter applies fixed factory scale factors only. Run
`.venv/bin/python -m pytest -q tests/test_imu.py` before Pi validation.

### Tilt interpretation configuration migration

Add `tilt_exit_threshold_m_s2`, `lateral_axis` and `forward_axis` from canonical
`sensors.imu.motion` to existing deployments; missing fields fail validation.
The existing tilt enter key is preserved but now refers to a normalized gravity
component. Review enter/exit together. For responsive operation, adopt the
canonical IMU poll interval and confirmation duration; polling requires restart,
motion thresholds/mounting require only reload. See the installation guide for
axis verification and DEBUG diagnostics.
