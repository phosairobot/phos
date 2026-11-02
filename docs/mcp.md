# MCP Semantic Adapter

PHOS starts its trusted-local MCP Streamable HTTP endpoint as part of normal
runtime startup. Install the `mcp` optional dependency, configure `mcp` in the
canonical PHOS configuration, then start PHOS normally:

```bash
systemctl --user restart phos.service
```

PHOS requires Python 3.11 or later for MCP and pins the supported SDK family as
`mcp>=2.0,<3`. The PHOS listener continues to own Uvicorn binding and shutdown;
the SDK supplies only the Streamable HTTP ASGI app. This keeps listener
lifecycle, Web Admin enable/disable, and port rebinding in `McpServerManager`.
Under SDK 2.x the ASGI app owns the protocol's per-client session handling and
its lifespan cleanup; PHOS does not retain SDK session objects. Uvicorn closes
that app as part of the existing listener shutdown path.

The committed default endpoint is `http://127.0.0.1:8001/mcp`. MCP is enabled
by default, binds only to loopback, and uses one listener in the same process,
runtime loop, `PhosApplicationService`, and `AgentApplicationService` as PHOS.
Each HTTP client has its own MCP session but never creates a runtime or service
graph. Stopping PHOS gracefully stops the listener and its sessions. If an
enabled endpoint cannot bind, PHOS performs normal shutdown and exits with an
error so the service manager applies its configured failure policy.

When Web Administration is enabled, **Settings → Network → MCP** can enable or disable
only this listener and change its port without restarting PHOS. The page and
`/api/v1/settings/mcp` report the actual listener state and endpoint. A failed
rebind restores the prior listener and does not persist the requested port;
existing client sessions disconnect when a listener is intentionally stopped or
rebound. The loopback host and `/mcp` path remain deliberately non-editable.

```yaml
mcp:
  enabled: true
  host: 127.0.0.1
  port: 8001
  path: /mcp
```

Older configuration files without this section retain these defaults. A
non-loopback host is allowed only by explicit configuration and emits a warning.
Bearer authentication is available when configured, but deployment/TLS policy
remains the operator's responsibility; do not expose this endpoint publicly.

## Agent identity and transitional local authentication

Every inbound MCP request passes through one authentication middleware boundary
before any tool or Resource reaches `AgentApplicationService`. The middleware
resolves an immutable `AgentIdentity` (`id`, optional display name, and an
existing permission-profile reference); the application facade then applies the
existing profile permissions before capability and runtime checks. Identity,
permission, capability, and current activity remain separate concerns.

Without configured credential references, the Stage 3.1 compatibility mode uses
`LocalAgentAuthenticator`, a deliberately transitional resolver
that identifies every trusted-local client as `local-agent` with the existing
`standard` profile. It does not verify credentials and must not be treated as
production-grade authentication. This preserves existing clients while a later
stage adds credential-backed authentication, persistence, and per-client policy.

Stage 3.2 persists the default identity in the canonical `agents` configuration.
`agents` absent from an older configuration retains `local-agent`/`standard`.
Once present, invalid records, duplicate IDs, unknown profiles, or an unknown
default fail configuration loading; no fallback grants broader access. Changes
require a PHOS restart because the registry is created at startup.

## Bearer authentication

An agent entry may contain only a non-secret bearer `secret_ref`. The actual
token is read by trusted runtime code from `SecretsService`, never from runtime
configuration or `AgentIdentity`. If any credential references are configured,
identity is request-scoped: every MCP request must provide `Authorization:
Bearer TOKEN`; missing or invalid credentials fail before application execution
and never fall back to `default_agent`. Without credential references, Stage
3.2 default-agent compatibility remains. Comparison uses a constant-time
secret comparison. Tokens are not logged or returned in MCP errors. Expiry and
external identity providers remain deferred.

## Credential lifecycle

Stage 3.4 provides `AgentCredentialService` as the application/service boundary
for future management surfaces:

```text
create / rotate / revoke
→ AgentCredentialService
→ SecretsService
→ stable credential.secret_ref
→ BearerAgentAuthenticator
```

The service generates a URL/header-safe 256-bit bearer with the operating
system CSPRNG. It returns a bearer only from creation or rotation, never via
status or a read-back operation. Rotation replaces the value at the same
`secret_ref`, immediately invalidating the old bearer; revocation removes the
stored secret and leaves its configured `AgentIdentity` and permission profile
intact. Authentication looks up the secret for each request, so no PHOS restart
is needed. Expiry policies and external identity providers remain future work.

