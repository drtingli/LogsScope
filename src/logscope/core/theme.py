"""Appearance settings (Qt-free): fonts and colours per area, selected-row colours,
grid lines and the bookmark marker.  Everything is validated so a hand-edited or
imported file can never break the UI."""

from __future__ import annotations

import copy
import re

AREAS = ("main", "filter", "search", "bookmarks")
AREA_LABELS = {"main": "Main view", "filter": "Filter panel",
               "search": "Search results", "bookmarks": "Bookmarks"}
HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
DEFAULT_MARKER = "\u2605"                      # black star
MAX_MARKER_LEN = 12                            # code points; room for ZWJ emoji sequences
MARKER_PRESETS = ["\u2605", "\u2B50", "\U0001F516", "\U0001F4CC", "\U0001F4CD", "\U0001F6A9",
                  "\u2757", "\u26A0\uFE0F", "\U0001F41E", "\U0001F525", "\U0001F534",
                  "\U0001F7E2", "\U0001F535", "\U0001F4A1", "\u2705", "\u274C"]


# extra pixels added to the text height of a row (space between the lines of text)
DEFAULT_ROW_SPACING = {"main": 4, "filter": 4, "search": 4, "bookmarks": 6}
MAX_ROW_SPACING = 40


def default_theme() -> dict:
    return {
        "version": 1,
        # "" / 0 = default (the fixed-width system font for log views, the UI font for bookmarks)
        "areas": {a: {"font_family": "", "font_size": 0, "fg": "", "bg": "", "row_spacing": DEFAULT_ROW_SPACING[a]}
                  for a in AREAS},
        "selection": {"fg": "#ffffff", "bg": "#2f6fdb"},
        "grid": {"horizontal": True, "vertical": True, "color": "#d9d9d9"},
        "bookmark_marker": DEFAULT_MARKER,
    }


def clean_colour(v, default: str = "") -> str:
    return v.lower() if isinstance(v, str) and HEX.match(v) else default


def clean_marker(v, default: str = "") -> str:
    if not isinstance(v, str):
        return default
    v = v.strip()
    return v[:MAX_MARKER_LEN] if v else default


def sanitize_theme(obj) -> dict:
    out = default_theme()
    if not isinstance(obj, dict):
        return out
    areas = obj.get("areas")
    if isinstance(areas, dict):
        for a in AREAS:
            src = areas.get(a)
            if not isinstance(src, dict):
                continue
            dst = out["areas"][a]
            fam, size = src.get("font_family"), src.get("font_size")
            dst["font_family"] = fam.strip()[:100] if isinstance(fam, str) else ""
            dst["font_size"] = size if isinstance(size, int) and not isinstance(size, bool) \
                and 4 <= size <= 72 else 0
            sp = src.get("row_spacing")
            dst["row_spacing"] = sp if isinstance(sp, int) and not isinstance(sp, bool) \
                and 0 <= sp <= MAX_ROW_SPACING else DEFAULT_ROW_SPACING[a]
            dst["fg"] = clean_colour(src.get("fg"), "")
            dst["bg"] = clean_colour(src.get("bg"), "")
    sel = obj.get("selection")
    if isinstance(sel, dict):
        out["selection"]["fg"] = clean_colour(sel.get("fg"), out["selection"]["fg"])
        out["selection"]["bg"] = clean_colour(sel.get("bg"), out["selection"]["bg"])
    grid = obj.get("grid")
    if isinstance(grid, dict):
        for k in ("horizontal", "vertical"):
            if isinstance(grid.get(k), bool):
                out["grid"][k] = grid[k]
        out["grid"]["color"] = clean_colour(grid.get("color"), out["grid"]["color"])
    out["bookmark_marker"] = clean_marker(obj.get("bookmark_marker"), DEFAULT_MARKER) or DEFAULT_MARKER
    return out


def nudge_row_spacing(theme: dict, delta: int) -> dict:
    """A copy of ``theme`` with the row spacing of every area changed by ``delta`` pixels (kept in range)."""
    out = copy.deepcopy(theme)
    for a in AREAS:
        cur = out["areas"][a].get("row_spacing", DEFAULT_ROW_SPACING[a])
        out["areas"][a]["row_spacing"] = max(0, min(MAX_ROW_SPACING, int(cur) + int(delta)))
    return out


def reset_row_spacing(theme: dict) -> dict:
    out = copy.deepcopy(theme)
    for a in AREAS:
        out["areas"][a]["row_spacing"] = DEFAULT_ROW_SPACING[a]
    return out


def theme_from_json(obj) -> dict:
    """Strict entry point for imported files."""
    if not isinstance(obj, dict) or not any(k in obj for k in ("areas", "selection", "grid")):
        raise ValueError("not a LogScope theme file")
    return sanitize_theme(obj)


def copy_theme(theme: dict) -> dict:
    return copy.deepcopy(theme)


def blend(color: str, base: str, alpha: float) -> str:
    """``color`` laid over ``base`` with the given opacity (both '#rrggbb') -> '#rrggbb'."""
    c, b = int(color[1:], 16), int(base[1:], 16)
    out = 0
    for shift in (16, 8, 0):
        cv, bv = (c >> shift) & 255, (b >> shift) & 255
        out |= int(round(bv + (cv - bv) * alpha)) << shift
    return f"#{out:06x}"


def luminance(color: str) -> float:
    v = int(color[1:], 16)
    chan = []
    for shift in (16, 8, 0):
        x = ((v >> shift) & 255) / 255.0
        chan.append(x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4)
    return 0.2126 * chan[0] + 0.7152 * chan[1] + 0.0722 * chan[2]


def contrast_fg(bg: str, preferred: str = "") -> str:
    """Text colour readable on ``bg``: ``preferred`` if it has enough contrast, else black / white."""
    def ratio(a: str, b: str) -> float:
        la, lb = luminance(a), luminance(b)
        hi, lo = max(la, lb), min(la, lb)
        return (hi + 0.05) / (lo + 0.05)
    if preferred and ratio(preferred, bg) >= 4.5:
        return preferred
    return "#000000" if ratio("#000000", bg) >= ratio("#ffffff", bg) else "#ffffff"
