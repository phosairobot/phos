import asyncio

from robot.runtime import build_runtime
from robot.ui import MemoryEyeDisplay


def test_runtime_starts_and_stops_registered_components():
    async def exercise():
        display = MemoryEyeDisplay()
        runtime = build_runtime(eye_display=display)
        stop_event = asyncio.Event()
        stop_event.set()
        await runtime.run(stop_event)
        return runtime.core, display

    core, display = asyncio.run(exercise())

    assert not core.is_running
    assert display.frames


def test_runtime_start_callback_runs_only_after_runtime_startup():
    async def exercise():
        runtime = build_runtime(eye_display=MemoryEyeDisplay())
        observed = []
        runtime.register_started_callback(lambda: observed.append((runtime._started, runtime._loop is not None)))
        stop_event = asyncio.Event()
        stop_event.set()
        await runtime.run(stop_event)
        return observed

    assert asyncio.run(exercise()) == [(True, True)]
