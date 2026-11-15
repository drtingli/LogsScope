"""Bookmark list <-> JSON.  In memory: ``{line_index (0-based): Bookmark}``.
In files: 1-based ``line`` numbers, matching what the user sees in the table.
Files written by v0.2 (comment only, no tags) are still readable."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from .jsonio import atomic_write_json, read_json
from .theme import clean_marker

VERSION = 3
SNIPPET = 200
MAX_TAGS = 20
MAX_TAG_LEN = 40


@dataclass
class Bookmark:
    comment: str = ""
    tags: list[str] = field(default_factory=list)
    marker: str = ""               # "" = the default marker from the theme (any emoji / text)


def normalize_tags(raw) -> list[str]:
    """Accepts a list of strings or a comma / semicolon separated string.
    Trims, drops empties, removes duplicates (case-insensitive, first spelling wins)."""
    if isinstance(raw, str):
        raw = raw.replace(";", ",").replace("\uff0c", ",").split(",")
    out: list[str] = []
    seen: set[str] = set()
    if not isinstance(raw, (list, tuple)):
        return out
    for t in raw:
        if not isinstance(t, str):
            continue
        t = " ".join(t.split())[:MAX_TAG_LEN]
        if t and t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)
        if len(out) >= MAX_TAGS:
            break
    return out


def tags_text(tags: list[str]) -> str:
    return ", ".join(tags)


def all_tags(bm: dict[int, Bookmark]) -> list[str]:
    """Every tag in use, sorted case-insensitively."""
    seen: dict[str, str] = {}
    for b in bm.values():
        for t in b.tags:
            seen.setdefault(t.lower(), t)
    return sorted(seen.values(), key=str.lower)


def bookmarks_to_list(bm: dict[int, Bookmark], text_fn: Callable[[int], str] | None = None) -> list[dict]:
    out = []
    for ln in sorted(bm):
        item = {"line": ln + 1, "comment": bm[ln].comment, "tags": list(bm[ln].tags)}
        if bm[ln].marker:
            item["marker"] = bm[ln].marker
        if text_fn is not None:
            try:
                item["text"] = text_fn(ln)[:SNIPPET]
            except Exception:
                item["text"] = ""
        out.append(item)
    return out


def bookmarks_from_list(items) -> dict[int, Bookmark]:
    """Tolerant: silently skips malformed entries."""
    out: dict[int, Bookmark] = {}
    if not isinstance(items, list):
        return out
    for it in items:
        if not isinstance(it, dict):
            continue
        ln = it.get("line")
        if isinstance(ln, bool) or not isinstance(ln, int) or ln < 1:
            continue
        comment = it.get("comment", "")
        out[ln - 1] = Bookmark(comment if isinstance(comment, str) else "",
                               normalize_tags(it.get("tags")), clean_marker(it.get("marker"), ""))
    return out


def write_bookmark_file(path: str, log_path: str, bm: dict[int, Bookmark],
                        text_fn: Callable[[int], str] | None = None) -> None:
    atomic_write_json(path, {"version": VERSION, "file": log_path,
                             "bookmarks": bookmarks_to_list(bm, text_fn)})


def read_bookmark_file(path: str) -> tuple[str, dict[int, Bookmark]]:
    """Returns ``(log_path_it_was_saved_for, bookmarks)``; ValueError if not a bookmark file."""
    obj = read_json(path)
    if not isinstance(obj, dict) or not isinstance(obj.get("bookmarks"), list):
        raise ValueError("not a LogScope bookmark file")
    file = obj.get("file")
    return (file if isinstance(file, str) else ""), bookmarks_from_list(obj["bookmarks"])
