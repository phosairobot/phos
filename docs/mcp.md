# MCP Semantic Adapter

PHOS starts its trusted-local MCP Streamable HTTP endpoint as part of normal
runtime startup. Install the `mcp` optional dependency, configure `mcp` in the
canonical PHOS configuration, then start PHOS normally:

```bash
systemctl --user restart phos.service
```

The committed default endpoint is `http://127.0.0.1:8001/mcp`. MCP is enabled
by default, binds only to loopback, and uses one listener in the same process,
runtime loop, `PhosApplicationService`, and `AgentApplicationService` as PHOS.
Each HTTP client has its own MCP session but never creates a runtime or service
graph. Stopping PHOS gracefully stops the listener and its sessions. If an
enabled endpoint cannot bind, PHOS performs normal shutdown and exits with an
error so the service manager applies its configured failure policy.

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
