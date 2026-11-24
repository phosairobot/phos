# SPDX-License-Identifier: Apache-2.0
"""Paired, in-memory Pi camera evaluation: python -m robot.vision.benchmark."""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import platform
import statistics
import sys
import time

from .camera import Picamera2CameraProvider
from .detector import OpenCVFaceDetector
from .expression import OpenCVExpressionProvider
from .pipeline import crop_face
from .selection import FaceSelector
from .smoother import ExpressionSmoother


def semantic(label):
    """Legacy raw-label grouping; not the runtime semantic decision policy."""
    if label in {"happy", "happiness"}:
        return "happy"
    if label in {"surprise", "surprised"}:
        return "surprise"
    return "neutral" if label == "neutral" else "other"


def summarize(rows):
    """Summarize one model/posed phase; missing faces remain in the denominator."""
    valid = [row for row in rows if row["label"] is not None]
    result = {"samples": len(rows), "face_samples": len(valid)}
    if not valid:
        return result
    labels = Counter(row["label"] for row in valid)
    latency = sorted(row["inference_ms"] for row in valid)
    adjacent = [(a, b) for a, b in zip(rows, rows[1:])
                if a["label"] is not None and b["label"] is not None]
    result.update(
        predicted_classes=dict(labels),
        mean_confidence=statistics.mean(row["confidence"] for row in valid),
        neutral_fraction=sum(row["semantic"] == "neutral" for row in valid) / len(valid),
        semantic_agreement=sum(row["semantic"] == row["expected"] for row in valid) / len(valid),
        label_switches=sum(a["label"] != b["label"] for a, b in adjacent),
        semantic_switches=sum(a["semantic"] != b["semantic"] for a, b in adjacent),
        adjacent_pairs=len(adjacent),
        unknown_fraction=sum(row["semantic"] == "unknown" for row in valid) / len(valid),
        raw_neutral_fraction=sum(row["label"] == "neutral" for row in valid) / len(valid),
        stable_fraction=sum(row["stable_label"] is not None for row in rows) / len(rows),
        stable_classes=dict(Counter(row["stable_label"] for row in rows if row["stable_label"])),
        inference_p50_ms=statistics.median(latency),
        inference_p95_ms=latency[math.ceil(0.95 * len(latency)) - 1],
        classify_mean_ms=statistics.mean(row["classify_ms"] for row in valid),
    )
    return result


async def compare_crop(crop, providers, smoothers, *, timestamp, reverse=False):
    """Both classifiers see the very same crop and acquisition timestamp."""
    rows = []
    names = list(providers)
    for name in reversed(names) if reverse else names:
        row = dict(model=name, timestamp=timestamp, label=None, confidence=None,
                   semantic=None, stable_label=None, inference_ms=None, classify_ms=None)
        if crop is None:
            smoothers[name].reset()
        else:
            started = time.perf_counter()
            observation = await providers[name].classify(crop)
            elapsed = (time.perf_counter() - started) * 1000
            if observation is None:
                smoothers[name].reset()
            else:
                stable = smoothers[name].observe(observation, timestamp=timestamp)
                row.update(label=observation.label, confidence=observation.confidence,
                           semantic=smoothers[name].decision.label,
                           probabilities=dict(observation.probabilities),
                           reason=smoothers[name].decision.reason,
                           temporal=smoothers[name].temporal_state,
                           stable_label=stable.label if stable else None,
                           inference_ms=providers[name].last_inference_ms, classify_ms=elapsed)
        rows.append(row)
    return rows


def emit(value):
    print(json.dumps(value), flush=True)


async def run(arguments):
    import cv2
    import numpy as np

    providers = {
        "ferplus": OpenCVExpressionProvider.from_preset(arguments.ferplus, "ferplus"),
        "mobilefacenet": OpenCVExpressionProvider.from_preset(arguments.candidate, "mobilefacenet"),
    }
    metadata = dict(type="metadata", platform=platform.platform(), machine=platform.machine(),
                    python=platform.python_version(), opencv=cv2.__version__,
                    opencv_threads=cv2.getNumThreads(), interval=arguments.interval,
                    seconds_per_phase=arguments.seconds, models={})
    for name, path in (("ferplus", arguments.ferplus), ("mobilefacenet", arguments.candidate)):
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        metadata["models"][name] = dict(bytes=path.stat().st_size, sha256=digest.hexdigest())
        # Exclude import, loading and three warm-up forwards from the measurements.
        for _ in range(3):
            await providers[name].classify(np.zeros((112, 112, 3), dtype=np.uint8))
    emit(metadata)
    camera = Picamera2CameraProvider()
    detector = OpenCVFaceDetector()
    try:
        await camera.start()
        for phase, expected in enumerate(("neutral", "happy", "surprised", "neutral")):
            print(f"Prepare {expected}: hold the visible expression; sampling starts in 5 seconds.",
                  file=sys.stderr, flush=True)
            await asyncio.sleep(5)
            smoothers = {name: ExpressionSmoother() for name in providers}
            selector = FaceSelector()
            samples = {name: [] for name in providers}
            started = time.monotonic()
            index = 0
            while time.monotonic() - started < arguments.seconds:
                tick = time.monotonic()
                frame = await camera.capture_frame()
                acquired = time.monotonic()
                faces = await detector.detect(frame)
                height, width = frame.shape[:2]
                selection = selector.select(faces, width=width, height=height, timestamp=acquired)
                face = selection.face
                if selection.new_track:
                    for smoother in smoothers.values():
                        smoother.reset()
                crop = crop_face(frame, face, reference_size=selection.crop_size) if selection.expression_ready else None
                rows = await compare_crop(crop, providers, smoothers, timestamp=acquired,
                                          reverse=bool(index % 2))
                for row in rows:
                    row.update(type="sample", phase=phase, expected=expected, sample=index,
                               timestamp=acquired - started,
                               crop_shape=list(crop.shape) if crop is not None else None,
                               detected_boxes=[vars(box) for box in faces],
                               selected_box=vars(face) if face is not None else None,
                               selection_reason=selection.reason,
                               rejected_boxes=[dict(box=vars(box), reason=reason) for box, reason in selection.rejected])
                    samples[row["model"]].append(row)
                    emit(row)
                index += 1
                await asyncio.sleep(max(0, arguments.interval - (time.monotonic() - tick)))
            for name, rows in samples.items():
                emit(dict(type="summary", phase=phase, expected=expected, model=name,
                          elapsed_seconds=time.monotonic() - started, **summarize(rows)))
    finally:
        await camera.stop()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ferplus", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--seconds", type=float, default=30)
    parser.add_argument("--interval", type=float, default=1 / 3)
    arguments = parser.parse_args()
    for name in ("seconds", "interval"):
        value = getattr(arguments, name)
        if not math.isfinite(value) or value <= 0:
            parser.error(f"--{name} must be finite and positive")
    try:
        asyncio.run(run(arguments))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
