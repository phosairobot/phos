# PHOS 1.0.0 release record

The current implemented capabilities are the complete 1.0.0 scope. The
**authoritative version** is `robot.__version__` in `src/robot/__init__.py`.
Setuptools derives package metadata from it; startup logs and System / Status
use that same value. Versioning is not runtime configuration.

## Included scope

- Raspberry Pi 3 runtime with an 800×600 animated face, independent render and
  Vision cadence, blink/gaze behavior, visual accents and state priority.
- Layered, lightweight eye rendering with named cyan, blue, green, turquoise,
  amber, violet or white iris themes in canonical display configuration.
- Local camera face selection/tracking and gaze; uncertain visible-expression
  observations mediated by BehaviorEngine, including abstention and decay.
- Optional local-display camera picture-in-picture diagnostics, off by default,
  using the existing Vision camera owner and latest in-memory frame.
- Explicit local ONNX or optional AWS Rekognition expression provider; cloud
  work is bounded, cached and rate-limited, without automatic provider fallback.
- One canonical `config/phos.json`, strict startup/editor validation,
  config-relative paths and atomic saves. Deprecated explicit CLI overrides
  remain compatible; they never persist into the file.
- Authenticated trusted-LAN web administration in its own process, with General,
  Network, Display & Appearance, Vision, Expression Recognition, Logging,
  Web Administration / Security and read-only System / Status pages.
- Bootstrap password `phos`, mandatory rotation, salted password hashes outside
  configuration, expiring/revocable sessions, logout, CSRF and attempt limits.
- Validated reload of logging level, eye appearance and camera preview through
  reusable runtime services, plus confirmed PHOS restart through a user systemd
  service; no OS reboot.
- AWS credentials remain external in the SDK chain. There are no credential
  fields or credential values in configuration/status pages.

## Installation and operation

Use the [installation guide](installation.md)
for the exact supported source-deployment procedure and dependency snapshot.
Start from the Pi's graphical desktop session:

```bash
cd /home/pi/phos
.venv/bin/python src/robot/main.py --config config/phos.json
```

The canonical defaults start only eyes. Set `web.enabled` true and `web.host`
to the Pi's LAN address (or `0.0.0.0` for all IPv4 interfaces) to enable access.
Open `http://<PI-LAN-IP>:8080/`, enter `phos`, change it to a different 12–256
character password, and log in again. Password recovery, per-domain editing,
backup and HTTP limits are in the [administration manual](web-administration.md).

These examples are edits **inside the existing complete configuration**, not
separate files. Preserve all other required fields:

```json
{"expression": {"enabled": true, "provider": "local"}}
```

Local mode requires the model path, labels and preprocessing already configured
under `expression.local`. Install/check the model from the installation guide.
Face tracking without expressions uses `vision.face_tracking_enabled: true` and
`expression.enabled: false`.

```json
{"expression": {"enabled": true, "provider": "aws"}}
```

AWS mode uses `expression.aws` region/timeouts/limits and external credentials.
It needs no local ONNX file; selected face crops leave the Pi. Set
`expression.enabled` false to disable expression processing. Reload logging
level, iris appearance or camera preview; restart for other configuration
changes. Password changes take effect immediately.

## Hardening in this release

Camera configuration/start failures close the allocated camera. Camera closure
is also attempted if stop fails. Partially started Vision and cancellation of
startup release camera/core resources. Regression tests use fake hardware.

Source sync now stops on errors, creates deployment directories and copies the
manual, packaging metadata and web dependency snapshot alongside the source.
It still preserves deployed configuration, models and administrator data.
Packaged installs carry the canonical JSON as data rather than recreating defaults.

## Verification and limits

Automated tests cover lifecycle rollback/cancellation, eye geometry and behavior,
tracking, expression semantics, local/AWS selection using mocks, configuration
validation/persistence, authentication, bootstrap rotation, CSRF, revocation,
domain isolation, validated reload, restart confirmation and a real local web-worker
socket/shutdown test.
Detailed verification evidence and environment gaps are recorded in
[the implementation handoff](current-state.md#phos-100-finalization).

The hardware record documents eyes/display and Pi Camera/tracking as operational.
For deployment testing, run these checks on the target:

1. Confirm Python imports, camera preview and the model checksum per installation.
2. Run eyes-only and verify 800×600 output, animation cadence and Ctrl+C cleanup.
3. Enable tracking; move/lose/reacquire a face and confirm smooth bounded gaze.
4. Test local expressions with the actual model and verify happy/surprised
   observations, UNKNOWN abstention and neutral remaining disabled until calibrated.
5. If AWS is used, verify credentials/region/permissions, bounded requests,
   connectivity failure and continued local eye/tracking behavior.
6. Test first login, password change/logout, all domain pages from phone/tablet/
   desktop, valid/invalid saves and restart application of saved settings.
7. Install the user service; test logging-level reload, invalid reload rejection
   and confirmed browser restart with temporary web loss and changed network settings.
8. Repeat start/stop, including SIGTERM, and check camera and port release plus
   Pi CPU/RAM use while logging in and running Vision.

Known limitations:

- Expression accuracy has not been established; MobileFaceNet is the current
  configured candidate and FER+ a documented comparison/rollback. See
  [model evaluation](vision-model-evaluation.md); no new accuracy claim is made.
- HTTP is unencrypted and trusted-LAN-only. No TLS/public Internet deployment,
  multi-user administration or remote password reset is included.
- Status reports parent-owned active configuration, not live health. AWS credentials are not
  probed. A crashed web worker requires PHOS restart.
- Logging level, iris color and camera preview are reloadable; other configuration changes require restart; no automatic backup or cross-process
  file locking. Use one PHOS process per configuration/credential store.
- Raspberry Pi OS/camera packages are installed by apt and are platform-specific;
  the Python web snapshot is pinned but this is not a byte-reproducible OS image.
- Python 3.11+ is the documented release-installation baseline. The package's
  existing Python >=3.9 compatibility declaration is retained; older interpreters
  need compatible dependency resolution and are not the pinned release baseline.

There is no commit, release tag or publication implied by this release record.
Post-1.0 work is explicitly deferred in the [roadmap](roadmap.md).
