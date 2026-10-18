"""Small, conservative helpers for BCP-47-compatible language tags."""
from __future__ import annotations

import re


_LANGUAGE_TAG = re.compile(r"^(?P<language>[A-Za-z]{2,3})(?:-(?P<subtags>[A-Za-z0-9]{2,8}(?:-[A-Za-z0-9]{2,8})*))?$")


def normalize_language_tag(value: str | None) -> str | None:
    """Normalize common language-tag casing without detecting or inventing one.

    Values outside the small BCP-47-compatible subset are returned unchanged so
    provider-specific or unknown values retain their original meaning.
    """
    if value is None or not isinstance(value, str):
        return value
    candidate = value.replace("_", "-")
    match = _LANGUAGE_TAG.fullmatch(candidate)
    if match is None:
        return value
    subtags = match.group("subtags")
    if not subtags:
        return match.group("language").lower()
    normalized = []
    for index, subtag in enumerate(subtags.split("-")):
        if index == 0 and len(subtag) == 4 and subtag.isalpha():
            normalized.append(subtag.title())
        elif (len(subtag) == 2 and subtag.isalpha()) or (len(subtag) == 3 and subtag.isdigit()):
            normalized.append(subtag.upper())
        else:
            normalized.append(subtag.lower())
    return f"{match.group('language').lower()}-{'-'.join(normalized)}"
