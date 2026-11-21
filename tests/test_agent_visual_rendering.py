from pathlib import Path
from types import SimpleNamespace
import asyncio

from robot.core.agent_visual_state import AgentVisualStateLeaseManager
from robot.core.behavior_engine import BehaviorEngine
from robot.core.runtime import RobotCore
from robot.core.state import RobotState
from robot.ui.backgrounds import BackgroundResolver
from robot.ui.eyes import EyeRenderer
from robot.ui.runtime import EyeRenderLoop
from robot.ui.state import FaceExpression
from robot.ui.state_effects import StateEffectResolver


class FakeClock:
    def __init__(self): self.now = 0.0
    def __call__(self): return self.now
    def advance(self, seconds): self.now += seconds


class FakeCall:
    def __init__(self, due, callback): self.due, self.callback, self.cancelled = due, callback, False
    def cancel(self): self.cancelled = True


class FakeScheduler:
    def __init__(self, clock): self.clock, self.calls = clock, []
    def __call__(self, delay, callback):
        call = FakeCall(self.clock() + delay, callback)
        self.calls.append(call)
        return call
    def run_due(self):
        for call in tuple(self.calls):
            if not call.cancelled and call.due <= self.clock():
                call.cancelled = True
                call.callback()


class Display:
    def __init__(self): self.calls = []
    def draw(self, *args, **kwargs): self.calls.append((args, kwargs))


def _background_config(tmp_path, **paths):
    values = {f"background_{state.value}": None for state in RobotState}
    values.update(paths)
    return SimpleNamespace(backgrounds_enabled=True, resolve_path=lambda value: value, **values)


def _visuals(tmp_path):
    clock, scheduler = FakeClock(), None
    scheduler = FakeScheduler(clock)
    core = RobotCore()
    leases = AgentVisualStateLeaseManager(clock=clock, scheduler=scheduler)
    behavior = BehaviorEngine(core.events, clock=clock,
                              visual_state_supplier=lambda: leases.resolve(core.state))
    thinking, idle = tmp_path / "thinking.png", tmp_path / "idle.png"
    thinking.touch(); idle.touch()
    display = Display()
    loop = EyeRenderLoop(
        EyeRenderer(), display, lambda: behavior.face_state,
        background_resolver=BackgroundResolver(_background_config(
            tmp_path, background_thinking=thinking, background_idle=idle)),
        robot_state_supplier=lambda: behavior.visual_state,
        state_effect_resolver=StateEffectResolver(SimpleNamespace(
            state_effects_enabled=True, state_effects_preset="balanced",
            state_effects_palette="phos", state_effects_reduce_motion=False)),
        frame_state_supplier=behavior.visual_frame,
    )
    return core, leases, behavior, loop, display, clock, scheduler, thinking, idle


def test_thinking_lease_drives_face_background_and_state_effects(tmp_path):
    core, leases, behavior, loop, display, _clock, _scheduler, thinking, _idle = _visuals(tmp_path)
    leases.request("agent-a", "thinking", duration_ms=1000)

    face = behavior.face_state
    loop._draw(EyeRenderer().render(face, timestamp=0), timestamp=0)

    assert core.state is RobotState.IDLE
    assert behavior.robot_state is RobotState.IDLE
    assert behavior.visual_state is RobotState.THINKING
    assert face.expression is FaceExpression.CURIOUS and face.background == "#35245E"
    assert display.calls[-1][1]["background"].path == thinking
    assert display.calls[-1][1]["state_effect"].state == "thinking"


def test_activation_snaps_semantic_style_while_eye_geometry_interpolates(tmp_path):
    _core, leases, behavior, loop, display, clock, scheduler, thinking, idle = _visuals(tmp_path)
    renderer = EyeRenderer(transition_seconds=.2)

    idle_face, idle_state = behavior.visual_frame()
    loop._draw(renderer.render(idle_face, timestamp=0, visual_state=idle_state), timestamp=0,
               visual_state=idle_state)
    assert display.calls[-1][1]["background"].path == idle

    leases.request("agent-a", "thinking", duration_ms=250)
    thinking_face, thinking_state = behavior.visual_frame()
    first = renderer.render(thinking_face, timestamp=.01, visual_state=thinking_state)
    loop._draw(first, timestamp=.01, visual_state=thinking_state)
    middle = renderer.render(thinking_face, timestamp=.1, visual_state=thinking_state)
    loop._draw(middle, timestamp=.1, visual_state=thinking_state)
    settled = renderer.render(thinking_face, timestamp=.21, visual_state=thinking_state)
    loop._draw(settled, timestamp=.21, visual_state=thinking_state)

    assert thinking_state is RobotState.THINKING
    assert first.background == middle.background == settled.background == thinking_face.background
    assert first.eye_color == middle.eye_color == settled.eye_color
    assert first.eyes != settled.eyes  # Geometry retains the intended smooth transition.
    assert display.calls[-1][1]["background"].path == thinking
    assert display.calls[-1][1]["state_effect"].state == "thinking"

    clock.advance(.25); scheduler.run_due()
    idle_face, idle_state = behavior.visual_frame()
    expired = renderer.render(idle_face, timestamp=.25, visual_state=idle_state)
    loop._draw(expired, timestamp=.25, visual_state=idle_state)
    assert idle_state is RobotState.IDLE
    assert expired.background == idle_face.background
    assert display.calls[-1][1]["background"].path == idle
    assert display.calls[-1][1]["state_effect"].state == "idle"


