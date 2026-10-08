"""Start the PHOS robot runtime.

The default renderer opens the face fullscreen on the Pi's HDMI desktop.
"""

from __future__ import annotations

import asyncio
import argparse
import logging
import signal
import sys
from pathlib import Path

# Allow direct execution from a source checkout with
# ``python3 src/robot/main.py`` without a package installation.
SOURCE_DIRECTORY = Path(__file__).resolve().parent.parent
if str(SOURCE_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(SOURCE_DIRECTORY))

from robot import __version__
from robot.config import ConfigRepository, ConfigurationError, RuntimeConfig
from robot.runtime import PhosRuntime, build_runtime
from robot.lifecycle import RESTART_EXIT_CODE

logger = logging.getLogger(__name__)


def build_application(*, config: RuntimeConfig | None = None) -> PhosRuntime:
    """Compose the single PHOS application/runtime coordinator."""
    return build_runtime(config=config)


def _install_shutdown_handlers(loop: asyncio.AbstractEventLoop, stop_event: asyncio.Event) -> None:
    for shutdown_signal in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(shutdown_signal, stop_event.set)
        except NotImplementedError:
            # Signal handlers are unavailable on some event-loop platforms.
            pass


async def async_main(*, config: RuntimeConfig | None = None, lifecycle=None, web_server=None) -> None:
    stop_event = asyncio.Event()
    _install_shutdown_handlers(asyncio.get_running_loop(), stop_event)
    async def watch_restart():
        while not stop_event.is_set():
            if web_server is not None:
                try:
                    web_server.check_running()
                except RuntimeError:
                    stop_event.set()
                    raise
            if lifecycle is not None and lifecycle.restart_due:
                stop_event.set()
                return
            await asyncio.sleep(.1)
    watcher = asyncio.create_task(watch_restart()) if lifecycle is not None or web_server is not None else None
    try:
        runtime = build_application(config=config)
        if lifecycle is not None:
            if hasattr(runtime, "core"):
                from robot.services import PhosApplicationService
                lifecycle.register_application_service(PhosApplicationService(runtime, lifecycle=lifecycle))
            lifecycle.register_appearance_applier(runtime.apply_appearance)
            source_applier = getattr(runtime, "apply_base_visual_source", None)
            if source_applier is not None:
                lifecycle.register_base_visual_source_applier(source_applier)
            overlay_applier = getattr(runtime, "apply_environment_overlays", None)
            if overlay_applier is not None:
                lifecycle.register_environment_overlays_applier(overlay_applier)
            preview_applier = getattr(runtime, "apply_camera_preview", None)
            if preview_applier is not None:
                lifecycle.register_camera_preview_applier(preview_applier)
            motion_applier = getattr(runtime, "apply_imu_motion", None)
            if motion_applier is not None:
                lifecycle.register_imu_motion_applier(motion_applier)
            behavior_applier = getattr(runtime, "apply_imu_behavior", None)
            if behavior_applier is not None:
                lifecycle.register_imu_behavior_applier(behavior_applier)
            led_ring_applier = getattr(runtime, "apply_led_ring", None)
            if led_ring_applier is not None:
                lifecycle.register_led_ring_applier(led_ring_applier)
            environmental_applier = getattr(runtime, "apply_environmental_behavior", None)
            if environmental_applier is not None:
                lifecycle.register_environmental_behavior_applier(environmental_applier)
            sensor_status = getattr(runtime, "sensor_status", None)
            if sensor_status is not None:
                lifecycle.register_sensor_status(sensor_status)
            application_status = getattr(runtime, "application_status", None)
            if application_status is not None:
                lifecycle.register_application_status(application_status)
        await runtime.run(stop_event)
        if watcher is not None and watcher.done():
            watcher.result()
    finally:
        if watcher is not None:
            watcher.cancel()
            await asyncio.gather(watcher, return_exceptions=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Start the PHOS robot runtime.",
                                     argument_default=argparse.SUPPRESS,
                                     epilog="All individual setting flags are deprecated overrides; edit the active configuration file instead.")
    parser.add_argument("--config", type=Path, help="explicit PHOS configuration file; otherwise discover phos.yaml, phos.yml, then phos.json")
    parser.add_argument("--expression-provider", choices=("local", "aws"), help="deprecated: override expression.provider and enable expressions")
    parser.add_argument("--aws-region", help="deprecated: override expression.aws.region")
    parser.add_argument(
        "--face-tracking",
        action="store_true",
        help="enable Raspberry Pi Camera face tracking without expression classification",
    )
    parser.add_argument(
        "--expression-model",
        type=Path,
        help="path to an ONNX visible-expression model; also enables camera Vision",
    )
    parser.add_argument(
        "--expression-labels",
        help="comma-separated output labels, in the exact model-output order",
    )
    parser.add_argument(
        "--expression-input-size",
        help="ONNX model input size as WIDTHxHEIGHT (overrides expression.local.input_size)",
    )
    parser.add_argument(
        "--expression-scale",
        type=float,
        help="OpenCV DNN image scale for the expression model (overrides expression.local.scale)",
    )
    parser.add_argument(
        "--expression-mean",
        help="three OpenCV DNN image-mean values (overrides expression.local.mean)",
    )
    parser.add_argument(
        "--expression-no-swap-rb",
        action="store_true",
        help="do not swap BGR camera channels to RGB before expression inference",
    )
    parser.add_argument(
        "--expression-grayscale",
        action="store_true",
        help="convert RGB face crops to one-channel grayscale before expression inference",
    )
    parser.add_argument(
        "--expression-debug",
        action="store_true",
        help="log in-memory face-crop, blob, output, and smoothing diagnostics; no images are saved",
    )
    parser.add_argument(
        "--expression-crop-margin", type=float,
        help="square face crop margin per side, as a face-size fraction (0 to 0.5)",
    )
    arguments = vars(parser.parse_args())
    config_path = arguments.pop("config", None)
    try:
        # Compatibility flags become typed overrides of the one JSON model.
        # No flag supplies a separate default or persists its override.
        aliases = {"face_tracking": "face_tracking_enabled", "expression_model": "expression_model_path",
                   "expression_debug": "expression_diagnostics"}
        overrides = {aliases.get(name, name): value for name, value in arguments.items()}
        if "expression_labels" in overrides:
            overrides["expression_labels"] = tuple(v.strip() for v in overrides["expression_labels"].split(",") if v.strip())
        if "expression_input_size" in overrides:
            overrides["expression_input_size"] = tuple(int(v) for v in overrides["expression_input_size"].lower().split("x"))
        if "expression_mean" in overrides:
            overrides["expression_mean"] = tuple(float(v) for v in overrides["expression_mean"].split(","))
        if "expression_no_swap_rb" in overrides:
            overrides["expression_swap_rb"] = not overrides.pop("expression_no_swap_rb")
        if "expression_model_path" in overrides:
            overrides["expression_model_path"] = overrides["expression_model_path"].resolve()
            overrides["expression_enabled"] = True
        if "expression_provider" in overrides:
            overrides["expression_enabled"] = True
        # Region is an ordinary nested setting, resolved before model construction.
        repository = ConfigRepository(config_path)
        document = repository.document()
        if "aws_region" in overrides:
            if not isinstance(document, dict) or not isinstance(document.get("expression"), dict) or not isinstance(document["expression"].get("aws"), dict):
                raise ConfigurationError("expression.aws section is required")
            document["expression"]["aws"]["region"] = overrides.pop("aws_region")
        config = repository.load(document, overrides=overrides)
    except (ValueError, TypeError, OSError) as error:
        parser.error(str(error))
    handlers = [logging.StreamHandler(sys.stdout)]
    if config.log_file is not None:
        try:
            handlers.append(logging.FileHandler(config.resolve_path(config.log_file), encoding="utf-8"))
        except OSError:
            parser.error("Cannot open logging.file; check its path and permissions")
    logging.basicConfig(level=config.log_level, format="%(asctime)s %(levelname)s %(message)s", handlers=handlers)
    # Prevent SDK debug logs from exposing credential/signature details even when
    # PHOS diagnostics are enabled. Adapter logs contain only sanitized metadata.
    logging.getLogger("boto3").setLevel(logging.WARNING)
    logging.getLogger("botocore").setLevel(logging.WARNING)
    if arguments:
        logger.warning("Individual runtime CLI flags are deprecated; edit %s instead", repository.active_path)
    logger.info("PHOS %s", __version__)
    logger.info("PHOS configuration loaded: %s", repository.active_path)
    # The optional web worker is isolated from camera/rendering and is stopped
    # even when runtime startup or execution fails.
    from robot.web.server import WebServer
    with WebServer(repository, config) as web:
        asyncio.run(async_main(config=config, lifecycle=web.lifecycle, web_server=web))
    if web.lifecycle.restart_at is not None:
        raise SystemExit(RESTART_EXIT_CODE)



if __name__ == "__main__":
    main()
