# SPDX-License-Identifier: Apache-2.0
"""Manual development preview for PHOS eye expressions.

Run from the repository root with ``python3 src/robot/ui/demo.py``.
"""

from __future__ import annotations

import sys
import time
from dataclasses import replace
from pathlib import Path

SOURCE_DIRECTORY = Path(__file__).resolve().parents[2]
if str(SOURCE_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(SOURCE_DIRECTORY))

from robot.ui.display import TkEyeDisplay
from robot.ui.eyes import EyeRenderer
from robot.ui.state import FaceExpression, FaceState, VisualAccent


_VISUAL_STATES = {
    "1": (FaceExpression.NEUTRAL, VisualAccent.NEUTRAL),
    "2": (FaceExpression.HAPPY, VisualAccent.WARM),
    "3": (FaceExpression.CURIOUS, VisualAccent.CURIOUS),
    "4": (FaceExpression.SURPRISED, VisualAccent.ALERT),
    "5": (FaceExpression.SLEEPY, VisualAccent.SLEEPY),
}


def main() -> None:
    display = TkEyeDisplay()
    renderer = EyeRenderer()
    state = FaceState()
    display.open(800, 600, fullscreen=False)
    try:
        while True:
            for key in display.poll_keys():
                if key.lower() == "q":
                    return
                if key in _VISUAL_STATES:
                    expression, accent = _VISUAL_STATES[key]
                    state = replace(state, expression=expression, accent=accent, reaction_strength=1.0)
                elif key == "Left":
                    state = replace(state, pupil_x=state.pupil_x - 0.15)
                elif key == "Right":
                    state = replace(state, pupil_x=state.pupil_x + 0.15)
                elif key == "Up":
                    state = replace(state, pupil_y=state.pupil_y - 0.15)
                elif key == "Down":
                    state = replace(state, pupil_y=state.pupil_y + 0.15)
                state = state.normalized()
            display.draw(renderer.render(state, timestamp=time.monotonic()))
            time.sleep(1.0 / 30.0)
    finally:
        display.close()


if __name__ == "__main__":
    main()
