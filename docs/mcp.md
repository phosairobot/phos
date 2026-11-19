# MCP Semantic Adapter

New to MCP with PHOS? Start with [PHOS & MCP — Give AI Agents a Physical
Presence](mcp-overview.md) for the user-facing capabilities guide.

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
default fail configuration loading; no fallback grants broader access. Web
Admin updates replace the complete validated runtime registry, so agent
create/edit/delete changes apply to subsequent MCP requests without restart.

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

## Configurable authentication modes

The canonical `mcp.authentication` configuration defaults to `bearer`, which
preserves bearer-only operation. `none` resolves every request as the internal,
non-persistent `anonymous-mcp` principal using the configured anonymous profile;
a well-formed Bearer header is ignored for identity selection, while malformed
or unsupported Authorization headers are rejected. `mixed` resolves a missing
Authorization header as that anonymous principal and a valid Bearer header as
its configured agent. An explicitly invalid or revoked Bearer is always
rejected—PHOS never falls back to anonymous access after an invalid credential.

Anonymous access is enabled only when `none` or `mixed` is selected. It uses
the existing `standard` or `observer` permission profile and does not create an
agent record or credential. In mixed mode, revoking a bearer prevents use of
that authenticated identity, but does not prevent a client from reconnecting
without a token when anonymous access is enabled.

| Mode | Without token | With valid Bearer token |
| --- | --- | --- |
| Bearer | Rejected | Registered authenticated agent |
| None | `anonymous-mcp` | `anonymous-mcp` |
| Mixed | `anonymous-mcp` | Registered authenticated agent |

Bearer Token Only is the default. It resolves the registered agent identity and
its configured permission profile; credentials can be rotated or revoked. No
Authentication uses the shared `anonymous-mcp` principal, so callers are not
individually identifiable through MCP authentication. Mixed permits both: a
missing token selects the anonymous principal, while a valid token selects its
registered agent. Explicitly invalid or revoked Bearer tokens are rejected and
never fall back to anonymous access.

### Anonymous permissions

Both anonymous profiles use the same PHOS authorization boundary as registered
agents. `observer` is read-only. `standard` can use the semantic reads and
currently exposed actions permitted to a standard agent, including supported
expression, speech, sleep/wake, and user-signal requests, subject to runtime,
capability, privacy, and safety checks. It may also access Resources available
to the standard profile. Anonymous access never bypasses semantic arbitration
or PHOS safety protections.

## MCP Authentication & Security

Configure authentication in **Settings → MCP**. Select **Bearer Token Only**,
**No Authentication**, or **Mixed Authentication**. For No Authentication and
Mixed, choose the anonymous Standard or Observer profile. Web Administration
shows a security warning and requires explicit confirmation before enabling
unauthenticated access.

The validated authentication policy is applied to new MCP HTTP requests without
rebinding the listener or restarting PHOS. Clients affected by a mode change
should reconnect. Requests already being processed retain the policy snapshot
with which they started; PHOS does not claim targeted termination of SDK-owned
transport sessions or subscriptions.

### Security considerations for unauthenticated MCP access

An endpoint without token enforcement can be used by any client that can reach
it. With the Standard profile, an unintended caller may make PHOS speak, change
expressions, request sleep or wake, trigger semantic signals, and read allowed
environmental or presence-related context. This matters because PHOS is a
physical robot.

HTTPS encrypts traffic; it does **not** authenticate callers. A public HTTPS
tunnel can make a local MCP endpoint reachable from the Internet, and a URL is
not an adequate access control. A tunnel or reverse proxy with its own enforced
access restrictions is different from a publicly reachable tunnel, but PHOS
still applies its configured MCP authentication and authorization.

Anonymous callers share one internal identity. PHOS cannot reliably distinguish
anonymous applications, individual bearer revocation does not block anonymous
access, and audit attribution is limited. Existing safety arbitration is not a
substitute for authentication.

Recommended safeguards:

1. Keep Bearer Token Only as the default.
2. Enable anonymous access only when it is required.
3. Restrict access at the tunnel, reverse proxy, or network boundary.
4. Use Observer when actions are unnecessary; use Standard only when needed.
5. Monitor unexpected MCP access and disable anonymous access when finished.
6. Do not expose Web Administration or unrelated PHOS services through an MCP tunnel.

## Connecting Remote MCP Clients

