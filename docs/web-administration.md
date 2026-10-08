# PHOS web administration user manual

PHOS includes an optional single-administrator configuration editor. It uses the
same typed configuration model and validation as startup, with no camera, AWS or display access
from the web worker. Saving never applies settings. Reload applies logging level,
supported eye appearance and camera preview settings; other runtime changes
require restarting PHOS.

## Install and enable

Use the [PHOS 1.3.0 installation procedure](installation.md#recommended-unified-installer)
on the Pi (Python 3.11+). Its unified installer creates the canonical environment
and installs the Web Admin dependency together with all other supported runtime
components. For contributor-specific manual setup, use the concise
[advanced/manual path](installation.md#advancedmanual-installation); do not create
a separate Web Admin environment.

The canonical `web` section defaults to `enabled: false`, `host: "127.0.0.1"`,
`port: 8080`, preserving eyes-only startup without extra dependencies. Existing
deployments must add this required section to their complete configuration file; source
sync does not overwrite deployed settings.

For access from another device on a **trusted LAN**, edit the existing section:

```yaml
web:
  enabled: true
  host: 0.0.0.0
  port: 8080
```

An explicit LAN interface IP is more restrictive than `0.0.0.0`, which binds all
IPv4 interfaces. IPv6 literals are also accepted. Hostnames are not bind settings.
No separate web CLI flags exist.

Use the [user systemd service](installation.md#managed-startup-and-browser-restart)
for production and browser restart. For a foreground diagnostic run:

```bash
cd /home/pi/phos
.venv/bin/python src/robot/main.py
```

With dependencies already available to system Python, the unchanged command is
`python3 src/robot/main.py`.
Open **http://<PI-LAN-IP>:8080/** from your phone, tablet or desktop. Obtain the
Pi address locally with `hostname -I`. With the default loopback binding, only
**http://127.0.0.1:8080/** on the Pi can connect. A custom port changes both URLs.
`web.host` and `web.port` are startup-only listener settings: save the complete
active configuration and restart PHOS (for example, `systemctl --user restart
phos.service`) before using the newly configured address. They are not applied
by Reload configuration.
Do not configure router port forwarding or expose this interface to the Internet.

## First login and password changes

1. Enter the bootstrap password **`phos`**; there is no username field.
2. The editor remains inaccessible until you change that password.
3. Enter current password `phos`, a different new password of 12–256 characters,
   and its confirmation. Prefer a long, unique passphrase.
4. Log in again using the new password. Configuration is now available.

Use **Change password** later; current password and confirmation are required.
Every successful change ends all sessions, including your own. **Log out** also
invalidates that session on the server. Sessions expire after 30 minutes from
login (not extended by activity) and all expire when PHOS restarts. Up to 32
sessions are retained; a new login beyond that retires the oldest.

The single account permits five password-verification attempts per minute across
all clients, including successful logins and password-change attempts. Wait one
minute after a throttle message. This global budget prevents bypass by changing
IP or cookies, but another LAN client can temporarily deny login. Limits reset
on restart. There is no persistent lockout or remote password reset.

## Live presence and attention

Dashboard and PHOS Status show read-only Presence state, people count,
Attention state, target ID, normalized target position, and confidence. The
initial semantic status snapshot is refreshed through the existing SSE stream;
edge events refresh the target state without adding a writable vision control.
Unavailable detector confidence is displayed as unavailable rather than as a
synthetic 100% value.

They also show **Observed Expression** separately from PHOS's own expression.
This is a read-only, uncertain selected-face classifier result with availability,
label, confidence, provider, model and observation time. It may be unavailable
while Presence remains present; it never controls PHOS expression or state.

## Edit configuration

The administration home is **Dashboard**. The sidebar groups read-only and
writable runtime pages under Basic/Advanced, followed by configuration Settings;
**System Actions** is a direct Advanced destination. The same navigation appears
on every authenticated page and wraps for phone and tablet screens. The current
page is highlighted. No frontend framework is needed.

| Page | Settings and actions |
| --- | --- |
| Dashboard | Read-only overview and navigation to configuration pages. |
| PHOS Status | Read-only semantic state and subsystem health summary. |
| Controls | Writable runtime actions only: separate Robot State, Expression, Visual Source and Overlay cards. Each action uses Remote API capabilities and reports feedback in its own card. |
| Network | Web bind address (`web.host`) and port (`web.port`). Wi-Fi, DNS and other OS networking remain managed on the Pi. |
| Display & Appearance | Display dimensions, fps, fullscreen and transitions; blink/gaze intervals, gaze smoothing and reaction decay from `behavior`. |
| Vision | Face tracking, camera resolution/cadence, face detection and optional display-only camera picture-in-picture preview. |
| Expression Recognition | Provider selection/enabling and observation cadence/crop margin; smoothing; local ONNX model, labels and preprocessing; AWS region/confidence/timeouts; a separate cloud cost/rate-limit group. |
| Display & Appearance | Display, eye behavior, base visual source and WS2812B LED ring settings. Base visual source selects Manual, Environment, or PHOS State persistent intent; error/sleep and IMU reactions temporarily override it. The ring selector offers saturated green, red, yellow, blue, violet, white, cyan, turquoise, orange and magenta. LED pin/count require restart; enabled state, brightness, base color, visual-state following and update rate use Reload configuration. |
| Sensors | Environmental type (BME280/BMP280), CCS811 air quality, environmental behavior and GY-521/MPU-6050 motion: enable, I2C address, polling and stale timeout; read-only current readings, interpreter state/reason, age and sensor health from the parent runtime. Hardware settings require Restart PHOS; IMU and environmental interpretation settings use Reload configuration. |
| Logging | Supported log level, output file and expression diagnostics. No credential/payload logging switches; SDK credential/request debug output remains suppressed. |
| Advanced Configuration | Complete YAML editor for advanced users. It preserves the active YAML/YML/JSON storage format. |
| Web Administration / Security | Enable/disable web administration (`web.enabled`) and a link to the separate password-change page. Passwords are never runtime configuration. |
| Diagnostics | Read-only runtime health and saved-versus-active configuration diagnostics. |
| System Actions | Reload configuration and, when managed by the documented systemd service, review/confirm Restart PHOS. Restart is visually marked as disruptive; reboot and shutdown are not supported. |

Active-configuration information is shown in **Diagnostics**, visually separated
from editable settings. Deprecated CLI overrides, if used, appear in startup
settings but do not change the saved file. The software version comes from the authoritative `robot.__version__`; live
health is not inferred from the active configuration snapshot.

Every editable page has its own **Save** button. It merges only that page's
fields into the same active configuration file, validates the **complete configuration**,
and atomically saves it. Other areas are preserved. There are no per-page
configuration files or independent validation schemas. Save before navigating
away: unsaved input is not carried between pages. A save in another tab makes
previously loaded pages stale, including pages in other areas.

Fields use checkboxes, numeric inputs, text inputs, comma-separated arrays and
provider/log-level dropdowns. Optional paths/region may be blank. Numeric pairs
are entered as `640, 480` or `3.5, 6.5`; labels are entered in model-output order.
The canonical model remains authoritative for all ranges, types and relationships;
see [the field reference](development.md#field-reference).

### Advanced Configuration YAML editor

Open **Settings → Configuration** for the complete advanced editor. It always
displays YAML, including when the active file is legacy JSON. The page identifies
the active path and storage format; saving YAML input back to a JSON source keeps
that source JSON and never creates or switches to a YAML file.

Use **Validate** to parse YAML and run full `RuntimeConfig` validation without
writing. Syntax errors include a line/column when available; invalid roots and
semantic validation errors are shown without a stack trace. **Save configuration**
performs the same validation and atomic active-source save, then requires an
explicit **System Actions → Restart PHOS** choice before settings become active.
It never reloads or restarts PHOS automatically. A stale revision is rejected
with a request to reload rather than overwriting another editor's save.

Invalid configuration is rejected before persistence, but advanced settings
should still be edited carefully. Structured settings pages remain available for
common changes. The editor warns before navigation with unsaved changes.

Navigation is defined by a small domain registry, separate from the canonical
schema. Future implemented subsystems can add areas there. LED Ring,
Audio, Voice, LLM, Home Assistant, Remote API and MCP have no settings or
placeholder pages in this milestone.

Choose **local** or **aws** under Expression Recognition. The matching provider fields are
shown; switching keeps the inactive provider's settings for later use. Both
sections remain accessible if JavaScript is disabled. Enabling local expressions
requires a readable ONNX model and valid labels. AWS selection does not require
an ONNX file. Switching the provider does not implicitly enable expressions.

**AWS mode sends selected face crops to AWS when expressions are enabled.**
Credentials remain exclusively in the external SDK chain/environment/profile/
role mechanism. There are no access-key, secret-key or token fields. This editor
does not probe credential availability or make AWS requests.

**Save** on an editable page converts fields and calls the same model validation and
atomic persistence used elsewhere. Invalid input leaves the file unchanged,
displays an error in the relevant group and preserves non-sensitive input.
If validation finds a problem in another area, the page links to that area;
the current changes remain unsaved. All fields, including
inactive provider fields, must remain valid. After a validation error all provider
groups are shown so inactive settings can also be corrected. Config-relative model/log
paths keep their existing interpretation. Missing active model paths can be
repaired in the editor while PHOS is already running; malformed configuration or invalid
schema edited outside the UI requires local repair.

Successful saves report that configuration is saved and direct you to **System
 actions**. Save alone does not change running settings. Use Reload for logging,
eye appearance and camera preview settings, or Restart PHOS for all other changes.
Turning web off takes effect at
restart. A page loaded before another save is rejected as stale: reload it
before editing again. Do not edit the active configuration simultaneously from the terminal.

Saving flushes a temporary file in the same directory, then atomically replaces
the target file. A validation or replacement failure preserves the old target.
No automatic previous-version backup is kept. Before editing, use for example:

```bash
cp config/phos.yaml config/phos.yaml.backup
```

To recover, stop PHOS, restore a known-good complete file with
`cp config/phos.yaml.backup config/phos.yaml`, and restart. An interrupted worker
may leave an unused hidden temporary file; it is never loaded as configuration.

## Local password recovery and storage

Credential data is in **`.phos-admin/password.json` beside the selected
file**: normally `/home/pi/phos/config/.phos-admin/password.json`. This stores
only a salted Werkzeug PBKDF2-SHA256 hash (1,000,000 iterations) and the mandatory
change flag. No plaintext password, session key or AWS credential is stored.
The directory is owner-only (`0700`) and file owner-only (`0600`); run PHOS as
its normal OS user. All selected configuration files in the same directory
share this administrator store. Only one PHOS web instance per store is supported.

Forgotten password recovery requires local filesystem/terminal access:

1. Stop PHOS completely.
2. Disable LAN access temporarily by setting `web.host` to `127.0.0.1`, or
   disconnect the Pi from the network during bootstrap.
3. Move the entire credential directory aside (choose an unused backup name):

   ```bash
   cd /home/pi/phos
   mv config/.phos-admin config/.phos-admin-recovery-backup
   ```

4. Restart PHOS. A missing credential directory is created with bootstrap
   password `phos`. Complete the mandatory change locally.
5. Restore the desired trusted-LAN binding and restart. Retired hash backups
   remain sensitive local files; do not commit or share them.

For a custom configuration, use its parent directory instead of `config/`.
A missing or corrupt password file *inside an existing directory* fails closed;
it never silently re-enables `phos`. Use the same whole-directory recovery
procedure. Do not delete only `password.json` while the server is running.

## Security and operational limits

- This milestone serves **HTTP**, without TLS. Passwords and session cookies
  can be intercepted on an untrusted network. Only use a trusted LAN; perform
  initial password setup locally where possible. Public Internet access, cloud
  access and reverse-proxy/TLS deployment are outside this milestone.
- Flask signed cookies use a random per-worker secret, HttpOnly and
  SameSite=Strict. Secure is intentionally unset because this server uses HTTP;
  setting it would prevent LAN browser login over HTTP. Server-side random
  session IDs allow logout/password-change revocation and absolute expiry.
- Flask-WTF protects all POST actions, including login, password changes, saves
  and logout, against CSRF. Pages use escaping, a restrictive content security
  policy, no-store caching and anti-framing headers. Password fields are never
  repopulated. Request bodies and credentials are not logged.
- Waitress runs in a separate process with two threads, bounded connections and
  request sizes. Password hashing cannot block the render event loop, but all
  work still shares the Pi's limited CPU. Measure performance on target hardware.
- The worker starts before the runtime; a startup failure prevents a misleading
  enabled-but-unreachable launch. Missing dependencies, invalid credential
  permissions and occupied ports must be fixed locally. Shutdown terminates
  the worker and releases its port, even if runtime startup fails. If the worker
  crashes later, the parent shuts down and exits nonzero for supervisor recovery.
- Filesystem access is trusted. The editor can select model/cascade/log paths
  within the privileges of the PHOS OS account. Run as a normal user, not root.
- Configurations/credentials are atomically replaced, but there is no automatic
  backup, cross-process edit lock, arbitrary subsystem hot reload or high-availability service. A dead web worker
  now causes graceful parent shutdown and a nonzero exit for systemd recovery.

Implementation uses [Flask security guidance](https://flask.palletsprojects.com/en/stable/web-security/),
[Flask-WTF CSRF protection](https://flask-wtf.readthedocs.io/en/1.2.x/csrf/),
[Werkzeug password hashing](https://werkzeug.palletsprojects.com/en/stable/utils/#werkzeug.security.generate_password_hash)
and [Waitress](https://docs.pylonsproject.org/projects/waitress/en/stable/arguments.html).

## Verification on the Pi

Enable LAN access, start PHOS, complete the first login, and check the editor
from a phone and desktop. Open Display & Appearance, save a harmless display setting and confirm the eyes
keep running unchanged until restart. Verify the saved value after restart,
then test logout, wrong password and local recovery. If camera tracking is
already enabled, confirm it stays responsive while logging in/saving. Provider
switches still require the model/AWS setup and physical verification described
in [installation](installation.md) and [Vision](vision.md); automated web tests
use no camera, display, actual credentials or AWS calls.


## Login request rejected

A form/session verification error occurs before the password is checked. Reload
`http://<PI-LAN-IP>:8080/login` after restarting PHOS and allow cookies for that
address. Old tabs contain tokens invalidated by a restart. If it persists, check
the terminal for `Administration CSRF rejection`; the reason is logged without
passwords or token values. Browser favicon/missing-page requests do not clear
the login session. Update the Pi's source if using the initial implementation.


## Save vs Reload vs Restart PHOS

Lifecycle actions are on **System actions**, separate from editable domain forms.
System / Status remains read-only and links to those actions. Every operation
requires administrator authentication after mandatory bootstrap rotation;
state changes also require CSRF protection.

| Operation | Effect |
| --- | --- |
| Save on a domain page | Validates and atomically persists the full active configuration source. Does not change active settings. |
| Reload configuration | Reads that same file, validates every setting and active path with startup's model, then applies logging level, iris theme, LED ring visual settings and all `vision.camera_preview` settings through shared runtime services. Shows applied fields and remaining restart-required fields. |
| Restart PHOS | Requires the managed service and explicit confirmation. Validates the saved file, requests graceful application shutdown, then systemd starts PHOS again from disk. |

If any setting/path is invalid, Reload applies **nothing**, including logging
level. Restart is also rejected before shutdown when the saved file is invalid.
No-op reloads report no changes. SDK logging stays at WARNING or above even when
PHOS logging is switched to DEBUG. No credentials, tokens or request payloads
are exposed by this feature.

The **Display & Appearance** page also offers the canonical `display.iris_color`
choice: cyan, blue, green, turquoise, amber, violet or white. It configures the
base iris theme; semantic accent tints still come from BehaviorEngine-produced
FaceState. Save it with the other Display & Appearance fields, then use Reload
configuration. The renderer receives the validated appearance through the
runtime's display-loop update boundary; the iris color blends smoothly into the
next frames. PHOS, Vision, camera, BehaviorEngine and providers keep running.
Expression semantics do not enter the renderer directly.

The **Vision** page also manages the optional camera preview on the physical
PHOS display. It is off by default; choose a corner, scale and maximum preview
FPS, then select whether to show the current face box and expression diagnostics.
Save and use Reload configuration to apply these fields immediately. The preview
uses the existing camera owner and latest in-memory frame, stays on the local
display, and is neither recorded nor exposed over this administration interface.

Iris color and camera preview settings are reloadable. Display geometry, cadence,
fullscreen and behavioral timing still require restart. Other implemented settings requiring restart are
web enabled/host/port; camera resolution/tracking/detection; expression enabled/provider, preprocessing,
smoothing and AWS policy; log destination and expression diagnostics. Switching
local/AWS is never done live. Password changes use their separate immediate
session-revoking mechanism, independent of Save/Reload.

The page shows configuration path, last successful startup/reload UTC time,
whether saved and active settings differ, reloadable differences and fields
requiring restart. Active settings are tracked by the parent application service,
not inferred from the saved file. The timestamp does not mean restart-only
settings were applied: those remain listed as pending. Runtime health and AWS
credential availability are not monitored.

To reload, open System actions and click **Reload configuration**. To restart,
click **Review and confirm restart**, read the interruption notice, check the
confirmation checkbox and press **Restart PHOS**. GET/page navigation never
restarts anything. A confirmation can be used only once. The acknowledgement is:
**Restart requested. PHOS will reload the configuration on startup.**

Expect temporary web loss and log in again after a few seconds. A slow connection
may lose the acknowledgement as shutdown begins; check the service locally before
retrying. The saved network settings determine the new URL. If web administration
was disabled, use the Pi terminal to enable it again. Unsaved form edits are lost.

Follow [managed startup](installation.md#managed-startup-and-browser-restart) to
install the user service. Manual terminal launches support Reload, but browser
Restart is unavailable; stop and rerun the normal startup command locally. Do
not run a manual copy beside the service (camera/port contention). No reboot,
arbitrary command execution, privileged shell or generic service-management API
is provided. **Reboot Raspberry Pi** is deferred beyond 1.3.0.

Configuration must remain valid until restart completes. Avoid concurrent local
file edits; a file changed or hardware removed after validation can still cause
startup to fail. Systemd bounds repeated startup failures; inspect its journal
and repair locally as described in installation. A lifecycle channel failure
reports unavailable rather than pretending that settings were applied.

Preview reload failures are shown as errors and logged in the parent. Earlier
successful appearance changes may already be active; inspect System actions
after correcting the camera/dependency problem. Invalid JSON/schema applies
nothing. An accepted preview configuration does not certify live image quality;
check the physical display and logs. The lifecycle channel waits up to five
seconds. If a native camera start takes longer, the action may still complete;
the response reports uncertainty and the channel stays unavailable until PHOS
restarts. Check locally rather than assuming the operation was cancelled.


## Sensors — BME280 / BMP280

After [I2C setup](installation.md#optional-environmental-sensor), open
**Sensors**, select **Type → BME280** or **BMP280**, enable the sensor, choose the actual `0x76`/`0x77` address and save.
Polling and stale timeout are seconds; the editor uses canonical validation.
All changes, including timing and disable, require **System actions → Restart
PHOS** (or a manual stop/start). Reload leaves them pending without touching
sensor, display or Vision services. Saving enabled does not mean a sensor exists.

The read-only panel reflects the parent application/status service at page load:
it shows the runtime's semantic robot state, active visual source, resolved
expression and interpreted motion state alongside the provider-neutral sensor
snapshot. The web worker never imports or reads a sensor provider directly.
Temperature
(°C), relative humidity (%) for BME280, and atmospheric/station pressure (hPa), last successful
UTC update, age in seconds, and status. Refresh for another snapshot after saving
any edits. This is not an automatically refreshing dashboard. No sensor driver
runs in the web worker and no extra network sensor endpoint is introduced.

BMP280 has no humidity sensor: its value is null and the panel shows **Not
supported**, including when unavailable. The panel shows the active sensor type
and uses explicit capabilities from runtime. A saved selection does not change
the displayed active type/capabilities until restart.

Status is disabled, starting, available, unavailable, stale or stopped. Failed
and stale measurements are hidden, while last-update/age remain visible for
diagnosis. Before the first successful read there is no timestamp. Missing
libraries/hardware, wrong chip/address and I/O errors show unavailable; errors
are sanitized to their exception type. Consult the Pi journal and installation
troubleshooting. A broken parent channel shows status unavailable, never guessed
values from saved configuration. Sensor failures do not change PHOS behavior.

Selecting a sensor type does not install its driver. If the sensor error is
`ModuleNotFoundError` and the Pi journal says `No module named 'bmp280'`, follow
the [BMP280 driver recovery instructions](installation.md#missing-bmp280-driver)
to install and verify the dependency in PHOS's virtual environment. Check the
active configuration path under System / Status if the logged sensor type
differs from the file you edited.


### CCS811 air quality

The same **Sensors** page has a **CCS811 air quality** configuration group:
enabled, address (`0x5a`/`0x5b`), polling interval and stale timeout. Install its
optional dependency and verify wiring using the
[CCS811 setup instructions](installation.md#optional-ccs811-air-quality-sensor).
Every setting requires **Save → System actions → Restart PHOS**; Reload leaves
changes pending. The committed default is disabled.

The read-only panel shows **eCO2 (estimated equivalent CO2), ppm**, **TVOC, ppb**,
last successful UTC update, age, health and compensation input. eCO2 is not a
direct NDIR CO2 measurement. Values are hidden during `warming_up`, unavailable,
stale or disabled states; the initial timestamp is absent. Refresh for a new
snapshot; this page does not acquire hardware or automatically stream readings.

The runtime withholds readings for 20 minutes after initialization. A new
sensor needs longer first-use conditioning; see installation. Missing DATA_READY
keeps polling without restarting the conditioning period. Fault recovery or
PHOS restart does initialize it again. Fresh BME280 temperature/humidity may
supply compensation automatically. BMP280 lacks humidity; stale, failed or
missing environmental data use clearly labeled device defaults. The panel
reports the input last written, not a guarantee that the displayed gas sample
already incorporates it. Consult the journal for hardware ERROR_ID diagnostics.

### GY-521 / MPU-6050 motion

The **GY-521 / MPU-6050 motion** group edits enabled state, `0x68`/`0x69` address,
polling and stale timeout, which require **System actions → Restart PHOS**. Its
motion threshold subgroup is live-reloadable: save then use **Reload
configuration**, without resetting the sensor. The
read-only panel shows acceleration X/Y/Z in m/s² and angular velocity X/Y/Z in
°/s, interpreted motion state, tilt direction, last event, timestamp, age and status. `Factory scale only; no offset calibration`
means the adapter converted its ±2 g / ±250 °/s raw scale but did not require a
motionless startup calibration. Values are hidden whenever unavailable or stale;
the page never opens I2C itself. Setup and Pi verification are in the
[MPU-6050 installation guide](installation.md#optional-gy-521-mpu-6050-imu).

IMU visual intensity and timing appear under **Display & Appearance → Eye
behavior**. They are applied by **Reload configuration**. Persistent movement and
tilt react while their motion state remains active; SHAKE and IMPACT use brief
alert overlays followed by smooth decay. `imu_tilt_eye_asymmetry_strength`
mirrors horizontal tilt eye sizes; impact strength must exceed shake strength so
the strongest alert remains unambiguous. Robot states such as
sleeping, speaking and error retain priority.
