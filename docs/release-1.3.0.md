# PHOS 1.3.0 — Presence, Attention & Expression Awareness

The authoritative version is `robot.__version__` in `src/robot/__init__.py`.
Setuptools metadata, startup logging, Web Admin status, and the documentation
homepage derive from that source. The checked-in OpenAPI document declares the
same current release version.

## Highlights

- **Presence awareness:** confirmed, hysteretic human-presence state with
  `person_entered` and `person_left` semantic events.
- **Attention:** `idle`, `acquiring`, `tracking`, and `lost` state, target
  acquired/lost/changed semantics, configurable loss hold, and semantic gaze.
- **Presence LED reactions:** configured enter/leave sweeps arbitrate as bounded
  transients, then re-resolve the current persistent LED state.
- **Observed facial expression:** local ONNX/FER+ classifier telemetry exposes
  label, real classifier confidence, provider/model and freshness separately
  from Presence.
- **Expression Reaction Policy:** provider-neutral confirmation, threshold,
  cooldown and bounded-duration reactions use conservative mappings while
  preserving Attention gaze and ambient overlays.
- **Realtime visibility:** API/SSE and Web Admin expose Presence, Attention,
  Observed Expression, PHOS Expression, and expression-reaction status.
- **Unified installation:** `scripts/install-phos.sh` installs supported system
  packages and the canonical `.[all]` runtime extra, prepares the Local ONNX
  model, and runs software-only smoke checks in one recommended command.

## Architecture and integration

Presence and ObservedExpression are independent read models. Providers do not
control the renderer or LED hardware directly; `BehaviorEngine` remains the
behavioral arbitration owner. Application-service read models and semantic event
forwarding supply the Remote API, SSE stream, and read-only Web Admin cards.

## Configuration

Canonical `RuntimeConfig` includes `presence`, `attention`, and
`expression_reactions`. Existing complete configuration documents receive only
the scoped defaults required for these sections; strict full-document validation
remains in effect. Administrator credentials and other secrets remain outside
runtime JSON.

## Testing and release gates

The automated regression suite and strict documentation build have passed.
For camera/display/LED integration testing on Raspberry Pi, confirm
enter/leave hysteresis, Attention loss hold and gaze, LED sweep arbitration,
classifier availability/freshness, reaction confirmation/cooldown, and Web
Admin/API SSE updates.

## Known limitations

PHOS does not provide face recognition, persistent identity, biometric
identification, eye-contact detection, voice-based emotion inference, or
advanced multi-person selection. ObservedExpression is an uncertain classifier
observation of a visible facial expression; it is not an inference of a person's
true emotional state.
