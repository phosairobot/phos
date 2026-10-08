# Vision

## Goal

Add local visual perception using Raspberry Pi Camera while keeping the
application independent from Picamera2, OpenCV and any specific ML model.

The system classifies **visible facial expressions**. It must not claim to
know a person's true internal emotional state.

## Architecture

```text
Raspberry Pi Camera
        |
        v
  CameraProvider
        |
        v
   FaceDetector
        +--> normalized face position --> Robot Core --> BehaviorEngine --> FaceState
        |
        +--> ExpressionProvider --> temporal smoothing --> Robot Core
```

## CameraProvider

Initial implementation: `Picamera2CameraProvider`.

Default target:
- 640x480 RGB
- Picamera2 hidden behind the provider abstraction
- capture frequency may be higher than inference frequency

## Local camera picture-in-picture preview

The optional diagnostic preview uses the same `VisionPipeline` camera owner and
the latest in-memory frame; display code never opens Picamera2 or another
capture path. It is disabled by default. When enabled, a small (default 25%
display-width) bordered image appears at the bottom-right of the PHOS display,
with optional selected face box and already-produced raw/semantic expression
labels. No image or video is persisted or sent to Web Admin or another network
service. A single latest-frame reference and one bounded resize/encoding job
prevent backlog; preview refresh is capped at 5 FPS by default. The resize runs
off the Tk eye-render thread.

The display passes raw binary PPM data to Tk. Preview encoding or image-loading
failures are logged as `Could not render camera preview` once per failure streak,
so a missing overlay can be diagnosed without flooding the log or stopping eyes.

Manage the preview in Web Admin → Vision. Save the canonical configuration and
use System → Reload configuration to apply enabled state, corner, scale, maximum
FPS and diagnostic labels without restarting PHOS. If the preview is the only
Vision feature enabled, reload starts the existing Vision pipeline/camera once;
disabling it releases that pipeline when face tracking and expression
recognition are also disabled. Changing preview options while Vision is already
running does not restart Vision or the camera. Camera/display geometry and
other Vision settings remain restart-required.

## FaceDetector

Initial implementation: `OpenCVFaceDetector`.

Start with OpenCV Haar Cascade because it is lightweight enough for a
Raspberry Pi 3. A DNN detector may replace it later without changing callers.

## Face continuity and expression crops

The detector previously sorted boxes by area and the pipeline selected the
largest independently on every frame. That allowed a larger false positive or
second face to replace the previous target immediately; rectangular crops also
changed aspect ratio before model resize. These code paths explain a mechanism
for instability, but bounding-box logs alone cannot establish that a detection
is actually a face. The historical camera tests did not visually verify crops.

`FaceSelector`, an internal geometric helper in Vision, now associates detections
with the last selected box. It does not recognize identity or store images.

- Initial acquisition chooses the largest valid detection, with deterministic
  position tie-breaking. Invalid/out-of-frame boxes and aspect ratios outside
  0.5–2.0 are rejected. Haar settings and model preprocessing remain unchanged.
- Continuations require width and height ratios between 2/3 and 1.5, center
  displacement no greater than 0.60 times the previous longest side, and either
  IoU of at least 0.15 or displacement no greater than 0.25 sides. Compatible
  candidates rank by `2 * IoU - normalized_distance - absolute_log_size_changes`,
  rather than area. Other boxes are logged as competing detections or rejected
  position/size jumps. These are conservative geometric heuristics, not proof
  of a real face or person identity.
- The last match is remembered for at most one second. Missing/rejected boxes
  do not advance that timeout. During a miss, no stale box reaches inference
  or gaze; existing face-lost behavior runs and expression evidence resets.
  After expiry a new acquisition is allowed, without mixing expression evidence
  across tracks. Frame-size changes also reset continuity.
- Gaze immediately receives the selected **current detection's** normalized
  center through the existing event. Expression inference waits for two
  consecutive matching detection cycles, including after a miss. This rejects
  one-frame expression crops while retaining responsive face tracking.
