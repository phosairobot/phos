# Remote API

PHOS exposes a versioned, provider-neutral remote API for observing and
controlling its semantic state. Every request passes through
`PhosApplicationService`: clients never access GPIO, sensor buses, camera
frames, renderer objects, or vendor providers directly.

The base path is `/api/v1`. The checked-in [OpenAPI 3.1 contract](api/openapi.yaml)
is the detailed schema source; the running server publishes the same contract at
`/openapi.json` and local Swagger UI at `/docs`.

## Access and authentication

The API shares the Web Admin authentication boundary. Sign in through `/login`
in the same browser before using Swagger UI. It uses the existing `phos_admin`
HttpOnly, SameSite session cookie; there is no API token or separate API account.
Unauthenticated `/api/...` requests receive JSON `401` errors, not login-page
redirects. A session that still must change its initial password receives JSON
`403` until that is completed.

The listener uses canonical `web.host` and `web.port` configuration and requires
a restart after changes. The committed configuration is loopback `127.0.0.1` on
port `8080`; do not assume that port when connecting to another installation.
For LAN access, bind deliberately to an appropriate local interface, retain Web
Admin authentication, and place TLS plus any additional access control at a
trusted reverse proxy. Do not expose the listener directly to the Internet.

Examples below use a local listener. They assume an authenticated session cookie
has already been established. For `curl`, pass that existing cookie with
`--cookie "$PHOS_ADMIN_COOKIE"`; no credentials are shown or stored here.

```sh
curl --cookie "$PHOS_ADMIN_COOKIE" http://127.0.0.1:8080/api/v1/status
curl --cookie "$PHOS_ADMIN_COOKIE" http://127.0.0.1:8080/openapi.json
```

## Read endpoints

All read endpoints use `GET` and return JSON.

| Endpoint | Purpose |
| --- | --- |
| `/api/v1/status` | Consolidated robot, visual, environmental, motion, health, presence, attention and touch state. |
| `/api/v1/presence` | Current provider-neutral Presence read model. |
| `/api/v1/attention` | Current provider-neutral Attention read model and optional target. |
| `/api/v1/voice` | Voice/STT availability and the last completed transcript; never raw audio. |
| `/api/v1/observed-expression` | Latest uncertain classifier observation for the selected face. |
| `/api/v1/state` | Lifecycle state and whether Core is running. |
| `/api/v1/environment` | Environmental sensor availability and current measurements when available. |
| `/api/v1/motion` | IMU availability and current semantic motion snapshot. |
| `/api/v1/health` | Runtime uptime, Python version and subsystem health. |
| `/api/v1/capabilities` | Discoverable command metadata and observable vocabulary. |
| `/api/v1/overlay` | Environmental, manual, and resolved ambient-overlay intent. |
| `/api/v1/config` | Saved and active canonical non-secret configuration. |

For example:

```sh
curl --cookie "$PHOS_ADMIN_COOKIE" http://127.0.0.1:8080/api/v1/health
curl --cookie "$PHOS_ADMIN_COOKIE" http://127.0.0.1:8080/api/v1/environment
```

Unavailable, stale, disabled, and warming-up sensor states remain explicit in
responses; clients must not treat missing measurements as current data.

## Voice capture

`POST /api/v1/voice/listen` starts one explicit capture session;
`POST /api/v1/voice/stop` ends it, and `POST /api/v1/voice/cancel` discards it.
These commands go through the application service and do not expose microphone
or STT-provider controls. Semantic SSE events cover listening, speech edges,
transcription completion, cancellation and safe errors.

`GET /api/v1/voice` returns `enabled`, `state`, `listening`,
`speech_detected`, `stt_provider`, `stt_available`, `language`,
`last_transcript`, `last_confidence`, `last_transcription_at`,
`last_transcription_duration_ms`, and `last_error`. The lifecycle is `idle →
listening → thinking → idle`: listening captures/detects speech, thinking runs
STT, and idle is inactive/completed. An empty recognizer result is successful
with `last_transcript: null` and no error; `voice_empty_utterance` instead
denotes audio shorter than the configured minimum, while `voice_error` denotes
a provider/capture failure. Observable event names are
`voice_listening_started`, `voice_speech_started`, `voice_speech_ended`,
`voice_transcription_started`, `voice_transcription_completed`,
`voice_empty_utterance`, `voice_session_cancelled`, and `voice_error`.

