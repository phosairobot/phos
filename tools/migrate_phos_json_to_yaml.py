#!/usr/bin/env python3
"""Explicit one-time migration of a PHOS JSON configuration to YAML.

This tool is intentionally separate from normal startup: PHOS itself accepts
only YAML configuration files and never modifies a legacy JSON file.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from robot.config import ConfigurationError, RuntimeConfig, atomic_write_text
import yaml


def _unique_object(pairs):
    document = {}
    for key, value in pairs:
        if key in document:
            raise ConfigurationError(f"Duplicate configuration key: {key}")
        document[key] = value
    return document


def migrate(source: Path, destination: Path) -> None:
    source, destination = source.resolve(), destination.resolve()
    if source.suffix.lower() != ".json":
        raise ConfigurationError("Migration source must be a .json file.")
    if destination.suffix.lower() not in {".yaml", ".yml"}:
        raise ConfigurationError("Migration destination must be a YAML file (.yaml or .yml).")
    if destination.exists():
        raise ConfigurationError(f"Refusing to overwrite existing YAML configuration: {destination}")
    try:
        document = json.loads(source.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
    except json.JSONDecodeError as error:
        raise ConfigurationError(f"{source}: invalid JSON at line {error.lineno}, column {error.colno}") from error
    except (OSError, UnicodeError) as error:
        raise ConfigurationError(f"Cannot read configuration file: {source} ({type(error).__name__})") from error
    if not isinstance(document, dict):
        raise ConfigurationError(f"{source}: JSON root must be an object")
    config = RuntimeConfig.from_dict(document, base_dir=source.parent)
    content = yaml.safe_dump(config.persistence_document(destination), default_flow_style=False,
                             allow_unicode=True, sort_keys=False)
    atomic_write_text(destination, content if content.endswith("\n") else content + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Convert a PHOS JSON configuration to YAML once.")
    parser.add_argument("source", type=Path, help="existing .json configuration")
    parser.add_argument("destination", type=Path, help="new .yaml configuration; must not exist")
    args = parser.parse_args()
    try:
        migrate(args.source, args.destination)
    except ConfigurationError as error:
        parser.error(str(error))
    print(f"Created {args.destination.resolve()}; retained {args.source.resolve()} as the backup.")


if __name__ == "__main__":
    main()