- Crop side length uses the longest detected side with an EMA coefficient of
  0.5 to reduce scale jitter. The center follows the current detection, avoiding
  added gaze/crop motion lag. The square always contains the current detected
  face, even when smoothing would otherwise make it too small.
- Default margin is 10% per side: a stationary 100-pixel face yields a 120-pixel
  square. Set `expression.crop_margin` in `config/phos.yaml` (valid range 0–0.5). Near image edges the
  square shifts inside the frame; margin is reduced if necessary. If no square
  can contain the detected face within the image, expression inference is
  skipped. There is no padding, non-square stretching, or saved image.

Set `logging.expression_diagnostics` true in the central JSON to log every detection
cycle's full box list, selected box, selection reason, rejected boxes/reasons,
and expression eligibility. Expression passes also log the final crop rectangle,
margin and smoothed size. With tracking alone, use
`vision.face_tracking_enabled=true` and `expression.enabled=false` in JSON to isolate detection
without running FER. Disable diagnostics after testing to limit log volume.
The paired benchmark uses the same selector and default square crop policy,
and records detected/selected/rejected boxes and selection reasons numerically.
Historical benchmark results used independent largest-box selection and tight
rectangular crops, so they are not directly comparable preprocessing baselines.

### Physical crop verification on Raspberry Pi

1. Start with the empty scene, then enter and hold still for 20–30 seconds.
   Persistent selected boxes while nobody is present indicate false positives;
   the new continuity rules cannot reject a consistently detected background
   pattern merely because it is stable. Confirm the real face is in view using
   the camera preview separately if needed; stop PHOS before another process
   acquires the camera. No preview/image storage is added to the runtime.
2. Move slowly left/right and nearer/farther. Look for `continuity_match`, modest
   crop-size changes, correct gaze, and consistently square crop coordinates.
   Inspect whether boxes cover the actual face; numeric stability is insufficient
   evidence of correct framing. Repeated `size_jump`/`position_jump` during normal
   movement may mean these initial gates need Pi-side tuning.
3. Introduce a second person, including a larger face away from the current
   target. PHOS should retain the first plausible track. Crossing/overlapping
   people remain ambiguous without identity recognition.
4. Leave/re-enter, move abruptly, or briefly occlude the face. Rejected detections
   must not reach FER; gaze uses existing face-loss behavior. Reacquisition after
   the one-second timeout requires a second matching detection before inference.
5. Approach each image edge. Verify crop coordinates stay within 640x480 and the
   detected face remains contained. Then repeat neutral/smile/surprise at a fixed
   distance and lighting while comparing FER outputs. Do not infer model accuracy
   until framing is verified. Check display smoothness, CPU load and shutdown.

This change does not tune FERPlus semantic thresholds or BehaviorEngine reactions.
No fresh physical camera evidence has been collected for these selection defaults.

## ExpressionProvider

Initial implementation: `OpenCVExpressionProvider`.

Use an ONNX model loaded through `cv2.dnn.readNetFromONNX`.

Typical labels may include:
- angry
- disgusted
- fearful
- happy
- sad
- surprised
- neutral

The replacement candidate is OpenCV Zoo MobileFaceNet FP32 ONNX; see
[model evaluation](vision-model-evaluation.md) for its contract, limitations,
measurements and paired camera test. It runs through the existing provider and
runtime configuration. Production promotion awaits Pi camera verification.

The previous baseline is ONNX Model Zoo `emotion-ferplus-8.onnx`. Its output labels must stay
in this exact order: `neutral`, `happiness`, `surprise`, `sadness`, `anger`,
`disgust`, `fear`, `contempt`.

## Raspberry Pi 3 performance policy

Do not run expression inference for every camera frame.

Suggested initial targets:
- camera capture/preview: up to 15-30 FPS when useful
- face detection: about 3-5 FPS
- expression inference: about 2-5 FPS

Prefer cropping the face and resizing it to a small model input such as
64x64 or 96x96.

Avoid full PyTorch/TensorFlow runtimes on Raspberry Pi 3 unless a concrete
need justifies them. Prefer OpenCV DNN + ONNX.

## Semantic evidence and temporal confirmation