The voice API exposes capture/transcription status only; transcript command
interpretation and robot actions are not implemented.

Presence has `no_one`, `person_present`, and reserved `person_engaged` values.
Attention has `idle`, `acquiring`, `tracking`, and `lost` values. Target ID is
runtime-local only; position is normalized, and confidence may be `null` when a
detector did not supply it. These are observation models, not identity or
biometric-recognition APIs.

Observed Expression is independent telemetry, not PHOS's own visual expression
and not a statement of a person's emotion. It returns HTTP 200 with
`available: false` when no current classified face exists (or its 1.5-second
freshness window has expired); this does not imply that Presence is `no_one`. When available, confidence is the actual classifier
confidence. `observed_expression_changed` SSE events are emitted only when
availability or label changes, not for confidence jitter alone.

Touch is physical-input telemetry, not a remote command. Its read model reports
only the last valid completed `tap`, `long_press`, `swipe_left`, or
`swipe_right`; raw pointer movement and startup-splash input are not recorded.
The SSE stream emits the matching edge event name once per completed gesture,
with position, normalized position and duration payload fields.

## Capabilities

`GET /api/v1/capabilities` is the discovery endpoint. `observable_states`
contains readable runtime vocabulary, including values that cannot be submitted
(for example robot `error`). `commands` describes writable operations and their
HTTP method, endpoint, request field(s), and allowed values.

Current semantic command values are:

| Command | Allowed values |
| --- | --- |
| `set_robot_state` | `idle`, `listening`, `thinking`, `speaking`, `sleeping` |
| `set_expression` | `neutral`, `happy`, `curious`, `surprised` |
| `set_visual_source` | `manual`, `environment`, `state` |
| `set_overlay.temperature` | `none`, `cold`, `warm` |
| `set_overlay.air_quality` | `none`, `warning`, `bad` |

Use capability metadata rather than hard-coding these values: it is derived from
the same application validation rules as the commands.

## Semantic commands

All command bodies must be JSON objects. Invalid JSON bodies receive
`invalid_request`; invalid semantic values receive a structured JSON error of
the form `{"error":{"code":...,"message":...,"details":...}}` with HTTP
`400`. A command may also return `503` when the PHOS runtime is unavailable.

### Set lifecycle state

`POST /api/v1/state`

```json
{"state":"sleeping"}
```

`/capabilities` lists the stable writable vocabulary; `error` is observable
only and is rejected as runtime-only. The existing state machine enforces
`IDLE → LISTENING → THINKING → SPEAKING → IDLE` with its documented sleeping
alternatives, so a skipped transition returns HTTP `409` with
`invalid_state_transition` and the current/target values. A successful response
is the current robot-state document, for example:

```json
{"state":"sleeping","running":true}
```

`GET /api/v1/state` and the `robot` member of `/api/v1/status` also include
`current`, stable `writable` values and state-dependent `allowed_next` values.
`/api/v1/capabilities` exposes the complete stable transition graph under
`commands.set_robot_state.transitions`; clients use it to disable invalid
targets without removing or reordering the writable options.

```sh
curl --cookie "$PHOS_ADMIN_COOKIE" -X POST http://127.0.0.1:8080/api/v1/state \
  -H 'Content-Type: application/json' -d '{"state":"sleeping"}'
```

### Trigger expression

`POST /api/v1/expression`

```sh
curl --cookie "$PHOS_ADMIN_COOKIE" -X POST http://127.0.0.1:8080/api/v1/expression \
  -H 'Content-Type: application/json' -d '{"expression":"happy"}'
```

