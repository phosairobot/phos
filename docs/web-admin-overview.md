---
hide:
  - navigation
  - toc
classes:
  - phos-home
---

# Web Admin

<div class="phos-page-intro" markdown>

<span class="phos-kicker">CONFIGURE WITH BOUNDARIES</span>

Web Admin is PHOS’s optional, single-administrator configuration editor for a
trusted LAN. It does not access the camera, hardware drivers or renderer
directly: it uses the same canonical `config/phos.json` model, validation and
atomic persistence as startup.

</div>

## First access and safety

When enabled, browse to the configured Pi address. The bootstrap password is
`phos`, must be changed on first login, and there is no username. Password
hashes live outside runtime JSON; sessions expire and all forms use CSRF
protection. This is HTTP for a trusted LAN—not a public Internet service.

## Configuration domains

The sidebar exposes Dashboard, PHOS Status, Controls, Sensors, API, Diagnostics,
System Actions and focused Settings pages. Controls is the only writable runtime
action page: Robot State, Expression, Visual Source and Overlay are separate,
responsive cards. System Actions exposes only supported lifecycle operations.

Diagnostics reports saved-versus-active configuration and reload/restart-required
differences. Status does not claim live hardware health or probe AWS credentials.

## Live runtime dashboard

Dashboard, PHOS Status and Sensors show a current semantic snapshot, then keep
it updated through the authenticated Remote API event stream. The default
Waitress deployment uses Server-Sent Events at `/api/v1/events` (rather than a
WebSocket upgrade), preserving the same application-service event contract.
The connection indicator reports connected, reconnecting or disconnected. Last
valid values remain visible while reconnecting; a bounded reconnect backoff uses
a conservative 30-second snapshot fallback and always obtains a fresh snapshot
after reconnection.

The Sensors dashboard presents each optional BMP280/environmental sensor,
CCS811 and MPU6050 independently, including freshness and unavailable, stale,
warming-up or degraded conditions. It displays PHOS's interpreted environment,
motion, visual source and overlay intent without reimplementing sensor
interpreters in the browser. Runtime controls and overlay choices are populated
from Remote API capabilities on Controls, not browser-maintained enum lists.

The same read-only status cards show face-display touch telemetry: enabled
state, the last completed semantic gesture, position, normalized position,
duration and timestamp. Raw pointer input is neither retained nor streamed.

## Save, reload, restart

<div class="phos-callout" markdown>

**Save** validates and atomically persists the complete JSON file; it does not
change the running process. **Reload configuration** can apply logging level,
iris appearance, LED-ring visual settings and camera-preview settings. Other
valid changes remain explicitly restart-required. **Restart PHOS** is available
only through the managed user systemd service, with confirmation; it never
reboots the Pi.

</div>

Use the [canonical Web Admin manual](web-administration.md) for the full domain
map, recovery steps and security limits.