`ExpressionProvider` retains its model-specific top label/confidence and now
also supplies the complete labelled probability distribution. `ExpressionSmoother`
interprets that distribution before accumulating temporal evidence. Only four
semantic labels leave this path: `neutral`, `happy` (smile/happy-like),
`surprised` (surprise-like), and `unknown`. These are visible appearance cues,
not measurements of internal emotion or direct measurements of eyelid opening.

Conservative initial defaults (not calibrated accuracy guarantees):

| Semantic candidate | Minimum probability | Margin over next raw class | Consecutive samples | Minimum duration |
| --- | --- | --- | --- | --- |
| happy / happiness | 0.80 | 0.30 | 3 | 800 ms |
| surprise / surprised | 0.85 | 0.35 | 3 | 600 ms |
| neutral, only when explicitly enabled after calibration | 0.95 | 0.50 | 3 | 1200 ms |

The existing `minimum_confidence` constructor setting is an additional floor;
it cannot lower these class thresholds. The class floors already imply strong
margins for normalized distributions; the margin check makes the ambiguity rule
explicit. Evidence must remain consecutive and gaps must not exceed 1.5 seconds.
A changed candidate, rejected/missing prediction, no face, duplicate/backward
timestamp or excessive gap resets confirmation. Count alone cannot confirm a
burst of fast samples. Storage is bounded to the confirmation count, with recent
confidence averaged; duration records the current uninterrupted run.

**UNKNOWN is abstention, never a neutral vote.** Missing distributions (including
legacy top-label-only providers), malformed probabilities, negative/unsupported
classes, weak evidence and pending confirmation produce UNKNOWN. No negative
FER class maps to a PHOS expression. A missing face still uses the existing
face-lost event and clears evidence. Face tracking remains independent.

Neutral is **disabled by default**, including in the runtime and benchmark.
The recorded Pi comparisons show false neutral even at high confidence, so
raising a threshold alone is insufficient justification for accepting it.
`ExpressionSmoother(neutral_enabled=True)` is available for a subsequently
validated model/camera configuration; it still requires positive neutral
probability, margin and temporal evidence. There is deliberately no automatic
fallback from rejected smile/surprise to neutral.

The existing `vision.visual_expression_stable` event carries confirmed useful
observations **or explicit UNKNOWN**, preserving its name and payload fields.
For UNKNOWN, `confidence=0` means no accepted semantic evidence, not a model
probability, and `observed_for_ms=0`. `VisionResult` carries the same expression;
its status is UNSTABLE during confirmation, UNKNOWN on rejection, and STABLE
on acceptance. NOT_DUE/no-face remain separate outcomes.

UNKNOWN does not refresh or replace a BehaviorEngine reaction. PHOS preserves
its current face momentarily while the existing decay returns it to baseline
(at 0.30 strength/second, at most three seconds from strength 0.90). That baseline
NEUTRAL face is **not** a claim that the observed person looks neutral.
Confirmed happy observations refresh the warm reaction. Surprise is a temporary
reaction: repeated surprise observations cannot refresh it. A confirmed happy
or neutral observation rearms surprise, with at least four seconds between
surprise triggers; UNKNOWN and face loss do not rearm it. RobotState retains
priority, and renderer interpolation and gaze remain unchanged.

With `logging.expression_diagnostics=true`, each inference logs raw scores/probabilities, ranked
top classes, final semantic result, acceptance/rejection reason and temporal
candidate/count/duration/confirmation. Reasons include `neutral_not_calibrated`,
`below_class_threshold`, `unsupported_class`, `missing_distribution`,
`invalid_distribution`, `temporal_pending`, and `confirmed`. Images are not saved.
The paired benchmark uses this same policy and includes distributions, reasons,
temporal state, UNKNOWN fraction and raw-neutral fraction alongside accepted
semantic statistics. Old benchmark measurements predate this policy.

## Model usefulness and geometric cues

