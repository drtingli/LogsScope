"""The right-click menu of the log panels (main view, filter result, search results and the sticky strips).

What the menu contains comes from ``settings["context_menu"]`` (see ``core.ctxmenu``); this module builds
it for the line under the mouse and carries out the chosen item."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QInputDialog, QMenu

from ..core import ctxmenu
from ..core.bookmarks import Bookmark
from .models import FULL_ROLE, GUTTER

MAX_COPY_LINES = 100_000
MAX_SELECTION_ARG = 2000        # lines passed to {selection}


def gather(tab, panel: str, table, view, pos, in_strip: bool) -> SimpleNamespace:
    """Everything the menu needs to know about the click: line, field, selection, state."""
    idx = table.indexAt(pos)
    has_row = idx.isValid()
    if has_row:
        sel = table.selectionModel()
        if not sel.isRowSelected(idx.row(), idx.parent()):
            table.selectRow(idx.row())                      # right-click on an unselected row selects it first
    lines = tab.selected_lines((table, view))
    line = view.line_number(idx.row()) if has_row else (int(lines[0]) if lines.size else -1)
    if has_row and lines.size == 0:
        lines = np.array([line], dtype=np.int64)
    field = ""
    if has_row and idx.column() >= GUTTER:
        field = table.model().data(idx, FULL_ROLE) or ""
    box = {"main": tab.box_main, "filter": tab.box_filter, "search": tab.box_results}[panel]
    ln_list = [int(x) for x in lines]
    return SimpleNamespace(
        tab=tab, panel=panel, table=table, view=view, in_strip=in_strip, box=box,
        has_row=has_row and line >= 0, has_field=bool(field), field=field, line=line, lines=lines,
        n_selected=int(lines.size), derived=tab.derived,
        all_bookmarked=bool(ln_list) and all(x in tab.bookmarks for x in ln_list),
        all_pinned=bool(ln_list) and all(x in box.pinned for x in ln_list))


def build_menu(tab, c: SimpleNamespace) -> QMenu:
    menu = QMenu(c.table)
    menu.setToolTipsVisible(True)
    items = ctxmenu.sanitize_menu(tab.settings["context_menu"])
    ctx = vars(c)
    last_sep = True
    for item in items:
        state = ctxmenu.visibility(item, ctx)
        if state == "hide":
            continue
        if item["type"] == "separator":
            if not last_sep:
                menu.addSeparator()
                last_sep = True
            continue
        act = menu.addAction(ctxmenu.dynamic_label(item, ctx))
        act.setEnabled(state == "show")
        if item["type"] == "builtin":
            act.setToolTip(ctxmenu.BUILTINS[item["id"]][2])
        else:
            act.setToolTip(f"{item['command']} {item['args']}".strip())
        act.triggered.connect(lambda _checked=False, it=item: run_item(tab, it, c))
        last_sep = False
    if menu.isEmpty():                                        # never leave the user without a way back
        act = menu.addAction(ctxmenu.BUILTINS["customize"][0])
        act.triggered.connect(lambda _checked=False: tab.request.emit("customize_menu", {}))
    return menu


def show_context_menu(tab, panel: str, table, view, pos, in_strip: bool = False) -> None:
    c = gather(tab, panel, table, view, pos, in_strip)
    build_menu(tab, c).exec(table.viewport().mapToGlobal(pos))


# --------------------------------------------------------------------------- running an item
def _texts(tab, lines, limit: int) -> list[str]:
    return [tab.reader.get_line(int(ln)) for ln in lines[:limit]]


def run_item(tab, item: dict, c: SimpleNamespace) -> None:
    try:
        if item["type"] == "command":
            _run_command(tab, item, c)
        else:
            _run_builtin(tab, item["id"], c)
    except Exception as exc:                                   # a menu action must never take the window down
        tab.status_message.emit(f"Context menu: {exc}")


def _clip(text: str, what: str, tab) -> None:
    QGuiApplication.clipboard().setText(text)
    tab.status_message.emit(what)


def _run_builtin(tab, iid: str, c: SimpleNamespace) -> None:
    lines = c.lines
    if iid == "copy_lines":
        c.table.copy_selected_lines()
    elif iid == "copy_field":
        _clip(c.field, f"Copied {len(c.field):,} characters to the clipboard", tab)
    elif iid == "copy_numbered":
        rows = [f"{int(ln) + 1}\t{txt}" for ln, txt in zip(lines[:MAX_COPY_LINES], _texts(tab, lines, MAX_COPY_LINES))]
        _clip("\n".join(rows), f"Copied {len(rows):,} line(s) with line numbers to the clipboard", tab)
    elif iid == "copy_location":
        rows = [f"{tab.path}:{int(ln) + 1}" for ln in lines[:1000]]
        _clip("\n".join(rows), f"Copied {len(rows):,} location(s) to the clipboard", tab)
    elif iid == "find_text":
        finder = getattr(c.table, "panel_finder", None)
        if finder is not None:
            finder.show_bar(c.field)
    elif iid in ("filter_include", "filter_exclude"):
        tab.show_filter(True)
        tab.set_filter_spec({"text": c.field, "regex": False, "case": True, "invert": iid == "filter_exclude",
                             "context": 0}, apply=True)
    elif iid == "highlight_text":
        tab.request.emit("highlight_text", {"text": c.field})
    elif iid == "bookmark":
        on = not c.all_bookmarked
        for ln in (int(x) for x in lines):
            if on and ln not in tab.bookmarks:
                tab.bookmarks[ln] = Bookmark()
            elif not on:
                tab.bookmarks.pop(ln, None)
        tab._bookmarks_modified()
    elif iid == "bookmark_comment":
        first = int(lines[0])
        current = tab.bookmarks[first].comment if first in tab.bookmarks else ""
        text, ok = QInputDialog.getText(tab, "Bookmark", f"Comment for {lines.size:,} line(s):", text=current)
        if ok:
            for ln in (int(x) for x in lines):
                bm = tab.bookmarks.get(ln)
                if bm is None:
                    bm = tab.bookmarks[ln] = Bookmark()
                bm.comment = text.strip()
            tab._bookmarks_modified()
    elif iid == "pin":
        on = not c.all_pinned
        for ln in (int(x) for x in lines):
            c.box.set_pinned(ln, on)
    elif iid == "goto_main":
        tab.goto_line(c.line)
    elif iid == "time_diff":
        tab.request.emit("time_diff", {"lines": [int(x) for x in lines]})
    elif iid == "selection_to_tab":
        tab.lines_to_tab(lines, f"{tab.title} [selection]")
    elif iid == "jump_source":
        tab.request.emit("jump_source", {"line": c.line})
    elif iid == "customize":
        tab.request.emit("customize_menu", {})


def _run_command(tab, item: dict, c: SimpleNamespace) -> None:
    first = int(c.lines[0]) if c.lines.size else max(c.line, 0)
    values = {
        "line": tab.reader.get_line(first) if c.has_row else "",
        "field": c.field,
        "lineno": str(first + 1) if c.has_row else "",
        "file": tab.path,
        "selection": "\n".join(_texts(tab, c.lines, MAX_SELECTION_ARG)),
    }
    ctxmenu.launch_command(item["command"], item["args"], values)
    tab.status_message.emit(f"Started: {item['label']}")
