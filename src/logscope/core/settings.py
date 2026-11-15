"""JSON settings; portable mode keeps them next to the program.

Every assignment (``settings["key"] = value``) is written to disk immediately
when the value actually changed, so nothing is lost if the program crashes."""

from __future__ import annotations

import copy
import json
import os
import sys
from pathlib import Path

from .bookmarks import normalize_tags
from .ctxmenu import default_menu, sanitize_menu
from .theme import default_theme, sanitize_theme
from .highlight import DEFAULT_RULES, HighlightRule
from .jsonio import atomic_write_json
from .session import is_color, sanitize_session

HISTORY_LIMIT = 30
MAX_TAB_COLORS = 300

DEFAULTS = {
    "follow_tail": True,
    "filter_tail": True,         # filter panel keeps following the growing file
    "search_live": True,         # 'Search results' panel keeps following the growing file
    "theme": default_theme(),    # fonts / colours per area, selection, grid, bookmark marker
    "trigger_burst_limit": 25,   # max programs started per batch of new lines (safety net)
    "autofit_columns": True,     # column widths follow the longest text
    "wrap_text": False,          # wrap every field to the window width
    "timestamp_format": "",      # "" = auto-detect, "builtin:<name>" or a strptime format
    "tab_colors": {},            # {file path: "#rrggbb"} remembered between sessions
    "layout": "tabs",            # tabs | columns | rows | grid | custom (areas arranged by hand)
    "activity_led": True,        # LED in front of every tab name, flashes on new lines while following
    "arrange_cols": 0,           # last values of View > Layout > Arrange (0 = suggest from the tab count)
    "arrange_rows": 0,
    "save_dir": "",              # last folder used by "Save as"
    "paste_dir": "",             # last folder used by "paste clipboard as new file"
    "bookmark_tags": [],         # tags used so far (suggestions)
    "font_family": "",
    "font_size": 10,
    "recent_files": [],
    "encoding": "",              # "" = auto-detect; or e.g. "gbk"
    "regex_columnizers": [],     # parsing rules: [{"name","pattern","ignore_case","search","unmatched","names","auto","files","split_timestamp"}]
    "context_menu": default_menu(),   # right-click menu of the log panels (see core/ctxmenu.py)
    "toolbar_style": "icon_text_under",   # icon | text | icon_text_beside | icon_text_under
    "toolbar_icon_size": 24,     # 16 | 20 | 24 | 32
    "ui_theme": "system",        # window theme: system (follow Windows) | light | dark
    "time_sync": True,           # scrolling / jumping in a linked tab moves the other linked tabs too
    "highlight_groups": {"Default": [r.to_dict() for r in DEFAULT_RULES]},
    "active_highlight_group": "Default",
    "find_history": [],
    "filter_history": [],
    "saved_filters": [],         # [{"name","text","regex","case","invert","context"}]
    "restore_session": True,
    "session": {},
}


def default_dir(portable: bool) -> Path:
    if portable:
        return Path(sys.argv[0]).resolve().parent / "LogScopeData"
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", str(Path.home()))) / "LogScope"
    return Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "logscope"


