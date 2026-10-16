"""Resolved, cached state-background presentation independent of display backend."""
from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path

from robot.core.state import RobotState

logger = logging.getLogger(__name__)
_STATES = tuple(item.value for item in RobotState)


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

    def _asset(self, state, configured) -> BackgroundAsset:
        if not self.enabled or configured is None:
            return BackgroundAsset(state)
        path = configured
        if path.suffix.lower() not in {".png", ".jpg", ".jpeg"} or not path.is_file():
            logger.warning("PHOS VISUAL: background unavailable state=%s reason=asset_missing", state)
            return BackgroundAsset(state, reason="asset_missing")
        return BackgroundAsset(state, path, True)

    def resolve(self, state: RobotState | str) -> BackgroundAsset:
        state = state.value if isinstance(state, RobotState) else state
        asset = self._assets.get(state, BackgroundAsset(str(state), reason="unknown_state"))
        if asset.available:
            logger.info("PHOS VISUAL: background state=%s asset=%s", asset.state, asset.name)
        return asset
