# PHOS installation on Raspberry Pi

> **Current air-quality installation:** ENS160 and AHT21 are wired to the ESP32 I2C bus, not Raspberry Pi I2C. ENS160 uses `0x53` or `0x52`; AHT21 uses `0x38`. Verify supply, logic levels, common ground and serial JSONL telemetry. The controller publishes estimated eCO2, TVOC, AQI-UBA 1–5, ENS160 validity/compensation and AHT21 temperature/humidity. After reconnect, the Pi requires fresh usable telemetry; historic readings are not revived.

## PHOS 1.8.0 reproducible installation

Use Raspberry Pi OS **with a graphical desktop**, Python 3.11+ and an 800×600
HDMI display on the Pi 3. Tk needs an active X display (XWayland on a Wayland
desktop). Raspberry Pi OS Lite alone is insufficient. The recommended production
launch is the user systemd service below; terminal launch is for diagnostics.
These instructions use the desktop user's `~/phos` (`/home/pi/phos` for user `pi`).

This is a reproducible source/dependency procedure for the current 1.8.0 source,
not a frozen OS image. Record
`cat /etc/os-release`, `uname -m`, `python3 --version` and apt package versions
with the release acceptance results. Actual fresh-Pi acceptance remains
component-specific; see [current state](current-state.md) and the
[roadmap](roadmap.md).

## Recommended: unified installer

On the Pi, as the desktop user:

```bash
cd ~
git clone https://github.com/phosairobot/phos.git phos
cd ~/phos
./scripts/install-phos.sh
```

Use an audited commit when one is available. The source checkout already contains
`config/phos.yaml`; do not create
an incomplete configuration file. The installer validates Raspberry Pi OS/Debian ARM,
installs required APT packages, creates/reuses `.venv`, installs canonical
`.[all]` runtime extras, prepares the local ONNX model, and performs
software-only smoke checks. It does not require connected hardware. OpenCV DNN
loads ONNX directly: **onnxruntime, TensorFlow and PyTorch are not required**.

The APT packages are `build-essential`, `ca-certificates`, `git`, `i2c-tools`,
`libcap-dev`, `opencv-data`, `python3-dev`, `python3-opencv`,
`python3-picamera2`, `python3-pyaudio`, `python3-smbus`, `python3-tk`, `python3-venv`,
`rpicam-apps`, and `wget`.
`.[all]` includes Web Admin/Remote API, Vision, voice (Vosk, Piper, Cartesia,
and ElevenLabs integrations), AWS, environmental sensing, IMU, LED-ring, and
USB/serial controller Python support.

The optional controller transport uses `pyserial` (included by `.[all]`; install
`.[hardware-controller]` for that dependency alone). It is disabled by default;
configure a specific serial device path in `hardware_controller.serial.device`.
PHOS does not perform USB-device discovery or poll controller sensors.

### OpenCV provider invariant

PHOS intentionally uses the Raspberry Pi OS `python3-opencv` package as its
only `cv2` provider, inherited by `.venv` through `--system-site-packages`.
`.[all]` must not install `opencv-python`, `opencv-python-headless`, or an
OpenCV contrib wheel: those distributions overwrite the same `cv2` namespace
and can leave Haar cascades or binary modules mismatched. PHOS uses OpenCV for
Haar detection and DNN/ONNX processing, not HighGUI windows; the face display
is Tk.
The accepted PHOS runtime range is OpenCV `>=4.10,<5`; it is enforced by the
installer smoke check because the Debian package, rather than PyPI metadata,
owns that version selection. NumPy remains system/package-manager compatible;
PHOS does not pin or downgrade it. Python 3.13 remains supported, with the
separate voice extra retaining `audioop-lts`; voice does not depend on OpenCV.

If an earlier installation ran an affected `.[all]`, recreate the virtual
environment rather than uninstalling overlapping files in place:

```bash
cd ~/phos
deactivate 2>/dev/null || true
rm -rf .venv
./scripts/install-phos.sh
.venv/bin/python tools/check_opencv.py
```

The check prints the loaded `cv2` path/version and Haar-data path, and fails if
multiple known Python OpenCV distributions are visible or the face cascade
cannot load.

### Advanced/manual installation

For contributors or constrained deployments, install the same supported runtime
manually:

