"""Display adapters for rendered PHOS eye geometry."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
import logging
import os
import time
from typing import Any, Deque, List, Optional

from .eyes import EyeFrame, EyeGeometry
from .touch import TouchInputAdapter

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AmbientOverlayLayout:
    """Central, eye-relative geometry shared by all ambient overlays."""

    anchor_x: float
    anchor_y: float
    safe_top: float
    safe_bottom: float
    sweat: tuple[tuple[float, float, float, float], ...] = ()
    snow: Optional[tuple[float, float, float]] = None
    haze: tuple[tuple[float, float, float, float], ...] = ()


def ambient_overlay_layout(frame: EyeFrame) -> AmbientOverlayLayout:
    """Return bounded central overlay geometry without drawing it.

    The band is measured from the current upper eye edge, so visual overlays
    remain attached to the face as eye openness changes and never enter the
    eye/eyelid area.
    """
    w, h = frame.width, frame.height
    eye_top = min(eye.center_y - eye.radius_y for eye in frame.eyes)
    safe_top = max(0.0, eye_top - h * .13)
    safe_bottom = max(safe_top, eye_top - h * .015)
    anchor_x = w / 2
    anchor_y = max(safe_top + h * .027, eye_top - h * .09)
    sweat: tuple[tuple[float, float, float, float], ...] = ()
    snow: Optional[tuple[float, float, float]] = None
    haze: tuple[tuple[float, float, float, float], ...] = ()
    if frame.ambient_overlay.temperature == "warm":
        sweat = (
            (anchor_x - w * .035, anchor_y - h * .012, w * .018, h * .025),
            (anchor_x + w * .025, anchor_y + h * .010, w * .014, h * .020),
        )
    elif frame.ambient_overlay.temperature == "cold":
        snow = (anchor_x, anchor_y, min(w, h) * .030)
    if frame.ambient_overlay.air_quality != "none":
        count = 2 if frame.ambient_overlay.air_quality == "warning" else 3
        band_height = h * .018
        band_spacing = h * .010
        haze_start = safe_bottom - band_height - band_spacing * (count - 1)
        haze = tuple(
            (anchor_x - w * .13, haze_start + index * band_spacing,
             anchor_x + w * .13, haze_start + band_height + index * band_spacing)
            for index in range(count)
        )
    return AmbientOverlayLayout(anchor_x, anchor_y, safe_top, safe_bottom, sweat, snow, haze)


@dataclass(frozen=True)
class CameraPreviewView:
    """UI-neutral, in-memory image and already-produced Vision diagnostics."""
    frame: Any
    face_box: Optional[tuple[int, int, int, int]] = None
    expression: Optional[str] = None
    confidence: Optional[float] = None
    semantic_expression: Optional[str] = None


@dataclass(frozen=True)
class CameraPreviewSettings:
    enabled: bool = False
    position: str = "bottom_right"
    scale: float = .25
    max_fps: int = 5
    show_face_box: bool = True
    show_expression: bool = True
    show_confidence: bool = True


class EyeDisplay(ABC):
    """Display-driver boundary for precomputed eye geometry."""

    @abstractmethod
    def open(self, width: int, height: int, *, fullscreen: bool) -> None:
        raise NotImplementedError

    @abstractmethod
    def draw(self, frame: EyeFrame, preview: Optional[CameraPreviewView] = None,
             preview_settings: Optional[CameraPreviewSettings] = None) -> None:
        raise NotImplementedError

    @abstractmethod
    def draw_startup(self, *, state: str, message: str, image_path=None, title="PHOS", subtitle="Starting...") -> None:
        raise NotImplementedError

    @abstractmethod
    def poll_keys(self) -> List[str]:
        raise NotImplementedError

    @abstractmethod
    def close(self) -> None:
        raise NotImplementedError


class MemoryEyeDisplay(EyeDisplay):
    """Headless display adapter used by tests."""

    def __init__(self) -> None:
        self.frames: List[EyeFrame] = []
        self.startup_frames: List[tuple[str, str]] = []

    def open(self, width: int, height: int, *, fullscreen: bool) -> None:
        return None

    def draw(self, frame: EyeFrame, preview: Optional[CameraPreviewView] = None,
             preview_settings: Optional[CameraPreviewSettings] = None) -> None:
        self.frames.append(frame)

    def draw_startup(self, *, state: str, message: str, image_path=None, title="PHOS", subtitle="Starting...") -> None:
        self.startup_frames.append((state, message))

    def poll_keys(self) -> List[str]:
        return []

    def close(self) -> None:
        return None


class TkEyeDisplay(EyeDisplay):
    """Lightweight fullscreen Tkinter display for the HDMI desktop."""

    def __init__(self) -> None:
        self._root = None
        self._canvas = None
        self._tk = None
        self._keys: Deque[str] = deque()
        self._preview_executor = None
        self._preview_future: Optional[Future] = None
        self._preview_photo = None
        self._preview_next_at = 0.0
        self._preview_failed = False
        self._preview_image_item = None
        self._preview_overlay_items = []
        self._startup_photo = None
        self._startup_image_item = None
        self._startup_image_path = None
        self._touch_adapter = None

    def set_touch_handler(self, handler, **settings) -> None:
        self._touch_adapter = TouchInputAdapter(handler, **settings)

    def open(self, width: int, height: int, *, fullscreen: bool) -> None:
        if self._root is not None:
            return
        try:
            import tkinter as tk
        except ImportError as error:
            raise RuntimeError("Tkinter is required to display PHOS eyes.") from error
        try:
            root = tk.Tk()
        except tk.TclError as error:
            raise RuntimeError("Could not open the PHOS HDMI display from this session.") from error
        root.title("PHOS")
        root.geometry(f"{width}x{height}+0+0")
        root.attributes("-fullscreen", fullscreen)
        root.bind("<Escape>", lambda _event: root.attributes("-fullscreen", False))
        root.bind("<Key>", lambda event: self._keys.append(event.keysym))
        canvas = tk.Canvas(root, highlightthickness=0, borderwidth=0)
        canvas.pack(fill=tk.BOTH, expand=True)
        canvas.configure(cursor="none")
        canvas.bind("<ButtonPress-1>", lambda event: self._touch_adapter and self._touch_adapter.down(event.x, event.y))
        canvas.bind("<ButtonRelease-1>", lambda event: self._touch_adapter and self._touch_adapter.release(event.x, event.y))
        self._root = root
        self._canvas = canvas
        self._tk = tk
        self._preview_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="phos-preview")

    def draw(self, frame: EyeFrame, preview: Optional[CameraPreviewView] = None,
             preview_settings: Optional[CameraPreviewSettings] = None) -> None:
        if self._root is None or self._canvas is None:
            raise RuntimeError("Display has not been opened.")
        self._canvas.delete("all")
        self._startup_image_item = None
        self._startup_image_path = None
        self._startup_photo = None
        self._canvas.configure(background=frame.background)
        for eye in frame.eyes:
            self._draw_eye(eye, frame)
        self._draw_ambient_overlay(frame)
        settings = preview_settings or CameraPreviewSettings()
        if preview is not None and settings.enabled:
            self._draw_preview(preview, settings, frame.width, frame.height)
        else:
            self._preview_photo = None
            self._preview_image_item = None
            self._preview_overlay_items = []
            if self._preview_future is not None:
                self._preview_future.cancel()
                self._preview_future = None

    def draw_startup(self, *, state: str, message: str, image_path=None, title="PHOS", subtitle="Starting...") -> None:
        if self._root is None or self._canvas is None:
            raise RuntimeError("Display has not been opened.")
        if self._startup_image_item is not None and self._startup_image_path == image_path:
            return
        self._canvas.delete("all")
        self._canvas.configure(background="#02050D")
        if image_path is None:
            logger.warning("STARTUP SPLASH LOAD FAILED: path=None exception=no splash image configured")
            return
        try:
            from pathlib import Path
            path = Path(image_path)
            root_geometry = self._root.winfo_geometry() if hasattr(self._root, "winfo_geometry") else "unknown"
            canvas_width = self._canvas.winfo_width() if hasattr(self._canvas, "winfo_width") else "unknown"
            canvas_height = self._canvas.winfo_height() if hasattr(self._canvas, "winfo_height") else "unknown"
            logger.info("STARTUP SPLASH root_geometry=%s", root_geometry)
            logger.info("STARTUP SPLASH canvas_size=%sx%s", canvas_width, canvas_height)
            logger.info("STARTUP SPLASH LOADER: resolved_path=%s exists=%s readable=%s", path, path.is_file(), path.is_file() and os.access(path, os.R_OK))
            photo = self._tk.PhotoImage(file=str(path))
            logger.info("STARTUP SPLASH source_size=%sx%s", photo.width(), photo.height())
            if (photo.width(), photo.height()) != (800, 600):
                raise RuntimeError("startup splash must be a pre-rendered 800x600 PNG")
            self._startup_photo = photo
            self._startup_image_path = image_path
            self._startup_image_item = self._canvas.create_image(400, 300, image=photo, anchor="center")
            logger.info("STARTUP SPLASH resized_size=800x600")
            logger.info("STARTUP SPLASH draw_position=(400,300)")
            logger.info("STARTUP SPLASH anchor=center item=%s", self._startup_image_item)
        except Exception as error:
            logger.warning("STARTUP SPLASH LOAD FAILED: path=%s exception=%s", image_path, error)

    def poll_keys(self) -> List[str]:
        if self._root is not None:
            self._root.update_idletasks()
            self._root.update()
        keys = list(self._keys)
        self._keys.clear()
        return keys

    def close(self) -> None:
        if self._root is not None:
            try:
                self._root.destroy()
            except self._tk.TclError:
                pass
        self._root = None
        self._canvas = None
        self._tk = None
        self._preview_photo = None
        self._startup_photo = None
        self._startup_image_item = None
        self._startup_image_path = None
        if self._preview_executor is not None:
            self._preview_executor.shutdown(wait=False, cancel_futures=True)
            self._preview_executor = None
        self._preview_future = None

    def _draw_ambient_overlay(self, frame: EyeFrame) -> None:
        overlay = frame.ambient_overlay
        layout = ambient_overlay_layout(frame)
        if overlay.temperature == "warm":
            for x, y, rx, ry in layout.sweat:
                self._canvas.create_oval(x-rx, y-ry, x+rx, y+ry, fill="#7FE8FF", outline="")
        elif overlay.temperature == "cold":
            x, y, radius = layout.snow
            for dx, dy in ((-radius, 0), (radius, 0), (0, -radius), (0, radius), (-radius*.72, -radius*.72), (radius*.72, radius*.72)):
                self._canvas.create_line(x-dx, y-dy, x+dx, y+dy, fill="#B9E9FF", width=2)
        if overlay.air_quality != "none":
            color = "#D99B4B" if overlay.air_quality == "warning" else "#C76550"
            for x1, y1, x2, y2 in layout.haze:
                self._canvas.create_arc(x1, y1, x2, y2, start=190, extent=160, style=self._tk.ARC, outline=color, width=3)

    def _draw_eye(self, eye: EyeGeometry, frame: EyeFrame) -> None:
        if eye.closed:
            self._canvas.create_arc(
                eye.center_x - eye.radius_x,
                eye.center_y - 18,
                eye.center_x + eye.radius_x,
                eye.center_y + 24,
                start=200,
                extent=140,
                style=self._tk.ARC,
                outline=frame.eye_color,
                width=12,
            )
            return
        # A compact stack of Canvas primitives gives the eye body and iris
        # depth without raster assets, per-frame filters, or external graphics.
        self._canvas.create_oval(
            eye.center_x - eye.radius_x,
            eye.center_y - eye.radius_y + 7,
            eye.center_x + eye.radius_x,
            eye.center_y + eye.radius_y + 7,
            fill="#071522",
            outline="",
        )
        self._canvas.create_oval(
            eye.center_x - eye.radius_x,
            eye.center_y - eye.radius_y,
            eye.center_x + eye.radius_x,
            eye.center_y + eye.radius_y,
            fill="#8FA8B8",
            outline="#526C80",
            width=3,
        )
        inset = 5
        self._canvas.create_oval(
            eye.center_x - eye.radius_x + inset,
            eye.center_y - eye.radius_y + inset,
            eye.center_x + eye.radius_x - inset,
            eye.center_y + eye.radius_y - inset,
            fill=frame.eye_color,
            outline="",
        )
        self._canvas.create_oval(
            eye.pupil_x - eye.iris_radius,
            eye.pupil_y - eye.iris_radius,
            eye.pupil_x + eye.iris_radius,
            eye.pupil_y + eye.iris_radius,
            fill="#123246",
            outline="",
        )
        iris_inset = max(1.5, eye.iris_radius * 0.10)
        self._canvas.create_oval(
            eye.pupil_x - eye.iris_radius + iris_inset,
            eye.pupil_y - eye.iris_radius + iris_inset,
            eye.pupil_x + eye.iris_radius - iris_inset,
            eye.pupil_y + eye.iris_radius - iris_inset,
            fill=frame.iris_color,
            outline="",
        )
        # The dark pupil is distinct from the colored iris; small glints sell
        # a glassy surface without a blur/filter pass.
        self._canvas.create_oval(
            eye.pupil_x - eye.pupil_radius,
            eye.pupil_y - eye.pupil_radius,
            eye.pupil_x + eye.pupil_radius,
            eye.pupil_y + eye.pupil_radius,
            fill=frame.pupil_color,
            outline="#071522",
            width=2,
        )
        highlight = max(2.0, eye.pupil_radius * 0.24)
        self._canvas.create_oval(
            eye.pupil_x - eye.iris_radius * 0.33 - highlight,
            eye.pupil_y - eye.iris_radius * 0.33 - highlight,
            eye.pupil_x - eye.iris_radius * 0.33 + highlight,
            eye.pupil_y - eye.iris_radius * 0.33 + highlight,
            fill="#FFFFFF",
            outline="",
        )
        glint = max(1.2, highlight * 0.42)
        self._canvas.create_oval(
            eye.pupil_x + eye.iris_radius * 0.30 - glint,
            eye.pupil_y + eye.iris_radius * 0.28 - glint,
            eye.pupil_x + eye.iris_radius * 0.30 + glint,
            eye.pupil_y + eye.iris_radius * 0.28 + glint,
            fill="#D9FBFF",
            outline="",
        )

    def _draw_preview(self, preview, settings, display_width, display_height):
        now = time.monotonic()
        if self._preview_future is not None and self._preview_future.done():
            try:
                _width, _height, encoded = self._preview_future.result()
                self._preview_photo = self._tk.PhotoImage(data=encoded, format="PPM")
                self._preview_failed = False
            except Exception:
                self._preview_photo = None
                if not self._preview_failed:
                    logger.exception("Could not render camera preview")
                self._preview_failed = True
            self._preview_future = None
        if self._preview_future is None and now >= self._preview_next_at:
            shape = getattr(preview.frame, "shape", ())
            if len(shape) >= 2:
                target_width = max(80, int(display_width * settings.scale))
                self._preview_future = self._preview_executor.submit(_encode_preview_ppm, preview.frame, target_width)
                self._preview_next_at = now + 1.0 / settings.max_fps
        if self._preview_photo is None:
            return
        image_width, image_height = self._preview_photo.width(), self._preview_photo.height()
        margin = 16
        x = margin if "left" in settings.position else display_width - image_width - margin
        y = margin if settings.position.startswith("top") else display_height - image_height - margin
        self._canvas.create_rectangle(x - 3, y - 3, x + image_width + 3, y + image_height + 3,
                                      fill="#06111b", outline="#61d8e8", width=2)
        self._canvas.create_image(x, y, image=self._preview_photo, anchor="nw")
        shape = getattr(preview.frame, "shape", ())
        if settings.show_face_box and preview.face_box and len(shape) >= 2:
            sx, sy = image_width / shape[1], image_height / shape[0]
            fx, fy, fw, fh = preview.face_box
            self._canvas.create_rectangle(x + fx * sx, y + fy * sy,
                x + (fx + fw) * sx, y + (fy + fh) * sy, outline="#ffe66d", width=2)
        if settings.show_expression:
            labels = []
            if preview.expression:
                label = preview.expression
                if settings.show_confidence and preview.confidence is not None:
                    label += f" {preview.confidence:.0%}"
                labels.append(f"Raw: {label}")
            if preview.semantic_expression:
                labels.append(f"PHOS: {preview.semantic_expression}")
            for index, label in enumerate(labels):
                self._canvas.create_text(x + 5, y + image_height - 5 - index * 17, text=label,
                    anchor="sw", fill="white", font=("TkDefaultFont", 9, "bold"))


def _encode_preview_ppm(frame, target_width):
    """Resize and encode off the Tk/display thread; only one job can be pending."""
    import cv2
    height, width = frame.shape[:2]
    target_height = max(1, round(height * target_width / width))
    small = cv2.resize(frame, (target_width, target_height), interpolation=cv2.INTER_AREA)
    header = f"P6 {target_width} {target_height} 255\n".encode("ascii")
    # Tk's PPM reader requires raw binary data, not base64 text.
    return target_width, target_height, header + small.tobytes()
