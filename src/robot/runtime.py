"""Thin application coordinator for Core, Vision, behavior, and display."""

from __future__ import annotations

import asyncio
import os
import subprocess
import shutil
from dataclasses import asdict, is_dataclass, replace
import logging
import time
from typing import Callable, Optional

from robot.config import RuntimeConfig
from robot.hardware.environmental import environmental_provider_type
from robot.hardware.ccs811 import CCS811Provider
from robot.hardware.mpu6050 import MPU6050Provider
from robot.hardware.ws2812b import LEDRingProvider, LEDRingSocketProvider
from robot.motion import MotionSettings
from robot.motion import MotionState
from robot.sensors import (EnvironmentalSensorProvider, EnvironmentalSensorService,
                           AirQualitySensorProvider, AirQualitySensorService,
                           IMUSensorProvider, IMUSensorService)
from robot.core import (BehaviorEngine, EnvironmentalInterpreter, EnvironmentalSettings, Event, EventBus,
                        RobotCore, RobotState, STATE_CHANGED, PresenceInterpreter, AttentionManager)
from robot.core.behavior_engine import ENVIRONMENTAL_STATE_CHANGED, IMU_MOTION_STATE
from robot.core.touch import TOUCH_EVENT, TOUCH_EVENT_NAMES, TouchStatus
from robot.core.expression_reaction import ExpressionReactionPolicy
from robot.core.startup import StartupReadiness, StartupState
from robot.ui import (CameraPreviewSettings, CameraPreviewView, EyeDisplay, EyeRenderer, LEDRingController,
                      LEDRingSettings, TkEyeDisplay)
from robot.ui.runtime import EyeRenderLoop
from robot.voice import (AplayAudioOutputProvider, PiperTTSProvider, PyAudioCaptureProvider,
                         ResamplingAudioCaptureProvider, TTSBusyError, TTSConfigurationError,
                         VoiceCaptureSession, VoskSTTProvider)
from robot.vision import (
    ExpressionSmoother,
    OpenCVExpressionProvider,
    OpenCVFaceDetector,
    Picamera2CameraProvider,
    VisionPipeline,
)

from robot.vision.aws_expression import AWSExpressionProvider

logger = logging.getLogger(__name__)


