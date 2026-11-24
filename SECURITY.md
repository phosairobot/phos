# Security Policy

## Supported Versions

Security maintenance targets the latest `1.9.x` release line on `main`.
Use the latest patch release in that line to receive security fixes.

| Version | Security updates |
| --- | --- |
| Latest `1.9.x` release on `main` | Supported |
| Older versions | Unsupported; upgrade to the supported release |
| Unreleased development snapshots on `dev` | No release support guarantee |

## Reporting a Vulnerability

**Do not disclose security vulnerabilities through public GitHub Issues.**
Do not publish exploit details, credentials, or private camera/audio data.

Use [GitHub Private Vulnerability Reporting](https://github.com/phosairobot/phos/security/advisories/new):
open the repository's **Security** tab → **Advisories** → **Report a
vulnerability**. Keep investigation and coordination in the private report
until a disclosure plan is agreed with the maintainers.

Maintainers aim to acknowledge reports within **72 hours**. This is an
acknowledgment target, not a promise of a fix within that period. If you have
not received a response, follow up in the private report.

Please include:

- The affected PHOS version, branch or commit, and relevant dependencies.
- Reproduction steps and a minimal proof of concept, where possible.
- The expected and actual behavior, security impact, and required access.
- Relevant deployment details: Raspberry Pi OS/Python versions, enabled
  services, authentication mode, network exposure, and attached controller.
- Sanitized logs or configuration excerpts, with all secrets and personal data
  removed, plus any suggested mitigation.

## Security Architecture & Deployment Best Practices

PHOS runs on a Raspberry Pi and exposes semantic application services through
Web Admin, the REST API, and MCP. These adapters reuse shared validation and
application services rather than providing direct hardware, GPIO, renderer, or
serial access. These boundaries do not protect against a compromised Pi account
or operating system.

### Network Exposure

PHOS is designed for **trusted local networks (LAN) and localhost**. The
committed [configuration](config/phos.yaml) disables Web Admin by default;
when enabled, its default listener is `127.0.0.1:8080`. The REST API shares
that listener under `/api/v1`. MCP is enabled by default at
`http://127.0.0.1:8001/mcp`.

Never expose these HTTP listeners directly to the public internet. Remote
access requires a carefully configured reverse proxy with TLS termination and
access controls, or a private VPN such as Tailscale or WireGuard. TLS alone
does not authorize a caller. The PHOS listeners do not provide TLS themselves;
keep backend ports private and prevent clients from bypassing the proxy or VPN.

Retain loopback binding when possible. If LAN binding is necessary, select a
specific trusted interface, restrict inbound access with a firewall, and avoid
router port forwarding. Plain HTTP on a LAN does not encrypt passwords, session
cookies, bearer tokens, or returned data.

Keep Raspberry Pi OS and PHOS dependencies updated. Run PHOS as a dedicated,
unprivileged account with only the device permissions it requires; protect
configuration, credential files, backups, and service environment files from
other users. See the [Web Admin deployment manual](docs/web-administration.md).

### MCP & API Authentication

Web Admin uses a single administrator account with a mandatory initial password
change, expiring sessions, CSRF protection for administration forms, and bounded
login attempts. Complete bootstrap before granting network access. The REST API
uses the same administrator session cookie; it does **not** accept MCP bearer
tokens or provide a separate API-token account. Treat that session as
administrative access. See the [REST API manual](docs/remote-api.md).

Manage each MCP agent's token through **Web Admin → Settings → Agents**.
Creation and rotation reveal the newly issued token once; stored tokens cannot
be retrieved through status/list operations. Rotate compromised tokens promptly
and revoke credentials for clients that no longer need access. Runtime
configuration stores only non-secret credential references; `SecretsService`
resolves stored values. Never put credentials in `config/phos.yaml`, source
control, logs, screenshots, or vulnerability-report attachments.

For the PHOS MCP bridge, supply an already-issued token through the
`PHOS_MCP_TOKEN` environment variable, using a protected launcher or service
environment rather than a literal saved in shell history. The bridge sends it
in the HTTP `Authorization: Bearer` header. Do not pass tokens in CLI flags,
URLs, or query strings. `PHOS_MCP_TOKEN` configures the bridge client; it does
not provision a server credential.

The default MCP authentication mode is `bearer`. **Without configured agent
credential references, compatibility mode accepts the configured default local
agent without verifying a token.** Provision credentials and verify that
unauthenticated requests are rejected before allowing access beyond localhost.
Modes `none` and `mixed` deliberately permit anonymous access; revoking a token
does not remove anonymous access in those modes.

Apply least privilege: use `observer` for clients needing only basic semantic
reads. It excludes camera snapshots, physical history, and control operations.
The default local agent uses `standard`, which includes control and the dedicated
`read_camera_snapshot` permission. Grant that profile only to trusted clients
that need its broader access. The configured anonymous profile also defaults to
`standard`, so review it before enabling anonymous access. See the
[MCP authentication and permissions guide](docs/mcp.md).

### Camera, Audio & Data Privacy

Camera face tracking, local camera preview, expression processing, object
awareness, voice input, and TTS are disabled in the committed configuration.
Enable only the features needed, with the knowledge and consent of people nearby.

- Physical measurement and transition history is bounded and **RAM-only**:
  at most six hours, 360 samples per metric, and 500 transitions, discarded on
  restart. This history excludes images, audio, transcripts, and identity data.
- PHOS does not send automatic cloud telemetry. Explicitly selected cloud
  providers are separate: AWS expression analysis can send selected face crops,
  cloud STT sends captured audio, and cloud TTS sends text. Review each provider's
  privacy and retention terms before enabling it.
- Camera snapshots require an explicit authorized MCP request and a running
  Vision pipeline. They are bounded JPEG responses, not continuous video streams;
  there is no default video streaming or image persistence. **There is no
  separate snapshot-enable switch:** disabling local preview does not prevent
  permitted MCP clients from reading snapshots while another Vision feature
  keeps the camera running. Restrict camera permissions as well as camera use.
- Object awareness is disabled by default and performs local, on-demand
  inference when enabled; it retains no object history or images.
- Voice capture requires enabling voice input and explicitly starting a capture
  session. The REST voice status can expose the last transcript to an
  authenticated administrator, but not raw audio. Audio debug dumps and retained
  final TTS WAV files are disabled by default; enabling them creates local files
  that require access controls and deliberate deletion.

External MCP clients, agents, and configured providers may retain data they
receive; PHOS's RAM-only history policy does not govern those systems. See the
[Vision privacy documentation](docs/vision.md) and [MCP guide](docs/mcp.md).

### Hardware / Serial Isolation

The **`phos-hw` v1** JSON Lines interface to the ESP32 is intended strictly for
a **local physical USB/serial connection**. The controller acquires sensor data
and executes resolved hardware commands; PHOS retains semantic behavior and
authorization decisions. The controller is disabled by default in the committed
configuration.

Treat physical access, attached firmware, and permission to open the serial
device as trusted boundaries. Protocol validation, capability discovery, and
heartbeats are not cryptographic authentication or encryption. Do not bridge
the serial port to a network listener or expose raw controller commands through
Web Admin, REST, or MCP. Restrict device access to the PHOS service account and
attach only trusted controllers running reviewed firmware. See the
[hardware controller guide](docs/hardware-controller.md).