The [recorded paired camera evaluation](vision-model-evaluation.md) demonstrated
neither reliable smile/surprise discrimination nor reliable neutral detection.
Both models repeatedly selected neutral for prompted non-neutral poses; poor
face coverage and unverified crops prevent assigning model-level accuracy.
FER remains useful as an experimental source of probability evidence and timing
measurements. This policy contains its failures; it cannot recover a smile that
the model scores as neutral or prove that a confident smile score is correct.

Inspection of `OpenCVFaceDetector` and `FaceRegion` shows only a Haar bounding
rectangle and normalized center. No mouth corners, eyelid points, eye opening,
landmarks or calibrated neutral facial geometry are available. Rectangle size
and movement cannot establish a smile or surprise. Consequently no geometric
expression claim or new detector/framework is introduced. A future lightweight
cue adapter would need independently validated mouth/eyelid measurements and
Pi 3 end-to-end timing before fusion; a heavy landmark runtime is not justified
by the current evidence. Existing camera channel-order inconsistency remains a
separate documented limitation below; this change preserves that runtime.

## Semantics

Facial-expression classifiers are uncertain visual observations.

Prefer:
- "the visible expression appears positive"
- "happy-like expression detected"

Avoid:
- "the person is happy"
- psychological conclusions based solely on facial appearance

## AI integration

Vision must not invoke the LLM directly.

Preferred flow:

```text
Vision -> Robot Event/State -> Agent context
```

Simple UI/behavior reactions should be possible without involving the LLM.

## Initial implementation

`VisionPipeline` is the lifecycle-managed service implementing this flow. It
captures RGB frames in memory and rate-limits Haar face detection. For every
detection interval with a face, it publishes `vision.face_position`, whose
`face_position.x` and `face_position.y` are the detected face center normalized
to `[-1, 1]` in camera coordinates. Vision does not choose pupil geometry;
`BehaviorEngine` clamps and smooths that provider-neutral attention input into
`FaceState`. When a face is lost, Vision publishes `vision.face_lost`.

Expression inference remains optional and independently rate-limited. When it
is configured, the pipeline publishes stable observations as
`vision.visual_expression_stable`; its payload contains
`visual_expression.label`, `confidence`, and `observed_for_ms` and does not
make a claim about internal emotional state.

`BehaviorEngine` is the only consumer that turns accepted semantic observations
into eye intent, following the evidence/UNKNOWN rules above. The provider-neutral
behavior and visual-accent contract lives in `docs/architecture.md`.

The default Pi adapter uses `Picamera2CameraProvider` at 640x480. Install the
Pi Camera and OpenCV dependencies using the Raspberry Pi OS instructions in
`docs/installation.md`, which includes the MobileFaceNet candidate launch command.
For rollback/comparison, the ONNX Model Zoo FER+ baseline expects a
`1x1x64x64` grayscale input and emits eight scores. The provider converts the
RGB face crop to grayscale before creating its OpenCV DNN blob. Configure `expression.enabled=true`, `expression.provider="local"`, and replace
the `expression.local` object in `config/phos.yaml` with:

```json
{
  "model_path": "../models/expression/emotion-ferplus-8.onnx",
  "labels": ["neutral", "happiness", "surprise", "sadness", "anger", "disgust", "fear", "contempt"],
  "input_size": [64, 64],
  "grayscale": true,
  "scale": 1,
  "mean": [0, 0, 0],
  "swap_rb": false
}
```

Then run `python3 src/robot/main.py`.

FER+ preprocessing uses unscaled grayscale pixel values (`scale=1`), zero mean
and no channel swap. `happiness` and `surprise` are candidates for happy and surprised semantics;
`sadness`, `anger`, `disgust`, `fear`, and `contempt` produce UNKNOWN. These model-specific names never reach
the renderer.

For temporary Pi-side inspection without saving camera data, set
`logging.expression_diagnostics=true` in JSON. It logs the selected face rectangle and
crop shape, the grayscale/blob shapes, raw FER+ scores, softmax probabilities,
forward inference time, and whether the smoother rejected or published the observation. Disable the
setting after diagnosis because it logs every expression inference.
Face tracking alone needs no ONNX model: set `vision.face_tracking_enabled=true`
and `expression.enabled=false`, then use the same config-file startup command.

