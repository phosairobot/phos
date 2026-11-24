# SPDX-License-Identifier: Apache-2.0
"""Raspberry Pi Camera adapter."""

from __future__ import annotations

import asyncio
from typing import Any, Tuple

from .provider import CameraProvider


class Picamera2CameraProvider(CameraProvider):
    """Capture 640x480 BGR arrays through Picamera2.

    Picamera2 is imported only when the adapter starts, allowing the rest of
    the project and its tests to run on non-Raspberry Pi machines.
    """

    def __init__(self, resolution: Tuple[int, int] = (640, 480)) -> None:
        if resolution[0] <= 0 or resolution[1] <= 0:
            raise ValueError("Camera resolution values must be positive.")
        self._resolution = resolution
        self._camera: Any = None

    async def start(self) -> None:
        if self._camera is not None:
            return
        try:
            from picamera2 import Picamera2
        except ImportError as error:
            raise RuntimeError("Picamera2 is required for Raspberry Pi Camera capture.") from error

        camera = Picamera2()
        try:
            configuration = camera.create_preview_configuration(
                # On the supported Picamera2 build RGB888 produces BGR array
                # bytes.  Preserve that established camera configuration and
                # expose BGR consistently to the OpenCV-facing pipeline.
                main={"size": self._resolution, "format": "RGB888"}
            )
            camera.configure(configuration)
            camera.start()
        except BaseException:
            # Picamera2 may own resources before configuration/start succeeds.
            camera.close()
            raise
        self._camera = camera

    async def capture_frame(self) -> Any:
        if self._camera is None:
            raise RuntimeError("Camera provider has not been started.")
        return await asyncio.to_thread(self._camera.capture_array, "main")

    async def stop(self) -> None:
        if self._camera is None:
            return
        camera = self._camera
        self._camera = None
        try:
            await asyncio.to_thread(camera.stop)
        finally:
            await asyncio.to_thread(camera.close)
