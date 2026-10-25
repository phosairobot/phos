# MCP Semantic Adapter

PHOS Stage 1 provides a trusted-local, stdio-only MCP adapter over semantic
application services. Install the optional `mcp` extra on Python 3.10 or newer,
then run this instead of the usual PHOS process:

```bash
python -m robot.mcp --config config/phos.yaml
```

That process owns the single PHOS runtime and embeds the MCP server. Do not run
it alongside a normal PHOS runtime: no IPC attachment exists, and two runtime
owners would compete for hardware. Stdio closure requests normal shutdown;
MCP does not own a separately running PHOS process.

Available tools are `phos_get_status`, `phos_get_physical_context`,
`phos_get_presence`, `phos_get_attention`, `phos_get_observed_expression`,
`phos_set_expression(expression)`, `phos_speak(text)`, `phos_sleep`,
`phos_wake`, and `phos_signal_user(signal)`. Inputs use the existing semantic
expression/signal vocabulary and the existing 500-character speech bound.
Results are JSON-compatible semantic application results.

`phos_get_status` returns compact agent status only: `running`, `state`, broad
semantic availability, and degradation reasons. It is not the operational
`/api/v1/status` contract and never includes provider names, device paths,
controller sessions, model files, audio backends, raw sensor structures, LED
animation details, renderer/background data, or touch/startup diagnostics. Use
`phos_get_physical_context` for current semantic perception values.

Its `availability.voice` means the voice-input session is configured, enabled,
and has no current capture-session error. It does not mean PHOS is listening,
and it is independent of STT-provider availability and speech/TTS availability.

Presence excludes tracking identifiers; attention exposes only normalized target
coordinates/confidence, never an ID; observed expression excludes provider and
model details. Action acknowledgements are compact semantic documents:
expression `{expression, applied}`, sleep/wake `{state, applied}`, speech
`{accepted}`, and signals `{intent, applied}`. The stdio server runs on the
PHOS runtime loop, so its actions use the application service's nonblocking
same-loop path rather than synchronously waiting on work queued to that loop.

The default profile is `standard`; callers can construct a server with another
`AgentAccess`, such as `observer`, through `create_mcp_server`. Permission
denial is distinct from invalid input and unavailable capability/service errors.
No tool exposes raw sensors, LED animations, renderer or camera objects,
controller/serial configuration, configuration mutation, or recovery/admin
controls.

This is not a complete security boundary. Stage 1 has no authentication,
accounts, tokens, network listener, resources, subscriptions, event history,
camera snapshots, or asynchronous notifications. It is intended only for a
trusted local MCP client.
