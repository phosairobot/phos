# Hardware

Use the [Hardware BOM](hardware-bom.md) to plan parts and build tiers; this page
provides the wiring, addresses, power, and device setup details.

> Current environmental baseline: ENS160 and AHT21 share the ESP32 I2C bus (ENS160 `0x53` or `0x52`; AHT21 `0x38`). The ESP32 publishes phos-hw v1 `sensor_update` and ENS160 `sensor_status` JSONL telemetry. The Pi owns freshness, validity, reconnect/session isolation and semantic decisions; it does not poll these devices directly.

This document records the physical hardware that is part of PHOS or has been selected for a future integration milestone.

The table identifies current adapters and planned components. Choose parts with
implemented providers for the capabilities you want to enable.

## Installed / available

| Component             | Current state        | Interface / power                                      | Intended role                                                          | Notes                                                                                |
| --------------------- | -------------------- | ------------------------------------------------------ | ---------------------------------------------------------------------- | ------------------------------------------------------------------------------------ |
| Raspberry Pi          | Available            | Raspberry Pi 3                                         | Main PHOS computer and hardware controller                             | Primary target platform                                                              |
| Display               | Available / working  | 5 inch, 800x600                                        | PHOS face / eyes                                                       | Exact electrical/display interface should remain documented from the real build      |
| Microphone            | Available            | Exact model/interface TBD                              | Future voice input                                                     | Exact model/interface still to document                                              |
| Speaker               | Planned              | TBD                                                    | Future voice output                                                    | Not yet specified                                                                    |
| Raspberry Pi Camera   | Available / working  | Raspberry Pi camera interface                          | Vision and face tracking                                               | Already used by the current PHOS Vision stack                                        |
| BME280 / BMP280 | Integrated optional sensor | I2C bus 1, 3.3 V logic | Temperature, relative humidity where supported, atmospheric pressure | Verify actual breakout/address for each installation |
| ENS160 + AHT21 | ESP32 controller telemetry | I2C on controller | eCO2/TVOC/AQI-UBA plus temperature/humidity | ENS160 eCO2 is not direct NDIR CO2; controller reports validity and compensation |
| GY-521 (MPU-6050)     | Integrated optional sensor | I2C | 6-axis inertial sensing and interpreted motion | Verify mounting, wiring and thresholds for each installation |
| WS2812B RGB LED ring  | Optional controller-backed output | ESP32 data output; typically 5 V LED supply | Semantic color and bounded animations via `phos-hw` v1 | Firmware-selected ESP32 pin/count; check power budget and level shifting |
| Servos/motors         | Planned              | TBD                                                    | Future physical movement                                               | Not yet specified                                                                    |

## Face display session

The reference Raspberry Pi face display is part of PHOS while the runtime is
running, so its X11 session is configured not to blank or enter DPMS standby at
startup. This keeps the fullscreen face visible and avoids HDMI audio being
suspended while the display wakes, which can otherwise affect the first local
TTS phrase after a long idle period.

For the reference X11 deployment, the desktop-session bridge validates the active
local `seat0` logind session before importing its graphical environment (including
`DISPLAY` and, where required, `XAUTHORITY`) into the PHOS user service. SSH/X11
forwarding is not a robot-face display source. PHOS then applies the following
idempotent commands:

```bash
xset s off
xset -dpms
xset s noblank
```