def test_release_and_expiry_restore_idle_visual_surfaces(tmp_path):
    core, leases, behavior, loop, display, clock, scheduler, _thinking, idle = _visuals(tmp_path)
    leases.request("agent-a", "thinking", duration_ms=250)
    loop._draw(EyeRenderer().render(behavior.face_state, timestamp=0), timestamp=0)
    leases.request("agent-a", "idle")
    loop._draw(EyeRenderer().render(behavior.face_state, timestamp=.1), timestamp=.1)
    assert display.calls[-1][1]["background"].path == idle
    assert display.calls[-1][1]["state_effect"].state == "idle"

    leases.request("agent-a", "thinking", duration_ms=250)
    clock.advance(.25); scheduler.run_due()
    loop._draw(EyeRenderer().render(behavior.face_state, timestamp=.25), timestamp=.25)
    assert core.state is RobotState.IDLE
    assert behavior.visual_state is RobotState.IDLE
    assert display.calls[-1][1]["background"].path == idle
    assert display.calls[-1][1]["state_effect"].state == "idle"


def test_first_frame_after_expiry_uses_one_idle_visual_snapshot(tmp_path):
    core, leases, behavior, loop, display, clock, scheduler, _thinking, idle = _visuals(tmp_path)
    leases.request("agent-a", "thinking", duration_ms=250)

    thinking_face, thinking_state = behavior.visual_frame()
    loop._draw(EyeRenderer().render(thinking_face, timestamp=0), timestamp=0, visual_state=thinking_state)
    assert thinking_state is RobotState.THINKING

    clock.advance(.25); scheduler.run_due()
    face, visual_state = behavior.visual_frame()
    loop._draw(EyeRenderer().render(face, timestamp=.25), timestamp=.25, visual_state=visual_state)

    assert core.state is RobotState.IDLE
    assert visual_state is RobotState.IDLE
    assert face.expression is FaceExpression.NEUTRAL
    assert display.calls[-1][1]["background"].path == idle
    assert display.calls[-1][1]["state_effect"].state == "idle"


def test_render_snapshot_keeps_surfaces_consistent_if_expiry_occurs_mid_frame(tmp_path):
    _core, leases, behavior, loop, display, clock, scheduler, thinking, _idle = _visuals(tmp_path)
    leases.request("agent-a", "thinking", duration_ms=250)
    face, visual_state = behavior.visual_frame()

    # Model the deadline callback running after the snapshot but before draw.
    # The captured state remains authoritative for this frame; the next frame
    # will independently acquire the post-expiry idle snapshot.
    clock.advance(.25); scheduler.run_due()
    loop._draw(EyeRenderer().render(face, timestamp=.25), timestamp=.25, visual_state=visual_state)

    assert visual_state is RobotState.THINKING
    assert display.calls[-1][1]["background"].path == thinking
    assert display.calls[-1][1]["state_effect"].state == "thinking"


def test_transition_diagnostics_are_bounded_and_include_composition_inputs(tmp_path, caplog):
    _core, leases, behavior, loop, _display, _clock, _scheduler, _thinking, _idle = _visuals(tmp_path)
    caplog.set_level("DEBUG", logger="robot.ui.runtime")
    leases.request("agent-a", "thinking", duration_ms=1000)
    face, state = behavior.visual_frame()
    renderer = EyeRenderer()
    for timestamp in (0, .01, .02, .03):
        loop._draw(renderer.render(face, timestamp=timestamp, visual_state=state), timestamp=timestamp,
                   visual_state=state, face_state=face)

    frames = [record.message for record in caplog.records if "PHOS VISUAL FRAME" in record.message]
    assert len(frames) == 3
    assert all("effective=thinking" in message and "background_state=thinking" in message and
               "effect_state=thinking" in message for message in frames)


