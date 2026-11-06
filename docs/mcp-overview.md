# PHOS & MCP — Give AI Agents a Physical Presence

[Model Context Protocol (MCP)](https://modelcontextprotocol.io/) is an open standard for connecting AI applications with external tools and contextual information. Its [specification](https://modelcontextprotocol.io/specification) defines the protocol; it does not supply intelligence. An AI Agent provides reasoning. PHOS provides bounded physical perception and controlled physical execution.

```text
AI Agent → MCP client or bridge → PHOS → semantic services → physical capabilities
```

PHOS can run on its own, but MCP lets a compatible, authenticated agent ask about the robot's current context and request supported actions. PHOS continues to own hardware, robot state, safety boundaries, and permission checks.

## Ask PHOS or request an action

These synchronous interactions return a result to the caller. Availability depends on configured hardware and services; unavailable or degraded capabilities are reported rather than assumed to work.

| Capability | What an agent can do | Interface | Access |
| --- | --- | --- | --- |
| Robot status | Read robot state, available capabilities, and degraded conditions. | `phos_get_status`, `phos://status` | Observer or standard |
| Physical context | Read available environment, air-quality, motion, presence, attention, and observed-expression semantics. | `phos_get_physical_context`, `phos://physical-context` | Observer or standard |
| Environment | Read temperature, humidity where available, pressure, and air-quality indicators with availability/freshness context. | `phos://environment` | Observer or standard |
| Presence and attention | Read whether people are present and the semantic attention state—never identity. | `phos_get_presence`, `phos_get_attention` | Observer or standard |
| Observed expression | Read a bounded visible-expression classification; it is not an emotion or identity claim. | `phos_get_observed_expression` | Observer or standard |
| Camera observation | Retrieve the privacy-bounded camera snapshot when camera access is permitted. | `phos://vision/snapshot` | Standard only |
| Expression | Request a supported PHOS expression. | `phos_set_expression` | Standard only |
| Voice | Ask PHOS to speak bounded text through configured TTS. | `phos_speak` | Standard only |
| Robot state | Request normal sleep or wake transitions. | `phos_sleep`, `phos_wake` | Standard only |
| Semantic signal | Request a supported, transient user-attention signal. | `phos_signal_user` | Standard only |

An `observer` profile is deliberately read-only. A `standard` profile can use the currently exposed semantic actions and, when camera is available, the protected snapshot Resource. PHOS checks permissions on every authenticated request; a client never receives direct GPIO, serial-controller, or renderer access. See the [MCP reference](mcp.md) for the exact endpoint, authentication, and credential lifecycle.

## React to changes

MCP resource subscriptions provide asynchronous *change cues*, not long-running tool jobs and not unsolicited chat messages. A subscribing client decides what to do after it is notified.

```text
Subscribe → PHOS state changes → resource update notification → agent refreshes Resource
```

The verified subscribable update targets are:

- `phos://status` for robot-state, capability, and system-health changes.
- `phos://physical-context` for robot-state plus relevant environment, air-quality, motion, presence, attention, and observed-expression changes.
- `phos://environment` for relevant environmental condition or availability changes.

PHOS coalesces repeated update cues. The client rereads the updated Resource to obtain current data. `phos://vision/snapshot` is readable by authorized clients, but PHOS does not publish subscription updates for it. The separately owned Streamable-HTTP bridge forwards the supported MCP subscription path and can reconnect upstream; client behavior after a disconnection remains the client's responsibility.

In short: **synchronous** means “what is PHOS's current state?”; **asynchronous** means “notify my agent when this selected PHOS state changes.”

## Practical ways to use MCP

### Environmental awareness

PHOS provides fresh, availability-aware environmental observations. The agent uses those observations in its own reasoning or response. This is synchronous; it requires the relevant configured sensors and read permission.

### A physical voice for an agent

PHOS provides speech and a supported expression request. The agent decides what to say and when to use it. This is synchronous and requires a `standard` agent, a working configured TTS path, and the requested supported expression.

### Presence-aware assistance

PHOS provides presence and attention semantics without identifying people. The agent decides whether a response is appropriate. This is synchronous and requires the optional vision pipeline plus read permission.

### Reactive state monitoring

PHOS provides Resource update cues when state changes, such as sleep or wake. The agent subscribes, refreshes the notified Resource, and decides its next step. This is asynchronous and depends on MCP client subscription support.

### Camera-assisted observation

PHOS provides an authorized current snapshot. The agent performs any further visual interpretation. This is synchronous, requires camera availability, and is restricted to the `standard` profile.

## Get started

1. Configure and start PHOS's trusted-local Streamable HTTP MCP endpoint.
2. Create an agent and credential in **Settings → Agents** when credential mode is enabled.
3. Connect a compatible client to the endpoint and provide the bearer through its safe credential mechanism.
4. Start with status or physical context, then request only capabilities your configured agent is allowed to use.

For connection details, profiles, bearer handling, full tool/resource contracts,
remote HTTPS-tunnel guidance, and security considerations for anonymous access,
continue to the [technical MCP reference](mcp.md#mcp-authentication-security).