class PhosRuntime:
    """Own application lifecycle without embedding Vision or UI implementation."""

    def __init__(
        self,
        core: RobotCore,
        behavior_engine: BehaviorEngine,
        eye_render_loop: EyeRenderLoop,
        *,
        vision_pipeline: Optional[VisionPipeline] = None,
        config: Optional[RuntimeConfig] = None,
        vision_forced: bool = False,
        sensor_service: Optional[EnvironmentalSensorService] = None,
        air_quality_service: Optional[AirQualitySensorService] = None,
        imu_service: Optional[IMUSensorService] = None,
        led_ring_controller: Optional[LEDRingController] = None,
        presence_interpreter: Optional[PresenceInterpreter] = None,
        attention_manager: Optional[AttentionManager] = None,
        startup: Optional[StartupReadiness] = None,
        voice_session: Optional[VoiceCaptureSession] = None,
        tts_provider=None, audio_output=None,
    ) -> None:
        self.core = core
        self._behavior_engine = behavior_engine
        self._eye_render_loop = eye_render_loop
        self._vision_pipeline = vision_pipeline
        self._config = config
        self._vision_forced = vision_forced
        self._sensor_service = sensor_service
        self._air_quality_service = air_quality_service
        self._imu_service = imu_service
        self._led_ring_controller = led_ring_controller
        self._presence_interpreter = presence_interpreter
        self._attention_manager = attention_manager
        self._startup = startup or StartupReadiness()
        self._ready_sound_played = False
        self._voice_session = voice_session
        self._tts_provider, self._audio_output = tts_provider, audio_output
        self._tts_lock = asyncio.Lock()
        self._touch_status = TouchStatus(enabled=bool(config and config.touch_enabled))
        self._environmental_interpreter = None
        self._loop = None
        self._vision_changed: Optional[asyncio.Event] = None
        self._vision_lock = asyncio.Lock()
        self._preview_tasks = set()
        self._stopping = False
        self._unsubscribers: list[Callable[[], None]] = []
        self._started = False
        self._vision_started = False

    def publish_motion_state(self, state: MotionState) -> None:
        loop = self._loop
        if loop is None or self._stopping:
            return
        def publish():
            task = asyncio.create_task(self.core.events.publish(Event(IMU_MOTION_STATE, {"state": state.value})))
            task.add_done_callback(_log_motion_publish_failure)
        loop.call_soon_threadsafe(publish)

    def publish_environmental_state(self, state, reason, temperature_overlay=None, air_quality_overlay=None) -> None:
        loop = self._loop
        if loop is None or self._stopping:
            return
        def publish():
            task = asyncio.create_task(self.core.events.publish(Event(ENVIRONMENTAL_STATE_CHANGED,
                {"state": state.value, "reason": reason,
                 "temperature_overlay": getattr(temperature_overlay, "value", "none"),
                 "air_quality_overlay": getattr(air_quality_overlay, "value", "none")})))
            task.add_done_callback(_log_motion_publish_failure)
        loop.call_soon_threadsafe(publish)

    def apply_appearance(self, config: RuntimeConfig) -> None:
        """Apply validated appearance through the display runtime boundary."""
        self._eye_render_loop.request_appearance(iris_color=config.iris_color)

    def apply_imu_behavior(self, config: RuntimeConfig) -> None:
        self._behavior_engine.configure_imu_reactions(
            config.imu_reaction_strength, config.imu_tilt_gaze_strength, config.imu_tilt_eye_asymmetry_strength,
            config.imu_shake_reaction_strength, config.imu_impact_reaction_strength,
            config.imu_shake_reaction_duration_seconds, config.imu_impact_reaction_duration_seconds,
            config.imu_reaction_cooldown_seconds)

    def apply_environmental_behavior(self, config: RuntimeConfig) -> None:
        if self._environmental_interpreter is None:
            raise RuntimeError("Environmental behavior service is not configured")
        self._environmental_interpreter.configure(_environmental_settings(config))

    def apply_base_visual_source(self, config: RuntimeConfig) -> None:
        self._behavior_engine.configure_base_visual_source(config.base_visual_source)

    def apply_environment_overlays(self, config: RuntimeConfig) -> None:
        self._behavior_engine.configure_environment_overlays(config.environment_overlays_enabled)

    def apply_led_ring(self, config: RuntimeConfig) -> None:
        if self._led_ring_controller is None:
            raise RuntimeError("LED ring service is not configured")
        # Pin/count changes remain pending restart, so never pass saved hardware
        # values to an already-open provider during a visual-only reload.
        active = self._config
        settings = _led_settings(config)
        if active is not None:
            settings = replace(settings, led_count=active.led_ring_led_count, gpio_pin=active.led_ring_gpio_pin)
        self._led_ring_controller.configure(settings)

    def sensor_status(self) -> dict:
        """Read-only application boundary, safe for the lifecycle thread."""
        state = {"environmental": self._sensor_service.snapshot()} if self._sensor_service is not None else {}
        if self._air_quality_service is not None:
            state["ccs811"] = self._air_quality_service.snapshot()
        if self._imu_service is not None:
            state["imu"] = self._imu_service.snapshot()
        if self._led_ring_controller is not None:
            state["led_ring"] = self._led_ring_controller.snapshot()
        if self._environmental_interpreter is not None:
            state["environmental_behavior"] = {"state": self._environmental_interpreter.state.value,
                                                "reason": self._environmental_interpreter.reason,
                                                "temperature_overlay": self._environmental_interpreter.temperature_overlay.value,
                                                "air_quality_overlay": self._environmental_interpreter.air_quality_overlay.value}
        return state

    def application_status(self) -> dict:
        """Read-only semantic status boundary for local adapters.

        This snapshots existing in-memory application state only. It never
        opens a device or invokes a provider, so web/API polling cannot affect
        the render loop or sensor workers.
        """
        def plain(value):
            if is_dataclass(value):
                return {key: plain(item) for key, item in asdict(value).items()}
            if hasattr(value, "value"):
                return value.value
            if isinstance(value, dict):
                return {key: plain(item) for key, item in value.items()}
            return value
        sensors = self.sensor_status()
        return {
            "robot": {"state": self.core.state.value, "running": self.core.is_running},
            "visual": plain(self._behavior_engine.face_state),
            "active_visual_source": self._behavior_engine.base_visual_source,
            "environment": sensors.get("environmental", {"status": "unavailable", "available": False}),
            "motion": sensors.get("imu", {"status": "unavailable", "available": False}),
            "startup": self._startup.document(),
            "touch": self.touch_status(),
            "voice": self.voice_status(),
        }

    def touch_status(self) -> dict:
        """Read-only completed-gesture state for application adapters."""
        return self._touch_status.document()

    def voice_status(self) -> dict:
        return self._voice_session.status() if self._voice_session else {"enabled": False, "state": "idle", "listening": False, "speech_detected": False, "stt_provider": None, "stt_available": False, "last_transcript": None, "last_confidence": None, "language": None, "last_transcription_at": None, "last_transcription_duration_ms": None, "last_error": None}

    async def start_listening(self):
        if self._voice_session is None: raise RuntimeError("Voice is unavailable")
        return await self._voice_session.start()

    async def stop_listening(self, *, cancelled=False):
        return self.voice_status() if self._voice_session is None else await self._voice_session.stop(cancelled=cancelled)

    async def speak(self, text: str) -> None:
        """Serialize local synthesis/playback through the semantic state boundary."""
        if self._tts_provider is None or self._audio_output is None:
            raise TTSConfigurationError("Text-to-speech is disabled.")
        if self._tts_lock.locked():
            raise TTSBusyError("PHOS is already speaking.")
        async with self._tts_lock:
            audio = None
            try:
                await self.core.transition_to(RobotState.SPEAKING, reason="tts")
                logger.info("TTS synthesis started provider=local")
                audio = await asyncio.to_thread(self._tts_provider.synthesize, text)
                logger.info("TTS synthesis completed provider=local")
                logger.info("TTS playback started")
                await asyncio.to_thread(self._audio_output.play, audio)
                logger.info("TTS playback completed")
            except Exception:
                logger.exception("TTS provider/runtime error")
                raise
            finally:
                if audio is not None:
                    audio.path.unlink(missing_ok=True)
                if self.core.state is RobotState.SPEAKING:
                    await self.core.transition_to(RobotState.IDLE, reason="tts_completed")

    def apply_imu_motion(self, config: RuntimeConfig) -> None:
        """Apply validated interpretation settings without reopening the IMU."""
        if self._imu_service is None:
            raise RuntimeError("IMU service is not configured")
        self._imu_service.configure_motion(_motion_settings(config))

    def apply_camera_preview(self, config: RuntimeConfig) -> None:
        """Apply from the lifecycle thread and acknowledge runtime acceptance."""
        loop = self._loop
        if loop is None or not self._started or self._stopping:
            raise RuntimeError("PHOS runtime is not ready for preview reload")
        try:
            caller_loop = asyncio.get_running_loop()
        except RuntimeError:
            caller_loop = None
        if caller_loop is loop:
            raise RuntimeError("Preview reload must use the lifecycle thread")
        future = asyncio.run_coroutine_threadsafe(self._apply_camera_preview(config), loop)
        # The IPC client bounds its response wait and reports an in-flight result
        # as uncertain. Keep this acknowledgement tied to actual completion:
        # native camera startup may outlast the HTTP request. Shutdown cancels
        # and drains the task, so it cannot start a camera after runtime exit.
        future.result()

    async def _apply_camera_preview(self, config):
        task = asyncio.current_task()
        self._preview_tasks.add(task)
        try:
            async with self._vision_lock:
                if self._stopping or not self._started:
                    raise RuntimeError("PHOS runtime is stopping")
                names = ("camera_preview_enabled", "camera_preview_position", "camera_preview_scale",
                         "camera_preview_max_fps", "camera_preview_show_face_box",
                         "camera_preview_show_expression", "camera_preview_show_confidence")
                previous = self._config
                updated = RuntimeConfig.from_dict(previous.to_dict(), base_dir=previous._base_dir,
                    overrides={name: getattr(config, name) for name in names})
                # Validate the effective configuration, retaining pending restart fields.
                self._config = updated
                try:
                    await self._reconcile_vision_preview()
                    self._eye_render_loop.request_preview_settings(_preview_settings(updated))
                except BaseException:
                    self._config = previous
                    if self._vision_pipeline is not None:
                        self._vision_pipeline.configure_preview(previous.camera_preview_enabled)
                    raise
        finally:
            self._preview_tasks.discard(task)

    async def _supervise_vision(self):
        while True:
            if not self._vision_started or self._vision_pipeline is None:
                if self._vision_changed is None:
                    return
                await self._vision_changed.wait()
                self._vision_changed.clear()
                continue
            wait_task = asyncio.create_task(self._vision_pipeline.wait())
            change_task = asyncio.create_task(self._vision_changed.wait())
            try:
                done, _ = await asyncio.wait((wait_task, change_task), return_when=asyncio.FIRST_COMPLETED)
            finally:
                for task in (wait_task, change_task):
                    if not task.done():
                        task.cancel()
                await asyncio.gather(wait_task, change_task, return_exceptions=True)
            if change_task in done:
                self._vision_changed.clear()
                if not self._vision_started:
                    try:
                        await wait_task
                    except asyncio.CancelledError:
                        pass
                    continue
            if wait_task in done:
                try:
                    wait_task.result()
                    if self._vision_started:
                        raise RuntimeError("Vision pipeline stopped unexpectedly.")
                except asyncio.CancelledError:
                    if self._vision_started:
                        raise RuntimeError("Vision pipeline was cancelled unexpectedly.")

    async def _reconcile_vision_preview(self) -> None:
        if self._vision_pipeline is None or self._config is None:
            return
        configure_preview = getattr(self._vision_pipeline, "configure_preview", None)
        if configure_preview is not None:
            configure_preview(self._config.camera_preview_enabled)
        wanted = self._config.vision_enabled or self._vision_forced
        if wanted and not self._vision_started:
            self._vision_started = True
            try:
                await self._vision_pipeline.start()
                logger.info("PHOS vision pipeline started for camera preview")
            except BaseException:
                # Camera ownership may already have been acquired before failure.
                await self._vision_pipeline.stop()
                self._vision_started = False
                raise
            finally:
                self._vision_changed.set()
        elif not wanted and self._vision_started:
            # Tell the supervisor before awaiting stop, which completes wait().
            self._vision_started = False
            self._vision_changed.set()
            try:
                await self._vision_pipeline.stop()
            except BaseException:
                # Retain ownership so final shutdown retries cleanup.
                self._vision_started = True
                self._vision_changed.set()
                raise
            logger.info("PHOS vision pipeline stopped after preview was disabled")

    async def start(self) -> None:
        if self._started:
            raise RuntimeError("PHOS runtime is already running.")
        self._unsubscribers = [self.core.events.subscribe(STATE_CHANGED, self._log_state_transition)]
        self._stopping = False
        self._loop = asyncio.get_running_loop()
        self._vision_changed = asyncio.Event()
        splash = self._config.resolve_startup_splash_image() if self._config else None
        sound = self._config.resolve_startup_ready_sound() if self._config else None
        splash_configured = (self._config.startup_splash_image if self._config and self._config.startup_splash_image is not None
                             else "package:robot.assets/images/phos-startup-800x600.png")
        sound_configured = (self._config.startup_ready_sound_file if self._config and self._config.startup_ready_sound_file is not None
                            else "package:robot.assets/audio/phos-startup.wav")
        player = self._config.startup_ready_sound_player if self._config else None
        player_resolved = shutil.which(player) if player else None
        logger.info(
            "PHOS STARTUP ASSETS: splash.configured=%s splash.resolved=%s splash.exists=%s splash.readable=%s; "
            "sound.configured=%s sound.resolved=%s sound.exists=%s sound.readable=%s sound.player=%s sound.player_resolved=%s sound.device=%s",
            splash_configured, splash,
            bool(splash and splash.is_file()), bool(splash and splash.is_file() and os.access(splash, os.R_OK)),
            sound_configured, sound,
            bool(sound and sound.is_file()), bool(sound and sound.is_file() and os.access(sound, os.R_OK)),
            player, player_resolved, self._config.startup_ready_sound_device if self._config else None,
        )
        self._startup.begin("display", required=True)
        self._startup.begin("core", required=True)
        self._startup.begin("vision")
        self._startup.begin("sensors")
        self._startup.begin("led_ring")
        try:
            await self.core.start()
            self._startup.set("display", StartupState.READY)
            self._startup.set("core", StartupState.READY)
            if self._sensor_service is not None:
                await self._sensor_service.start()
            self._startup.set("sensors", StartupState.READY if self._config and (self._config.environmental_enabled or self._config.ccs811_enabled or self._config.imu_enabled) else StartupState.UNAVAILABLE, "optional sensors disabled" if self._config and not (self._config.environmental_enabled or self._config.ccs811_enabled or self._config.imu_enabled) else None)
            if self._air_quality_service is not None:
                await self._air_quality_service.start()
            if self._imu_service is not None:
                await self._imu_service.start()
            if self._led_ring_controller is not None:
                self._led_ring_controller.start()
            self._startup.set("led_ring", StartupState.READY if self._config and self._config.led_ring_enabled else StartupState.UNAVAILABLE, "optional LED ring disabled" if self._config and not self._config.led_ring_enabled else None)
            logger.info("PHOS core, behavior engine, and renderer started")
            if self._vision_pipeline is not None and self._config is not None and (self._config.vision_enabled or self._vision_forced):
                # Stop must also release a partially started camera/pipeline.
                self._vision_started = True
                await self._vision_pipeline.start()
                logger.info("PHOS vision pipeline and camera started")
                self._startup.set("vision", StartupState.READY)
            else:
                self._startup.set("vision", StartupState.UNAVAILABLE, "optional vision disabled")
            self._started = True
            logger.info("STARTUP: %s", self._startup.overall_state.value)
            self._play_ready_sound()
            logger.info("PHOS runtime started")
        except asyncio.CancelledError:
            await self.stop()
            raise
        except Exception:
            self._startup.set("core", StartupState.FAILED, "runtime startup failure")
            logger.exception("PHOS runtime failed during startup")
            await self._transition_to_error("runtime startup failure")
            await self.stop()
            raise

    def _play_ready_sound(self) -> None:
        if self._ready_sound_played or self._config is None:
            return
        logger.info("STARTUP SOUND: enabled=%s configured_path=%s device=%s", self._config.startup_ready_sound_enabled, self._config.startup_ready_sound_file, self._config.startup_ready_sound_device)
        if not self._config.startup_ready_sound_enabled:
            return
        sound = self._config.resolve_startup_ready_sound()
        logger.info("STARTUP SOUND: resolved_path=%s exists=%s readable=%s", sound, bool(sound and sound.is_file()), bool(sound and sound.is_file() and os.access(sound, os.R_OK)))
        if not sound.is_file() or not os.access(sound, os.R_OK):
            logger.warning("STARTUP SOUND FAILED: reason=file_not_found path=%s", sound)
            return
        command = [self._config.startup_ready_sound_player, "-q"]
        player = shutil.which(command[0])
        logger.info("STARTUP SOUND PLAYER: configured=%s resolved=%s", command[0], player)
        if player is None:
            logger.warning("STARTUP SOUND FAILED: reason=player_not_found player=%s", command[0])
            return
        command[0] = player
        if self._config.startup_ready_sound_device is not None:
            command.extend(("-D", self._config.startup_ready_sound_device))
        command.append(str(sound))
        logger.info("STARTUP SOUND COMMAND: %r", command)
        try:
            logger.info("READY SOUND: playback_started")
            result = subprocess.run(command, capture_output=True, text=True, check=False)
            logger.info("STARTUP SOUND RESULT: returncode=%s stdout=%s stderr=%s", result.returncode, result.stdout.strip(), result.stderr.strip())
            if result.returncode:
                logger.warning("READY SOUND: playback_failed")
                return
            self._ready_sound_played = True
            logger.info("READY SOUND: playback_success")
        except OSError as error:
            logger.warning("READY SOUND: playback_failed reason=%s", error)

    async def run(self, stop_event: asyncio.Event) -> None:
        """Run until requested to stop or a supervised subsystem fails."""
        await self.start()
        stop_task = asyncio.create_task(stop_event.wait(), name="runtime-stop-request")
        supervisors = [
            asyncio.create_task(self._behavior_engine.wait(), name="behavior-engine-supervisor"),
            asyncio.create_task(self._eye_render_loop.wait(), name="eye-render-supervisor"),
        ]
        if self._vision_pipeline is not None:
            supervisors.append(asyncio.create_task(self._supervise_vision(), name="vision-supervisor"))
        try:
            done, _pending = await asyncio.wait([stop_task, *supervisors], return_when=asyncio.FIRST_COMPLETED)
            if stop_task not in done:
                failed = next(task for task in done if task in supervisors)
                try:
                    failed.result()
                    raise RuntimeError("A PHOS subsystem stopped unexpectedly.")
                except asyncio.CancelledError:
                    raise RuntimeError("A PHOS subsystem was cancelled unexpectedly.")
                except Exception as error:
                    logger.exception("PHOS subsystem failed", exc_info=error)
                    await self._transition_to_error(type(error).__name__)
                    # The failure is represented by Core's ERROR state and
                    # clean shutdown below; callers must not receive a second
                    # exception while the failing subsystem is being released.
                    return
        finally:
            for task in [stop_task, *supervisors]:
                if not task.done():
                    task.cancel()
            await asyncio.gather(stop_task, *supervisors, return_exceptions=True)
            await self.stop()

    async def stop(self) -> None:
        self._stopping = True
        tasks = tuple(self._preview_tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._loop = None
        if self._led_ring_controller is not None:
            self._led_ring_controller.stop()
        if self._air_quality_service is not None:
            await self._air_quality_service.stop()
        if self._imu_service is not None:
            await self._imu_service.stop()
        if self._sensor_service is not None:
            await self._sensor_service.stop()
        if self._vision_started and self._vision_pipeline is not None:
            try:
                await self._vision_pipeline.stop()
            except Exception:
                logger.exception("PHOS vision shutdown failed")
            self._vision_started = False
            logger.info("PHOS vision pipeline stopped")
        if self.core.is_running:
            try:
                await self.core.stop()
            except Exception:
                logger.exception("PHOS core shutdown failed")
            logger.info("PHOS core, renderer, and behavior engine stopped")
        for unsubscribe in self._unsubscribers:
            unsubscribe()
        self._unsubscribers = []
        self._started = False
        logger.info("PHOS runtime stopped")

    async def _transition_to_error(self, reason: str) -> None:
        if self.core.is_running and self.core.state is not RobotState.ERROR:
            try:
                await self.core.transition_to(RobotState.ERROR, reason=reason)
            except Exception:
                logger.exception("Could not transition PHOS core to ERROR")

    @staticmethod
    def _log_state_transition(event: Event) -> None:
        logger.info("PHOS state changed to %s", event.data.get("current"))


def build_runtime(
    *,
    config: Optional[RuntimeConfig] = None,
    eye_display: Optional[EyeDisplay] = None,
    vision_pipeline: Optional[VisionPipeline] = None,
    vision_factory: Optional[Callable[[EventBus], VisionPipeline]] = None,
    sensor_provider_factory: Optional[Callable[[], EnvironmentalSensorProvider]] = None,
    air_quality_provider_factory: Optional[Callable[[], AirQualitySensorProvider]] = None,
    imu_provider_factory: Optional[Callable[[], IMUSensorProvider]] = None,
    led_ring_provider_factory: Optional[Callable[[LEDRingSettings], LEDRingProvider]] = None,
) -> PhosRuntime:
    """Compose a runtime; tests may inject Vision, display and sensor providers."""
    if vision_pipeline is not None and vision_factory is not None:
        raise ValueError("Provide either vision_pipeline or vision_factory, not both.")
    config = RuntimeConfig.from_file() if config is None else config
    config.validate()
    config.validate_paths()
    core = RobotCore()
    behavior_engine = BehaviorEngine(
        core.events, blink_interval=config.blink_interval_seconds,
        gaze_interval=config.gaze_interval_seconds, face_gaze_smoothing=config.face_gaze_smoothing,
        reaction_decay_per_second=config.reaction_decay_per_second,
        imu_reaction_strength=config.imu_reaction_strength, imu_tilt_gaze_strength=config.imu_tilt_gaze_strength,
        imu_tilt_eye_asymmetry_strength=config.imu_tilt_eye_asymmetry_strength,
        imu_shake_reaction_strength=config.imu_shake_reaction_strength,
        imu_impact_reaction_strength=config.imu_impact_reaction_strength,
        imu_shake_reaction_duration_seconds=config.imu_shake_reaction_duration_seconds,
        imu_impact_reaction_duration_seconds=config.imu_impact_reaction_duration_seconds,
        imu_reaction_cooldown_seconds=config.imu_reaction_cooldown_seconds,
        presence_led_reactions_enabled=config.presence_led_reactions_enabled,
        presence_led_entered_duration_seconds=config.presence_led_entered_duration_ms / 1000,
        presence_led_left_duration_seconds=config.presence_led_left_duration_ms / 1000,
        presence_led_entered_direction=config.presence_led_entered_direction,
        presence_led_left_direction=config.presence_led_left_direction,
    )
    behavior_engine.configure_base_visual_source(config.base_visual_source)
    behavior_engine.configure_environment_overlays(config.environment_overlays_enabled)
    presence = PresenceInterpreter(core.events)
    attention = AttentionManager(core.events, lost_hold_seconds=config.attention_lost_hold_ms / 1000)
    vision_holder = {"pipeline": vision_pipeline}
    startup = StartupReadiness()
    async def voice_transition(target, reason):
        if core.state is not target:
            await core.transition_to(target, reason=reason)
    voice = VoiceCaptureSession(
        ResamplingAudioCaptureProvider(PyAudioCaptureProvider(config.voice_input_device, config.voice_input_device_index,
            config.voice_capture_sample_rate, config.voice_channels, config.voice_chunk_ms), config.voice_processing_sample_rate),
        VoskSTTProvider(config.resolve_path(config.voice_stt_model_path), config.voice_stt_language), core.events, voice_transition,
        enabled=config.voice_enabled, sample_rate=config.voice_processing_sample_rate, channels=config.voice_channels, chunk_ms=config.voice_chunk_ms,
        speech_start_ms=config.voice_speech_start_ms, silence_end_ms=config.voice_silence_end_ms,
        min_utterance_ms=config.voice_min_utterance_ms, max_utterance_ms=config.voice_max_utterance_ms,
        pre_roll_ms=config.voice_pre_roll_ms, threshold=config.voice_vad_threshold,
        debug_dump_utterance_wav=config.voice_debug_dump_utterance_wav,
        debug_utterance_wav_path=config.resolve_path(config.voice_debug_utterance_wav_path))
    tts_provider = None
    audio_output = None
    if config.tts_enabled:
        tts_provider = PiperTTSProvider(config.tts_local_executable,
                                        config.resolve_path(config.tts_local_model_path),
                                        config.tts_local_speaker_id)
        audio_output = AplayAudioOutputProvider(config.tts_audio_output_player, config.tts_audio_output_device,
                                                 sample_rate=config.tts_audio_output_sample_rate,
                                                 channels=config.tts_audio_output_channels,
                                                 sample_width=config.tts_audio_output_sample_width,
                                                 preroll_ms=config.tts_audio_output_preroll_ms)
    def publish_touch(event) -> None:
        runtime = runtime_holder.get("runtime")
        if not config.touch_enabled or runtime is None or not runtime._started:
            return
        payload = {"kind": event.kind.value, "x": event.x, "y": event.y,
                   "normalized_x": event.normalized_x, "normalized_y": event.normalized_y,
                   "duration_ms": event.duration_ms}
        runtime._touch_status = runtime._touch_status.record(event)
        logger.info("TOUCH EVENT kind=%s x=%s y=%s duration_ms=%s", event.kind.value, event.x, event.y, event.duration_ms)
        asyncio.create_task(core.events.publish(Event(TOUCH_EVENT, payload)))
        asyncio.create_task(core.events.publish(Event(TOUCH_EVENT_NAMES[event.kind.value], payload)))

    def startup_view() -> dict:
        view = startup.document()
        splash_image = config.resolve_startup_splash_image()
        view["splash"] = {"image": str(splash_image),
                          "title": config.startup_splash_title, "subtitle": config.startup_splash_subtitle}
        return view
    eye_render_loop = EyeRenderLoop(
        EyeRenderer(width=config.display_width, height=config.display_height,
                    transition_seconds=config.display_transition_seconds, iris_color=config.iris_color),
        eye_display or TkEyeDisplay(),
        lambda: behavior_engine.face_state,
        fps=config.display_fps,
        fullscreen=config.fullscreen,
        preview_supplier=lambda: _preview_view(vision_holder["pipeline"]),
        preview_settings=_preview_settings(config),
        startup_supplier=startup_view if config.startup_splash_enabled else None,
        touch_handler=publish_touch,
        touch_settings={"tap_max_duration_ms": config.touch_tap_max_duration_ms, "tap_max_movement_px": config.touch_tap_max_movement_px, "long_press_min_duration_ms": config.touch_long_press_min_duration_ms, "long_press_max_movement_px": config.touch_long_press_max_movement_px, "swipe_min_distance_px": config.touch_swipe_min_distance_px, "swipe_max_vertical_drift_px": config.touch_swipe_max_vertical_drift_px, "swipe_max_duration_ms": config.touch_swipe_max_duration_ms},
    )
    core.add_behavior(presence)
    core.add_behavior(attention)
    core.add_behavior(behavior_engine)
    core.add_behavior(eye_render_loop)
    runtime_holder = {}
    environmental_interpreter = EnvironmentalInterpreter(
        _environmental_settings(config),
        sink=lambda state, reason, temperature_overlay, air_quality_overlay: runtime_holder["runtime"].publish_environmental_state(
            state, reason, temperature_overlay, air_quality_overlay),
    )
    injected_vision = vision_pipeline is not None or vision_factory is not None
    resolved_vision = vision_pipeline or (
        vision_factory(core.events) if vision_factory is not None else _build_configured_vision(config, core.events)
    )
    vision_holder["pipeline"] = resolved_vision
    provider_type = environmental_provider_type(config.environmental_type)
    sensors = EnvironmentalSensorService(
        sensor_provider_factory if sensor_provider_factory is not None else
        lambda: provider_type(address=int(config.environmental_i2c_address, 16)),
        sensor_type=config.environmental_type,
        available_measurements=provider_type.available_measurements,
        enabled=config.environmental_enabled,
        poll_interval_seconds=config.environmental_poll_interval_seconds,
        stale_after_seconds=config.environmental_stale_after_seconds,
        reading_sink=lambda reading, now: environmental_interpreter.observe_environmental(reading.temperature_c, now=now),
        unavailable_sink=lambda status: environmental_interpreter.unavailable(now=time.monotonic(),
            source="environmental", status=status),
    )
    if config.environmental_enabled:
        logger.info("%s enabled: I2C bus 1, address %s, polling every %s seconds",
                    config.environmental_type.upper(), config.environmental_i2c_address, config.environmental_poll_interval_seconds)
    air_quality = AirQualitySensorService(
        air_quality_provider_factory if air_quality_provider_factory is not None else
        lambda: CCS811Provider(address=int(config.ccs811_i2c_address, 16)),
        enabled=config.ccs811_enabled, poll_interval_seconds=config.ccs811_poll_interval_seconds,
        stale_after_seconds=config.ccs811_stale_after_seconds, compensation_supplier=sensors.compensation,
        reading_sink=lambda reading, now: environmental_interpreter.observe_air_quality(reading.eco2_ppm, reading.tvoc_ppb, now=now),
        unavailable_sink=lambda status: environmental_interpreter.unavailable(now=time.monotonic(),
            source="air_quality", status=status),
    )
    if config.ccs811_enabled:
        logger.info("CCS811 enabled: I2C bus 1, address %s, polling every %s seconds",
                    config.ccs811_i2c_address, config.ccs811_poll_interval_seconds)
    imu = IMUSensorService(
        imu_provider_factory if imu_provider_factory is not None else
        lambda: MPU6050Provider(address=int(config.imu_i2c_address, 16)),
        enabled=config.imu_enabled, poll_interval_seconds=config.imu_poll_interval_seconds,
        stale_after_seconds=config.imu_stale_after_seconds,
        motion_settings=_motion_settings(config),
        motion_state_sink=lambda state: runtime_holder["runtime"].publish_motion_state(state),
    )
    if config.imu_enabled:
        logger.info("MPU-6050 enabled: I2C bus 1, address %s, polling every %s seconds",
                    config.imu_i2c_address, config.imu_poll_interval_seconds)
    led_ring = LEDRingController(
        _led_settings(config), lambda: behavior_engine.face_state,
        led_ring_provider_factory if led_ring_provider_factory is not None else
        lambda settings: LEDRingSocketProvider(led_count=settings.led_count),
    )
    runtime = PhosRuntime(core, behavior_engine, eye_render_loop, vision_pipeline=resolved_vision,
                       config=config, vision_forced=injected_vision, sensor_service=sensors,
                       air_quality_service=air_quality, imu_service=imu, led_ring_controller=led_ring,
                       presence_interpreter=presence, attention_manager=attention, startup=startup, voice_session=voice,
                       tts_provider=tts_provider, audio_output=audio_output)
    runtime_holder["runtime"] = runtime
    runtime._environmental_interpreter = environmental_interpreter
    return runtime


def _motion_settings(config: RuntimeConfig) -> MotionSettings:
    return MotionSettings(config.imu_motion_movement_threshold_m_s2,
                          config.imu_motion_tilt_threshold_m_s2,
                          config.imu_motion_shake_threshold_deg_s,
                          config.imu_motion_impact_threshold_m_s2,
                          config.imu_motion_confirmation_seconds,
                          config.imu_motion_cooldown_seconds,
                          config.imu_motion_tilt_exit_threshold_m_s2,
                          config.imu_motion_lateral_axis, config.imu_motion_forward_axis)


def _environmental_settings(config: RuntimeConfig) -> EnvironmentalSettings:
    return EnvironmentalSettings(config.environmental_behavior_enabled, config.cold_enter_temperature,
        config.cold_exit_temperature, config.warm_enter_temperature, config.warm_exit_temperature,
        config.air_quality_warning_eco2, config.air_quality_warning_tvoc,
        config.air_quality_bad_eco2, config.air_quality_bad_tvoc,
        config.environmental_confirmation_seconds, config.environmental_recovery_seconds)


def _led_settings(config: RuntimeConfig) -> LEDRingSettings:
    return LEDRingSettings(config.led_ring_enabled, config.led_ring_led_count, config.led_ring_gpio_pin,
                           config.led_ring_brightness, config.led_ring_base_color,
                           config.led_ring_follow_visual_state, config.led_ring_update_rate_hz,
                           config.led_ring_imu_reactions_enabled, config.led_ring_directional_strength,
                           config.led_ring_directional_sector_size, config.led_ring_shake_strength,
                           config.led_ring_impact_strength, config.led_ring_imu_animation_color, config.led_ring_directional_animation_speed,
                           config.led_ring_bottom_led_index, config.led_ring_forward_led_index,
                           config.led_ring_clockwise)


def _log_motion_publish_failure(task):
    if not task.cancelled() and task.exception() is not None:
        logger.exception("IMU motion event handling failed", exc_info=task.exception())


def _build_configured_vision(config: RuntimeConfig, events: EventBus) -> Optional[VisionPipeline]:
    # Construct a dormant owner for later preview reload; constructors do not
    # import camera/OpenCV dependencies or acquire hardware.
    expression_provider = None
    smoother = None
    if config.expression_enabled and config.expression_provider == "aws":
        expression_provider = AWSExpressionProvider(config.cloud_expression, diagnostics=config.expression_diagnostics)
        smoother = ExpressionSmoother(
            minimum_confidence=config.expression_minimum_confidence,
            minimum_observations=config.expression_minimum_observations,
            neutral_enabled=config.expression_neutral_enabled,
            maximum_gap_seconds=config.cloud_expression.cache_ttl_seconds,
        )
    elif config.expression_enabled:
        expression_provider = OpenCVExpressionProvider(
            config.resolve_path(config.expression_model_path),
            config.expression_labels,
            input_size=config.expression_input_size,
            scale=config.expression_scale,
            mean=config.expression_mean,
            swap_rb=config.expression_swap_rb,
            grayscale=config.expression_grayscale,
            diagnostics=config.expression_diagnostics,
        )
        smoother = ExpressionSmoother(
            minimum_confidence=config.expression_minimum_confidence,
            minimum_observations=config.expression_minimum_observations,
            maximum_gap_seconds=config.expression_local_maximum_gap_seconds,
            neutral_enabled=config.expression_neutral_enabled,
        )
    logger.info("Expression provider: %s", config.expression_provider if expression_provider else "disabled")
    return VisionPipeline(
        Picamera2CameraProvider(config.camera_resolution),
        OpenCVFaceDetector(
            config.resolve_path(config.cascade_path), scale_factor=config.detector_scale_factor,
            min_neighbors=config.detector_min_neighbors, min_size=config.detector_min_size,
        ),
        expression_provider,
        smoother,
        events=events,
        capture_interval_seconds=1.0 / config.vision_capture_fps,
        detection_interval_seconds=1.0 / config.face_detection_fps,
        expression_interval_seconds=1.0 / config.expression_inference_fps,
        diagnostics=config.expression_diagnostics,
        crop_margin=config.expression_crop_margin,
        preview_enabled=config.camera_preview_enabled,
        publish_face_position=config.face_tracking_enabled or config.expression_enabled,
        observed_expression_provider=config.expression_provider if config.expression_enabled else None,
        observed_expression_model=(config.resolve_path(config.expression_model_path).name
                                   if config.expression_enabled and config.expression_provider == "local"
                                   and config.expression_model_path is not None else None),
        expression_reaction_policy=ExpressionReactionPolicy(
            enabled=config.expression_reactions_enabled,
            min_confidence=config.expression_reactions_min_confidence,
            confirmation_ms=config.expression_reactions_confirmation_ms,
            cooldown_ms=config.expression_reactions_cooldown_ms,
            reaction_duration_ms=config.expression_reactions_duration_ms,
        ),
    )


def _preview_settings(config):
    return CameraPreviewSettings(config.camera_preview_enabled, config.camera_preview_position,
        config.camera_preview_scale, config.camera_preview_max_fps,
        config.camera_preview_show_face_box, config.camera_preview_show_expression,
        config.camera_preview_show_confidence)


def _preview_view(pipeline):
    snapshot = getattr(pipeline, "preview_snapshot", None) if pipeline is not None else None
    if snapshot is None:
        return None
    face = snapshot.face
    return CameraPreviewView(snapshot.frame,
        None if face is None else (face.x, face.y, face.width, face.height),
        snapshot.raw_expression, snapshot.confidence, snapshot.semantic_expression)
