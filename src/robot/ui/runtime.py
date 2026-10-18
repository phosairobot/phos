"""Fixed-rate UI runtime, independent from behavior and Vision timing."""

from __future__ import annotations

import asyncio
from functools import partial
import logging
import time
from collections.abc import Callable
from threading import Lock
from typing import Optional

from robot.core.behaviors import Behavior

from .display import CameraPreviewSettings, CameraPreviewView, EyeDisplay
from .backgrounds import BackgroundAsset, BackgroundResolver
from .eyes import EyeRenderer
from .state_effects import StateEffectParams, StateEffectResolver
from .eye_safe import EyeSafeParams, EyeSafeResolver
from .state import FaceState
from robot.core.startup import StartupState

logger = logging.getLogger(__name__)


class EyeRenderLoop(Behavior):
    """Render the latest FaceState at a display rate independent of Vision."""

    name = "eye-render-loop"

    def __init__(
        self,
        renderer: EyeRenderer,
        display: EyeDisplay,
        state_supplier: Callable[[], FaceState],
        *,
        fps: int = 30,
        fullscreen: bool = True,
        preview_supplier: Optional[Callable[[], Optional[CameraPreviewView]]] = None,
        preview_settings: Optional[CameraPreviewSettings] = None,
        startup_supplier: Optional[Callable[[], dict]] = None,
        touch_handler: Optional[Callable] = None,
        touch_settings: Optional[dict] = None,
        background_resolver: Optional[BackgroundResolver] = None,
        robot_state_supplier: Optional[Callable[[], object]] = None,
        state_effect_resolver: Optional[StateEffectResolver] = None,
        eye_safe_resolver: Optional[EyeSafeResolver] = None,
    ) -> None:
        if fps <= 0:
            raise ValueError("fps must be positive.")
        self._renderer = renderer
        self._display = display
        self._state_supplier = state_supplier
        self._frame_interval = 1.0 / fps
        self._fullscreen = fullscreen
        self._preview_supplier = preview_supplier
        self._preview_settings = preview_settings or CameraPreviewSettings()
        self._startup_supplier = startup_supplier
        self._touch_handler = touch_handler
        self._touch_settings = touch_settings or {}
        self._background_resolver = background_resolver
        self._robot_state_supplier = robot_state_supplier
        self._background_state = object()
        self._background_asset = BackgroundAsset("idle")
        self._background_revision = 0
        self._pending_background_resolver: Optional[BackgroundResolver] = None
        self._state_effect_resolver = state_effect_resolver
        self._pending_state_effect_resolver: Optional[StateEffectResolver] = None
        self._eye_safe_resolver = eye_safe_resolver
        self._pending_eye_safe_resolver: Optional[EyeSafeResolver] = None
        self._effect_dimensions = None
        self._state_effect_prewarm_handle = None
        self._task: Optional[asyncio.Task[None]] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._appearance_lock = Lock()
        self._appearance_ready = False
        self._pending_iris_color: Optional[str] = None
        self._pending_preview_settings: Optional[CameraPreviewSettings] = None

    def request_appearance(self, *, iris_color: str) -> None:
        """Queue a style update onto the display loop; safe from lifecycle threads."""
        with self._appearance_lock:
            if not self._appearance_ready or self._loop is None:
                self._pending_iris_color = iris_color
                return
            loop = self._loop
        loop.call_soon_threadsafe(partial(self._renderer.update_appearance, iris_color=iris_color))

    def request_preview_settings(self, settings: CameraPreviewSettings) -> None:
        with self._appearance_lock:
            if not self._appearance_ready or self._loop is None:
                self._pending_preview_settings = settings
                return
            loop = self._loop
        loop.call_soon_threadsafe(setattr, self, "_preview_settings", settings)

    def request_backgrounds(self, resolver: BackgroundResolver) -> None:
        """Publish a validated resolver on the display loop without restart."""
        with self._appearance_lock:
            if not self._appearance_ready or self._loop is None:
                self._pending_background_resolver = resolver
                return
            loop = self._loop
        loop.call_soon_threadsafe(self._replace_background_resolver, resolver)

    def request_state_effects(self, resolver: StateEffectResolver) -> None:
        """Publish effect semantics through the render-loop boundary."""
        with self._appearance_lock:
            if not self._appearance_ready or self._loop is None:
                self._pending_state_effect_resolver = resolver
                return
            loop = self._loop
        loop.call_soon_threadsafe(self._replace_state_effect_resolver, resolver)

    def request_eye_safe_layer(self, resolver: EyeSafeResolver) -> None:
        with self._appearance_lock:
            if not self._appearance_ready or self._loop is None:
                self._pending_eye_safe_resolver = resolver
                return
            loop = self._loop
        loop.call_soon_threadsafe(setattr, self, "_eye_safe_resolver", resolver)

    def _replace_state_effect_resolver(self, resolver: StateEffectResolver) -> None:
        self._state_effect_resolver = resolver
        if self._effect_dimensions is not None and hasattr(self._display, "prewarm_state_effect"):
            state = self._robot_state_supplier() if self._robot_state_supplier is not None else "idle"
            effect = resolver.resolve(state)
            self._schedule_state_effect_prewarm(effect)
        logger.debug("PHOS VISUAL: state effects configured enabled=%s preset=%s", resolver.enabled, resolver.preset)

    def _schedule_state_effect_prewarm(self, effect: StateEffectParams) -> None:
        """Keep texture preparation downstream of semantic runtime commands."""
        if self._effect_dimensions is None:
            return
        if self._loop is None:
            # Headless/test construction has no runtime queue; retain the
            # direct behavior for that safe, non-running case.
            self._display.prewarm_state_effect(effect, *self._effect_dimensions, time.monotonic())
            return
        if self._state_effect_prewarm_handle is not None:
            self._state_effect_prewarm_handle.cancel()
        self._state_effect_prewarm_handle = self._loop.call_later(
            self._frame_interval, self._prewarm_state_effect, effect, self._effect_dimensions)

    def _prewarm_state_effect(self, effect: StateEffectParams, dimensions: tuple[int, int]) -> None:
        self._state_effect_prewarm_handle = None
        self._display.prewarm_state_effect(effect, *dimensions, time.monotonic())

    def _replace_background_resolver(self, resolver: BackgroundResolver) -> None:
        self._background_resolver = resolver
        self._background_state = object()
        self._background_revision += 1
        logger.debug("PHOS VISUAL: backgrounds configured enabled=%s", resolver.enabled)

    def background_status(self) -> dict:
        with self._appearance_lock:
            resolver = self._pending_background_resolver or self._background_resolver
        state = self._robot_state_supplier() if self._robot_state_supplier is not None else "idle"
        return resolver.status(state) if resolver is not None else {
            "enabled": False, "current_state": getattr(state, "value", state),
            "current_asset": None, "available": False, "reason": None, "configured": {}}

    async def start(self) -> None:
        if self._task is not None:
            raise RuntimeError("Eye render loop is already running.")
        try:
            self._loop = asyncio.get_running_loop()
            with self._appearance_lock:
                pending = self._pending_iris_color
                self._pending_iris_color = None
            if pending is not None:
                self._renderer.update_appearance(iris_color=pending)
            with self._appearance_lock:
                pending_preview = self._pending_preview_settings
                self._pending_preview_settings = None
            if pending_preview is not None:
                self._preview_settings = pending_preview
            with self._appearance_lock:
                pending_backgrounds = self._pending_background_resolver
                self._pending_background_resolver = None
            if pending_backgrounds is not None:
                self._replace_background_resolver(pending_backgrounds)
            with self._appearance_lock:
                pending_effects = self._pending_state_effect_resolver
                self._pending_state_effect_resolver = None
            if pending_effects is not None:
                self._replace_state_effect_resolver(pending_effects)
            with self._appearance_lock:
                pending_eye_safe = self._pending_eye_safe_resolver
                self._pending_eye_safe_resolver = None
            if pending_eye_safe is not None:
                self._eye_safe_resolver = pending_eye_safe
            initial = self._renderer.render(self._state_supplier(), timestamp=time.monotonic())
            self._display.open(initial.width, initial.height, fullscreen=self._fullscreen)
            if self._touch_handler is not None and hasattr(self._display, "set_touch_handler"):
                self._display.set_touch_handler(self._touch_handler, **self._touch_settings)
            self._draw(initial)
            self._display.poll_keys()
            self._task = asyncio.create_task(self._run(), name="eye-render-loop")
            with self._appearance_lock:
                self._appearance_ready = True
                pending = self._pending_iris_color
                self._pending_iris_color = None
                pending_preview = self._pending_preview_settings
                self._pending_preview_settings = None
            if pending is not None:
                self._renderer.update_appearance(iris_color=pending)
            if pending_preview is not None:
                self._preview_settings = pending_preview
        except Exception:
            with self._appearance_lock:
                self._appearance_ready = False
                self._loop = None
            try:
                self._display.close()
            except Exception:
                pass
            raise

    async def stop(self) -> None:
        with self._appearance_lock:
            self._appearance_ready = False
            self._loop = None
        if self._state_effect_prewarm_handle is not None:
            self._state_effect_prewarm_handle.cancel()
            self._state_effect_prewarm_handle = None
        failure: Optional[Exception] = None
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            except Exception as error:
                failure = error
            self._task = None
        try:
            self._display.close()
        except Exception as error:
            if failure is None:
                failure = error
        if failure is not None:
            raise failure

    async def wait(self) -> None:
        """Wait for the render task; used by the application supervisor."""
        if self._task is None:
            raise RuntimeError("Eye render loop is not running.")
        await asyncio.shield(self._task)

    async def _run(self) -> None:
        while True:
            started_at = time.monotonic()
            frame = self._renderer.render(self._state_supplier(), timestamp=started_at)
            preview = self._preview_supplier() if self._preview_supplier is not None and self._preview_settings.enabled else None
            self._draw(frame, preview, timestamp=started_at)
            self._display.poll_keys()
            await asyncio.sleep(max(0.0, self._frame_interval - (time.monotonic() - started_at)))

    def _draw(self, frame, preview=None, *, timestamp=None) -> None:
        self._effect_dimensions = (frame.width, frame.height)
        if self._startup_supplier is not None:
            startup = self._startup_supplier()
            state = startup["overall_state"]
            if state not in {StartupState.READY.value, StartupState.DEGRADED.value}:
                splash = startup.get("splash", {})
                self._display.draw_startup(state=state, message="Starting..." if state == "starting" else "Unable to start",
                                           image_path=splash.get("image"), title=splash.get("title", "PHOS"), subtitle=splash.get("subtitle", "Starting..."))
                return
        effect = self._state_effect()
        eye_safe = self._eye_safe()
        safe_kwargs = {"eye_safe": eye_safe} if eye_safe.enabled else {}
        effect_kwargs = ({"state_effect": effect, "effect_timestamp": timestamp or 0.0}
                         if effect.intensity > 0 else {})
        if self._preview_settings.enabled:
            background = self._background()
            if background.available:
                self._display.draw(frame, preview, self._preview_settings, background=background, **safe_kwargs, **effect_kwargs)
            else:
                self._display.draw(frame, preview, self._preview_settings, **effect_kwargs)
        else:
            background = self._background()
            if background.available:
                self._display.draw(frame, background=background, **safe_kwargs, **effect_kwargs)
            else:
                # Preserve the legacy call exactly while backgrounds are disabled
                # or no state image is usable.
                self._display.draw(frame, **effect_kwargs)

    def _state_effect(self) -> StateEffectParams:
        if self._state_effect_resolver is None:
            return StateEffectParams("idle")
        state = self._robot_state_supplier() if self._robot_state_supplier is not None else "idle"
        return self._state_effect_resolver.resolve(state)

    def _eye_safe(self) -> EyeSafeParams:
        return self._eye_safe_resolver.resolve() if self._eye_safe_resolver is not None else EyeSafeParams()

    def _background(self) -> BackgroundAsset:
        """Resolve only on semantic state changes, never in EyeRenderer."""
        if self._background_resolver is None or self._robot_state_supplier is None:
            return self._background_asset
        state = self._robot_state_supplier()
        if state != self._background_state:
            self._background_state = state
            self._background_asset = self._background_resolver.resolve(state)
        return self._background_asset
