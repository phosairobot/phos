# PHOS 1.3.0 — Presence, Attention & Expression Awareness

![PHOS robot](img/phos.png)

Modular robot targeting Raspberry Pi 3. Version 1.3.0 adds semantic Presence
and Attention, transient presence LED reactions, observed facial-expression
telemetry, and a cautious expression-reaction policy to the existing Remote API
and Web Admin baseline. See the [release scope and limitations](docs/release-1.3.0.md),
[installation guide](docs/installation.md) and [administration manual](docs/web-administration.md).

The public documentation portal is published through GitHub Pages at
<https://fazzola.github.io/phos/>. It presents the 1.3.0 source release candidate
and links back to these canonical repository documents; it is not a release tag.

## Install on Raspberry Pi OS

```bash
git clone https://github.com/phosairobot/phos.git
cd phos
./scripts/install-phos.sh
```

The installer is the recommended Raspberry Pi OS/Debian ARM setup path. It
installs supported runtime software, prepares the Local ONNX model, and performs
software-only checks; see the [canonical installation guide](docs/installation.md)
for required display/camera/hardware steps and the manual contributor path.

For a local website preview, install `requirements-docs.txt` and run
`.venv/bin/python -m mkdocs serve`; use `.venv/bin/python -m mkdocs build --strict`
for a production build in `build-site/`.

## Contact
@phosairobot

## Current hardware
- Raspberry Pi 3
- 5-inch 800x600 display
- Microphone

## Project principles
- Python first
- Modular architecture
- Hardware abstraction
- Test without hardware
- Incremental development
- Keep Raspberry Pi 3 constraints in mind

## Start here
1. Read `AGENTS.md`
2. Read `docs/architecture.md`
3. Read `docs/hardware.md`
4. Read `docs/roadmap.md`
5. Read `docs/decisions.md`

The optional BME280/BMP280 and CCS811 sensors drive confirmed environmental
behavior; CCS811 eCO2 is estimated/equivalent CO2, not direct NDIR CO2. The
optional GY-521/MPU-6050 drives interpreted motion behavior, and the optional
WS2812B ring renders semantic environmental color plus temporary IMU effects.
All new hardware remains subject to the physical acceptance checklist. Voice,
conversational AI, Home Assistant, remote control API and MCP remain deferred.

## Run PHOS

For production use the [user systemd service](docs/installation.md#managed-startup-and-browser-restart).
For a foreground diagnostic run from a graphical Raspberry Pi OS desktop session:

```bash
.venv/bin/python src/robot/main.py
```

[config/phos.yaml](config/phos.yaml) is the preferred, editable configuration and
complete default example. It starts the fullscreen animated eyes with camera
and expression processing disabled. Omitting `--config` discovers `phos.yaml`,
then `phos.yml`, then legacy `phos.json`. JSON remains supported for existing
installations and explicit `--config` paths. Press Ctrl+C to stop and Escape to leave fullscreen.

Edit the file, then restart using the same command:

- **Face tracking only:** set `vision.face_tracking_enabled` to `true`, keeping
  `expression.enabled` false. Install the camera/OpenCV dependencies first.
- **Camera preview:** set `vision.camera_preview.enabled` to `true`, then reload.
  The image appears on the robot display only, not in the browser.
- **Local expressions:** set `expression.enabled` to `true` and
  `expression.provider` to `"local"`. The included `expression.local` settings
  describe MobileFaceNet; download the model following
  [installation](docs/installation.md). Images remain on the Pi.
- **AWS expressions:** set `expression.enabled` to `true` and
  `expression.provider` to `"aws"`. Install boto3 and configure external
  credentials/region using [AWS setup](docs/installation.md#optional-aws-expression-mode).
  **Selected face crops leave the Pi and are sent to AWS.**

Expressions automatically enable local tracking. For diagnosis set
`logging.expression_diagnostics` to `true`; check `PHOS configuration loaded`
and `Expression provider` in the logs. Cloud requests run independently of gaze
and rendering, with cache, rate limits and failure backoff. See
[Vision policy](docs/vision.md#cloud-request-and-evidence-policy) for cost and
confirmation timing. Both modes observe facial cues, not true internal emotions.

Malformed settings or missing active local model files fail before camera or
display startup. Paths inside the active configuration are relative to that file's directory.
AWS credentials never belong in configuration. The old individual setting flags remain
only as deprecated overrides; migrate scripts to the command above.
The former expression-specific JSON files are replaced by `config/phos.yaml`.

The [configuration reference](docs/development.md#configuration) documents every
field, precedence and migration. The same typed validation and atomic persistence
are reused by the optional [web administration interface](docs/web-administration.md).
It provides authenticated section-based editing, local/AWS selection and password
management. Web access is disabled by default; enable it only on a trusted LAN.
Save writes configuration; System actions can reload logging level, iris color
and camera-preview settings or request a confirmed restart through the [user systemd service](docs/installation.md#managed-startup-and-browser-restart).
Preview can start/stop the existing camera owner when needed. Other settings,
including expression provider and camera resolution, require restart. OS reboot is not implemented.

## Eye demo

Preview eyes independently from Vision:

```bash
python3 src/robot/ui/demo.py
```

Use keys `1`–`5` for neutral, happy, curious, surprised, and sleepy. Use arrow
keys to move the pupils; press `q` to close the demo.


## AI abstraction

The robot uses `LLMProvider` so the conversational brain can be OpenAI, Anthropic, a LAN model or another backend without changing robot behavior code. See `docs/ai.md`.

## Agent guidance

PHOS uses hierarchical repository instructions: start with `AGENTS.md`, then read any more-specific `AGENTS.md` under the subsystem being changed. Detailed architecture and accepted decisions live under `docs/`. No external skill copy is required to work on this repository.
