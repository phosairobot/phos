# Developer quickstart

This path gets a compatible MCP client to its first PHOS interaction. PHOS uses
Streamable HTTP; it is not a local stdio server and it never exposes raw hardware
controls.

## Before you connect

1. Start PHOS and enable the MCP listener in its canonical configuration.
2. Keep the default listener on a trusted local network. The documented default
   endpoint is `http://127.0.0.1:8001/mcp`; use the actual configured host/port
   for a different installation.
3. In Web Admin, open **Settings → Agents**. Create or select an agent, assign
   the least-privileged profile, and create a bearer credential when bearer
   authentication is enabled. Copy it only when it is shown: PHOS cannot read it
   back later.
4. Start with an `observer` profile when only reading status or context. Use
   `standard` only for permitted actions and camera access.

For the full configuration and authentication matrix, use the
[MCP reference](mcp.md#mcp-authentication-security).

## Choose your client path

Use native Streamable HTTP when the client supports PHOS's protocol and safe
bearer credential handling. Otherwise, use the stdio bridge below with a
compatible client. Tools, resources, and notifications are separate client
features; check the [capability table](mcp-overview.md#client-capabilities-and-transports).

## Connect through the stdio bridge

The recommended [phos-mcp-bridge](https://github.com/phosairobot/phos-mcp-bridge)
requires **Node.js 20+** and connects compatible local `stdio` clients to PHOS Streamable HTTP. Build it using its
repository instructions and use an absolute path to its compiled entry point.
It reads the issued bearer from `PHOS_MCP_TOKEN`, then
connects upstream to PHOS; PHOS remains responsible for identity and permission
checks. For a remote Pi, use `http://<PHOS_IP>:8001/mcp` with your actual address
and configured network access. The default listener remains loopback-only;
see the [remote connection guide](mcp.md#connecting-remote-mcp-clients).

```bash
export PHOS_MCP_TOKEN='issued-bearer-value'
node /path/to/phos-mcp-bridge/dist/index.js \
  --url http://PHOS_HOST:8001/mcp
```

Do not put a bearer in a URL or visible command-line header. The bridge is an
integration maintained outside this repository; use its installed version's
client-specific configuration instructions. The PHOS-side boundary and this
environment-variable pattern are documented and tested here.

!!! warning "Do not replay physical actions automatically"

    The bridge never automatically retries `tools/call`, including after a
    transport failure: PHOS may already have executed the action. Check the
    robot's current state before deciding whether to submit another call.
    Pass `PHOS_MCP_TOKEN` through the launcher's environment or secret manager;
    never put the bearer in CLI flags, URLs, or committed configuration.

## First interaction

1. Discover tools and Resources from your MCP client.
2. Call `phos_get_status` or read `phos://status`.
3. Call `phos_get_physical_context` for currently available semantic context.
4. If your agent has the relevant permission, use
   `phos_get_physical_assessment` before making an interpretation from retained
   measurements across a sensor interruption.
5. With a `standard` profile and deliberate authorization, request one bounded
   action such as
   `phos_set_expression`, `phos_speak`, or `phos_signal_user`.

The response is semantic state, not a hardware driver handle. Capability and
availability errors are expected when optional hardware is disabled or not ready.

## Add event-driven interaction

For change cues, use a client supporting MCP revision `2026-07-28` and
`subscriptions/listen`. Open a listen stream for `phos://physical-context`,
then read initial context. After `notifications/resources/updated`, read the
resource again and let your external logic decide whether to call an output
tool. The notification does not start agent reasoning by itself.

Reopen the stream and read current context after disconnecting. PHOS has no
notification replay, and numeric sensor samples alone do not trigger cues.
Follow the [end-to-end sequence](mcp-overview.md#react-to-changes).

## What to build next

- Use `phos_query_measurements` and `phos_list_physical_transitions` only with
  `read_physical_history`; retained data is RAM-only and is discarded on restart.
- Use `phos_get_visible_objects` only with `read_perception`, an enabled camera,
  and a locally installed NanoDet model. It is on-demand and returns labels and
  confidence, not images or tracking.
- Subscribe to Resource change cues only if your MCP client supports
  `subscriptions/listen`; after a cue, read the Resource again. PHOS does not
  make an external Agent reason in the background.

See [MCP integration](mcp-overview.md) for a readable catalog and
[MCP reference](mcp.md) for schemas, limits, security, and error behavior.