Run those commands from the same local graphical session as PHOS when
troubleshooting; an SSH shell, including one with `DISPLAY=:10.0` or forwarded
X11, is not sufficient. See the deployment bridge setup and status checks in
[installation](installation.md#managed-startup-and-browser-restart).
If `xset` is unavailable or a command fails, PHOS logs a warning and continues.
The automatic behavior targets the reference X11 deployment. It deliberately
skips sessions identified as non-X11; Wayland display-power policy is outside
this deployment implementation.

## Environmental sensors

### BME280

PHOS supports a BME280 through `robot.hardware.bme280.BME280Provider` and the
provider-neutral environmental service. Web Admin → Sensors shows temperature
in °C, relative humidity in %, and station pressure in hPa (not sea-level adjusted).
Fresh readings can drive the confirmed environmental interpreter; raw values do
not reach eyes, LEDs or Vision.

Expected wiring for a **3.3 V-compatible I2C breakout**, with the Pi powered off:

| Breakout signal | Raspberry Pi 3 physical header pin | Function |
| --- | --- | --- |
| 3.3 V-compatible supply input | 1 | 3.3 V |
| GND | 6 | Ground |
| SDA | 3 | GPIO2 / SDA1 |
| SCL | 5 | GPIO3 / SCL1 |

These are expected signal connections, not a claim about your module's pin order
or VIN regulator. Check its schematic/labels before wiring. Pi GPIO uses 3.3 V
logic; do not pull SDA/SCL up to 5 V. The bare BME280 supply is 1.71–3.6 V,
with VDDIO 1.2–3.6 V; a breakout may add a regulator or level shifter, which must
be checked independently. For exposed CSB/SDO, follow the board schematic:
CSB high selects I2C; SDO low selects 0x76, high selects 0x77. Do not leave SDO
floating or assume the breakout has particular pull-ups. Sources:
[Bosch datasheet](https://www.bosch-sensortec.com/media/boschsensortec/downloads/datasheets/bst-bme280-ds002.pdf),
[driver wiring reference](https://bme280.readthedocs.io/en/latest/#gpio-pin-outs).

PHOS uses `/dev/i2c-1` and the configured `sensors.environmental.i2c_address` string,
`"0x76"` or `"0x77"`. It does not scan or silently fall back to another address.
The selected adapter checks register 0xD0: BME280 requires chip ID 0x60,
BMP280 requires 0x58. A mismatched chip is unavailable; there is no automatic
switch to another type. See [setup and bus verification](installation.md#optional-environmental-sensor).
Place the sensor away from Pi/display heat and check readings against a reference;
no enclosure-heating correction or calibration offset is applied.

### BMP280

PHOS also supports BMP280 through `robot.hardware.bmp280.BMP280Provider`, using
exactly the same environmental service and I2C bus ownership as BME280. Select
one sensor in **Web Admin → Sensors → Type**, save and Restart PHOS. BMP280
measures temperature (°C) and station pressure (hPa); it has **no humidity
sensor**. Its reading contains `humidity_percent: null`; the UI says **Not
supported**, never 0%. Both adapters publish explicit available measurements.

The 3.3 V-compatible I2C wiring assumptions in the BME280 table above apply to
BMP280 breakouts too; confirm the actual board schematic and pin labels. Address
is configurable as 0x76 or 0x77. Power off before changing modules. Follow the
single [installation procedure](installation.md#optional-environmental-sensor)
for dependencies, bus enablement and verification.

### ENS160 and AHT21 through the ESP32 controller

ENS160 and AHT21 share the ESP32 I2C bus. ENS160 uses `0x53` or `0x52`; AHT21
uses `0x38`. The ESP32 publishes phos-hw v1 `sensor_update` and `sensor_status`
records over serial, and the Raspberry Pi exposes them as `sensors.ens160` and
`sensors.aht21`. Neither device has a direct Raspberry Pi I2C provider.

ENS160 reports estimated eCO2 in ppm, TVOC in ppb and AQI-UBA on a 1–5 scale.
Validity and AHT21-compensation status accompany its measurements. AHT21 provides
controller-provenance temperature and relative humidity. Per-measurement
freshness, validity and controller-session ownership determine usability.

### Shared I2C bus

BME280/BMP280 and MPU-6050 can share the Raspberry Pi I2C bus if their actual
module configuration and addresses are compatible. ENS160 and AHT21 use the
ESP32 bus instead.

Wiring and setup checklist:

- verify the exact I2C addresses of both physical modules;
- verify module voltage requirements and whether each breakout includes regulation/level shifting;
- document the Raspberry Pi pins used for SDA, SCL, power, and ground;
- verify that I2C is enabled on Raspberry Pi OS;
- distinguish the expected BME280 wiring above from the as-built wiring verified on the real hardware.

## Inertial sensor

### GY-521 (MPU-6050)

PHOS supports an optional GY-521 module based on the MPU-6050 through
`robot.hardware.mpu6050.MPU6050Provider`, disabled by default. It publishes
raw acceleration in m/s² and angular velocity in °/s through the IMU service;
it does not publish orientation, gestures or behavior inputs.

The device provides six inertial measurement axes:

- 3-axis accelerometer: X, Y, Z linear acceleration;
- 3-axis gyroscope: X, Y, Z angular velocity / rotation rate.

Wire the verified board to Pi bus 1: **VCC → 3.3 V**, **GND → GND**, **SDA → GPIO2/pin 3**, and **SCL → GPIO3/pin 5**. Use 3.3 V logic and confirm the actual breakout’s regulator and pull-ups before applying power. AD0 low selects `0x68`; AD0 high selects `0x69`. INT, XDA and XCL are not used by PHOS and stay unconnected.

Important distinction: the MPU-6050 measures acceleration and angular velocity. Absolute orientation is not a direct raw sensor output and would require software-side estimation/filtering if needed later.

The adapter wakes the chip, selects ±2 g / ±250 °/s scale, reads the six raw axes and applies only those fixed scale conversions. It intentionally performs no startup bias calibration because that would require PHOS to be perfectly still. Mounting bias and gravity remain in readings. See [installation](installation.md#optional-gy-521-mpu-6050-imu) for I2C verification and setup.

PHOS also derives stable software observations from those raw samples: STILL,
MOVING, TILT_LEFT/RIGHT/FORWARD/BACK, SHAKE and IMPACT. They are not hardware
interrupts. These observations drive documented eye and LED semantic intent
through BehaviorEngine; raw IMU data does not control GPIO, renderer or LEDs
directly.

Wiring and setup checklist:

- verify the exact GY-521 board revision and pin labels;
- verify its supply and logic-level requirements on the actual breakout board;
- verify the configured I2C address (commonly dependent on the AD0 pin state);
- document whether AD0, INT, or other auxiliary pins will be used;
- verify coexistence with BME280/BMP280 on the shared I2C bus;
- do not define gesture, orientation, impact, or movement semantics until the corresponding PHOS behavior contract is designed.

## WS2812B RGB LED ring

The optional WS2812B addressable RGB LED ring connects to the **ESP32
controller**, which drives its data line and animation timing. The Raspberry Pi
sends resolved `phos-hw` v1 `set_led_animation` messages over USB/serial; it
does not drive the ring's data line.

`SemanticLedService` arbitrates robot-state, environmental, and temporary user
signals. Output requires an active serial connection and LED capability
advertised by the controller for that session. Firmware owns the physical data
pin, LED count, color-channel ordering, and logical direction/origin mapping.

### Connection prerequisites

- Enable `hardware_controller.enabled` in `config/phos.yaml`.
- Select the actual `hardware_controller.serial.device` and `baud_rate`.
- Run firmware implementing `phos-hw` v1 capability discovery, heartbeats, and
  `set_led_animation`.
- Leave the separate legacy `led_ring.enabled` setting disabled for this build.
  Its pin/count and per-pixel settings do not configure the ESP32 ring.

See the [controller guide](hardware-controller.md#ws2812b-ring-connection) and
[installation steps](installation.md#optional-ws2812b-rgb-led-ring).

### Electrical considerations

Power the ring from a regulated supply sized for its specified voltage,
LED count, and worst-case brightness. WS2812B rings commonly use 5 V; consult
the actual ring's specifications when sizing the supply and wiring.

Connect the ring's data input to the **firmware-selected ESP32 output**, and
connect ESP32 ground to LED-supply ground. Use a suitable 3.3 V-to-5 V data-level
shifter where required, plus the manufacturer's recommended data-line protection
and supply decoupling. Do not power the ring from a GPIO pin. Keep the Pi
connection to the controller on USB/serial.

Start at low brightness. Robot-state and user signals resolve to bounded
animations such as solid, breathe, pulse, blink, and spin. On reconnect and a
fresh LED capability report, PHOS reapplies the currently resolved intent.
Loss of controller connectivity makes LED output unavailable while the display
and other robot services continue.

## Hardware/software boundary

Directly connected hardware does not belong directly inside BehaviorEngine, Vision, LLM, or UI code.

Future integrations should preserve this direction:

```text
Physical sensor / LED hardware
        ↓
Hardware-specific driver/provider
        ↓
Provider-neutral reading or command
        ↓
PHOS core / state / behavior integration
```

The implemented environmental, air-quality and IMU providers/services are described in [architecture](architecture.md#environmental-sensor-service). Their typed readings become semantic behavior through the core boundary; other sensors and light-output abstractions remain deferred.

## Wiring information still to record

The following details must be filled in after verifying the physical components:

- BME280 exact breakout/module revision;
- BME280 I2C address on the actual board;
- ENS160 and AHT21 controller wiring and addresses on the actual board;
- GY-521 exact breakout/module revision;
- MPU-6050 I2C address / AD0 configuration;
- whether the MPU-6050 INT pin will be used;
- Raspberry Pi SDA/SCL pins used in the final build;
- sensor power/logic voltage for the exact breakout boards;
- WS2812B ring LED count;
- WS2812B external power-supply specification;
- firmware-selected ESP32 WS2812B data pin;
- logic-level shifter choice, if required;
- final connector/wiring scheme;
- microphone interface;
- audio output hardware.

## Rules

- Record new hardware here when it becomes part of PHOS.
- Distinguish **selected/available hardware** from **software-integrated hardware**.
- Never assume an exact module revision, GPIO pin, I2C address, supply voltage, or protocol detail without verifying the real component.
- Hardware-specific implementation belongs under `src/robot/hardware/` unless the repository architecture explicitly establishes a different location.
- Application subsystems must not depend directly on Raspberry Pi hardware libraries.
- Use provider-neutral interfaces between hardware drivers and PHOS core logic.
- Use mocks/fakes for hardware-dependent tests.
- Do not make unapproved new sensors influence PHOS behavior.
- Do not assign unapproved semantic LED colors/states.

## Current hardware milestone status

Already operational:

- Raspberry Pi 3 runtime;
- 800x600 PHOS display/eyes;
- Raspberry Pi Camera and face tracking.

BME280/BMP280 and controller-backed ENS160/AHT21 integration is implemented;
fresh usable readings feed the provider-neutral environmental behavior interpreter.
ENS160 eCO2 remains an estimated/equivalent value, not direct NDIR CO2.

MPU-6050 motion behavior and controller-backed WS2812B semantic output are
implemented. Follow the sensor mounting and ESP32 ring setup instructions above.