```bash
sudo apt update
sudo apt install -y build-essential ca-certificates git i2c-tools libcap-dev opencv-data python3-dev python3-opencv python3-picamera2 python3-pyaudio python3-smbus python3-tk python3-venv rpicam-apps wget
python3 -m venv --system-site-packages .venv
.venv/bin/python -m pip install --upgrade pip setuptools wheel
.venv/bin/pip install -e '.[all]'
```

Then prepare the model below and run the installer smoke-check commands. The
script remains the recommended path because it performs those steps consistently.

```bash
.venv/bin/python -c "import robot; from robot.config import RuntimeConfig; RuntimeConfig.from_file(); print('PHOS configuration ready')"
.venv/bin/pip check
```

Alternative source transfer: review the destination in `run_pi.sh` and run it
from your development checkout. It copies release scripts, including the WS2812B
helper, seeds a missing configuration and preserves existing Pi
settings/models/administrator data; it neither installs dependencies nor restarts
PHOS. Upgrades must merge new required fields from the complete
canonical schema. The script has a site-specific destination, not auto-discovery.

## Camera and display check

Before starting PHOS, run this from the Pi desktop with the camera connected:

```bash
rpicam-hello --timeout 5000
.venv/bin/python src/robot/ui/demo.py
```

Close the camera test before PHOS acquires it. In the eye demo use `1`–`5` for
expressions, arrows for gaze and `q` to exit. Resolve camera connection/desktop
permission problems before proceeding. Camera packages and setup follow
[Raspberry Pi's supported camera documentation](https://www.raspberrypi.com/documentation/computers/camera_software.html).

## Configure PHOS and Web Admin

Edit `~/phos/config/phos.yaml`. Release defaults start only eyes: tracking,
expressions, camera preview, environmental sensors and Web Admin are disabled. Keep this complete file;
all required sections (`web`, `display`, `led_ring`, `presence`, `attention`,
`behavior`, `vision`, `expression`, `expression_reactions`, `voice`, `tts`,
`sensors`, `logging`)
are required, including inactive provider fields and `vision.camera_preview`.
Paths inside the active configuration resolve relative to its directory. YAML uses
spaces for indentation; scalar values must retain their intended types. YAML is
the canonical editable format; existing legacy JSON remains a supported fallback.

For trusted-LAN administration set `web.enabled` to `true` and `web.host` to the
Pi's LAN address or `0.0.0.0`; default port is 8080. No provider or password
secrets belong in configuration. Validate before any camera/display startup:

```bash
cd ~/phos
PYTHONPATH=src .venv/bin/python -c "from robot.config import ConfigRepository; ConfigRepository().load(); print('Configuration valid')"
```

Enable gaze with `vision.face_tracking_enabled: true`. Enable the local display
picture-in-picture with `vision.camera_preview.enabled: true` (it is **not a web
video stream**). Keep expressions off until choosing one provider below.

Voice and TTS are disabled by default. The installed `.[all]` extras provide
Local/Vosk STT, Local/Piper TTS, and optional Cartesia/ElevenLabs integrations;
models, provider selection, and audio settings remain configuration-driven.
Configure cloud credentials only through the write-only Web Admin Credentials
page or `SecretsService`, never in YAML. See [voice](voice.md) and [TTS](tts.md)
for the provider-specific setup and physical-validation limits.

## Local ONNX expression model

The unified installer downloads and verifies the configured MobileFaceNet model
unless the local copy already has the expected checksum. For manual installation,
prepare it with:

```bash
cd ~/phos
mkdir -p models/expression
wget -O models/expression/facial_expression_recognition_mobilefacenet_2022july.onnx \
  https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/facial_expression_recognition/facial_expression_recognition_mobilefacenet_2022july.onnx
echo '4f61307602fc089ce20488a31d4e4614e3c9753a7d6c41578c854858b183e1a9  models/expression/facial_expression_recognition_mobilefacenet_2022july.onnx' | sha256sum -c -
wget -O models/expression/opencv-zoo-LICENSE \
  https://raw.githubusercontent.com/opencv/opencv_zoo/main/LICENSE
```

Stop if the checksum differs. Set `expression.enabled: true` and
`expression.provider: "local"`; retain the supplied model path, labels and
preprocessing. No AWS dependency is needed. The candidate's recognition quality
is not established; read [model evaluation](vision-model-evaluation.md).
Keep neutral reactions disabled until calibrated. Revalidate the active
configuration source.

## Start PHOS

For a first foreground check from the desktop:

```bash
cd ~/phos
PYTHONPATH=src .venv/bin/python -m robot.main --config config/phos.yaml
```

Check the startup version is **1.8.0** and the logged configuration path is the
file you edited. Escape leaves fullscreen; Ctrl+C stops PHOS. Stop this process
before installing/starting the production service below.

When web is enabled, open `http://<PI-LAN-IP>:8080/`, enter `phos`, set a different
12–256 character password, then log in again. There is no username. Password
changes revoke all sessions. Use the [administration manual](web-administration.md)
for domain editing, validation, recovery and trusted-LAN HTTP limitations.

## Managed startup and browser restart

The **recommended production launch** is `deploy/phos.service`, a user systemd
service running with the desktop user's camera/display/file permissions. No root
service, sudo endpoint or adapter-owned shell command is involved. From a terminal
**in that user's Pi graphical desktop**, after stopping any foreground PHOS:

```bash
cd ~/phos
mkdir -p ~/.config/systemd/user
cp deploy/phos.service ~/.config/systemd/user/phos.service
mkdir -p ~/.config/autostart
cp deploy/phos-session-env.desktop ~/.config/autostart/phos-session-env.desktop
systemctl --user daemon-reload
systemctl --user enable phos.service
journalctl --user -u phos.service -n 100 --no-pager
```

The unit uses `%h/phos`, `.venv/bin/python`, module execution, and the explicit
`%h/phos/config/phos.yaml` configuration path. Do not install it under
`/etc/systemd/system` or run it as root. The installed desktop autostart bridge
validates its own logind session (`Active=yes`, `Remote=no`, `Seat=seat0`, and
X11/Wayland) before importing that session's GUI variables and starting PHOS.
This binds the face to the physical local display, not an arbitrary user-manager
environment. The desktop file assumes the documented `/home/pi/phos` deployment;
for another desktop user, change its `Exec` path while retaining the same bridge.

Do not run the bridge from SSH. In particular, SSH X11 forwarding can supply
`DISPLAY=localhost:10.0` (or similar); it is rejected and cannot overwrite the
service target. After a local graphical login, confirm the bridge and service:

```bash
systemctl --user status phos.service
journalctl --user -u phos.service -n 100 --no-pager
```

Successful startup logs `PHOS DISPLAY: session_type=x11 display=:0
source=local-seat0` (the numeric display is discovered, not hardcoded). A missing
local graphical session leaves the service unstarted rather than opening Tk on an
SSH display. Direct execution of `src/robot/main.py` is unsupported because it
can shadow standard-library modules.

Operations:

```bash
systemctl --user restart phos.service
systemctl --user stop phos.service
systemctl --user start phos.service
journalctl --user -u phos.service -n 100 --no-pager
```

In Web Admin, **System actions → Reload configuration** applies logging level,
iris theme and every camera-preview setting. Supported Voice & Speech provider
and audio-output changes instead use live candidate activation and persistence;
other changed fields remain listed as restart-required. **Restart PHOS** requires confirmation, shuts down the runtime
and worker, exits with code 75 and lets systemd start the same entry point after
three seconds. Reconnect at the saved host/port and log in again. Disabling web
intentionally removes browser access. Manual launches cannot offer browser restart.
This never reboots the Pi.

Unexpected runtime or web-worker failure exits nonzero for systemd recovery.
The unit limits starts to three per 60 seconds and kills the entire service
control group on stop (20-second shutdown deadline). An explicit `systemctl stop`
does not restart it. Fix configuration/dependency/display errors before recovery:

```bash
systemctl --user reset-failed phos.service
systemctl --user start phos.service
```

The service marker `PHOS_SERVICE_MANAGED=1` plus systemd's `INVOCATION_ID` enables
browser restart; neither is a normal runtime setting. Do not set the marker in
manual launches. See [systemd service semantics](https://github.com/systemd/systemd/blob/main/man/systemd.service.xml).

## Optional AWS expression mode

Camera/OpenCV remain required. The unified installer already includes the
optional SDK; confirm its import with:

```bash
.venv/bin/python -c "import boto3; print('AWS SDK import OK')"
```

Configure credentials **outside the repository** using the
[standard Boto3 credential chain](https://docs.aws.amazon.com/boto3/latest/guide/credentials.html),
preferably the service user's `~/.aws/credentials` and `~/.aws/config` or a role.
Protect credential files with owner-only permissions. The default shared profile
needs no shell environment import. The AWS account must allow
`rekognition:DetectFaces` in the selected supported region; no S3 access is needed.

For a named profile already configured for this desktop user:

```bash
export AWS_PROFILE=phos
systemctl --user import-environment AWS_PROFILE
systemctl --user restart phos.service
```

For externally supplied environment credentials, import only the existing names
needed by your session before service start/restart:

```bash
systemctl --user import-environment AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_DEFAULT_REGION
# Only when using temporary credentials:
systemctl --user import-environment AWS_SESSION_TOKEN
```

Do not put secret values in JSON, unit files, shell history or issue logs. The
service does not automatically inherit interactive shell variables. Region
precedence is `expression.aws.region`, `AWS_REGION`, then SDK settings
(`AWS_DEFAULT_REGION` or profile). PHOS does not load a `.env` file.

Set `expression.enabled: true`, `expression.provider: "aws"` and the non-secret
`expression.aws` policy. No ONNX model is required. Restart PHOS. **Selected facial
crops leave the Pi and are sent to AWS**. Import checks and automated tests make
no AWS calls. Perform live AWS acceptance only with an authorized account.

Requests are single-flight, cached, rate-limited and backed off on error; they do
not silently switch providers. Diagnostics show sanitized latency/request counts.
Switching provider or disabling expressions requires restart. See
[Vision policy](vision.md#cloud-request-and-evidence-policy).

## Troubleshooting

- Missing `cv2`/`picamera2`: use the apt packages and the venv created with
  `--system-site-packages`; verify imports with `.venv/bin/python`.
- Missing image: confirm `vision.camera_preview.enabled`, the startup config
  path, and updated Python source; reload/restart. Check `phos.log` or journal for
  `Camera preview reload failed` or `Could not render camera preview`.
- Tk startup failure: launch/import DISPLAY and XAUTHORITY from the actual desktop
  session; SSH alone does not supply display authorization.
- No faces: check lighting/framing and `rpicam-hello` with PHOS stopped. Use
  `logging.expression_diagnostics` temporarily; restart to apply it.
- Config save/reload rejection: correct the complete schema and active model/log
  paths. Save alone never changes the running configuration.
- No browser after restart: use the newly saved address/port, check the journal
  and service start limit. Restart invalidates sessions.

Deprecated per-setting CLI overrides are still functional for compatibility;
production uses only `--config`. Supported eye and Vision diagnostic commands
remain available; no obsolete provider-specific JSON files are required.

## Optional environmental sensor

This is an optional PHOS 1.3.0 capability. Leave it
disabled until wired according to [hardware notes](hardware.md#bme280). Confirm
the breakout accepts 3.3 V power/logic; the exact board revision is not assumed.
With PHOS stopped, on Raspberry Pi OS as the desktop/service user:

```bash
sudo apt update
sudo apt install -y i2c-tools
sudo raspi-config nonint do_i2c 0
sudo usermod -aG i2c "$USER"
sudo reboot
```

Reboot ensures the I2C interface and desktop/user-service group membership take
effect. Alternatively enable **Interface Options → I2C** in `sudo raspi-config`.
See [Raspberry Pi configuration documentation](https://www.raspberrypi.com/documentation/computers/configuration.html).
After logging back into the desktop, stop PHOS before probing its bus:

```bash
systemctl --user stop phos.service
ls -l /dev/i2c-1
id -nG
i2cdetect -y 1 0x76 0x77
cd ~/phos
.venv/bin/python -m pip install 'RPi.bme280>=0.2.4,<0.3' 'bmp280>=1.0.0,<2' 'smbus2>=0.4,<1'
.venv/bin/python -m pip check
.venv/bin/python -c "import bme280, bmp280, smbus2; print('Environmental driver imports OK')"
```

For a foreground-only installation, stop the foreground process instead of the
systemctl command. The scan should show `76` or `77`; `--` means no response,
while `UU` means a kernel driver owns that address. Do not force competing access.
A responding address alone does not identify the chip: PHOS checks its chip
ID against the selected type (BME280 0x60, BMP280 0x58). Keep the bus at its default speed. No sudo is needed for the PHOS process.
If access is denied, confirm `i2c` membership and the device permissions after reboot.

The optional package extra is `.[environmental]` for both sensors; `.[bme280]`
and `.[bmp280]` install only the selected driver. Source launch uses the pip
dependencies above. [RPi.bme280](https://pypi.org/project/RPi.bme280/)
is a small pure-Python compensation/sampling driver using `smbus2`, without a
CircuitPython/graphics/numerical stack. It is an older library; the adapter is
isolated and target-Pi verification is still required.

RPi.bme280 0.2.4 always accesses humidity registers and has no BMP280 sampling
API. It is retained for existing BME280 behavior. BMP280 uses the small pure-Python
[Pimoroni bmp280](https://github.com/pimoroni/bmp280-python) 1.x driver (with
`i2cdevice`), passed the adapter-owned `smbus2.SMBus(1)`. Its forced-mode update
supplies temperature and pressure from one conversion. No CircuitPython, numeric
framework or second bus owner is introduced. Only the selected driver is imported.

For an existing config, first merge the required `sensors` object from the new
canonical file as described in [migration](development.md#environmental-configuration-migration).
In Web Admin → Sensors (or directly in the complete configuration), select type **BME280** or **BMP280**, set enabled to true,
choose the detected address, and set polling/stale timing. The canonical defaults
are maintained in `config/phos.yaml`. Save, validate and restart:

```bash
cd ~/phos
PYTHONPATH=src .venv/bin/python -c "from robot.config import ConfigRepository; ConfigRepository().load(); print('Configuration valid')"
systemctl --user restart phos.service
journalctl --user -u phos.service -n 100 --no-pager
```

Manual launch: `PYTHONPATH=src .venv/bin/python -m robot.main --config config/phos.yaml`.
Open **Web Admin → Sensors** and refresh the page to see current °C, % relative
humidity (BME280 only) and hPa, UTC last-update time, age and health. BMP280
humidity is null and shown as **Not supported**. The active type remains visible
when a different saved type is awaiting restart. Save alone changes only
the file; Reload leaves **all five** environmental fields pending for Restart PHOS.

Missing libraries, wrong address, absent hardware, invalid readings or I/O errors
make the sensor unavailable without stopping eyes/Vision/web. Retrying starts at
the polling interval, doubles to a maximum of 60 seconds (or the configured poll
interval if longer), and resets after success. Error warnings are limited to one
per minute; measurements are DEBUG-only. Old measurements are hidden immediately
after failure or when their age reaches the stale timeout. Last-update/age remain
visible. A slow I2C call occupies one worker only; no queue or replacement threads
accumulate. Shutdown waits at most one second for that worker; a stuck native
call is cleaned up when it returns or by process exit.

Physical acceptance: verify the actual address and chip, compare the supported measurements
with a reference, check advancing timestamps over several polls, then test a
wrong address and recovery after correcting it/restarting. Power off before
changing wiring. Confirm eyes, camera and web remain responsive with a missing
sensor; confirm disable plus restart removes sensor bus activity. Record actual
module, wiring, Pi OS, readings and service logs. No physical acceptance is
claimed by the mock tests.

### Missing BMP280 driver

If the journal reports `ModuleNotFoundError: No module named 'bmp280'`, install
the driver into the same Python environment used to run PHOS. On the Raspberry
Pi, for the documented installation:

```bash
cd ~/phos
.venv/bin/python -m pip install 'bmp280>=1.0.0,<2' 'smbus2>=0.4,<1'
.venv/bin/python -c "import bmp280, smbus2; print('Driver OK')"
```

The sensor service retries automatically after installation. To restart the
managed service explicitly:

```bash
systemctl --user restart phos.service
journalctl --user -u phos.service -n 100 --no-pager
```

For a manual launch, stop and rerun PHOS with the same virtual environment.
Selecting BMP280 in Web Admin does not install its optional Python dependency.
If imports succeed but the runtime still reports a missing module, check that
the service uses this virtual environment and checkout.

The sensor type in the warning is the active runtime selection. For BMP280,
the deployed `sensors.environmental` block must have `"type": "bmp280"` and
`"enabled": true`. Check the configuration path shown in Web Admin → System /
Status; a development-machine file may differ from the Pi's file. Saved sensor
configuration changes require Restart PHOS even though driver failures retry
automatically.

## Optional GY-521 / MPU-6050 IMU

PHOS reads the GY-521's MPU-6050 accelerometer and gyroscope on I2C bus 1. Stop
PHOS before wiring or probing the bus. Connect **VCC to 3.3 V**, **GND to GND**,
**SDA to GPIO2/pin 3**, and **SCL to GPIO3/pin 5**. Ensure the specific breakout
uses 3.3 V I2C logic. Leave INT/XDA/XCL unconnected. AD0 low is `0x68`; AD0 high
is `0x69`.

```bash
systemctl --user stop phos.service
i2cdetect -y 1 0x68 0x69
cd ~/phos
.venv/bin/python -m pip install 'smbus2>=0.4,<1'
.venv/bin/python -m pip check
```

Expect `68` or `69`; `--` means no response and `UU` means another driver owns
the device. The optional package extra is `.[imu]`. Merge the complete
`sensors.imu` block from `config/phos.yaml` into an existing configuration, then
enable it in **Web Admin → Sensors → GY-521 / MPU-6050 motion** (or set
`"enabled": true` directly). All four fields require **Restart PHOS**:

```bash
cd ~/phos
PYTHONPATH=src .venv/bin/python -c "from robot.config import ConfigRepository; ConfigRepository().load(); print('Configuration valid')"
systemctl --user restart phos.service
journalctl --user -u phos.service -n 100 --no-pager
```

The status panel shows acceleration X/Y/Z in m/s² and angular velocity X/Y/Z in
°/s, with timestamp, age and health. PHOS configures ±2 g / ±250 °/s and converts
the raw readings using factory scale factors. It does not calibrate mounting
offsets at startup: keep the robot still only when comparing baseline values, and
expect one acceleration axis to include gravity. The interpreter estimates tilt from gravity; no full orientation fusion is implemented. If other Pi I2C devices share bus 1, retain their documented
10 kHz `i2c_arm_baudrate` setting.

The motion interpreter prioritizes IMPACT, SHAKE, confirmed TILT, MOVING,
then STILL. Tilt no longer requires the movement metric to be below threshold.
It low-pass filters acceleration with a fixed 0.2-second time constant using
sample elapsed time, then normalizes the gravity vector. The existing
`tilt_threshold_m_s2` remains the enter setting, now measured as normalized
lateral/forward gravity component times standard gravity, independent of total
acceleration magnitude. Default 4.0 corresponds to about 24°;
`tilt_exit_threshold_m_s2` defaults to 3.0 (about 18°). A confirmed direction
holds until below exit; otherwise the largest component wins (lateral wins exact
ties). `confirmation_seconds` defaults to 0.3 for entry, direction changes and
exit. Brief vibration cannot confirm a tilt. At the default 0.05-second IMU poll
interval (20 Hz), a normal 35° tilt settles in roughly half a second. Existing
five-second polling deployments must change that setting and restart to obtain
this responsiveness. Slower polling necessarily limits confirmation speed.

Mounting is explicit: `lateral_axis` and `forward_axis` each select a distinct
signed sensor axis (`x`, `-x`, `y`, `-y`, `z`, `-z`). Defaults retain the old
X/Y convention: positive mapped lateral acceleration means TILT_RIGHT, negative
means TILT_LEFT; positive mapped forward means TILT_FORWARD, negative means
TILT_BACK. At level, both mapped components should be near zero, with gravity
on the unused axis. Verify by physically tilting right and forward; reverse signs
or swap axes as needed. No startup calibration assumes that the robot is level.
Diagnostic roll/pitch are `asin(mapped normalized lateral/forward)` in degrees:
approximate signed inclinations, not full Euler attitude or yaw. Upside-down
orientation is not distinguished. Sustained linear acceleration cannot be fully
distinguished from gravity by this accelerometer-only method; freefall-like
filtered magnitudes below 0.25 g do not provide tilt evidence.

Movement still compares filtered acceleration magnitude with gravity (default
1.5 m/s²); shake requires opposite high gyro samples (180 °/s); impact uses raw
total acceleration (25 m/s²). Both override tilt. The event cooldown (2 seconds)
does not delay stable state publication. All motion settings apply through Web
Admin → Sensors → GY-521 / MPU-6050 motion → Save → Reload configuration,
without reopening I2C. Reload clears pending tilt evidence and filter history.
Enable DEBUG logging temporarily to see `IMU motion` diagnostics at most once
per second: filtered acceleration, pitch/roll, movement metric, normalized
enter/exit thresholds, tilt candidate, winning classification/reason, confirmed
state and confirmation duration. MOVING explains whether gravity is too small
or tilt is below threshold; a pending tilt leaves the previous state visible
until confirmation completes.

For interpreter acceptance on Pi: rest level, tilt each direction about 35° and
hold one second, gently vibrate while holding tilt, then return level. Confirm
STILL → the correct TILT → STILL in status and diagnostics; level vertical
movement should produce MOVING. Verify mounting signs before assessing eye
reactions. Save/reload thresholds and confirm the provider is not reopened.

With visual reactions enabled, sustained tilt visibly moves pupils in the tilt
direction and remains active while tilt is observed. Defaults use 86% of the safe
gaze range. Horizontal tilt uses `imu_tilt_eye_asymmetry_strength` 0.18:
TILT*LEFT opens the left eye and closes the right by the same amount; TILT_RIGHT
mirrors it. Left/right use shared openness 1.12 before that split; forward and
back keep equal eyes at 1.23 and 0.82 respectively.
MOVING recenters pupils, opens to 1.16 and uses 0.63 reaction strength. SHAKE is
a 1.18-open, 0.88-strength surprised alert for 1.35 seconds; IMPACT is the
stronger 1.25-open, 1.0-strength alert for 1.0 second. Both hold for 55% of the
duration and then decay smoothly. Configure the eight `behavior.imu*\*` values in
Web Admin → Display & Appearance → Eye behavior, save and use **Reload
configuration**. Impact strength must remain greater than shake strength. The
base iris theme returns after an alert because the alert is temporary semantic
tint, not an appearance change.

For physical display acceptance, enable the IMU and DEBUG logging, then confirm
that MOVING recenters the pupils, each held tilt produces the documented gaze
direction and mirrored eye-size change, SHAKE produces a clear amber surprised response and IMPACT is
visibly wider/brighter/stronger. Let each alert expire and verify the configured
iris theme returns. Confirm sleeping, speaking and error remain visually
authoritative over IMU intent.

For Pi acceptance, verify the detected address and WHO_AM_I, stable six-axis
values, wrong-address recovery, a restart, and coexistence with every connected
I2C sensor. Record board revision and wiring before treating it as accepted.

## Optional WS2812B RGB LED ring

PHOS uses the optional `rpi-ws281x` provider for a WS2812B ring. Decide the
actual ring count, data GPIO and power arrangement before following the enable
sequence below.

The default `led_ring` block is disabled and uses `led_count: 0` as an explicit
unconfigured value. Set the exact count, supported data GPIO and `enabled: true`
only after the helper starts successfully. GPIO pin and LED count require a
restart; enabled state, brightness, base color, semantic following and update
rate can later use Web Admin → Display & Appearance → WS2812B LED ring → Save →
**Reload configuration**. The status snapshot reports disabled, starting,
available or unavailable plus the current semantic state/effect.

`rpi-ws281x` requires privileged mailbox and physical-memory access. PHOS keeps
the desktop/camera service unprivileged and uses the separate root-owned
`deploy/phos-led.service` helper instead.

### Enable the ring on a deployed Pi

From the development checkout, first run the deployment script. It transfers
`scripts/phos_ws2812b_helper.py`, the standalone smoke test and the service-unit
template to `~/phos`; it does not install or restart the root service:

```bash
./run_pi.sh
```

On the Pi, install the driver into the same virtual environment used by the
helper. In `~/phos/deploy/phos-led.service`, set `--count` and `--pin` to the
physical ring's exact LED count and BCM data GPIO. Its Python path and working
directory must also match the PHOS installation. Then install or update the
unit and start it:

```bash
cd ~/phos
.venv/bin/python -m pip install 'rpi-ws281x>=5.0,<6'
sudo cp deploy/phos-led.service /etc/systemd/system/phos-led.service
sudo systemctl daemon-reload
sudo systemctl enable --now phos-led.service
sudo systemctl restart phos-led.service
sudo systemctl status phos-led.service --no-pager
ls -l /run/phos-led.sock
```

The helper owns only `/run/phos-led.sock`, which is writable by the `gpio` group.
The normal PHOS user service sends bounded RGB frames to that socket and never
opens `/dev/vcio` or `/dev/mem`. The service must report `active (running)` and
the socket must exist before enabling PHOS LED output.

The available LED base colors are green `#00FF40`, red `#FF1A1A`, yellow
`#FFD400`, blue `#007BFF`, violet `#A020F0`, white `#FFFFFF`, cyan `#00E5FF`,
turquoise `#00FFC8`, orange `#FF7A00`, and magenta `#FF00C8`. PHOS sends these
as logical RGB and uniformly scales them for `brightness`; it does not apply
gamma correction. The helper explicitly configures standard WS2812B `GRB` wire
order, so do not reorder the configured palette values.

Next update the deployed canonical `~/phos/config/phos.yaml` so its count and
GPIO exactly match the helper. Replace its `led_ring` object with values for the
actual ring; for a 12-pixel ring on BCM GPIO 18:

```json
"led_ring": {
  "enabled": true,
  "led_count": 12,
  "gpio_pin": 18,
  "brightness": 0.30,
  "base_color": "cyan",
  "follow_visual_state": true,
  "update_rate_hz": 10.0,
  "imu_reactions_enabled": true,
  "directional_strength": 0.65,
  "directional_sector_size": 3,
  "shake_strength": 0.85,
  "impact_strength": 1.0,
  "imu_animation_color": "yellow",
  "directional_animation_speed": 12.0,
  "bottom_led_index": 0,
  "forward_led_index": 0,
  "clockwise": true
}
```

Keep the surrounding JSON valid. `run_pi.sh` deliberately preserves an existing
Pi configuration, so it does not make this configuration change for you. Start
PHOS with the new configuration:

```bash
systemctl --user restart phos.service
```

After LED count or GPIO changes, update both the helper unit and `phos.json`,
then restart `phos-led.service` followed by `phos.service`. Brightness, base
color, semantic following and update rate may instead use Web Admin → Display &
Appearance → WS2812B LED ring → Save → **Reload configuration**.

To isolate wiring and driver setup from PHOS, stop the PHOS service and run the
standalone color smoke test. It requires the exact LED count and leaves the ring
off when it finishes:

```bash
systemctl --user stop phos.service
cd ~/phos
sudo .venv/bin/python scripts/test_ws2812b.py --count 12 --pin 18
```

Replace `12` and `18` with the actual count and BCM GPIO. It shows red, green,
blue and white at low brightness. This temporary root invocation is diagnostic;
do not run the normal PHOS service as root.

Power the ring from a correctly sized 5 V supply as determined from the actual
ring's documentation and LED count. Do not draw LED power from a GPIO pin. Tie
the Pi and LED-supply grounds together. WS2812B data reliability may require a
3.3 V-to-5 V logic-level shifter; use the ring maker's recommended data-line
protection and supply decoupling. Verify wiring without PHOS first, then start
with low brightness. A driver, permission or write failure only disables ring
output; PHOS eyes and other services continue.

The ring is steady in its configured base color when neutral. Warm/curious use a
gentle pulse, alert uses a short yellow pulse, sleepy a dim violet fade and error
a steady red. Existing transient alert strength drives the alert pulse, so shake
and impact require no direct IMU-to-LED coupling. After an alert or directional
fill, confirm the currently resolved persistent state returns: environmental
color when active, otherwise the configured base color.

## Controller-backed ENS160 and AHT21

ENS160 and AHT21 are connected to the ESP32 shared I2C bus, not Raspberry Pi
I2C. ENS160 uses `0x53` (or `0x52`) and AHT21 uses `0x38`. The controller
publishes measurements and status through phos-hw v1 JSON Lines, so PHOS needs
no Pi-side driver, I2C setup, optional package, or migration for either device.

Verify ESP32 wiring, firmware and the serial controller connection. The Sensors
page reports `sensors.ens160` with estimated eCO2 (ppm), TVOC (ppb), AQI-UBA
(1–5), validity and compensation status, and `sensors.aht21` with controller
temperature and relative humidity. eCO2 is an estimate, not direct NDIR CO2.
During ENS160 startup or warm-up, and after disconnects, PHOS withholds unusable
gas values while independently fresh AHT21 and BMP280/BME280 values remain
available. A reconnect requires fresh telemetry for its new controller session.

### ENS160/AHT21 verification

Confirm the ESP32 controller is connected and advertises sensor capability, then
inspect Sensors status for fresh `sensors.ens160` and `sensors.aht21` records.
ENS160 gas values are usable only when validity and freshness permit them; AHT21
freshness is independent. Diagnose missing readings through controller serial
telemetry, ESP32 wiring and power, rather than Raspberry Pi I2C tools.
