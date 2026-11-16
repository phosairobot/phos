#!/usr/bin/env python3
"""Emit deterministic, headless Physical Personality scheduler traces.

This is a diagnostic aid only.  It creates no runtime services, changes no
configuration, and has no MCP or hardware surface.  Each trace uses neutral
FaceState, IDLE, the same EyeRenderer geometry, and a seeded RNG.
"""

from __future__ import annotations

import argparse
import csv
import random
import sys

from robot.core import BehaviorEngine, EventBus
from robot.ui import EyeRenderer


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preset", choices=("analytical", "playful", "calm"), required=True)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--seconds", type=float, default=120.0)
    parser.add_argument("--fps", type=int, default=30)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.seconds <= 0 or args.fps <= 0:
        raise SystemExit("--seconds and --fps must be positive")
    now = [0.0]
    engine = BehaviorEngine(EventBus(), clock=lambda: now[0], rng=random.Random(args.seed))
    engine.configure_physical_personality(args.preset)
    renderer = EyeRenderer(width=800, height=600, transition_seconds=.18)
    writer = csv.writer(sys.stdout)
    writer.writerow(("seconds", "preset", "blink_phase", "gaze_phase", "pupil_x", "pupil_y", "left_pupil_x", "left_pupil_y"))
    frames = round(args.seconds * args.fps)
    for frame in range(frames + 1):
        now[0] = frame / args.fps
        engine._advance_blink(now[0])  # Deliberately calls the scheduler, not a runtime loop.
        engine._advance_idle_gaze(now[0])
        state = engine.face_state
        eye = renderer.render(state, timestamp=now[0]).eyes[0]
        writer.writerow((f"{now[0]:.3f}", args.preset, state.blink_phase.value, engine._gaze_phase,
                         f"{state.pupil_x:.3f}", f"{state.pupil_y:.3f}",
                         f"{eye.pupil_x:.2f}", f"{eye.pupil_y:.2f}"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
