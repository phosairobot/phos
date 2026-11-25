#!/usr/bin/env python3
"""Regenerate PHOS branding from img/neon_phos_logo.png using macOS sips.

This development utility adds no dependency to the Raspberry Pi runtime.
Resize proportionally and pad rectangular assets; never crop or recolor the logo.
"""
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "img/neon_phos_logo.png"
DOCS = ROOT / "docs/assets/images"
WEB = ROOT / "src/robot/web/static/images"
RUNTIME = ROOT / "src/robot/assets/images"


def main() -> None:
    sips = shutil.which("sips")
    if sips is None:
        raise SystemExit("Brand asset regeneration requires macOS sips; runtime use does not.")
    if not SOURCE.is_file():
        raise SystemExit(f"Missing branding source: {SOURCE}")

    with tempfile.TemporaryDirectory(prefix="phos-branding-") as temporary:
        for size, destinations in (
            (64, (DOCS / "neon_phos_logo-favicon.png", WEB / "neon_phos_logo-favicon.png")),
            (96, (WEB / "neon_phos_logo-96.png",)),
            (180, (DOCS / "neon_phos_logo-touch.png", WEB / "neon_phos_logo-touch.png")),
            (224, (WEB / "neon_phos_logo-224.png",)),
            (512, (DOCS / "neon_phos_logo.png",)),
            (780, (WEB / "neon_phos_logo.png",)),
        ):
            image = Path(temporary) / f"logo-{size}.png"
            subprocess.run([sips, "-Z", str(size), str(SOURCE), "--out", str(image)],
                           check=True, capture_output=True)
            for destination in destinations:
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(image, destination)

        for size, height, width, destination in (
            (600, 600, 800, RUNTIME / "neon_phos_logo-startup-800x600.png"),
            (630, 630, 1200, DOCS / "neon_phos_logo-social.png"),
        ):
            image = Path(temporary) / f"logo-{width}x{height}.png"
            subprocess.run([sips, "-Z", str(size), str(SOURCE), "--out", str(image)],
                           check=True, capture_output=True)
            subprocess.run([sips, "-p", str(height), str(width), str(image),
                            "--padColor", "02050D"], check=True, capture_output=True)
            if struct.unpack(">II", image.read_bytes()[16:24]) != (width, height):
                raise SystemExit(f"Incorrect dimensions for {destination}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(image, destination)


if __name__ == "__main__":
    main()
