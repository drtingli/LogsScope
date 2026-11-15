"""Session = open files + per-file view state (bookmarks, position, filter...)."""

from __future__ import annotations

from .layout import sanitize_layout

import re

from .actions import sanitize_action
from .bookmarks import bookmarks_from_list, bookmarks_to_list
from .jsonio import atomic_write_json, read_json

VERSION = 2
_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")


def is_color(c) -> bool:
    return isinstance(c, str) and _COLOR.match(c) is not None
_FILTER_KEYS = {"text": str, "regex": bool, "case": bool, "invert": bool, "context": int}


def sanitize_filter(d) -> dict | None:
    if not isinstance(d, dict) or not isinstance(d.get("text"), str) or not d["text"]:
        return None
    out = {}
    for k, typ in _FILTER_KEYS.items():
        v = d.get(k, typ())
        if typ is int:
            v = v if isinstance(v, int) and not isinstance(v, bool) else 0
            v = max(0, min(v, 50))
        elif not isinstance(v, typ):
            v = typ()
        out[k] = v
    action = sanitize_action(d.get("action"))
    if action is not None:
        out["action"] = action
    return out


def sanitize_find(d) -> dict | None:
    if not isinstance(d, dict) or not isinstance(d.get("text"), str) or not d["text"]:
        return None
    return {"text": d["text"], "regex": d.get("regex") is True, "case": d.get("case") is True}


def clean_offset(v) -> float:
    ok = isinstance(v, (int, float)) and not isinstance(v, bool) and abs(v) < 1e9 and v == v
    return float(v) if ok else 0.0


MAX_PINS = 1000


def clean_pins(v) -> list[int]:
    if not isinstance(v, list):
        return []
    out = {x for x in v if isinstance(x, int) and not isinstance(x, bool) and x >= 0}
    return sorted(out)[:MAX_PINS]


def sanitize_tab(d) -> dict | None:
    if not isinstance(d, dict) or not isinstance(d.get("path"), str) or not d["path"]:
        return None
    line = d.get("line", 0)
    col = d.get("columnizer")
    return {
        "path": d["path"],
        "bookmarks": bookmarks_to_list(bookmarks_from_list(d.get("bookmarks"))),
        "follow": d.get("follow") if isinstance(d.get("follow"), bool) else True,
        "line": line if isinstance(line, int) and not isinstance(line, bool) and line > 0 else 0,
        "columnizer": col if isinstance(col, str) and col else None,
        "filter": sanitize_filter(d.get("filter")),
        "find": sanitize_find(d.get("find")),
        "filter_visible": d.get("filter_visible") is True,
        "color": d["color"].lower() if is_color(d.get("color")) else "",
        "title": d["title"].strip()[:100] if isinstance(d.get("title"), str) else "",
        "ts_format": d["ts_format"].strip()[:100] if isinstance(d.get("ts_format"), str) else "",
        "linked": d.get("linked") is True,                 # takes part in time sync / merging
        "ts_offset": clean_offset(d.get("ts_offset")),     # seconds added to this file's timestamps
        "pinned": clean_pins(d.get("pinned")),             # sticky lines of the main view (0-based)
    }


def sanitize_session(obj) -> dict:
    tabs = []
    if isinstance(obj, dict) and isinstance(obj.get("tabs"), list):
        tabs = [t for t in (sanitize_tab(x) for x in obj["tabs"]) if t is not None]
    cur = obj.get("current") if isinstance(obj, dict) else 0
    cur = cur if isinstance(cur, int) and not isinstance(cur, bool) and 0 <= cur < len(tabs) else 0
    out = {"version": VERSION, "tabs": tabs, "current": cur}
    arrangement = sanitize_layout(obj.get("layout"), len(tabs)) if isinstance(obj, dict) else None
    if arrangement is not None:
        out["layout"] = arrangement                         # how the tabs are arranged in areas
    return out


def session_actions(session: dict) -> list[str]:
    """Commands that would be armed by restoring this session."""
    return [t["filter"]["action"]["command"] for t in session.get("tabs", [])
            if t.get("filter") and t["filter"].get("action") and t["filter"]["action"].get("enabled", True)]


def strip_actions(session: dict) -> dict:
    for t in session.get("tabs", []):
        if t.get("filter"):
            t["filter"].pop("action", None)
    return session


def save_session_file(path: str, session: dict) -> None:
    atomic_write_json(path, sanitize_session(session))


def load_session_file(path: str) -> dict:
    obj = read_json(path)
    if not isinstance(obj, dict) or not isinstance(obj.get("tabs"), list):
        raise ValueError("not a LogScope session file")
    return sanitize_session(obj)