Allowed values are `neutral`, `happy`, `curious`, and `surprised`. A successful response is
`{"expression":"happy"}`. A command is a 30-second manual semantic override
owned by `BehaviorEngine`: it takes priority over Vision, environment and motion
expression selection while PHOS is IDLE, but RobotState visual intent remains
higher priority. The response includes its remaining lifetime. Other readable
expressions, such as `worried`, are not writable and return
`unsupported_expression_command`; unknown values return `invalid_expression`.

### Set visual source

`POST /api/v1/visual-source`

```sh
curl --cookie "$PHOS_ADMIN_COOKIE" -X POST http://127.0.0.1:8080/api/v1/visual-source \
  -H 'Content-Type: application/json' -d '{"source":"environment"}'
```

Allowed values are `manual`, `environment`, and `state`. The response is, for
example, `{"source":"environment"}`. Invalid values return
`invalid_visual_source`.

### Configuration

`PATCH /api/v1/config` validates and persists a partial canonical configuration
document, then applies the reloadable part. `GET /api/v1/config` returns saved,
active, and pending configuration. Unknown fields and invalid values return
`invalid_configuration`. See [Web Admin](web-administration.md) for the
configuration model and restart policy. Its body is a partial canonical
`RuntimeConfig` document rather than a command enum; allowed fields and values
are therefore the canonical configuration schema.

```sh
curl --cookie "$PHOS_ADMIN_COOKIE" -X PATCH http://127.0.0.1:8080/api/v1/config \
  -H 'Content-Type: application/json' \
  -d '{"display":{"base_visual_source":"environment"}}'
```

A successful response includes `saved`, reload `applied` paths, and `pending`
restart-required paths. Invalid JSON objects return `invalid_request`; unknown
or invalid configuration fields return `invalid_configuration`.

## Overlay commands

Ambient overlays keep temperature and air-quality channels independent.
`GET /api/v1/overlay` returns three concepts:

- `environmental`: current sensor-derived intent from environmental interpretation.
- `override`: active manual/transient values, plus `expires_at` when timed.
- `resolved`: the intent currently presented after arbitration.

Manual values take precedence over their corresponding environmental channel.
When an override expires, or `DELETE /api/v1/overlay` clears it, PHOS resolves
again against the *current* environmental intent—it never restores a cached
value from when the override was created.

Example response:

```json
{
  "environmental": {"temperature":"warm","air_quality":"none"},
  "override": {"active":false,"temperature":null,"air_quality":null,"expires_at":null},
  "resolved": {"temperature":"warm","air_quality":"none"}
}
```

`POST /api/v1/overlay` requires at least one of `temperature` or `air_quality`.
`duration_ms`, if supplied, is a positive integer lifetime in milliseconds.

```sh
curl --cookie "$PHOS_ADMIN_COOKIE" -X POST http://127.0.0.1:8080/api/v1/overlay \
  -H 'Content-Type: application/json' \
  -d '{"temperature":"warm","duration_ms":3000}'

curl --cookie "$PHOS_ADMIN_COOKIE" -X POST http://127.0.0.1:8080/api/v1/overlay \
  -H 'Content-Type: application/json' \
  -d '{"temperature":"cold","air_quality":"warning","duration_ms":5000}'

curl --cookie "$PHOS_ADMIN_COOKIE" -X DELETE http://127.0.0.1:8080/api/v1/overlay
```

Invalid temperature, air-quality, or duration values return respectively
`invalid_overlay_temperature`, `invalid_overlay_air_quality`, or
`invalid_overlay_duration`; an empty request returns `invalid_overlay`.

## Events

`GET /api/v1/events` is a Server-Sent Events (SSE) stream, not a WebSocket. It
emits bounded semantic events as `type`, `timestamp`, and `payload`, suppresses
consecutive duplicate payloads, and sends keepalive comments while idle. The
current WSGI deployment does not implement a WebSocket endpoint.

The stream forwards `presence_changed`, `person_entered`, `person_left`,
`attention_changed`, acquired/lost/changed attention target events,
`observed_expression_changed`, and `expression_reaction_changed` in addition to
existing semantic updates. The Web Admin uses these only for its read-only
status display.

```sh
curl --cookie "$PHOS_ADMIN_COOKIE" -N http://127.0.0.1:8080/api/v1/events
```