class Settings:
    def __init__(self, directory: Path) -> None:
        self.dir = Path(directory)
        self.path = self.dir / "settings.json"
        self.data: dict = copy.deepcopy(DEFAULTS)
        self.load()

    # ----------------------------------------------------------------- file io
    def load(self) -> None:
        try:
            text = self.path.read_text(encoding="utf-8-sig")
        except OSError:
            return                                   # no file yet (first start)
        try:
            loaded = json.loads(text)
            if not isinstance(loaded, dict):
                raise ValueError("settings root must be an object")
        except ValueError:
            try:                                     # keep the unreadable file for the user
                os.replace(self.path, self.path.with_name(self.path.name + ".bad"))
            except OSError:
                pass
            return
        old_rules = loaded.pop("highlight_rules", None)       # v0.1 -> groups
        if isinstance(old_rules, list) and "highlight_groups" not in loaded:
            loaded["highlight_groups"] = {"Default": old_rules}
        self.data.update(loaded)
        for k, default in DEFAULTS.items():                    # wrong types -> default
            if not isinstance(self.data.get(k), type(default)):
                self.data[k] = copy.deepcopy(default)

    def save(self) -> None:
        try:
            atomic_write_json(self.path, self.data)
        except OSError:
            pass

    def __getitem__(self, k):
        return self.data[k]

    def __setitem__(self, k, v) -> None:
        if self.data.get(k) != v:
            self.data[k] = v
            self.save()

    # ---------------------------------------------------------------- recents
    def add_recent(self, path: str, limit: int = 10) -> None:
        rec = [p for p in self.data["recent_files"] if p != path]
        self["recent_files"] = ([path] + rec)[:limit]

    # ------------------------------------------------------------------ theme
    def theme(self) -> dict:
        return sanitize_theme(self.data["theme"])

    def set_theme(self, theme: dict) -> None:
        self["theme"] = sanitize_theme(theme)

    def context_menu(self) -> list[dict]:
        return sanitize_menu(self.data["context_menu"])

    def set_context_menu(self, items) -> None:
        self["context_menu"] = sanitize_menu(items)

    # ------------------------------------------------------------ bookmark tags
    def remember_tags(self, tags) -> list[str]:
        known = normalize_tags(list(self.data["bookmark_tags"]) + list(tags))
        self["bookmark_tags"] = sorted(known, key=str.lower)
        return list(self.data["bookmark_tags"])

    # ---------------------------------------------------------------- history
    def push_history(self, key: str, text: str, limit: int = HISTORY_LIMIT) -> list[str]:
        """Most-recent-first, de-duplicated.  Returns the new list."""
        if not text:
            return list(self.data[key])
        hist = [t for t in self.data[key] if t != text]
        self[key] = ([text] + hist)[:limit]
        return list(self.data[key])

    # --------------------------------------------------------- saved filters
    def saved_filters(self) -> list[dict]:
        return [f for f in self.data["saved_filters"]
                if isinstance(f, dict) and isinstance(f.get("name"), str)
                and isinstance(f.get("text"), str) and f["text"]]

    def save_filter(self, spec: dict) -> None:
        """Insert or replace (by name)."""
        items = [f for f in self.saved_filters() if f["name"] != spec["name"]]
        items.append(dict(spec))
        items.sort(key=lambda f: f["name"].lower())
        self["saved_filters"] = items

    def delete_filter(self, name: str) -> None:
        self["saved_filters"] = [f for f in self.saved_filters() if f["name"] != name]

    # ------------------------------------------------------ highlight groups
    def highlight_groups(self) -> dict[str, list[HighlightRule]]:
        out: dict[str, list[HighlightRule]] = {}
        for name, rules in self.data["highlight_groups"].items():
            lst = []
            for d in rules if isinstance(rules, list) else []:
                try:
                    lst.append(HighlightRule(**d))
                except TypeError:
                    continue
            out[str(name)] = lst
        if not out:
            out["Default"] = list(DEFAULT_RULES)
        return out

    @property
    def active_group(self) -> str:
        groups = self.highlight_groups()
        name = self.data["active_highlight_group"]
        return name if name in groups else next(iter(groups))

    def highlight_rules(self) -> list[HighlightRule]:
        return self.highlight_groups()[self.active_group]

    def set_highlight_groups(self, groups: dict[str, list[HighlightRule]], active: str) -> None:
        if not groups:
            groups = {"Default": []}
        self.data["highlight_groups"] = {n: [r.to_dict() for r in rs] for n, rs in groups.items()}
        self.data["active_highlight_group"] = active if active in groups else next(iter(groups))
        self.save()

    def set_active_group(self, name: str) -> None:
        if name in self.data["highlight_groups"]:
            self["active_highlight_group"] = name

    # ------------------------------------------------------------ tab colours
    def tab_color(self, path: str) -> str:
        c = self.data["tab_colors"].get(path, "")
        return c if is_color(c) else ""

    def set_tab_color(self, path: str, color: str) -> None:
        colors = dict(self.data["tab_colors"])
        colors.pop(path, None)
        if is_color(color):
            colors[path] = color.lower()
        while len(colors) > MAX_TAB_COLORS:          # forget the oldest entries
            colors.pop(next(iter(colors)))
        self["tab_colors"] = colors

    # ---------------------------------------------------------------- session
    def last_session(self) -> dict:
        return sanitize_session(self.data["session"])
