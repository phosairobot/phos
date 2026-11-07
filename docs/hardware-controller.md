# How PHOS Communicates with Its External Hardware Controller

PHOS implements the `phos-hw` v1 external-controller boundary. It is a generic
serial protocol that an ESP32 firmware can implement; PHOS does not embed or
maintain that firmware in this repository.

## Responsibilities

The Raspberry Pi owns high-level semantics, state, arbitration, and application
services. The controller owns supported physical execution and sensor
acquisition. It does not decide robot behavior or expose a second Web/API/MCP
control plane.

This separation keeps hardware modular, isolates physical execution, supports
reconnection after transport failure, and lets a controller implementation be
replaced or extended without moving PHOS semantics into firmware.

## Connection and lifecycle

Enable `hardware_controller` with the configured serial transport, device, and
baud rate. PHOS performs capability discovery, sends heartbeats, reconnects
after transport failure using the configured delay, and exposes bounded
connection status/recent diagnostics in Web Admin. Controller transport failure
does not give Web clients direct serial access.

## Protocol

Messages are framed JSON objects with `protocol: "phos-hw"`, `version: 1`, a
`type`, optional correlation fields, and an object `payload`. Capability reports
describe controller-supported features. Controller `sensor_update` and
`motion_update` messages feed the same semantic snapshots used by local sensor
providers; semantic LED commands are resolved by PHOS before the controller
executes timing/physical mapping.

See [Architecture: external controller protocol](architecture.md#external-hardware-controller-protocol-v1)
for exact message contracts, validation, bounds, failure handling, and LED
arbitration. See [Development](development.md#external-hardware-controller-administration)
for configuration and Web Admin behavior.

## Verified v1 commands and messages

`phos-hw` v1 uses UTF-8 JSON Lines: one bounded JSON object per newline. Every
message has `protocol: "phos-hw"`, `version: 1`, `type`, and object `payload`;
`id`, `reply_to`, and `timestamp_ms` are optional correlation fields. PHOS
starts a reader before capability discovery, retries discovery until a valid
peer frame arrives, sends `ping` as its heartbeat, and reconnects after a
transport failure using the configured delay. A controller `heartbeat` updates
the recorded heartbeat status. On disconnect, PHOS retains bounded diagnostics
but disables active controller LED output.

Malformed lines remain rejected. Their bounded diagnostics identify whether
UTF-8 or JSON parsing failed, the frame length, a short hexadecimal prefix, and
the connection generation; complete untrusted frames are not retained. Valid
later lines received in the same serial read continue through normal protocol
validation. ESP32 boot/debug output must not share the protocol UART, and every
firmware protocol write must emit one complete UTF-8 JSON object followed by a
newline.

The protocol recognizes the following vocabulary. “Recognized” is not the same
as “currently emitted by PHOS”: the current serial adapter transmits only
`request_capabilities`, `ping`, and resolved `set_led_animation`.

| Protocol name | Direction | Purpose / data | Response or unavailable behavior |
| --- | --- | --- | --- |
| `request_capabilities` | Pi → controller | Starts discovery; empty payload may carry an `id`. | Controller supplies `capabilities`; PHOS retries while connected until a valid peer frame. |
| `capabilities` | controller → Pi | Controller-defined feature metadata, including optional sensor/LED support. | Activates session capabilities; LEDs require advertised support. |
| `ping` | Pi → controller | PHOS heartbeat; empty payload. | Peer activity keeps the session healthy; v1 requires no separate `pong`. |
| `heartbeat` | controller → Pi | Controller liveness cue. | Updates PHOS's recorded heartbeat time. |
| `sensor_update` | controller → Pi | Semantic sensor/value/unit or supported push event. | Valid updates enter existing semantic services; invalid updates are rejected safely. |
| `motion_update` | controller → Pi | Semantic motion `state` and `intensity`. | Exact validated values update the IMU service. |
| `orientation_update` | controller → Pi | Finite `x`, `y`, `z` acceleration and angular-velocity vectors. | Exact validated vectors update IMU telemetry. |
| `set_led_animation` | Pi → controller | PHOS-resolved LED animation semantics. | Sent only for an active LED-capable session; otherwise unavailable. |
| `hello`, `controller_status`, `warning`, `error`, `ack` | controller → Pi | Recognized lifecycle/diagnostic/acknowledgement vocabulary. | Recorded as bounded diagnostics; no PHOS semantic mapping currently. |
| `configure_sensor_reporting`, `stop_led_animation`, `set_led_brightness`, `clear_led` | Pi → controller | Reserved recognized command vocabulary. | Not transmitted by the current PHOS adapter. |

Unknown message types, unsupported versions, malformed JSON, and invalid
validated payloads receive bounded protocol errors. Unknown top-level fields
are ignored; arbitrary controller data never reaches PHOS services.

## Verified JSON Lines examples

Each complete object below is followed by a newline on the wire.

```json
{"protocol":"phos-hw","version":1,"type":"request_capabilities","id":"phos-1-cap-1","payload":{}}
```

```json
{"protocol":"phos-hw","version":1,"type":"sensor_update","payload":{"sensor":"temperature","value":24.8,"unit":"c"}}
```

```json
{"protocol":"phos-hw","version":1,"type":"set_led_animation","payload":{"animation":"breathe","color":"#42a5ff","brightness":0.5,"period_ms":1200}}
```

## Sensor and motion observations

Controller-backed observations enter the same semantic services as local
providers. BME280/BMP280 can provide temperature and pressure; only BME280
supplies humidity. CCS811 reports air-quality indicators, including eCO2 as an
*estimated CO2 equivalent*, not direct CO2. MPU6050 telemetry is represented by
semantic motion updates and/or acceleration and angular-velocity vectors.
Physical hardware, wiring, and firmware support vary by installation.

The accepted semantic sensor values are temperature in `c`, pressure in `hpa`,
humidity in `%`, eCO2 in `ppm`, TVOC in `ppb`, and the push event
`{"sensor":"push","event":"pressed"}`. Motion states are `stationary`,
`moving`, directional tilt, `shake`, or `impact`, with `low`, `medium`, or
`high` intensity. PHOS owns the meaning of those observations after they cross
the protocol boundary.

## Operational status

The PHOS controller protocol, reconnect lifecycle, capability discovery, and
controller-backed sensor/LED integration are implemented and automatically
tested. Controller firmware, wiring, and physical-device acceptance remain
deployment-specific and should be verified with the matching hardware.
