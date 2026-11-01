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
non-loopback host is allowed only by explicit configuration and emits a warning:
MCP authentication, agent identity, and network authorization are not yet
implemented. Do not expose this endpoint to the public internet.

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
snapshot implementation is required. For a trusted-LAN PHOS endpoint:

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
not an event log: updates while disconnected are not replayed; reconnect, open
a new listen stream, and read current Resources. Current trusted-local MCP has
no agent identity, so subscription visibility cannot be individualized until
authentication exists. The stdio bridge has not been validated for long-lived
listen streams in this repository.

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
