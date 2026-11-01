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

- Complete: semantic event model, event filtering, event deduplication, and
  `SemanticSubscription` lifecycle.
- Complete: capability/system-health semantic events, semantic snapshot
  Resources, camera snapshot Resource, and stdio bridge Resource forwarding.
- Complete: MCP async semantic Resource cues via `subscriptions/listen`.
  Notifications are URI-only change cues; clients re-read Resources. Synchronous
  camera snapshots do not deliver async events.
- Pending: MCP authentication and per-client authorization.

Sensor availability/freshness events are edge-triggered and do not publish raw
telemetry. Raspberry Pi validation confirmed environment loss/recovery and IMU
availability Resource cues; the monitor sleep-loop fake-clock test remains a
non-blocking test improvement.

## Validation and release gate

Automated tests cover MCP discovery and reads, permission enforcement, snapshot
encoding and preview independence, application/lifecycle behavior, and Web
Admin configuration paths. The target-Pi validation remains a release
acceptance task: verify `resources/list` exposes `phos://vision/snapshot`, then
verify `resources/read` returns a non-empty `image/jpeg` blob, including with
Camera Preview disabled. No physical Raspberry Pi validation, tag, deployment,
or publication is claimed by this source release candidate.
