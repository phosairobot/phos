# Expression model replacement evaluation

## Selection and status

Selected candidate: **OpenCV Zoo Progressive Teacher / MobileFaceNet, FP32 ONNX**,
`facial_expression_recognition_mobilefacenet_2022july.onnx`.
This is a runnable replacement configuration, **not yet a validated production
upgrade**. Connected successfully to `pi@192.168.1.128` and measured both
installed models on the Raspberry Pi 3. Two paired camera runs were completed,
but detection coverage remained too low for a useful accuracy comparison and
neither model produced a non-neutral prediction. Do not infer reduced neutral bias from
a model's size, dataset accuracy or successful synthetic inference.

The architecture remains CameraProvider → FaceDetector → ExpressionProvider →
ExpressionSmoother → observations → BehaviorEngine → FaceState. Face tracking,
runtime scheduling, smoothing thresholds and behavior mappings are preserved.
No TensorFlow, PyTorch, ONNX Runtime or new PHOS subsystem is required.

## Candidate comparison

| Candidate | Evidence and decision |
| --- | --- |
| OpenCV Zoo MobileFaceNet | Published ONNX designed for OpenCV DNN; 112px input; verified download and local CPU forward. Best immediate fit for the existing provider. |
| EmotiEffLib / HSEmotion EfficientNet-B0 | Maintained ONNX alternative; 224px input and channel-specific ImageNet normalization. Worth a subsequent experiment if the selected candidate fails; not benchmarked or integrated here. |
| EmotiEffLib MobileNet | Published `mobilenet_7.h5` is a Keras artifact, not a verified drop-in ONNX file. Not selected over an available OpenCV-compatible ONNX model. |
| FERPlus | Existing PHOS baseline; reported real-world neutral bias motivates replacement. Retained for paired measurements and rollback. |

MobileFaceNet is a compact mobile CNN, not MobileNetV2/V3. No verified
MobileNetV2/V3 ONNX artifact with a better-supported PHOS deployment contract
was established in this evaluation. An ONNX candidate is available, so no export
step is necessary. No unverified conversion is represented as working.

