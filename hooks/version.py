"""Expose the canonical package version in documentation Markdown at build time."""
from pathlib import Path
import re


def on_page_markdown(markdown, page, config, files):
    source = Path(config["config_file_path"]).parent / "src" / "robot" / "__init__.py"
    match = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', source.read_text(encoding="utf-8"), re.MULTILINE)
    if match is None:
        raise ValueError("Could not read canonical robot.__version__ for documentation")
    return markdown.replace("{{ phos_version }}", match.group(1))