def test_equivalent_visual_reconfiguration_during_thinking_preserves_surfaces(tmp_path):
    _core, leases, behavior, loop, display, _clock, _scheduler, thinking, _idle = _visuals(tmp_path)
    leases.request("agent-a", "thinking", duration_ms=1000)
    face, state = behavior.visual_frame()
    loop._draw(EyeRenderer().render(face, timestamp=0, visual_state=state), timestamp=0,
               visual_state=state, face_state=face)
    asset, revision, effects = loop._background_asset, loop._background_revision, loop._state_effect_resolver

    loop._replace_background_resolver(BackgroundResolver(_background_config(
        tmp_path, background_thinking=thinking, background_idle=tmp_path / "idle.png")))
    loop._replace_state_effect_resolver(StateEffectResolver(SimpleNamespace(
        state_effects_enabled=True, state_effects_preset="balanced",
        state_effects_palette="phos", state_effects_reduce_motion=False)))
    face, state = behavior.visual_frame()
    loop._draw(EyeRenderer().render(face, timestamp=.01, visual_state=state), timestamp=.01,
               visual_state=state, face_state=face)

    assert state is RobotState.THINKING
    assert loop._background_asset is asset and loop._background_revision == revision
    assert loop._state_effect_resolver is effects
    assert display.calls[-1][1]["background"].path == thinking
    assert display.calls[-1][1]["state_effect"].state == "thinking"


def test_same_loop_agent_appearance_is_applied_before_first_thinking_frame(tmp_path):
    _core, leases, behavior, loop, display, _clock, _scheduler, _thinking, _idle = _visuals(tmp_path)
    agent_thinking = tmp_path / "agent-thinking.png"
    agent_thinking.touch()

    async def exercise():
        loop._loop = asyncio.get_running_loop()
        loop._appearance_ready = True
        # This models the accepted MCP path: lease commit first, then the
        # lifecycle's follow-agent appearance reconciliation, without yielding
        # to the render task between those synchronous operations.
        leases.request("agent-a", "thinking", duration_ms=1000)
        loop.request_backgrounds(BackgroundResolver(_background_config(
            tmp_path, background_thinking=agent_thinking, background_idle=tmp_path / "idle.png")))
        loop.request_state_effects(StateEffectResolver(SimpleNamespace(
            state_effects_enabled=True, state_effects_preset="balanced",
            state_effects_palette="amber", state_effects_reduce_motion=False)))
        face, state = behavior.visual_frame()
        loop._draw(EyeRenderer().render(face, timestamp=0, visual_state=state), timestamp=0,
                   visual_state=state, face_state=face)

    asyncio.run(exercise())
    assert display.calls[-1][1]["background"].path == agent_thinking
    effect = display.calls[-1][1]["state_effect"]
    assert effect.state == "thinking" and effect.palette.primary == "#FFE05B"

def test_real_core_states_win_over_thinking_for_all_visual_surfaces(tmp_path):
    core, leases, behavior, loop, display, _clock, _scheduler, _thinking, _idle = _visuals(tmp_path)
    leases.request("agent-a", "thinking", duration_ms=1000)
    for state in (RobotState.LISTENING, RobotState.SPEAKING, RobotState.SLEEPING, RobotState.ERROR):
        behavior._visual_state_supplier = lambda state=state: leases.resolve(state)
        loop._draw(EyeRenderer().render(behavior.face_state, timestamp=0), timestamp=0)
        assert behavior.visual_state is state
        assert display.calls[-1][1]["state_effect"].state == state.value
    assert core.state is RobotState.IDLE


def test_runtime_shutdown_closes_lease_manager_without_core_transition(tmp_path):
    from robot.runtime import build_runtime
    from robot.ui import MemoryEyeDisplay

    clock, scheduler = FakeClock(), None
    scheduler = FakeScheduler(clock)
    leases = AgentVisualStateLeaseManager(clock=clock, scheduler=scheduler)
    runtime = build_runtime(eye_display=MemoryEyeDisplay(), agent_visual_state=leases)
    leases.request("agent-a", "thinking", duration_ms=1000)

    import asyncio
    asyncio.run(runtime.stop())

    assert runtime.core.state is RobotState.IDLE
    assert scheduler.calls[-1].cancelled