Sources: [OpenCV model card](https://github.com/opencv/opencv_zoo/tree/main/models/facial_expression_recognition),
[EmotiEffLib inference implementation](https://github.com/sb-ai-lab/EmotiEffLib/blob/main/emotiefflib/facial_analysis.py),
[MobileNet weights](https://github.com/sb-ai-lab/EmotiEffLib/blob/main/models/affectnet_emotions/mobilenet_7.h5),
[FERPlus model card](https://github.com/onnx/models/tree/main/validated/vision/body_analysis/emotion_ferplus).

## Model contract

- Source: OpenCV Zoo; its model card declares Apache-2.0 for this directory.
  Retain the repository [license](https://github.com/opencv/opencv_zoo/blob/main/LICENSE).
- Model card reports **88.27% RAF-DB evaluation accuracy**. This does not
  establish PHOS camera accuracy or the exact checkpoint training mixture.
- File size: **4,791,892 bytes** (4.57 MiB); FERPlus published artifact:
  **35,040,571 bytes** (33.42 MiB), about 7.3 times larger.
- Input: float32 NCHW **1×3×112×112**, RGB, normalized as `(pixel / 255 - 0.5) / 0.5`.
  Existing provider configuration: size 112×112, scale `1/127.5`, mean
  `(127.5,127.5,127.5)`, grayscale off, channel swap on for current Pi capture.
- Output label order: **angry, disgust, fearful, happy, neutral, sad, surprised**.
  Provider converts scores to confidence using its existing probability handling.
- Existing BehaviorEngine maps happy to warm, surprised to alert, neutral to
  neutral variation, and remaining labels to gentle curious/attentive behavior.
  The benchmark groups these as happy, surprise, neutral and other. It retains
  raw labels and existing smoothing; it does not sum negative-class probabilities.

The [reference preprocessing](https://github.com/opencv/opencv_zoo/blob/main/models/facial_expression_recognition/facial_fer_model.py)
uses five-landmark alignment. PHOS supplies an unaligned Haar crop, resized by
OpenCV. This is an intentional evaluation limitation: off-axis faces and crop
placement may reduce accuracy. Landmark detection was not added. If this crop
fails practical tests, reject the candidate rather than claim the published
RAF-DB result applies to PHOS.

The current camera channel-order mismatch and preserved FER+ baseline are
documented in [Vision](vision.md#camera-channel-order-inconsistency).

## Verified evidence

The downloaded candidate matches SHA-256
`4f61307602fc089ce20488a31d4e4614e3c9753a7d6c41578c854858b183e1a9`.
OpenCV DNN loaded it and completed CPU inference locally, using the PHOS
provider. Synthetic zero crops were used only for compatibility and latency.

| Measurement | MobileFaceNet | FERPlus |
| --- | --- | --- |
| Local Mac forward p50 / p95 | 3.02 / 3.46 ms | Not measured |
| Raspberry Pi 3 synthetic forward p50 / p95 | 114.83 / 123.51 ms | 253.72 / 268.63 ms |
| First camera run predictions | Neutral on all 17 detected crops | Neutral on all 17 detected crops |
| First camera run stable outputs | None | None |
| Neutral / smile / surprise behavior | No stable reaction demonstrated | No stable reaction demonstrated |

Local timing: macOS 15.7.5 x86_64, OpenCV 5.0.0, eight OpenCV threads,
five warm-up forwards and 30 timed forwards. These are **not Pi estimates**.
Pi timing: Raspberry Pi 3 Model B Rev 1.2, aarch64, Linux
6.18.34+rpt-rpi-v8, OpenCV 4.10.0, four OpenCV threads, explicit OpenCV backend
and CPU target. Each installed artifact matched the documented SHA-256. Five
warm-up forwards and 30 measured forwards used zero-filled input tensors;
preprocessing, camera and detection were excluded. MobileFaceNet was about
2.2 times faster. Immediately afterward, temperature was 59.1°C and
`vcgencmd get_throttled` returned `0x50005` (current and historical undervoltage
and throttling). These are timings under that power condition, not an
unthrottled performance baseline. No Pi dependencies were changed.

### First paired camera run

The existing Pi benchmark ran with Picamera2 at 640×480 and the same Haar crop
for both models, following live pose prompts. Four phases lasted 30 seconds
each. Only **17 of 342 frames** yielded a detected crop (5.0% coverage).
Most detected crops were about 56–61 pixels wide. No saved image was inspected,
so these detections are not independently confirmed as correctly framed faces.

| Requested pose | Detected / sampled | FERPlus mean confidence | MobileFaceNet mean confidence | Predictions |
| --- | --- | --- | --- | --- |
| Neutral, first | 0 / 89 | N/A | N/A | No classification |
| Smile | 4 / 86 | 0.665 | 0.898 | Both neutral on every crop |
| Surprise | 10 / 80 | 0.672 | 0.889 | Both neutral on every crop |
| Neutral, final | 3 / 87 | 0.663 | 0.886 | Both neutral on every crop |

Neither model published a stable label. There were zero label switches, but
only two adjacent detected pairs across the entire run; this is not evidence
of useful temporal stability. False-neutral fraction on the requested smile
and surprise phases was 100% among detected crops for both models. The sample
is small and detection is intermittent, so this does not establish model-level
accuracy. Higher candidate confidence did not produce better recognition.

During the smile phase, forward p50/p95 were 307.40/327.16 ms for FERPlus and
127.26/131.58 ms for MobileFaceNet. During surprise they were 301.78/318.03 ms
and 129.01/132.33 ms respectively. Camera loading and thermal/power conditions
can differ from the synthetic run. The benchmark did not run the renderer;
physical eye reactions remain to be verified in the main runtime.

Raw numeric results are local at `/private/tmp/phos-expression-live.jsonl`;
no images or face crops were saved. Repeat with closer, upright, well-lit
framing and reliable face detection before deciding on production promotion.

### Closer repeat and orientation diagnostic

A second prompted run used the same models, camera configuration and 30-second
phases after asking the participant to move closer and face the upright camera.
It detected crops in **33 of 328 frames** (10.1% coverage). Most crops were
72–83 pixels wide; one was 156 pixels wide. Every prediction was neutral.

| Requested pose | Detected / sampled | FERPlus mean confidence | MobileFaceNet mean confidence |
| --- | --- | --- | --- |
| Neutral, first | 1 / 89 | 0.667 | 0.869 |
| Smile | 2 / 88 | 0.676 | 0.868 |
| Surprise | 28 / 63 | 0.666 | 0.871 |
| Neutral, final | 2 / 88 | 0.659 | 0.884 |

Both smoothers returned neutral on 13 samples during the surprise phase
(20.6% of that phase's samples); no stable happy/surprise result appeared.
There were no predicted-label switches. Both models had a 100% false-neutral
fraction among detected crops in the smile and surprise phases. MobileFaceNet
again provided higher confidence without better discrimination. Surprise-phase
forward p50/p95: FERPlus 303.13/327.87 ms; MobileFaceNet 131.80/137.58 ms.
Raw numeric results: `/private/tmp/phos-expression-live-repeat.jsonl` on the Mac.

After the run, an in-memory diagnostic applied the existing detector to eight
fresh camera frames at 0°, 90°, 180° and 270° rotations. Only one frame had a
0° detection (53×53 at x=464, y=302); no rotated variant detected a face.
Frame-average grayscale brightness ranged from 118.2 to 120.4 out of 255,
with standard deviation about 41.6–42.2. The frames are not uniformly black,
but these statistics cannot establish whether the participant is in view,
properly exposed or in focus. No images were saved or visually inspected.

**Conclusion:** no production promotion. Check the live preview and the actual
Haar rectangle/crop before further classifier evaluation. The rotation probe
provides no evidence that a simple quarter-turn rotation fixes detection.
Do not treat intermittent detections as verified faces or lower confidence
thresholds to conceal this failure. No runtime/detector settings were changed.

## Install and run

Use the exact verified download/checksum and candidate PHOS launch commands in
[installation](installation.md#start-phos). The existing Pi apt-installed
OpenCV/Picamera2 dependencies suffice for the intended path; no model export.
Model files stay external to Git. Deployment is not performed by this evaluation.

Keep the existing FERPlus file for comparison. If it is missing, obtain it with:

```bash
cd ~/phos
mkdir -p models/expression
wget -O models/expression/emotion-ferplus-8.onnx \
  https://media.githubusercontent.com/media/onnx/models/main/validated/vision/body_analysis/emotion_ferplus/model/emotion-ferplus-8.onnx
echo 'a2a2ba6a335a3b29c21acb6272f962bd3d47f84952aaffa03b60986e04efa61c  models/expression/emotion-ferplus-8.onnx' | sha256sum -c -
```

## Paired real-camera comparison

Stop the main PHOS process so the test can acquire Picamera2. From the Pi checkout
containing the updated `src/`, run:

```bash
cd ~/phos
PYTHONPATH=src python3 -m robot.vision.benchmark \
  --ferplus models/expression/emotion-ferplus-8.onnx \
  --candidate models/expression/facial_expression_recognition_mobilefacenet_2022july.onnx \
  --seconds 30 > /tmp/phos-expression-comparison.jsonl
```

Watch terminal prompts: neutral → smile (`happy`) → surprise → neutral, with
five seconds to prepare each pose. Each phase lasts 30 seconds. Keep one person
at the same distance and under unchanged light throughout the paired run. Both
models receive the **same crop from the same frame**; inference order alternates
to reduce systematic order effects. No frames or crops are stored. JSONL contains
numeric measurements and labels only; omit redirection to inspect it live.

Metadata records model hashes, sizes, platform, OpenCV version and thread count.
Each sample records predicted class, confidence, PHOS semantic class, stable
label, elapsed timestamp, crop shape, forward time and total classify time.
Summaries contain class counts, confidence mean, label/semantic switches and
adjacent-pair counts, stable-output fraction, neutral fraction, semantic
agreement with the requested pose, and p50/p95 forward latency. No-face samples
reset smoothers and reduce stable-output coverage; they are excluded from
classification agreement and neutral-rate denominators.

Neutral fraction during smile/surprise is the practical false-neutral metric;
during neutral it is agreement, not evidence of a fault. Confidence is uncalibrated
and is not directly comparable as accuracy between models. Pose prompts are
human test instructions, not independently verified ground-truth annotations.

The target paired sampling interval is 1/3 second, with three warm-up passes
excluded. Two models run sequentially, so actual cadence can be slower than
production. Gaps over the existing 1.5-second smoother limit can prevent stable
outputs. Interpret paired stability with the recorded timestamps and also test
the candidate alone in PHOS. Pair timings exclude camera/detection from
`classify_ms`; `inference_ms` measures forward plus output extraction. This tool
does not measure display FPS or end-to-end reaction latency.

## Physical acceptance

1. Repeat the paired run in normal room light and dimmer/side light, with the
   same subject, position and camera settings for each pair. Repeat at normal
   interaction distance and with mild head turns; record conditions separately.
2. Compare smile/surprise agreement and false-neutral rates, neutral agreement,
   stable coverage and switch rates. Prefer fewer false-neutral predictions
   without introducing frequent happy/surprise reactions to a resting face.
   Repeat runs before drawing a conclusion; do not tune on one favorable frame.
3. Launch candidate PHOS from the graphical desktop. Check face following,
   sustained warm response to a smile, alert response to surprise, and return
   to neutral. Negative-looking expressions should yield UNKNOWN and allow baseline decay.
4. Leave/re-enter the frame and vary distance. Check no-face reset, suppression
   of unstable/low-confidence predictions, eye animation and clean Ctrl+C.
5. Set `logging.expression_diagnostics=true` in `config/phos.yaml` for single-model latency. Record Pi model/OS,
   OpenCV version, p50/p95, CPU use and temperature/throttling under sustained
   operation (`top`, `vcgencmd measure_temp`, `vcgencmd get_throttled`). At the
   3 Hz inference target, classification plus other Vision work must fit the
   available budget without visibly degrading tracking or rendering.

Promote the candidate only after these measurements show a practical improvement.
If it fails on unaligned crops or Pi CPU load, keep the FER+ rollback command and
record the failure; do not conceal it by lowering confidence thresholds or
forcing a non-neutral class.

## Semantic policy update

The measurements above describe the previous top-class smoother. The current
runtime and paired benchmark now share the abstaining semantic policy documented
in [Vision](vision.md#semantic-evidence-and-temporal-confirmation). Raw neutral
predictions remain visible in diagnostics, but do not become accepted neutral
observations by default. `raw_neutral_fraction` preserves the old bias metric;
`neutral_fraction` now counts accepted semantic neutral, and `unknown_fraction`
measures abstention among classified crops. Semantic agreement includes temporal
confirmation, so it is not raw classifier accuracy. Compare old and new results
using their definitions, not just matching column names.

Neither existing model is validated for smile, surprise or neutral recognition.
The Haar pipeline exposes no geometric facial features to complement it. The
semantic redesign adds no model, dependency or inference pass. Conservative
thresholds may produce mostly UNKNOWN with the current camera/model setup;
that is the intended outcome when the evidence is insufficient.

Physical verification of the new policy:

1. Use `python3 src/robot/main.py` with `logging.expression_diagnostics=true`.
   Check correct framing and reliable face detection first. The previously
   documented channel-order mismatch and crop quality remain limitations.
2. Hold a resting face, then a smile, then surprise for several seconds each.
   Compare raw top probabilities with semantic result and rejection reason.
   Expect neutral predictions to be UNKNOWN (`neutral_not_calibrated`). Strong
   happy evidence needs 3 samples/800 ms; surprise needs 3 samples/600 ms.
3. Try brief smiles, speaking, blinking, squinting, mild head turns, occlusion,
   side/dim light and different distances. They must not force a reaction;
   record false positives as well as missed expressions and UNKNOWN coverage.
4. Hold surprise: the PHOS reaction must fade even if the model continues to
   report surprise. After confirmed happy evidence and the four-second cooldown,
   a new confirmed surprise can trigger again. UNKNOWN alone cannot rearm it.
5. Leave/re-enter view; confirm evidence resets while tracking, idle gaze,
   eye animation and clean shutdown still work. Run under normal Pi CPU/power
   conditions and compare inference latency, temperature and throttling.
6. Repeat across people/lighting with independently checked poses and crops.
   Enable neutral only after demonstrating separation from smile/surprise and
   other appearances; high neutral confidence by itself is insufficient.

No new physical camera validation was performed for this software change.
