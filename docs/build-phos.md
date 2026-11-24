# Build PHOS

PHOS is a progressive Raspberry Pi project: begin with the display and add
hardware only when the preceding layer works. Follow the setup and wiring
instructions for each added capability.

## Prepare

The documented reference target is a **Raspberry Pi 3** running Raspberry Pi OS
with a graphical desktop, Python 3.11+, and a 5-inch 800×600 display. Raspberry
Pi 4 and 5 compatibility is not established by the repository, so they are not
listed as supported replacements. Raspberry Pi OS Lite alone is insufficient
for the graphical eye renderer.

| Component | Status | Why it is needed |
| --- | --- | --- |
| Raspberry Pi 3 | Required baseline | Runs PHOS. |
| 5-inch 800×600 display | Required baseline | Shows the face and eyes. |
| Network connection | Recommended | Clone/install, updates, and optional Web Admin or MCP access. |
| Camera | Optional | Enables face, presence, attention, snapshots, and optional object awareness. |
| USB microphone and audio output | Optional | Enables configured voice capture and speech playback. |
| BME280/BMP280, MPU-6050 | Optional | Adds Pi I²C environmental or motion observations. |
| ESP32 with ENS160/AHT21 | Optional | Adds controller-backed air-quality and temperature/humidity telemetry. |
| WS2812B ring | Optional | Adds semantic LED feedback. |

Plan your parts with the [Hardware BOM](hardware-bom.md). Exact wiring,
voltage limits, and I²C addresses are maintained in the [hardware reference](hardware.md).
Choose an enclosure and mounts to fit your selected display and modules.

## Assemble and install

1. Connect the display and confirm that Raspberry Pi OS has an active local
   graphical desktop.
2. Follow the [canonical installation procedure](installation.md) exactly. It
   contains the supported package, display, camera, service, and security steps.
3. Start with the supplied complete `config/phos.yaml`. It safely enables eyes
   only; YAML is the only supported runtime configuration format.
4. Run the documented foreground command before enabling the managed service.
5. Add one optional capability at a time and perform its linked physical check.

## First boot checklist

- PHOS starts without a configuration-validation error.
- The local display shows the animated eyes.
- The startup output identifies the configuration file you edited.
- If enabled, Web Admin is reachable only on your trusted LAN and its bootstrap
  password is changed at first access.
- Optional camera, controller, sensor, and audio status are checked separately;
  an unavailable optional device must not be assumed healthy because eyes run.

See [installation](installation.md) for commands and troubleshooting, and
[Web Admin](web-administration.md) for safe configuration changes.

## First experiments

### 1. See an expression

**Requires:** the display baseline. **Expected result:** a supported expression
appears through the normal PHOS visual system. Use the Web Admin Controls page
or the documented [Remote API expression endpoint](remote-api.md), rather than
trying to access a renderer directly.

### 2. Read the environment

**Requires:** a configured, available supported sensor. **Expected result:**
Web Admin or the semantic API reports measurement availability and freshness.
BMP280 intentionally has no humidity value; ENS160 eCO2 is an estimate, not a
direct CO2 measurement. See [environmental awareness](environmental-awareness.md).

### 3. Request a semantic signal

**Requires:** a running PHOS instance; an LED ring is optional. **Expected
result:** PHOS accepts a semantic signal through the [Remote API](remote-api.md)
or a permitted MCP request. Hardware output is resolved by PHOS and may be
unavailable when no LED controller is installed.

### 4. Connect an Agent

**Requires:** MCP enabled and a compatible client. **Expected result:** the
client reads status or physical context, then performs only an action authorized
for its profile. Continue with the [developer quickstart](developer-quickstart.md).
