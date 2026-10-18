"""Resolved, cached state-background presentation independent of display backend."""
from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path

from robot.core.state import RobotState

logger = logging.getLogger(__name__)
_STATES = tuple(item.value for item in RobotState)
_FORMATS = {".png", ".jpg", ".jpeg"}


@dataclass(frozen=True)
class BackgroundAsset:
    state: str
    path: Path | None = None
    available: bool = False
    reason: str | None = None

    @property
    def name(self) -> str | None:
        return self.path.name if self.path else None


class BackgroundResolver:
    """Resolve configured state assets once; renderers receive only an asset."""
    def __init__(self, config) -> None:
        self.configure(config)

    def configure(self, config) -> None:
        self.enabled = config.backgrounds_enabled
        self._assets = {state: self._asset(state, config.resolve_path(getattr(config, f"background_{state}"))) for state in _STATES}
        self._last_reported: tuple[str, Path | None, str | None] | None = None

    def _asset(self, state, configured) -> BackgroundAsset:
        if not self.enabled or configured is None:
            return BackgroundAsset(state)
        path = configured
        if path.suffix.lower() not in _FORMATS:
            logger.warning("PHOS VISUAL: background unavailable state=%s reason=unsupported_format", state)
            return BackgroundAsset(state, path, False, "unsupported_format")
        if not path.is_file():
            logger.warning("PHOS VISUAL: background unavailable state=%s reason=asset_missing", state)
            return BackgroundAsset(state, path, False, "asset_missing")
        return BackgroundAsset(state, path, True)

    def resolve(self, state: RobotState | str) -> BackgroundAsset:
        state = state.value if isinstance(state, RobotState) else state
        asset = self._assets.get(state, BackgroundAsset(str(state), reason="unknown_state"))
        report = (asset.state, asset.path, asset.reason)
        if report != self._last_reported:
            self._last_reported = report
            if asset.available:
                logger.info("PHOS VISUAL: background changed state=%s asset=%s", asset.state, asset.name)
        return asset

    def status(self, state: RobotState | str) -> dict:
        asset = self.resolve(state)
        return {"enabled": self.enabled, "current_state": asset.state,
                "current_asset": asset.name, "available": asset.available,
                "reason": asset.reason,
                "configured": {name: asset.name for name, asset in self._assets.items() if asset.path is not None}}


def validate_background_assets(config) -> None:
    """Validate enabled assignments before they can enter the live pipeline."""
    if not config.backgrounds_enabled:
        return
    root = (config._base_dir / "backgrounds").resolve()
    for state in _STATES:
        configured = getattr(config, f"background_{state}")
        if configured is None:
            continue
        path = config.resolve_path(configured)
        try:
            path.relative_to(root)
        except ValueError as error:
            raise ValueError(f"display.backgrounds.{state}: asset must be inside {root.name}/") from error
        if path.suffix.lower() not in _FORMATS:
            raise ValueError(f"display.backgrounds.{state}: unsupported_format")
        if not path.is_file():
            raise ValueError(f"display.backgrounds.{state}: asset_missing")
        if not _decodes(path):
            raise ValueError(f"display.backgrounds.{state}: invalid_image")


def _decodes(path: Path) -> bool:
    try:
        import cv2
        return cv2.imread(str(path), cv2.IMREAD_COLOR) is not None
    except ImportError:
        # Production Raspberry Pi installations provide OpenCV; retaining this
        # conservative fallback keeps configuration inspection usable elsewhere.
        import imghdr
        return imghdr.what(path) in {"png", "jpeg"}
