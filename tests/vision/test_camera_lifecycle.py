"""Picamera2 resource ownership tested without the vendor package or hardware."""
import asyncio
import sys
from types import SimpleNamespace

import numpy as np
import pytest

from robot.vision.camera import Picamera2CameraProvider


@pytest.mark.parametrize("failure", ["configure", "start", "stop"])
def test_camera_closes_after_driver_failure(monkeypatch, failure):
    calls = []
    def record(name):
        calls.append(name)
        if name == failure:
            raise RuntimeError(name)
    driver = SimpleNamespace(create_preview_configuration=lambda **kw: {},
                             configure=lambda config: record("configure"),
                             start=lambda: record("start"), stop=lambda: record("stop"),
                             close=lambda: record("close"))
    monkeypatch.setitem(sys.modules, "picamera2", SimpleNamespace(Picamera2=lambda: driver))
    async def exercise():
        camera = Picamera2CameraProvider()
        with pytest.raises(RuntimeError, match=failure):
            await camera.start()
            await camera.stop()
        await camera.stop()  # Repeated shutdown must not reuse the closed driver.
        assert camera._camera is None
    asyncio.run(exercise())
    assert calls[-1] == "close" and calls.count("close") == 1


def test_camera_provider_preserves_picamera_bgr_capture_bytes(monkeypatch):
    captured = np.array([[[0, 0, 255], [255, 0, 0], [0, 255, 0]]], dtype=np.uint8)
    configurations = []
    driver = SimpleNamespace(
        create_preview_configuration=lambda **kwargs: configurations.append(kwargs) or kwargs,
        configure=lambda _config: None,
        start=lambda: None,
        stop=lambda: None,
        close=lambda: None,
        capture_array=lambda stream: captured,
    )
    monkeypatch.setitem(sys.modules, "picamera2", SimpleNamespace(Picamera2=lambda: driver))

    async def exercise():
        camera = Picamera2CameraProvider((3, 1))
        await camera.start()
        frame = await camera.capture_frame()
        await camera.stop()
        return frame

    frame = asyncio.run(exercise())
    assert configurations == [{"main": {"size": (3, 1), "format": "RGB888"}}]
    np.testing.assert_array_equal(frame, captured)
    # BGR representation of pure red, pure blue, and pure green respectively.
    assert frame[0].tolist() == [[0, 0, 255], [255, 0, 0], [0, 255, 0]]