PHOS exposes Streamable HTTP at its configured endpoint, for example
`http://<PHOS_IP>:8001/mcp`. An HTTPS tunnel or reverse proxy can provide a
remote endpoint such as `https://example.ngrok-free.app/mcp`; these URLs are
illustrative. The tunnel provides connectivity, while PHOS performs configured
authentication and permission checks.

```text
External MCP Client → HTTPS tunnel / reverse proxy → PHOS MCP Server
                                                    → authentication + permissions
                                                    → AgentApplicationService
                                                    → PHOS semantic services
```

For a remote client, verify transport reachability, MCP initialization, tool
discovery, Resource access, tool execution, and subscriptions separately. A
successful connection alone does not prove every capability is available.

### Example: connecting an AI assistant through an HTTPS tunnel

ChatGPT is one example of a compatible MCP client; the same approach applies to
other clients that support Streamable HTTP and the chosen authentication method.

1. Enable the PHOS MCP listener.
2. In **Settings → MCP**, select Mixed Authentication only if anonymous access is needed.
3. Select the least-privileged anonymous profile for the use case.
4. Configure an HTTPS tunnel to forward to the PHOS MCP port.
5. Configure the client with the resulting URL ending in `/mcp`.
6. Use its supported authentication option, connect, and verify tool discovery.
7. Test a read-only operation before requesting an authorized action.

Not every MCP client supports unauthenticated Streamable HTTP, and client setup
screens vary. In Bearer mode, supply an issued credential through the client's
safe credential mechanism rather than a URL or command line.

### Troubleshooting remote access

- Confirm the URL ends in `/mcp`, the tunnel forwards to the correct PHOS host
  and port, and the MCP listener is running.
- A Bearer-only endpoint rejects missing credentials. In Mixed mode, an invalid
  or revoked supplied Bearer is rejected; omit it only when anonymous access is
  intentionally enabled.
- Check tunnel, reverse-proxy, and client protocol-header errors separately from
  PHOS MCP errors. A tunnel `502`, timeout, or connection failure usually does
  not identify an MCP application problem.
- Reconnect clients after authentication configuration changes.

## Credential lifecycle

`AgentCredentialService` is the application/service boundary for credential
management surfaces:

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

Authenticated Web Admin exposes agent create/edit/delete and this lifecycle at
**Settings → Agents**. IDs are immutable; profile edits affect subsequent
requests using the same bearer. It generates a valid non-secret reference only
when a credential is created. The default agent cannot be deleted; deleting
another agent confirms revocation first. Newly created or rotated bearers appear
once in the immediate response and cannot be retrieved after navigation or
reload. Rotation, revocation, and deletion require confirmation.

Agent creation, deletion, and permission-profile edits do not require a PHOS
restart: new MCP authentication resolves the current registry. Already-open MCP
sessions are not forcibly terminated and may retain their current request or
session context; clients should establish a new request/session after a change.
Selecting a different default agent also persists and publishes immediately;
it changes only trusted-local/default compatibility resolution, never an
explicitly authenticated agent identity. The selected default is protected from
deletion until another configured agent becomes default.

## Permission profiles

`standard` agents can use the complete currently exposed semantic tool set and
the protected camera snapshot Resource/tool. `observer` agents retain read-only
semantic context but cannot request control actions such as sleep, wake,
speech, expression changes, or user signals; they also cannot read the camera
snapshot. Permissions are enforced by PHOS on every authenticated request.

## Connecting a client

