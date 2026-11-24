# SPDX-License-Identifier: Apache-2.0
"""Validate the checked-in OpenAPI contract without a runtime dependency."""
from __future__ import annotations
import json
from pathlib import Path

SPEC_PATH = Path(__file__).resolve().parents[3] / "docs" / "api" / "openapi.yaml"

def load_spec():
    # JSON is valid YAML 1.2, keeping validation available on the Pi without
    # adding a parser dependency to the robot runtime.
    return json.loads(SPEC_PATH.read_text(encoding="utf-8"))

def validate():
    spec = load_spec()
    assert spec["openapi"].startswith("3.1."), "OpenAPI 3.1 is required"
    assert spec["info"]["title"] and spec["info"]["version"]
    assert isinstance(spec["paths"], dict) and spec["components"]["schemas"]
    for path, operations in spec["paths"].items():
        assert path.startswith("/api/v1/") and isinstance(operations, dict)
        for method, operation in operations.items():
            assert method in {"get", "post", "patch", "put", "delete"}
            assert "responses" in operation
    for reference in _references(spec):
        assert _resolve(spec, reference) is not None, f"unresolved OpenAPI reference: {reference}"
    return spec


def _references(value):
    if isinstance(value, dict):
        if "$ref" in value:
            yield value["$ref"]
        for item in value.values():
            yield from _references(item)
    elif isinstance(value, list):
        for item in value:
            yield from _references(item)


def _resolve(document, reference):
    if not isinstance(reference, str) or not reference.startswith("#/"):
        return None
    current = document
    for part in reference[2:].split("/"):
        if not isinstance(current, dict):
            return None
        current = current.get(part.replace("~1", "/").replace("~0", "~"))
        if current is None:
            return None
    return current

if __name__ == "__main__":
    validate()
    print(f"valid OpenAPI 3.1 document: {SPEC_PATH}")
