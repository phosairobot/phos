# PHOS 1.8.0 release candidate

PHOS 1.8.0 is the MCP and Runtime Administration stabilization release. Its
authoritative source version is `robot.__version__` (`1.8.0`); packaging and
the Web Admin version display derive from that source.

## Included scope

- Trusted-local Streamable HTTP MCP server and stdio bridge compatibility.
- Permission-checked semantic MCP tools and semantic snapshot Resources.
- `phos://vision/snapshot`: a synchronous, read-only, on-demand `image/jpeg`
  Resource using the existing Vision pipeline's latest frame. It has no video
  streaming path, does not require Camera Preview, and never opens a second
  camera. The dedicated `read_camera_snapshot` permission is granted to the
  standard profile and withheld from the observer profile.
- Web Admin Runtime pages split by concern, including immediate Camera Preview
  hot settings and logging-level application through the lifecycle/runtime
  boundary.

MCP remains an application-service adapter:

```text
External Agent -> MCP -> AgentApplicationService
               -> PhosApplicationService -> PHOS runtime services
```

It does not provide direct access to hardware, camera providers, serial
controllers, renderers, sensor providers, or direct `RobotState` mutation.

## MCP Stage 2 status

- **MCP Stage 2 — COMPLETE.**
- Complete: semantic event model, event filtering, event deduplication, and
  `SemanticSubscription` lifecycle.
- Complete: capability/system-health semantic events, semantic snapshot
  Resources, camera snapshot Resource, and stdio bridge Resource forwarding.
- Complete: MCP async semantic Resource cues via `subscriptions/listen`.
  Notifications are URI-only change cues; clients re-read Resources. Synchronous
  camera snapshots do not deliver async events.
- Complete: sensor availability/freshness transition cues and externally
  validated long-lived stdio bridge forwarding. The bridge remains separately
  owned; this repository does not contain or modify its code.
- Pending: MCP authentication and per-client authorization.

Sensor availability/freshness events are edge-triggered and do not publish raw
telemetry. Resource updates are change cues, not an event log: they provide no
replay, history, or raw telemetry push, and a reconnect must open a new
subscription and read current Resources. Raspberry Pi validation confirmed
direct-HTTP `phos://status` sleep/wake notifications,
`phos://physical-context` semantic updates, `phos://environment` availability
updates, environment loss/recovery, and motion/IMU availability transitions.
The bridge validation observed two consecutive `phos://status` ResourceUpdated
notifications through a stdio subscription. No physical air-quality validation
is claimed. With Camera Preview disabled, `phos://vision/snapshot` remained
available and returned a non-empty `image/jpeg` blob. This validates resource
availability and JPEG output, not image-content quality. A full fake-clock test does not currently drive the long-running
sensor-freshness monitor sleep loop with a real sensor-service age calculation;
coverage through `_check_sensor_freshness()` exists, so this is a non-blocking
test follow-up.

## Validation and release gate

Automated tests cover MCP discovery and reads, permission enforcement, snapshot
encoding and preview independence, application/lifecycle behavior, and Web
Admin configuration paths. Raspberry Pi physical acceptance verified that, with
Camera Preview disabled, `phos://vision/snapshot` remained available and
`resources/read` returned a non-empty `image/jpeg` blob. This completes the
previously pending MCP Stage 2 camera-snapshot gate; it does not validate image
content quality. No tag, deployment, or publication is claimed by this source
release candidate.