Authenticated Web Admin exposes this lifecycle at **Settings → Agents** for
already-configured agents. It renders non-secret status only; newly created or
rotated bearers appear once in the immediate response and cannot be retrieved
after navigation or reload. Rotation and revocation require confirmation.

## Bridge authentication

MCP Stage 3.6 completes authentication support in the separately owned
`phos-mcp-bridge`. It is a transport-only stdio bridge; it does not validate a
credential or contain identity, permission, camera-permission, or
resource-permission logic:

```text
Claude / Inspector
→ MCP stdio
→ phos-mcp-bridge
→ Authorization: Bearer <credential>
→ PHOS Streamable HTTP MCP
→ BearerAgentAuthenticator
→ AgentIdentity
→ PermissionProfile
→ AgentApplicationService
```

The bridge reads its already-issued bearer from `PHOS_MCP_TOKEN` and injects it
only through its upstream Streamable HTTP transport. The bearer applies to
initialization, tools, Resources, subscriptions, and reconnects. An absent or
empty `PHOS_MCP_TOKEN` sends no `Authorization` header. A visible `--token` or
visible `--header Authorization` argument is rejected: do not put a bearer in
CLI arguments. Bearer values and Authorization headers are redacted from bridge
logging and error paths.

PHOS remains authoritative for authentication, `AgentIdentity` resolution,
`PermissionProfile`, authorization, camera permissions, and Resource
permissions. Invalid or missing credentials fail at PHOS; the bridge does not
retry as an unauthenticated or default identity. PHOS errors, including
`permission_denied`, are preserved.

Obtain the bearer through the PHOS credential lifecycle/Web Admin, then launch
the bridge without placing it on the command line:

```bash
export PHOS_MCP_TOKEN='...'

node /path/to/phos-mcp-bridge/dist/index.js \
  --url http://<phos-host>:8001/mcp
```

For Inspector TUI, the current working syntax is:

```bash
PHOS_MCP_TOKEN='...' \
npx --yes @modelcontextprotocol/inspector@latest --tui \
  --protocol-era modern \
  node /Users/francescoazzola/Documents/dev/phos-mcp-bridge/dist/index.js \
  -- \
  --url http://192.168.1.127:8001/mcp
```

The bridge does not manage the credential. If PHOS rotates or revokes it, the
old bearer no longer authenticates. Update `PHOS_MCP_TOKEN` and restart or
relaunch the bridge if required by its current implementation to establish a
new authenticated upstream MCP connection. Hot credential reload is not
claimed.

MCP exposes the existing `AgentApplicationService`, never hardware, renderers,
camera/provider objects, controllers, or raw runtime state. Available tools are
`phos_get_status`, `phos_get_physical_context`, `phos_get_presence`,
`phos_get_attention`, `phos_get_observed_expression`,
`phos_set_expression(expression)`, `phos_speak(text)`, `phos_sleep`,
`phos_wake`, and `phos_signal_user(signal)`. Input bounds, permission checks,
semantic result documents, and sanitized error mapping are transport-independent.

## MCP Resources

PHOS exposes read-only MCP Resources through the same permission-checked
application-service boundary as its tools:

| Resource | MIME type | Purpose |
| --- | --- | --- |
| `phos://status` | `application/json` | Compact semantic robot status. |
| `phos://physical-context` | `application/json` | Current Semantic Physical Context. |
| `phos://environment` | `application/json` | Current environmental semantic context. |
| `phos://vision/snapshot` | `image/jpeg` | Current on-demand camera snapshot. |

- `phos://status` — compact agent-facing semantic status.
- `phos://physical-context` — provider-independent semantic physical context.
- `phos://environment` — the environmental section of that physical context.
- `phos://vision/snapshot` — one current `image/jpeg` camera snapshot.

Resources are live snapshots: each read calls the same permission-checked
application-service boundary as the corresponding semantic tool. They do not
poll providers, mutate PHOS, replay history, or expose controller/camera/
renderer internals. The camera snapshot is synchronous, read-only and on
demand: it uses the existing Vision pipeline's latest in-memory frame, copies
it before bounded JPEG encoding, and never opens another camera or streams
video. It is protected by the dedicated `read_camera_snapshot` permission;
the standard profile has it while the observer profile deliberately does not.
This is separate from generic physical-context access because a snapshot may
contain people physically visible to the camera. It contains no camera device,
provider/backend, filesystem, identity, candidate-ID, debug, or EXIF/location
metadata.

### Discovery and snapshot read

