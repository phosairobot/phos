# PHOS roadmap

## PHOS 1.3.0 — Presence, Attention & Expression Awareness

PHOS 1.3.0 integrates provider-neutral Presence and Attention, observed
facial-expression telemetry, cautious expression reactions, and realtime Web
Admin/API visibility. See the [release record](release-1.3.0.md). Automated
regression has passed; target-Pi acceptance remains before tagging.

- [x] Versioned 1.3.0 source, package metadata and current-release documentation.
- [x] Presence hysteresis, semantic enter/leave events, Attention lifecycle,
  semantic gaze, and configurable LED arrival/departure sweeps.
- [x] ObservedExpression read model and conservative ExpressionReactionPolicy
  with confirmation, cooldown, bounded duration, lifecycle events and Web Admin visibility.
- [x] Unified Raspberry Pi OS installer with the canonical `.[all]` runtime
  aggregate, model preparation, smoke checks, and consolidated installation docs.
- [x] Complete automated release regression and strict documentation validation.
- [ ] Complete target-Pi camera/display/LED acceptance.

## PHOS 1.2.0 — Remote API release candidate

PHOS 1.2.0 adds the authenticated, provider-neutral Remote API with semantic
status and commands, capability discovery, temporary overlay overrides, local
OpenAPI/Swagger documentation and SSE events. See the
[release record](release-1.2.0.md). Target-Pi and complete-suite release gates
remain before tagging.

- [x] Versioned 1.2.0 source, package metadata and documentation homepage.
- [x] Remote API application-service boundary, local authentication, OpenAPI and
  offline Swagger UI.

## PHOS 1.1.0 — environmental and motion-awareness historical baseline

PHOS 1.1.0 combines the established 1.0 baseline with the implemented optional
environmental, air-quality, IMU, WS2812B and visual-arbitration capabilities.
See the [release record](release-1.1.0.md). It is **not ready to tag** until the
hardware and complete-suite release gates in that record are completed.

- [x] Raspberry Pi runtime, animated eyes, visual state/behavior and independent rendering
- [x] Camera face tracking and bounded gaze via BehaviorEngine
- [x] Facial-expression observations/reactions with local ONNX or optional AWS
- [x] Canonical JSON model, validation and atomic persistence
- [x] Authenticated web administration, mandatory bootstrap password change and external secrets
- [x] Eight configuration domains with isolated page saves and read-only status
- [x] Versioned 1.1.0 source, installation/user documentation and hardware-free regression tests
- [x] Validated logging-level, iris and camera-preview reload; confirmed supervisor-owned PHOS restart
- [x] Release hardening for failure propagation, preview lifecycle cleanup and bounded preview work
- [ ] Verify user systemd restart and display-session environment on the actual Pi
- [ ] Repeat the complete release acceptance sequence on the Pi after these changes
- [ ] Establish expression accuracy and calibrate neutral reactions on actual hardware
- [ ] Verify optional AWS behavior/latency and mobile/tablet LAN administration on the Pi

The hardware document records the display and Pi Camera/tracking as already
operational. Fresh release regression and expression-quality verification remain
separate from that prior evidence. See the release record for exact checks.

## Included in 1.1.0 — BME280 / BMP280

- [x] Optional BME280 and BMP280 I2C adapters and provider-neutral environmental service.
- [x] Canonical environmental type selector, restart-only policy, authenticated Sensors editor and live snapshot with explicit capabilities.
- [x] Hardware-free adapter/service/configuration/web regression coverage and setup documentation.
- [ ] Verify actual breakout wiring/address, real units/accuracy, recovery and concurrent eyes/Vision responsiveness on Raspberry Pi 3.

Environmental behavior and overlays are included in 1.1.0; physical acceptance
remains separate from implementation.

## Included in 1.1.0 — CCS811 air quality

- [x] Optional smbus2 CCS811 adapter with eCO2/TVOC, conditioning and error checks.
- [x] Shared sensor worker/lifecycle, canonical settings and Web Admin status/editor.
- [x] Service-level environmental compensation with safe missing-humidity handling.
- [x] Fake bus/provider tests and installation/configuration documentation.
- [ ] Verify actual module power/logic/wake wiring, both addresses as fitted, first-use stability, clock stretching and concurrent operation on Pi 3.
- [ ] Consider baseline persistence after physical validation; not implemented now.

## Included in 1.1.0 — GY-521 / MPU-6050 IMU and WS2812B ring

- [x] Optional smbus2 MPU-6050 adapter with typed six-axis readings and bounded worker lifecycle.
- [x] Canonical configuration, Web Admin editor/status and hardware-free adapter/runtime tests.
- [x] Provider-neutral, debounced motion interpretation with live-reloadable thresholds.
- [x] Motion-state behavior mapping to FaceState with live-reloadable visual intensity and timing.
- [x] Optional WS2812B semantic LED-ring provider/controller, canonical configuration and hardware-free tests.
- [ ] Verify board wiring, address, stable readings and coexistence on Raspberry Pi 3.

## PHOS 1.2 — remote application-service boundary

- [x] Provider-neutral application service with semantic status/read models and commands.
- [x] Versioned `/api/v1` JSON adapter for status, state, environment, motion,
  health, configuration, visual-source, expression and state commands.
- [x] Bounded semantic event contract with duplicate suppression; the WSGI
  adapter provides an SSE compatibility stream at `/api/v1/events`.
- [ ] Select and deploy a WebSocket-capable local server adapter if a true
  WebSocket transport is required; it must reuse the same service contract.

## Attention / Presence vertical slice

- [x] Provider-neutral selected-face observations, confirmed Presence edges,
  Attention acquisition/tracking/loss, semantic gaze, configured LED sweeps,
  application/SSE forwarding, and read-only Web Admin status.
- [ ] Perform the target-Pi camera/display/LED acceptance checklist; automated
  coverage does not establish physical behavior or calibration.

## Post-1.0 — explicitly deferred

| Area | Deferred work |
| --- | --- |
| Hardware acceptance | Physical wiring, power, level shifting, mounting and target-Pi acceptance for the optional sensors and WS2812B ring. CCS811 eCO2 must not be labeled direct CO2. |
| Remote-control API | Authenticated service-mediated control, authorization and documented contracts. |
| Advanced OS administration | Raspberry Pi reboot, if a future safe permission boundary is approved. |
| MCP server | Service-mediated tools; no direct hardware or subsystem-internal access. |
| STT | Completed Slice 3: explicit microphone capture, VAD and provider-neutral speech recognition; target-Pi validation remains pending. |
| TTS | Provider-neutral synthesis, playback and speaker verification. |
| Conversational LLM | End-to-end conversation through LLMProvider; existing skeletons are not a delivered conversation feature. |
| Home Assistant | Integration/tool layer and explicit permissions; not the reasoning core. |

Future Web/API/MCP/Voice adapters must reuse PHOS application services for
validation, authorization, configuration and behavior commands. They must not
access GPIO, hardware drivers or subsystem internals directly. Extend services
when a capability is approved; do not create parallel control implementations.

Existing ahead-of-scope AI/voice scaffolding is preserved. Implement one approved,
runnable vertical capability at a time; do not start deferred work automatically.
