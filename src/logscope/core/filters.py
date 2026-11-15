"""Saved filters <-> JSON files (Qt-free)."""

from __future__ import annotations

from .jsonio import atomic_write_json, read_json
from .session import sanitize_filter

VERSION = 1


def filters_to_json(filters: list[dict]) -> dict:
    return {"version": VERSION, "filters": filters}


def filters_from_json(obj) -> list[dict]:
    """Tolerant; raises ValueError when no usable filter is found."""
    items = obj.get("filters") if isinstance(obj, dict) else None
    out: list[dict] = []
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict) or not isinstance(it.get("name"), str) or not it["name"].strip():
            continue
        spec = sanitize_filter(it)
        if spec is not None:
            out.append({"name": it["name"].strip()[:80], **spec})
    if not out:
        raise ValueError("no saved filters found in file")
    return out


def has_actions(filters: list[dict]) -> bool:
    return any(f.get("action") for f in filters)


def write_filters_file(path: str, filters: list[dict]) -> None:
    atomic_write_json(path, filters_to_json(filters))


def read_filters_file(path: str) -> list[dict]:
    return filters_from_json(read_json(path))
