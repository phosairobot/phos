# PHOS 1.7.0 — External Hardware Controller & Semantic LED Control

The authoritative application version is `robot.__version__` in
`src/robot/__init__.py`: **1.7.0**. This release does not change the
`phos-hw` protocol version (**1**) or the REST API base path (**`/api/v1`**).

## Included Raspberry-side scope

- A generic, bounded UTF-8 JSON Lines `phos-hw` v1 serial transport with
  capability discovery, heartbeat monitoring, reconnect lifecycle, and
  bounded diagnostics for malformed controller input.
- Controller-backed environmental, air-quality, and IMU updates that enter the
  existing semantic sensor services. Selecting `provider: controller` prevents
  the corresponding local Raspberry I2C worker from polling the same hardware.
- Hardware Controller Web Admin configuration, connection/read status, and
  bounded recent RX/TX diagnostics through the application/lifecycle boundary.
- Capability-aware LED command delivery and reapplication after a new
  controller session, controlled by `SemanticLedService` rather than Web/API
  raw hardware commands.
- Semantic LED arbitration between robot state, environmental context, and
  bounded user signals. `POST /api/v1/signal` accepts `success`, `warning`,
  `error`, and `attention`; all are transient. `RobotState.ERROR` remains the
  persistent robot-state error indication.
- Validated LED animation vocabulary, including the `direction`, `mode`, and
  `origin` semantics where an animation supports them. These logical fields do
  not expose physical LED indexes; controller firmware owns that mapping.

## Boundaries and compatibility

`phos-hw` remains a hardware-agnostic protocol boundary, not a firmware API.
The Raspberry side currently transmits capability requests, heartbeat pings,
and resolved `set_led_animation` commands. It consumes the strict sensor,
motion, and orientation messages documented in the
[architecture reference](architecture.md#external-hardware-controller-protocol-v1).

The release contains no controller firmware. A deployed controller must
implement the documented v1 contract and be physically accepted with its
specific wiring, sensors, and LED layout. The Remote API remains semantic:
there is no raw controller or LED command endpoint.

## Verification completed in the repository

- Hardware-free controller/protocol, semantic LED, lifecycle, Web Admin, and
  protocol-tester regression coverage.
- API/OpenAPI and release-metadata consistency checks.
- Python compilation, strict documentation build, and diff whitespace checks.

Physical Raspberry Pi/controller acceptance is intentionally separate from
these repository checks. Repeat it after deployment with the chosen controller:
verify capability discovery, stable heartbeat/reconnect, controller sensor
updates without local I2C polling, IMU telemetry, LED output/reapply after a
session change, and semantic signal expiry/restoration.
