"""Ctrl+F / F3 inside any panel (main view, filter result, search results, bookmarks).

Every searchable table carries a ``panel_finder`` attribute with two methods -- ``show_bar()``
(Ctrl+F) and ``step(forward)`` (F3 / Shift+F3).  The main window looks for it from the widget that
has the keyboard focus, so the same keys search whichever panel you are working in.  Searching
wraps around at the end (or the beginning) of the panel.
"""

from __future__ import annotations

import re

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton, QTextEdit,
    QToolButton, QWidget,
)

from ..core.search import LineMatcher, find_in_rows, selection_to_query
from .widgets import HistoryCombo
from .workers import FuncWorker


def focused_finder(widget):
    """The ``panel_finder`` of ``widget`` or of its closest ancestor that has one."""
    w = widget
    while w is not None:
        f = getattr(w, "panel_finder", None)
        if f is not None:
            return f
        w = w.parentWidget()
    return None


def selected_text(widget) -> str:
    """Text the user has selected inside ``widget`` (a field opened by double-click, a text box ...).

    Empty when nothing is selected, or when ``widget`` belongs to a find bar itself."""
    w = widget
    while w is not None:
        if getattr(w, "is_find_bar", False):
            return ""
        w = w.parentWidget()
    if isinstance(widget, QLineEdit):
        return widget.selectedText()
    if isinstance(widget, (QPlainTextEdit, QTextEdit)):
        return widget.textCursor().selectedText()
    if hasattr(widget, "lineEdit") and callable(widget.lineEdit):       # an editable combo box
        le = widget.lineEdit()
        return le.selectedText() if le is not None else ""
    return ""


class MainFinder:
    """Adapter: the main table keeps using the tab's own find bar (with 'Find all')."""

    def __init__(self, tab) -> None:
        self.tab = tab

    def show_bar(self, text: str = "") -> None:
        self.tab.show_find(text)

    def step(self, forward: bool) -> None:
        self.tab.find(forward)


class RowsBackend:
    """Rows of a filter / search-result table: ``view`` maps rows to file lines."""

    def __init__(self, tab, table, view) -> None:
        self.tab, self.table, self.view = tab, table, view
        self._worker: FuncWorker | None = None

    def current_row(self) -> int:
        idx = self.table.currentIndex()
        return idx.row() if idx.isValid() else -1

    def search(self, matcher: LineMatcher, forward: bool, done) -> None:
        self.cancel()
        start = self.current_row()
        lines = self.view.snapshot()
        reader = self.tab.reader
        w = FuncWorker(lambda cancel, emit: find_in_rows(reader, matcher, lines, start, forward, cancel), self.tab)
        w.done.connect(lambda res, w=w: self._finished(w, done, res))
        self._worker = w
        w.start()

    def _finished(self, w, done, res) -> None:
        if w is not self._worker:
            return                                           # a newer search replaced this one
        self._worker = None
        if isinstance(res, BaseException):
            done(-1, False, str(res))
        else:
            done(res[0], res[1], "")

    def cancel(self) -> None:
        if self._worker is not None:
            self._worker.cancel.set()
            self._worker = None

    def goto(self, row: int) -> None:
        self.table.selectRow(row)
        self.table.scrollTo(self.table.model().index(row, 0), QAbstractItemView.ScrollHint.PositionAtCenter)
        self.table.setFocus()


class ItemRowsBackend:
    """Rows of a ``QTableWidget`` (the bookmark list); searches the text of all its cells."""

    def __init__(self, table) -> None:
        self.table = table

    def search(self, matcher: LineMatcher, forward: bool, done) -> None:
        t = self.table
        n = t.rowCount()
        cur = t.currentRow()
        if n == 0:
            done(-1, False, "")
            return
        if forward:
            first = cur + 1 if cur >= 0 else 0
            order = [(r, False) for r in range(first, n)] + [(r, True) for r in range(0, first)]
        else:
            first = cur - 1 if cur >= 0 else n - 1
            order = [(r, False) for r in range(first, -1, -1)] + [(r, True) for r in range(n - 1, first, -1)]
        for r, wrapped in order:
            cells = []
            for c in range(t.columnCount()):
                it = t.item(r, c)
                if it is not None:
                    cells.append(it.text())
            if matcher.match("\t".join(cells)):
                done(r, wrapped, "")
                return
        done(-1, False, "")

    def cancel(self) -> None:
        pass

    def goto(self, row: int) -> None:
        self.table.selectRow(row)
        self.table.scrollToItem(self.table.item(row, 0), QAbstractItemView.ScrollHint.PositionAtCenter)
        self.table.setFocus()


class PanelFindBar(QWidget):
    """A one-line find bar that sits under a panel and searches it through ``backend``."""

    def __init__(self, settings, backend, parent=None) -> None:
        super().__init__(parent)
        self.settings, self.backend = settings, backend
        self.panel_finder = self                       # focus inside the bar still means "this panel"
        self.is_find_bar = True                        # a selection inside the bar is not a search term
        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 2, 4, 2)
        self.edit = HistoryCombo("Find in this panel…")
        self.edit.set_history(settings["find_history"])
        self.edit.submitted.connect(lambda: self.step(True))
        self.regex = QCheckBox("Regex")
        self.case = QCheckBox("Case")
        prev_b, next_b = QPushButton("Prev"), QPushButton("Next")
        prev_b.setToolTip("Previous match (Shift+F3)")
        next_b.setToolTip("Next match (F3)")
        prev_b.clicked.connect(lambda: self.step(False))
        next_b.clicked.connect(lambda: self.step(True))
        close = QToolButton()
        close.setText("\u00d7")
        close.setToolTip("Close (Esc)")
        close.clicked.connect(self.hide_bar)
        self.label = QLabel("")
        lay.addWidget(QLabel("Find:"))
        lay.addWidget(self.edit, 1)
        for w in (self.regex, self.case, prev_b, next_b, self.label, close):
            lay.addWidget(w)
        esc = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        esc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        esc.activated.connect(self.hide_bar)
        self.hide()

    # ----------------------------------------------------------- panel_finder
    def show_bar(self, text: str = "") -> None:
        """Open the bar; ``text`` (the selection on screen) replaces what is in the box."""
        self.show()
        query = selection_to_query(text, self.regex.isChecked())
        if query:
            self.edit.set_text(query)
        self.edit.focus_and_select()

    def hide_bar(self) -> None:
        self.backend.cancel()
        self.hide()

    def step(self, forward: bool) -> None:
        text = self.edit.text()
        if not text:
            self.show_bar()
            return
        try:
            matcher = LineMatcher(text, self.regex.isChecked(), self.case.isChecked())
        except re.error as exc:
            self.label.setText(f"Invalid regex: {exc}")
            return
        self.edit.set_history(self.settings.push_history("find_history", text))
        self.label.setText("Searching\u2026")
        self.backend.search(matcher, forward, self._done)

    def _done(self, row: int, wrapped: bool, error: str) -> None:
        if error:
            self.label.setText(error)
        elif row < 0:
            self.label.setText("Not found")
        else:
            self.backend.goto(row)
            self.label.setText(f"Row {row + 1:,}" + ("  (wrapped around)" if wrapped else ""))
