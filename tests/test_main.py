import asyncio

from robot.config import RuntimeConfig
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


def test_normal_startup_creates_one_mcp_server_from_the_existing_application(monkeypatch):
    from robot import main
    from robot.core import RobotCore
    from robot.core.behavior_engine import BehaviorEngine

    created = []

    class Runtime:
        def __init__(self):
            self.core = RobotCore()
            self._behavior_engine = BehaviorEngine(self.core.events)
            self.callbacks = []
        def register_started_callback(self, callback): self.callbacks.append(callback)
        async def run(self, stop_event):
            for callback in self.callbacks:
                callback()
            await asyncio.sleep(0)
            stop_event.set()

    class FakeMcpHttpServer:
        def __init__(self, agent_service, *, host, port, path):
            self.agent_service, self.host, self.port, self.path = agent_service, host, port, path
            self.started, self.stopped = False, False
            created.append(self)
        async def serve(self):
            self.started = True
            while not self.stopped:
                await asyncio.sleep(0)
        async def stop(self): self.stopped = True

    runtime = Runtime()
    monkeypatch.setattr(main, "build_application", lambda **kwargs: runtime)
    monkeypatch.setattr("robot.mcp.McpHttpServer", FakeMcpHttpServer)
    monkeypatch.setattr(main, "_install_shutdown_handlers", lambda *args: None)

    asyncio.run(main.async_main(config=RuntimeConfig(mcp_port=8123, mcp_path="/robot-mcp")))

    assert len(created) == 1
    assert created[0].host == "127.0.0.1" and created[0].port == 8123 and created[0].path == "/robot-mcp"
    assert created[0].agent_service._application._runtime is runtime
    assert created[0].stopped
