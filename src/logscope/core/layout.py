"""Layout of the tab area as a tree of splits and tab groups (Qt-free, so it can be tested).

The window area is a tree.  A *group* holds some tabs (one is shown, the others are reached through the
group's own tab bar); a *split* places several nodes side by side (``"h"``) or one above the other (``"v"``)
and can be nested without limit (up to :data:`MAX_DEPTH` levels and :data:`MAX_GROUPS` groups).  Three columns
are one horizontal split with three groups; a 4 x 2 grid is a vertical split of four horizontal splits.

Nodes are plain dictionaries so they can be saved in the session file::

    {"t": "g", "tabs": [0, 3], "cur": 1}                       # a group: tab numbers, position of the shown one
    {"t": "s", "o": "h", "sizes": [30, 40, 30], "kids": [...]} # a split

The tab numbers refer to the tabs of the session (0-based).
"""

from __future__ import annotations

import math

MAX_DEPTH = 8
MAX_GROUPS = 64
MAX_CELLS_PER_SIDE = 8
MODES = ("tabs", "columns", "rows", "grid", "custom")
PRESETS = ("columns", "rows", "grid")
EDGE_BAND = 0.28                      # outer part of a group that means "split on this side" when dropping


def group(tabs=(), cur: int = 0) -> dict:
    tabs = list(tabs)
    return {"t": "g", "tabs": tabs, "cur": cur if 0 <= cur < len(tabs) else 0}


def split(orient: str, kids: list[dict], sizes: list[int] | None = None) -> dict:
    return {"t": "s", "o": orient, "sizes": list(sizes) if sizes else [1000] * len(kids), "kids": list(kids)}


def preset_shape(mode: str, n_tabs: int) -> tuple[int, int]:
    """(columns, rows) of the ready-made layouts for ``n_tabs`` tabs."""
    n = max(n_tabs, 1)
    if mode == "columns":
        return min(n, MAX_GROUPS), 1
    if mode == "rows":
        return 1, min(n, MAX_GROUPS)
    if mode == "grid":
        cols = max(2, math.ceil(math.sqrt(n))) if n > 1 else 1
        return cols, math.ceil(n / cols)
    return 1, 1


def arrange_tree(n_tabs: int, cols: int, rows: int) -> dict:
    """``rows`` x ``cols`` groups; tab ``i`` goes to cell ``i mod (rows * cols)`` (left to right, top to bottom),
    so more tabs than cells share cells, fewer tabs leave empty cells to drop tabs into."""
    cols = max(1, min(int(cols), MAX_CELLS_PER_SIDE * 8))
    rows = max(1, min(int(rows), MAX_CELLS_PER_SIDE * 8))
    while cols * rows > MAX_GROUPS:                      # keep within the group limit
        if cols >= rows:
            cols -= 1
        else:
            rows -= 1
    cells = cols * rows
    buckets: list[list[int]] = [[] for _ in range(cells)]
    for i in range(n_tabs):
        buckets[i % cells].append(i)
    row_nodes = []
    for r in range(rows):
        kids = [group(buckets[r * cols + c]) for c in range(cols)]
        row_nodes.append(kids[0] if cols == 1 else split("h", kids))
    return row_nodes[0] if rows == 1 else split("v", row_nodes)


def preset_tree(mode: str, n_tabs: int) -> dict:
    if mode == "tabs":
        return group(range(n_tabs))
    cols, rows = preset_shape(mode, n_tabs)
    return arrange_tree(n_tabs, cols, rows)


# ------------------------------------------------------------------------------------ validation
def _clean(node, n_tabs: int, used: set[int], depth: int, budget: list[int]):
    if not isinstance(node, dict) or depth > MAX_DEPTH or budget[0] <= 0:
        return None
    if node.get("t") == "g":
        tabs = []
        for i in node.get("tabs") if isinstance(node.get("tabs"), list) else []:
            if isinstance(i, int) and not isinstance(i, bool) and 0 <= i < n_tabs and i not in used:
                used.add(i)
                tabs.append(i)
        cur = node.get("cur")
        budget[0] -= 1
        return group(tabs, cur if isinstance(cur, int) and not isinstance(cur, bool) else 0)
    if node.get("t") == "s":
        orient = node.get("o")
        kids_raw = node.get("kids") if isinstance(node.get("kids"), list) else []
        sizes_raw = node.get("sizes") if isinstance(node.get("sizes"), list) else []
        kids, sizes = [], []
        for pos, k in enumerate(kids_raw[:MAX_GROUPS]):
            c = _clean(k, n_tabs, used, depth + 1, budget)
            if c is None:
                continue
            s = sizes_raw[pos] if pos < len(sizes_raw) else None
            kids.append(c)
            sizes.append(s if isinstance(s, int) and not isinstance(s, bool) and s > 0 else 1000)
        if not kids:
            return None
        if len(kids) == 1:
            return kids[0]
        return split("v" if orient == "v" else "h", kids, sizes)
    return None


