"""One canonical resolver for named and custom PHOS iris colours."""
from __future__ import annotations

import re


class IrisColorResolver:
    """Resolve legacy preset names and strict custom hex colours to ``#RRGGBB``."""

    PRESETS = {
        "cyan": "#28CEEB", "blue": "#4985FF", "green": "#41CD7D",
        "turquoise": "#23C8AF", "amber": "#F4AB3D", "violet": "#A670F5",
        "white": "#DAECF6",
    }
    _HEX = re.compile(r"#[0-9a-fA-F]{6}\Z")

    @classmethod
    def normalize(cls, value: object) -> str:
        """Return canonical uppercase hex, accepting existing preset names."""
        if not isinstance(value, str):
            raise ValueError("iris color must be a preset name or #RRGGBB")
        preset = cls.PRESETS.get(value.lower())
        if preset is not None:
            return preset
        if cls._HEX.fullmatch(value):
            return value.upper()
        raise ValueError("iris color must be a supported preset name or #RRGGBB")

    @classmethod
    def preset_for(cls, value: object) -> str | None:
        try:
            canonical = cls.normalize(value)
        except ValueError:
            return None
        return next((name for name, color in cls.PRESETS.items() if color == canonical), None)

    @classmethod
    def rgb(cls, value: object) -> tuple[int, int, int]:
        canonical = cls.normalize(value)
        return tuple(int(canonical[index:index + 2], 16) for index in (1, 3, 5))