Use Streamable HTTP directly when the client supports it. Otherwise a separate
transport bridge may be required; `phos-mcp-bridge` is separately owned and is
not configured or modified by this repository. For a generic compatible client,
connect to the configured endpoint (default `http://127.0.0.1:8001/mcp`) and
send an already-issued bearer through an HTTP Authorization header when
credential mode is enabled. See [Bridge authentication](#bridge-authentication)
for the verified boundary and keep bearer values out of command lines.

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
`phos_get_attention`, `phos_get_observed_expression`, `phos_get_camera_snapshot`,
`phos_capture_camera_snapshot`,
`phos_set_expression(expression)`, `phos_speak(text)`, `phos_sleep`,
`phos_wake`, `phos_signal_user(signal)`,
`phos_query_measurements(metric, start?, end?, limit?)`, and
`phos_list_physical_transitions(event_type?, limit?)`. Input bounds, permission
checks, semantic result documents, and sanitized error mapping are
transport-independent.

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

`phos_get_camera_snapshot` returns a JPEG encoded from the same latest cached
Vision frame as an MCP image content block, plus a small text block with
`captured_at` (UTC), `age_ms` (monotonic-clock elapsed time), and `freshness`
(`fresh` through 2 seconds, otherwise `stale`). `captured_at` is recorded when
PHOS receives the frame from its camera provider; it is not the request or JPEG
encoding time, and it is not a hardware exposure timestamp. Stale frames remain
available but are never labelled current. The tool never initiates a capture,
infers scene content, or changes presence/attention state. Tool calls are
bounded to one snapshot per second per authenticated identity; the existing
Resource remains available for its current read-only JPEG contract.

`phos_capture_camera_snapshot` has the same image, permission, JPEG-bound, and
metadata format, but its metadata starts `source=on_demand`. PHOS records the
current Vision frame sequence when the request is accepted and waits for a later
frame from the already-running pipeline, for up to three seconds. It is not a
separate sensor capture. The cached and on-demand tools share the same
per-identity transfer limit, so alternating calls cannot bypass it; at most four
on-demand waits may be pending. A timeout, stopped/restarted pipeline, or camera
failure returns a sanitized error and never falls back to the preceding frame.
Both camera paths reject JPEGs larger than 512 KiB.

Resources are live snapshots: each read calls the same permission-checked
application-service boundary as the corresponding semantic tool. They do not
poll providers, mutate PHOS, replay history, or expose controller/camera/
renderer internals. The camera snapshot is synchronous, read-only and encodes
the existing Vision pipeline's latest cached frame; it never opens another
camera, starts a capture, or streams video. It is protected by the dedicated
`read_camera_snapshot` permission;
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
Presence changes (`no_one` / `person_present`) and semantic attention changes
both update `phos://physical-context`. The re-read projection contains only
anonymous presence state, count, and reliable confidence plus semantic attention
state and non-identifying target coordinates/confidence; it never contains a
candidate ID, target ID, face embedding, or camera frame. Delivery is
best-effort: a notification says only that the Resource may have changed, not
that a client acted on it or that every intermediate state was observed.

This is standard MCP Resource subscription support, not MCP Events and not a
mechanism that independently activates any AI agent's reasoning. Compatible
agents choose whether to subscribe and what to do after re-reading a Resource.
The currently pinned Python MCP SDK provides `subscriptions/listen` but does
not provide native typed MCP Events discovery or the `events/list`,
`events/subscribe`, and `events/unsubscribe` methods. A future, optional,
provider-neutral MCP Events adapter remains deferred until those native SDK
extension points are available. It must be independently enabled and must not
change the endpoint behavior seen by ordinary MCP clients.
The separately owned `phos-mcp-bridge` has been
externally validated for a long-lived stdio `subscriptions/listen` connection:
two consecutive `phos://status` ResourceUpdated notifications were forwarded
from PHOS Streamable HTTP to the Inspector. Bridge code is not owned or
modified by this repository.

The bridge's Node 22 build and automated suite validate its standard MCP
subscription forwarding path. Live physical interoperability remains a manual
Raspberry Pi acceptance step.

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
# Temporal History v0.1

`phos_query_measurements(metric, start?, end?, limit?)` returns accepted
RAM-only numeric observations and a min/max/average summary. `start` and `end`
are timezone-aware RFC 3339 timestamps, the requested interval may not exceed
six hours, and `limit` is 1–360. `phos_list_physical_transitions(event_type?,
limit?)` returns confirmed semantic transitions with a 1–360 limit. Both require
`read_physical_history`. History holds at most six hours, one sample per metric
per minute, 360 samples per metric, and 500 transitions; it is lost on restart.
Missing, stale, and unavailable inputs are not fabricated or interpolated.
History excludes camera/audio data, transcripts, and identity.

## Physical Context Intelligence v0.1 — agent composition contract

PHOS supplies factual physical observations and bounded summaries. An external
Agent combines those facts with its own task context and is solely responsible
for interpretation and decisions. This is an on-demand composition pattern, not
a PHOS runtime subsystem: it starts no worker, polls no sensor, persists no
additional data, and adds no MCP tool or Resource.

Use `phos_get_physical_context` first to obtain the current semantic context.
Its `environment` section reports `status`, `available`, `temperature_c`,
`pressure_hpa`, `humidity_percent`, `last_update`, and `age_seconds`. Its
`air_quality` section similarly reports `status`, `available`, `eco2_ppm`,
`tvoc_ppb`, `aqi_uba`, `last_update`, and `age_seconds`. The same document
includes current `robot`, `presence`, and `attention` sections. The current
context establishes whether a reading is currently available or stale; history
does not turn an unavailable sensor into a current one.

For a selected time window, call the history tools with their actual signatures:

```text
phos_get_physical_context()
phos_query_measurements(metric, start?, end?, limit?)
phos_list_physical_transitions(event_type?, limit?)
```

For example, an Agent determines its own RFC 3339 UTC start timestamp for one
hour ago, then requests:

```text
phos_query_measurements("temperature_c", start="2026-11-19T09:00:00Z",
                        end="2026-11-19T10:00:00Z", limit=360)
```

The measurement result contains `metric`, `samples`, `count`, and `summary`.
Each sample contains `metric`, `value`, `unit`, `observed_at`, `source`, and
`quality`. When samples exist, `summary` contains `minimum`, `maximum`,
`average`, `delta`, `coverage`, `start`, and `end`; `start` and `end` are the
first and last returned samples. The `delta` is PHOS's arithmetic last-value
minus first-value summary, not an interpretation of comfort, safety, or cause.

Use the same query for `humidity_percent`, `pressure_hpa`, `eco2_ppm`,
`tvoc_ppb`, or `aqi_uba`. Query only the metrics relevant to the Agent's task;
there is no combined trend endpoint. For the latest confirmed presence change,
request:

```text
phos_list_physical_transitions(event_type="presence_changed", limit=1)
```

For recent attention or robot-state changes, use `event_type="attention_changed"`
or `event_type="robot_state_changed"`, with a suitable `limit`. A transition
result contains `transitions` and `count`; each transition contains `type`,
`current`, `previous`, `observed_at`, and `source`.

### Observation and interpretation boundary

The following examples show the boundary deliberately:

| Situation | PHOS measurement or semantic observation | PHOS mathematical summary | External Agent interpretation |
| --- | --- | --- | --- |
| Temperature rose in one requested hour | Returned `temperature_c` samples have `value` 20.0 then 21.0. | `summary.delta` is 1.0 with `coverage: "sufficient"`. | “Temperature increased by approximately 1 °C within the requested window.” |
| Air-quality values changed | Returned `eco2_ppm`, `tvoc_ppb`, or `aqi_uba` samples differ across the requested window. | Each metric query returns its own min/max/average/delta. | Describe the reported direction and magnitude; do not infer pollutant source or issue an autonomous response. |
| Presence became present | A `presence_changed` transition reports `previous: "no_one"` and `current: "person_present"`. | None. Transitions are semantic facts, not numerical trends. | “PHOS last confirmed a presence-state change to person present at `observed_at`.” |
| Trend cannot be established | The metric has no samples, or one sample with `summary.coverage: "insufficient"` and `summary.delta: null`. | No delta is supplied for one sample; no summary exists for zero samples. | “There is insufficient retained measurement history to describe a trend.” |

### Missing-data and retention contract

- No samples means PHOS has no retained observations for that metric and query;
  it does not mean the physical quantity was absent or unchanged.
- One sample cannot establish a delta. Its summary has `coverage: "insufficient"`
  and `delta: null`; it must not be presented as a complete trend.
- Two or more samples provide PHOS's `coverage: "sufficient"` summary, but do
  not guarantee continuous coverage of the requested interval. PHOS does not
  interpolate gaps.
- `phos_get_physical_context` is authoritative for current sensor status,
  availability, and age. Stale or unavailable sensors must not be represented
  as fresh merely because retained samples exist.
- No matching retained transition does not prove that no transition occurred.
  The requested event may predate retention, follow a restart, or not have been
  recorded by the active semantic source.
- A presence transition is a confirmed semantic observation after PHOS's
  presence confirmation logic; it is not proof of a person's exact arrival or
  departure time.
- All history is RAM-only, limited to the last six hours, and discarded on
  runtime restart.

This composition requires `read_physical_history` for both history tools.
`phos_get_physical_context` remains governed by `read_physical_context`; an
Agent without history permission must not derive history from the current
context.