def sanitize_layout(obj, n_tabs: int) -> dict | None:
    """The layout from a session file, or None when it is missing or unusable.  Tab numbers that do not
    exist or appear twice are dropped; a split with a single child collapses into that child."""
    if not isinstance(obj, dict):
        return None
    root = _clean(obj.get("root"), n_tabs, set(), 0, [MAX_GROUPS])
    if root is None:
        return None
    mode = obj.get("mode")
    return {"mode": mode if mode in MODES else "custom", "root": root}


def tab_numbers(node: dict) -> list[int]:
    """Tab numbers of a tree in layout order."""
    if node["t"] == "g":
        return list(node["tabs"])
    out: list[int] = []
    for k in node["kids"]:
        out += tab_numbers(k)
    return out


def count_groups(node: dict) -> int:
    return 1 if node["t"] == "g" else sum(count_groups(k) for k in node["kids"])


def depth(node: dict) -> int:
    return 1 if node["t"] == "g" else 1 + max(depth(k) for k in node["kids"])


def prune(node: dict | None, keep) -> dict | None:
    """Drop the tabs for which ``keep(number)`` is false.  A group that had tabs but lost them all is removed
    (it only held tabs that are not saved); a group that was empty from the start stays.  Splits left with one
    child collapse into it."""
    if node is None:
        return None
    if node["t"] == "g":
        kept = [i for i in node["tabs"] if keep(i)]
        if node["tabs"] and not kept:
            return None
        cur_tab = node["tabs"][node["cur"]] if node["tabs"] and 0 <= node["cur"] < len(node["tabs"]) else None
        return group(kept, kept.index(cur_tab) if cur_tab in kept else 0)
    kids, sizes = [], []
    for k, s in zip(node["kids"], node["sizes"]):
        p = prune(k, keep)
        if p is not None:
            kids.append(p)
            sizes.append(s)
    if not kids:
        return None
    return kids[0] if len(kids) == 1 else split(node["o"], kids, sizes)


def renumber(node: dict, mapping: dict[int, int]) -> dict:
    """Same tree with tab numbers replaced through ``mapping`` (numbers not in it are dropped)."""
    if node["t"] == "g":
        tabs = [mapping[i] for i in node["tabs"] if i in mapping]
        cur_tab = mapping.get(node["tabs"][node["cur"]]) if node["tabs"] and node["cur"] < len(node["tabs"]) else None
        return group(tabs, tabs.index(cur_tab) if cur_tab in tabs else 0)
    return split(node["o"], [renumber(k, mapping) for k in node["kids"]], node["sizes"])


# ------------------------------------------------------------------------------------- drop zones
def zone_at(x: float, y: float, w: float, h: float, band: float = EDGE_BAND) -> str:
    """Where in a ``w`` x ``h`` group a tab is dropped: ``left`` / ``right`` / ``top`` / ``bottom`` (split on that
    side) or ``center`` (add to the group)."""
    if w <= 0 or h <= 0:
        return "center"
    dist = {"left": x / w, "right": 1 - x / w, "top": y / h, "bottom": 1 - y / h}
    edge = min(dist, key=dist.get)
    return edge if dist[edge] < band else "center"


def zone_rect(zone: str, w: int, h: int) -> tuple[int, int, int, int]:
    """(x, y, width, height) that shows what a drop in ``zone`` would take."""
    if zone == "left":
        return 0, 0, w // 2, h
    if zone == "right":
        return w - w // 2, 0, w // 2, h
    if zone == "top":
        return 0, 0, w, h // 2
    if zone == "bottom":
        return 0, h - h // 2, w, h // 2
    return 0, 0, w, h


def zone_split(zone: str) -> tuple[str, bool] | None:
    """(orientation, new group goes before) for an edge zone, None for ``center``."""
    return {"left": ("h", True), "right": ("h", False), "top": ("v", True), "bottom": ("v", False)}.get(zone)


def insert_index(centers: list[float], x: float) -> int:
    """Position among tabs (given by the x of their centres) at which a tab dropped at ``x`` is inserted."""
    for i, c in enumerate(centers):
        if x < c:
            return i
    return len(centers)
