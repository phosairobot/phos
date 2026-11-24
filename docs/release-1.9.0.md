# PHOS 1.9.0 release candidate

The checked-out commit is tagged `1.9.0` and contains the Physical Context and
Object Awareness work described here. However, its authoritative package source
(`robot.__version__`), packaging metadata, Web Admin display, and release test
currently report `1.8.0`. This page is a release-candidate record, not evidence
that the package-version discrepancy has been resolved.

## Added

- **Temporal History v0.1:** bounded, RAM-only retention of accepted
  environmental and air-quality observations and confirmed semantic physical
  transitions. `phos_query_measurements` supplies retained samples plus
  min/max/average summaries; `phos_list_physical_transitions` supplies bounded
  semantic transition records.
- **Physical Context Intelligence v0.1:** an agent composition contract using
  `phos_get_physical_context`, `phos_query_measurements`, and
  `phos_list_physical_transitions`. PHOS reports observations and bounded
  summaries; external Agents interpret them and make decisions.
- **Object Awareness v0.1:** `phos_get_visible_objects` performs one
  authorized, on-demand NanoDet observation from the existing VisionPipeline
  frame. It returns only object labels, confidences, capture time, age,
  freshness, and truncation state.

## Fixed

- Duplicate semantic event subscription/recording during lifecycle startup.
- The local camera preview's RGB/BGR boundary. VisionPipeline frames remain BGR
  for OpenCV consumers, while the Tk PPM preview converts to RGB once.

## Configuration and compatibility

- Object awareness is disabled by default. Enabling it requires an
  operator-installed NanoDet-m-plus-1.5x_416 FP32 ONNX model, a valid configured
  path, and an already active VisionPipeline. It is on-demand only: no second
  camera owner, continuous detector, image retention, identity data, bounding
  boxes, or tracking is introduced.
- `phos_get_visible_objects` requires `read_perception`. Existing camera-snapshot
  access does not imply that permission.
- History tools require `read_physical_history`. Existing agent configurations
  need that permission explicitly when they should query retained history.
- History keeps at most six hours: one sample per metric per minute, 360 samples
  per metric, and 500 transitions. It is discarded on restart; missing,
  unavailable, and stale data are not fabricated or interpolated.
- These features are designed for the Raspberry Pi 3 profile, but object
  inference has material latency and memory cost. The model is not bundled, and
  operators should validate their own camera, model, and workload.

## Validation and release gate

The integrated source passed the complete automated suite (`1442 passed,
1 skipped`) and strict MkDocs build at the integrated development tip.
Socket-dependent MCP and Web listener tests passed when run in a
localhost-capable test environment. No ONNX model binary or credential is
tracked. Raspberry Pi validation evidence is feature-specific; this release
record does not claim universal hardware compatibility or a new hardware
acceptance result.

Before publication, merge the approved release commit into `main` and tag the
commit reachable from `origin/main`; the release workflow verifies that
provenance.