The `phos-mcp-bridge` dynamically forwards MCP Resources, so no bridge-specific
snapshot implementation is required. Set `PHOS_MCP_TOKEN` as described in
[Bridge authentication](#bridge-authentication) when PHOS uses bearer
credentials. For a trusted-LAN PHOS endpoint:

```bash
npx --yes @modelcontextprotocol/inspector@latest --cli \
  node /Users/francescoazzola/Documents/dev/phos-mcp-bridge/dist/index.js \
  --url http://192.168.1.127:8001/mcp \
  --method resources/list
```

The result includes the four URIs in the table above. Read the current snapshot
with the Inspector CLI syntax supported by the installed version, for example:

```bash
npx --yes @modelcontextprotocol/inspector@latest --cli \
  node /Users/francescoazzola/Documents/dev/phos-mcp-bridge/dist/index.js \
  --url http://192.168.1.127:8001/mcp \
  --method resources/read --uri phos://vision/snapshot
```

The response has MIME type `image/jpeg` and binary MCP Resource content. The
Inspector may serialize that content as a textual blob representation for CLI
output; PHOS itself does not use a JSON/base64 snapshot contract.

## Async resource-change subscriptions

Modern MCP clients negotiating protocol revision `2026-07-28` can use
`subscriptions/listen` for `phos://status`, `phos://physical-context`, and
`phos://environment`:

```text
listen -> notifications/resources/updated -> resources/read
```

Notifications are URI-only change cues, not PHOS event payloads. Robot state
maps to status and physical context; environmental condition maps to environment
and physical context; air quality, motion, presence, attention, and observed
expression map to physical context; capability and system-health changes map to
status. `phos://vision/snapshot` is never announced because it is on-demand,
not a video stream.

Numeric sensor samples remain read-only Resource data and do not cause push
notifications. Confirmed environmental/motion conditions and meaningful sensor
availability or freshness status transitions do cause Resource update cues.
`environment_availability_changed` updates both `phos://environment` and
`phos://physical-context`; `air_quality_availability_changed` and
`motion_availability_changed` update physical context only. Camera snapshots
remain excluded from continuous notifications.

The SDK owns listen-stream filtering, disconnect cleanup, and bounded
per-client delivery. PHOS owns one bounded bridge subscription for its MCP
listener and coalesces duplicate pending resource URIs. MCP subscriptions are
not an event log: there is no replay, no history, and no raw sensor-telemetry
push. Updates while disconnected are not replayed; reconnect, open a new
listen stream, and read current Resources. In credential mode each subscription
request is authenticated, but subscription authorization is deliberately
cue-only: `ResourceUpdated` contains only a URI, and every subsequent
`resources/read` authenticates and applies the resource permission again. A
subscription therefore cannot disclose a protected camera frame to an observer.
The separately owned `phos-mcp-bridge` has been
externally validated for a long-lived stdio `subscriptions/listen` connection:
two consecutive `phos://status` ResourceUpdated notifications were forwarded
from PHOS Streamable HTTP to the Inspector. Bridge code is not owned or
modified by this repository.

Stage 3.6 bridge authentication implementation and automated validation passed
in the bridge repository (`npm run typecheck`, `npm run build`, `npm test`,
`npm run lint`, and `git diff --check`; 10 tests passing). Its live physical
interoperability validation against PHOS is not recorded here and remains part
of the pending Stage 3.7 validation work.

## Raspberry Pi validation

```bash
systemctl --user restart phos.service
systemctl --user status phos.service --no-pager
ss -ltnp | grep 8001
journalctl --user -u phos.service -n 100 --no-pager
```

On the Pi, start the MCP Inspector with `npx @modelcontextprotocol/inspector`.
In its connection form select **Streamable HTTP** and enter
`http://127.0.0.1:8001/mcp`; connect, then call each available tool. From a
workstation, first use an SSH tunnel and use the same Inspector URL locally:

```bash
ssh -N -L 8001:127.0.0.1:8001 pi@PHOS_HOST
npx @modelcontextprotocol/inspector
```

For an explicitly configured LAN host, connect only from that trusted LAN to
`http://LAN_ADDRESS:PORT/PATH`; do not forward it to the Internet.

### Recorded physical validation

Direct Streamable HTTP validation confirmed `phos://status` sleep/wake
notifications, `phos://physical-context` semantic updates, and
`phos://environment` availability updates. Sensor freshness validation
confirmed environment loss/recovery and motion/IMU availability transitions.
The bridge validation above confirmed a stdio subscription to `phos://status`
and two consecutive ResourceUpdated notifications. These checks did not
include physical air-quality validation. With Camera Preview disabled,
`phos://vision/snapshot` remained available and `resources/read` returned an
`image/jpeg` Resource with a non-empty blob. This validates resource
availability and JPEG output only; it does not validate image content quality.
