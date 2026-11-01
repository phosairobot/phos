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

## Semantic snapshot resources

PHOS also exposes read-only MCP resources, each served as `application/json`:

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