### Camera channel-order inconsistency

The camera adapter currently requests Picamera2 `RGB888`, which yields BGR
array bytes despite the adapter's RGB documentation. The detector and legacy
FER+ grayscale conversion use `COLOR_RGB2GRAY`. The MobileFaceNet configuration
swaps channels to obtain the required RGB tensor. This evaluation preserves
the existing detector and FER+ baseline so results compare against current PHOS;
it does not silently change the camera contract. A future correction must update
camera, detector and expression configurations together and repeat the baseline.
See the [Picamera2 format mapping](https://github.com/raspberrypi/picamera2/blob/main/picamera2/request.py).

## Presence and attention

The selected face is converted to a provider-neutral observation before it
reaches Presence or Attention. It carries a runtime-local target ID, normalized
`x`/`y` position, normalized width/height, an optional detector confidence, and
a timestamp. A missing detector confidence remains unavailable; PHOS does not
invent `1.0` confidence.

`PresenceInterpreter` uses enter/leave hysteresis to move between `no_one` and
`person_present`. It emits `person_entered` and `person_left` only on confirmed
edges, never for every video frame. `person_engaged` is reserved in the state
vocabulary but no eye-contact, identity, voice, or other engagement inference
is implemented.

`AttentionManager` keeps target continuity separately from Presence. Its states
are `idle`, `acquiring`, `tracking`, and `lost`: a candidate first enters
`acquiring`, then a subsequent eligible observation enters `tracking`; failed
acquisition returns to `idle`. A changed target ID emits `attention_target_changed`.
Confirmed departure produces `attention_target_lost`, holds `lost` for canonical
`attention.lost_hold_ms`, then returns to `idle`. Acquisition and loss events
are edge-triggered. Behavior consumes this semantic state to update `FaceState`;
neither component drives the renderer directly.

### Observed facial expression

The same selected-face path also exposes an **Observed Expression** read model:
an uncertain expression-classifier result for the visible selected face. It is
not a confirmed emotion, mood, identity, PHOS's expression, or an identity
recognition result. The model retains the classifier's real confidence, provider
and configured local-model filename when available; no detector or Presence
confidence is substituted.

Known provider aliases are normalized once in this read model (for example,
`happiness` to `happy`, `surprise` to `surprised`, and `sadness` to `sad`),
without treating an unknown value as neutral. The preview overlay and Remote
API consume this same state. A valid result remains current for 1.5 seconds to
bridge ordinary inference-frame gaps, then becomes explicitly unavailable.

Face selection has independent sibling consumers: Presence answers whether
someone is present, while the expression classifier describes the selected
face's current visual resemblance. A disabled, slow, missing, or failing
classifier produces an unavailable observation only. It never changes Presence
hysteresis, Presence events, Attention, or face-loss semantics.

### Expression reaction policy

`ObservedExpression` is classifier telemetry; `ExpressionReactionPolicy` is a
separate conservative behavioral decision; and `ReactionIntent` is a transient
semantic request consumed by `BehaviorEngine`. PHOS's resulting expression is
its own visual output, not a mirror of a person. The default policy requires
70% confidence, 500ms label confirmation, and a 2500ms per-reaction cooldown;
it displays reactions for 1200ms. Happy maps to HAPPY, surprised to SURPRISED,
and sad/fearful/angry to CURIOUS; neutral, disgust and unknown labels do not
react. Reactions are suppressed outside IDLE or during a motion transient, and
preserve attention gaze and environmental overlays.

## Selectable local / AWS expressions

`expression.provider` in `config/phos.yaml` selects `local` (existing ONNX
settings) or `aws` (`AWSExpressionProvider`). With `expression.enabled=true`,
either selection enables the camera; AWS needs no ONNX file. The canonical file
disables expressions and standalone face tracking until explicitly enabled. Capture, Haar detection, geometric selection and square
cropping remain local. Only the selected crop is JPEG encoded in memory, capped
at 512 pixels on its longest side, and sent to Rekognition `DetectFaces` with
`Attributes=["EMOTIONS"]`. AWS always includes some default face attributes;
PHOS ignores these except face confidence. Multiple returned faces cause
abstention, not remote target selection. No S3, video streaming, identity
recognition, image files or biometric database is used.

**Privacy:** selecting AWS sends cropped facial images off the Raspberry Pi to
AWS for processing. Choose local mode to keep these images on the Pi. Neither
mode can establish a person's actual emotional state.

The adapter translates HAPPY/SURPRISED/CALM to happy/surprised/neutral; all other
categories contribute to unknown. AWS confidences are not assumed to be a
softmax distribution: missing mass becomes unknown and totals above one are
scaled down. Weak confidence is never amplified. These are conservative adapter
scores, not calibrated probabilities. The existing semantic thresholds still
apply, including neutral abstention. AWS types and labels never leave the adapter.
See the official [DetectFaces contract](https://docs.aws.amazon.com/rekognition/latest/APIReference/API_DetectFaces.html).

### Cloud request and evidence policy

- Two matching local detections plus one further second of uninterrupted eligible
  crops are required. Loss, reacquisition, invalid crops or a new geometric track
  invalidate cache and pending evidence. This is geometric stability, not identity.
- At most one background request exists. `asyncio.to_thread` runs SDK work;
  `classify` polls without awaiting the network, keeping local tracking/rendering
  independent. An invalidated in-flight response is discarded. Shutdown drains
  the worker; SDK connect/read timeouts apply, though credential resolution can
  add delay and an already transmitted request cannot be recalled.
- Requests start at least 30 seconds apart (also capped at two per minute through
  minimum spacing). No automatic SDK retries are enabled, preventing hidden
  billable retries. Counters include attempted requests, even preparation failures.
- A 16×16 grayscale crop comparison skips substantially identical crops before
  the 60-second refresh deadline. Mean absolute pixel change must exceed 0.08
  on a 0–1 scale to request earlier, still respecting cooldown. This is a cheap
  image-change heuristic, not expression or person recognition.
- Cached evidence expires 90 seconds after sampling, not after network completion.
  Similar input refreshes at 60 seconds, allowing overlap while waiting. Changed
  input may refresh after 30 seconds. An optional session cap defaults to zero
  (unlimited); expiry still produces UNKNOWN once the cap is exhausted.
- Three **distinct successful samples** with consistent accepted semantics are
  needed for confirmation. Cached reads do not increment counts or duration.
  Cloud evidence permits a maximum sample gap equal to its TTL; the local
  1.5-second rule is unchanged. With similar crops, first confirmation takes
  approximately two minutes, and is evidence from sparse samples, not proof
  of a continuously held expression. Confirmed cached evidence remains usable
  only within TTL. Gaps/expiry/loss/rejection reset confirmation.
- Failures clear current evidence and return UNKNOWN, logging only exception
  type (never SDK error text, credentials or images). Retry delay doubles from
  60 seconds to a 600-second cap; normal cooldown also applies. There is no
  automatic provider fallback. Missing SDK, credentials, region or permission
  is handled through the same bounded failure path.

For one hour of uninterrupted eligible face presence, similar crops produce
about **60 requests**; sufficiently changing crops can produce at most **120**
with defaults. Loss, failures and session caps reduce calls. These are attempt
bounds for a running session, not AWS price estimates. Restarting resets the
counter/backoff. Rekognition accuracy, network behavior and Pi performance have
not been verified on physical hardware by the unit tests.

At startup look for `Expression provider: aws` or `local`. With
`logging.expression_diagnostics=true`, rate-limited cloud policy logs show requested, cache,
unchanged, cooldown/backoff, stable-face-pending or session-limit reasons,
latency, neutral labels/confidence, discarded results and session attempt count.
Existing local selection debug logs remain per detection; disable debug for
normal operation. No request occurs without an eligible crop, so missing-face
reasons are visible in local selection diagnostics.

Operator commands and credentials: [installation](installation.md#optional-aws-expression-mode).
All settings and authoritative defaults: [development configuration](development.md#configuration).
