"""Display adapters for rendered PHOS eye geometry."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
import base64
import math
import struct
import zlib
import logging
import os
import time
from typing import Any, Deque, List, Optional

from .eyes import EyeFrame, EyeGeometry
from .backgrounds import BackgroundAsset
from .state_effects import StateEffectParams
from .state_effect_layer import StateEffectLayer
from .eye_safe import EyeSafeLayer, EyeSafeParams
from .touch import TouchInputAdapter
from robot.admin_access import AdminOverlayState

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


@dataclass(frozen=True)
class RecoveryOverlayLayout:
    """Canvas-relative geometry for the physical recovery overlay."""

    card_x: float
    card_y: float
    card_width: float
    card_height: float
    title_y: float
    message_y: float
    code_y: float
    countdown_y: float

    @property
    def card_right(self) -> float:
        return self.card_x + self.card_width

    @property
    def card_bottom(self) -> float:
        return self.card_y + self.card_height


def recovery_overlay_layout(width: int, height: int) -> RecoveryOverlayLayout:
    """Return a centered, safely inset recovery card for the current canvas."""
    margin_x, margin_y = max(24.0, width * .06), max(24.0, height * .08)
    card_width = min(width * .64, width - 2 * margin_x)
    card_height = min(height * .56, height - 2 * margin_y)
    card_x, card_y = (width - card_width) / 2, (height - card_height) / 2
    return RecoveryOverlayLayout(
        card_x, card_y, card_width, card_height,
        card_y + card_height * .22,
        card_y + card_height * .36,
        card_y + card_height * .58,
        card_y + card_height * .78,
    )


@dataclass(frozen=True)
class AdminAccessOverlayLayout:
    card_x: float
    card_y: float
    card_width: float
    card_height: float
    qr_x: float
    qr_y: float
    qr_size: int
    title_y: float
    url_y: float
    ip_y: float
    close_y: float

    @property
    def card_right(self) -> float: return self.card_x + self.card_width

    @property
    def card_bottom(self) -> float: return self.card_y + self.card_height


def admin_access_overlay_layout(width: int, height: int, modules: int) -> AdminAccessOverlayLayout:
    """Return a safely inset, centered layout for the QR Admin overlay."""
    margin_x, margin_y = max(48.0, width * .08), max(48.0, height * .12)
    card_width = min(width * .76, width - 2 * margin_x)
    card_height = min(height * .76, height - 2 * margin_y)
    card_x, card_y = (width - card_width) / 2, (height - card_height) / 2
    module = max(1, int(min(card_width * .60, card_height * .54) // modules))
    qr_size = module * modules
    return AdminAccessOverlayLayout(
        card_x, card_y, card_width, card_height,
        (width - qr_size) / 2, card_y + card_height * .18, qr_size,
        card_y + card_height * .09, card_y + card_height * .76,
        card_y + card_height * .84, card_y + card_height * .91,
    )


class EyeDisplay(ABC):
    """Display-driver boundary for precomputed eye geometry."""

    @abstractmethod
    def open(self, width: int, height: int, *, fullscreen: bool) -> None:
        raise NotImplementedError

    @abstractmethod
    def draw(self, frame: EyeFrame, preview: Optional[CameraPreviewView] = None,
             preview_settings: Optional[CameraPreviewSettings] = None, *, background: Optional[BackgroundAsset] = None,
             eye_safe: Optional[EyeSafeParams] = None, state_effect: Optional[StateEffectParams] = None, effect_timestamp: float = 0.0) -> None:
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

    def set_admin_overlay(self, overlay: AdminOverlayState) -> None:
        """Receive local-only administrative content on the display thread.

        Displays which do not render an overlay may safely retain the default.
        Keeping this outside ``draw`` preserves the existing adapter contract.
        """


class MemoryEyeDisplay(EyeDisplay):
    """Headless display adapter used by tests."""

    def __init__(self) -> None:
        self.frames: List[EyeFrame] = []
        self.startup_frames: List[tuple[str, str]] = []
        self.admin_overlays: List[AdminOverlayState] = []

    def open(self, width: int, height: int, *, fullscreen: bool) -> None:
        return None

    def draw(self, frame: EyeFrame, preview: Optional[CameraPreviewView] = None,
             preview_settings: Optional[CameraPreviewSettings] = None, *, background: Optional[BackgroundAsset] = None,
             eye_safe: Optional[EyeSafeParams] = None, state_effect: Optional[StateEffectParams] = None, effect_timestamp: float = 0.0) -> None:
        self.frames.append(frame)

    def draw_startup(self, *, state: str, message: str, image_path=None, title="PHOS", subtitle="Starting...") -> None:
        self.startup_frames.append((state, message))

    def poll_keys(self) -> List[str]:
        return []

    def close(self) -> None:
        return None

    def set_admin_overlay(self, overlay: AdminOverlayState) -> None:
        self.admin_overlays.append(overlay)


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
        self._background_photo = None
        self._background_key = None
        self._background_failed = set()
        self._state_effect_layer = StateEffectLayer()
        self._eye_safe_layer = EyeSafeLayer()
        self._eye_safe_photos = {}
        self._state_effect_failed = set()
        self._state_effect_photos = {}
        self._eye_safe_photos = {}
        self._admin_overlay = AdminOverlayState()

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
             preview_settings: Optional[CameraPreviewSettings] = None, *, background: Optional[BackgroundAsset] = None,
             eye_safe: Optional[EyeSafeParams] = None, state_effect: Optional[StateEffectParams] = None, effect_timestamp: float = 0.0) -> None:
        if self._root is None or self._canvas is None:
            raise RuntimeError("Display has not been opened.")
        self._canvas.delete("all")
        self._startup_image_item = None
        self._startup_image_path = None
        self._startup_photo = None
        self._canvas.configure(background=frame.background)
        self._draw_background(background, frame.width, frame.height)
        self._draw_eye_safe_layer(eye_safe, frame, background)
        self._draw_state_effect(state_effect, frame.width, frame.height, effect_timestamp)
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
        self._draw_admin_overlay(frame)

    def set_admin_overlay(self, overlay: AdminOverlayState) -> None:
        previous = self._admin_overlay
        self._admin_overlay = overlay
        if overlay.mode == "admin_access" and previous.mode != "admin_access" and overlay.qr_matrix:
            width, height = self._admin_canvas_dimensions(None)
            layout = admin_access_overlay_layout(width, height, len(overlay.qr_matrix))
            logger.debug("PHOS ADMIN: overlay geometry canvas=%sx%s card=(%.0f,%.0f)-(%.0f,%.0f) qr=(%.0f,%.0f)-(%.0f,%.0f) safe_margin=(%.0f,%.0f)",
                         width, height, layout.card_x, layout.card_y, layout.card_right, layout.card_bottom,
                         layout.qr_x, layout.qr_y, layout.qr_x + layout.qr_size, layout.qr_y + layout.qr_size,
                         width * .08, height * .12)

    def _admin_canvas_dimensions(self, frame: EyeFrame | None) -> tuple[int, int]:
        """Use Tk's usable canvas size; headless tests fall back to EyeFrame."""
        canvas = self._canvas
        try:
            width, height = int(canvas.winfo_width()), int(canvas.winfo_height())
            if width > 1 and height > 1:
                return width, height
        except (AttributeError, TypeError, ValueError):
            pass
        if frame is None:
            return 800, 600
        return frame.width, frame.height

    def _draw_admin_overlay(self, frame: EyeFrame) -> None:
        """Draw the final, local-only administrative layer above all content."""
        overlay = self._admin_overlay
        if overlay.mode == "admin_access":
            self._draw_admin_access_overlay(frame, overlay)
            return
        if overlay.mode != "recovery" or overlay.recovery_code is None:
            return
        expires_at = overlay.recovery_expires_at
        remaining = max(0, int((expires_at or 0) - time.monotonic()))
        minutes, seconds = divmod(remaining, 60)
        canvas = self._canvas
        layout = recovery_overlay_layout(frame.width, frame.height)
        canvas.create_rectangle(0, 0, frame.width, frame.height, fill="#020B1C", outline="")
        canvas.create_rectangle(layout.card_x, layout.card_y, layout.card_right, layout.card_bottom,
                                fill="#071B35", outline="#00C9FF", width=2)
        canvas.create_text(frame.width / 2, layout.title_y, text="PHOS ADMIN", fill="#00C9FF",
                           font=("TkDefaultFont", max(18, int(frame.height * .045)), "bold"))
        canvas.create_text(frame.width / 2, layout.message_y, text="Password recovery code", fill="#DDEEFF",
                           font=("TkDefaultFont", max(14, int(frame.height * .030))))
        canvas.create_text(frame.width / 2, layout.code_y, text=overlay.recovery_code, fill="#FFFFFF",
                           font=("TkFixedFont", max(30, int(frame.height * .085)), "bold"))
        canvas.create_text(frame.width / 2, layout.countdown_y, text=f"Expires in {minutes:02d}:{seconds:02d}",
                           fill="#9AC9E8", font=("TkDefaultFont", max(13, int(frame.height * .028))))

    def _draw_admin_access_overlay(self, frame: EyeFrame, overlay: AdminOverlayState) -> None:
        """Draw a crisp, matrix-based QR code without Pillow or image scaling."""
        matrix, url = overlay.qr_matrix, overlay.admin_url
        if not matrix or not url:
            return
        width, height = self._admin_canvas_dimensions(frame)
        modules = len(matrix)
        layout = admin_access_overlay_layout(width, height, modules)
        module = layout.qr_size // modules
        canvas = self._canvas
        canvas.create_rectangle(0, 0, width, height, fill="#020B1C", outline="")
        canvas.create_rectangle(layout.card_x, layout.card_y, layout.card_right, layout.card_bottom,
                                fill="#071B35", outline="#00C9FF", width=2)
        canvas.create_text(width / 2, layout.title_y, text="PHOS ADMIN", fill="#00C9FF",
                           font=("TkDefaultFont", max(18, int(height * .042)), "bold"))
        canvas.create_rectangle(layout.qr_x, layout.qr_y, layout.qr_x + layout.qr_size, layout.qr_y + layout.qr_size, fill="#FFFFFF", outline="")
        for row, values in enumerate(matrix):
            for column, enabled in enumerate(values):
                if enabled:
                    x, y = layout.qr_x + column * module, layout.qr_y + row * module
                    canvas.create_rectangle(x, y, x + module, y + module, fill="#02050D", outline="")
        ip = url.removeprefix("http://").split(":", 1)[0]
        canvas.create_text(width / 2, layout.url_y, text=url, fill="#DDEEFF",
                           font=("TkFixedFont", max(12, int(height * .024))))
        canvas.create_text(width / 2, layout.ip_y, text=f"IP: {ip}", fill="#9AC9E8",
                           font=("TkDefaultFont", max(12, int(height * .024))))
        canvas.create_text(width / 2, layout.close_y, text="Touch to close", fill="#9AC9E8",
                           font=("TkDefaultFont", max(12, int(height * .022))))

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
            logger.debug("STARTUP SPLASH root_geometry=%s", root_geometry)
            logger.debug("STARTUP SPLASH canvas_size=%sx%s", canvas_width, canvas_height)
            logger.debug("STARTUP SPLASH LOADER: resolved_path=%s exists=%s readable=%s", path, path.is_file(), path.is_file() and os.access(path, os.R_OK))
            photo = self._tk.PhotoImage(file=str(path))
            logger.debug("STARTUP SPLASH source_size=%sx%s", photo.width(), photo.height())
            if (photo.width(), photo.height()) != (800, 600):
                raise RuntimeError("startup splash must be a pre-rendered 800x600 PNG")
            self._startup_photo = photo
            self._startup_image_path = image_path
            self._startup_image_item = self._canvas.create_image(400, 300, image=photo, anchor="center")
            logger.debug("STARTUP SPLASH resized_size=800x600")
            logger.debug("STARTUP SPLASH draw_position=(400,300)")
            logger.debug("STARTUP SPLASH anchor=center item=%s", self._startup_image_item)
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
        self._background_photo = None
        self._background_key = None
        self._background_failed = set()
        self._state_effect_failed = set()
        self._state_effect_photos = {}
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

    def _draw_state_effect(self, effect: Optional[StateEffectParams], width: int, height: int, timestamp: float) -> None:
        if effect is None:
            return
        key = (effect.mode, width, height)
        try:
            self._state_effect_layer.render(self._canvas, effect, width, height, timestamp, arc_style=self._tk.ARC,
                                            glow_factory=self._state_effect_photo)
        except Exception as error:
            if key not in self._state_effect_failed:
                self._state_effect_failed.add(key)
                logger.warning("PHOS VISUAL: state effect unavailable mode=%s reason=%s", effect.mode, type(error).__name__)

    def _draw_eye_safe_layer(self, params: Optional[EyeSafeParams], frame: EyeFrame, background: Optional[BackgroundAsset]) -> None:
        if params is None:
            return
        try:
            for primitive in self._eye_safe_layer.primitives(params, frame, background):
                photo = self._eye_safe_photo(primitive)
                if photo is not None:
                    self._canvas.create_image(primitive.bounds[0], primitive.bounds[1], image=photo, anchor="nw")
        except Exception:
            logger.debug("PHOS VISUAL: eye safe layer unavailable", exc_info=True)

    def _eye_safe_photo(self, primitive):
        """Cache one transparent radial ellipse for each bounded mask shape."""
        x1, y1, x2, y2 = primitive.bounds
        width, height = max(2, round(x2 - x1)), max(2, round(y2 - y1))
        opacity = round(max(0.0, min(.5, primitive.opacity)) * 32) / 32
        key = (width, height, primitive.color, opacity)
        photo = self._eye_safe_photos.get(key)
        if photo is not None:
            return photo
        try:
            rgba = self._glow_rgba(width, height, primitive.color, opacity)
            scanlines = bytearray()
            row_bytes = width * 4
            for row in range(height):
                scanlines.append(0)
                scanlines.extend(rgba[row * row_bytes:(row + 1) * row_bytes])
            def chunk(name, data):
                return struct.pack(">I", len(data)) + name + data + struct.pack(">I", zlib.crc32(name + data) & 0xffffffff)
            encoded = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
                       + chunk(b"IDAT", zlib.compress(bytes(scanlines), 6)) + chunk(b"IEND", b""))
            photo = self._tk.PhotoImage(data=base64.b64encode(encoded), format="PNG")
            self._eye_safe_photos[key] = photo
            return photo
        except Exception:
            return None

    def prewarm_state_effect(self, effect: StateEffectParams, width: int, height: int, timestamp: float) -> None:
        """Prepare the small active-palette texture set before the next frame."""
        if self._tk is None:
            return
        started = time.monotonic()
        try:
            for primitive in self._state_effect_layer.primitives(effect, width, height, timestamp):
                if primitive.kind == "glow":
                    self._state_effect_photo(primitive)
        except Exception:
            logger.warning("PHOS VISUAL: state effect prewarm unavailable mode=%s", effect.mode)
        else:
            logger.debug("PHOS VISUAL: state_effect palette=%s prepare_ms=%.1f", effect.palette.primary,
                         (time.monotonic() - started) * 1000)

    def _state_effect_photo(self, primitive):
        """Return a cached transparent radial PNG for a glow-field primitive."""
        x1, y1, x2, y2 = primitive.bounds
        width, height = max(2, round(x2-x1)), max(2, round(y2-y1))
        # Bucketing alpha prevents smooth transitions from manufacturing a
        # PhotoImage each frame while retaining visible preset differences.
        opacity = round(max(0.0, min(.95, primitive.opacity)) * 24) / 24
        key = (width, height, primitive.color, opacity)
        photo = self._state_effect_photos.get(key)
        if photo is not None:
            return photo
        try:
            rgba = self._glow_rgba(width, height, primitive.color, opacity)
            # Keep this dependency-free: Tk supports transparent PNG directly,
            # while this tiny encoder is used only on cache misses (never in
            # the hot frame loop).
            scanlines = bytearray()
            row_bytes = width * 4
            for row in range(height):
                scanlines.append(0)  # PNG filter: None; makes round-trip bounded and explicit.
                start = row * row_bytes
                scanlines.extend(rgba[start:start + row_bytes])
            def chunk(name, data):
                return (struct.pack(">I", len(data)) + name + data
                        + struct.pack(">I", zlib.crc32(name + data) & 0xffffffff))
            encoded = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
                       + chunk(b"IDAT", zlib.compress(bytes(scanlines), 6)) + chunk(b"IEND", b""))
            photo = self._tk.PhotoImage(data=base64.b64encode(encoded), format="PNG")
            self._state_effect_photos[key] = photo
            return photo
        except Exception:
            return None

    @staticmethod
    def _glow_rgba(width, height, color, opacity):
        """Cached-texture input: saturated core with a still-soft transparent edge."""
        # This runs only for a cache miss.  Keep it stdlib-only: the Pi
        # display must not silently lose effects when optional NumPy/OpenCV is
        # absent. The hot render path merely reuses the resulting PhotoImage.
        red, green, blue = (int(color[index:index+2], 16) for index in (1, 3, 5))
        image = bytearray(width * height * 4)
        index = 0
        for y in range(height):
            yn = (2 * y / max(1, height - 1)) - 1
            for x in range(width):
                xn = (2 * x / max(1, width - 1)) - 1
                edge = max(0.0, 1.0 - (xn*xn + yn*yn))
                alpha = round((edge ** 1.35) * opacity * 255)
                image[index], image[index + 1], image[index + 2], image[index + 3] = blue, green, red, alpha
                index += 4
        return bytes(image)

    def _draw_background(self, asset: Optional[BackgroundAsset], width: int, height: int) -> None:
        """Draw one prepared state image beneath the face; never decode per frame."""
        if asset is None or not asset.available or asset.path is None:
            self._background_photo = None
            self._background_key = None
            return
        key = (asset.path, width, height)
        if key != self._background_key:
            self._background_key = key
            self._background_photo = self._prepare_background(asset, width, height)
        if self._background_photo is not None:
            self._canvas.create_image(0, 0, image=self._background_photo, anchor="nw")

    def _prepare_background(self, asset: BackgroundAsset, width: int, height: int):
        key = (asset.path, width, height)
        if key in self._background_failed:
            return None
        try:
            import cv2
            image = cv2.imread(str(asset.path), cv2.IMREAD_COLOR)
            if image is None:
                raise ValueError("invalid_image")
            source_height, source_width = image.shape[:2]
            scale = max(width / source_width, height / source_height)
            resized = cv2.resize(image, (round(source_width * scale), round(source_height * scale)), interpolation=cv2.INTER_AREA)
            left, top = (resized.shape[1] - width) // 2, (resized.shape[0] - height) // 2
            cropped = resized[top:top + height, left:left + width]
            rgb = cv2.cvtColor(cropped, cv2.COLOR_BGR2RGB)
            encoded = f"P6 {width} {height} 255\n".encode("ascii") + rgb.tobytes()
            return self._tk.PhotoImage(data=encoded, format="PPM")
        except Exception:
            self._background_failed.add(key)
            logger.warning("PHOS VISUAL: background unavailable state=%s reason=invalid_image", asset.state)
            return None

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
