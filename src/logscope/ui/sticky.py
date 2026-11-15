"""Sticky lines: tick a line in the Pin column and it stays at the top of its panel.

A :class:`StickyBox` wraps one panel table (main view, filter result or search results).  Above the
table it shows a small strip with the pinned lines, drawn like the table itself and kept in step with
it: the same columns and widths, the same horizontal scrolling, the same colours.  Unticking a line
in the strip (or in the table) removes it again.  Each panel has its own pinned lines.
"""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtWidgets import QFrame, QVBoxLayout, QWidget

from ..core.views import ListView
from .models import LogTableModel
from .tableview import LogTableView

MAX_STRIP_ROWS = 8          # more pinned lines than this scroll inside the strip
MAX_PINS = 1000


class StickyBox(QWidget):
    pins_changed = Signal()

    def __init__(self, tab, table: LogTableView, font, parent=None) -> None:
        super().__init__(parent)
        self.tab, self.table = tab, table
        self.pinned: set[int] = set()
        self.pview = ListView()
        self.pmodel = LogTableModel(tab.reader, self.pview, tab.registry.default, tab.highlight, tab.bookmarks)
        for m in (self.pmodel, table.model()):
            m.pinned = self.pinned
            m.on_pin = self.set_pinned
        self._main_vbar = False
        self._dirty = False

        self.strip = LogTableView(self.pmodel, font)
        self.strip.configure(False, False)
        self.strip.hscroll_target = table.horizontalScrollBar()      # Shift + wheel on the strip moves the table
        self.strip.horizontalHeader().hide()
        self.strip.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)   # follows the table
        self.strip.copied.connect(tab.status_message)
        self.rule = QFrame()
        self.rule.setFrameShape(QFrame.Shape.HLine)
        self.rule.setFrameShadow(QFrame.Shadow.Sunken)
        self.rule.setLineWidth(2)
        self.strip.hide()
        self.rule.hide()

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.strip)
        lay.addWidget(self.rule)
        lay.addWidget(table, 1)

        table.horizontalHeader().sectionResized.connect(self._section_resized)
        table.horizontalScrollBar().valueChanged.connect(self._follow_hscroll)
        table.horizontalScrollBar().rangeChanged.connect(lambda *_a: self._follow_hscroll())
        table.verticalScrollBar().rangeChanged.connect(self._main_vbar_range)

    # ------------------------------------------------------------------ pins
    def set_pinned(self, line: int, on: bool) -> None:
        """Called by the models when the Pin column is ticked / unticked."""
        if on:
            if len(self.pinned) >= MAX_PINS:
                self.tab.status_message.emit(f"At most {MAX_PINS} sticky lines per panel")
                return
            self.pinned.add(int(line))
        else:
            self.pinned.discard(int(line))
        self._schedule_rebuild()
        self.table.model().refresh_all()

    def set_pins(self, lines) -> None:
        """Restore pins (session): replaces the current ones."""
        self.pinned.clear()
        for ln in list(lines)[:MAX_PINS]:
            if isinstance(ln, int) and not isinstance(ln, bool) and ln >= 0:
                self.pinned.add(ln)
        self._rebuild()
        self.table.model().refresh_all()

    def prune(self, total: int) -> None:
        """The file got shorter (reset / rotated): forget pins beyond its end."""
        gone = {ln for ln in self.pinned if ln >= total}
        if gone:
            self.pinned -= gone
            self._rebuild()
            self.table.model().refresh_all()

    def _schedule_rebuild(self) -> None:
        """Rebuild after the click that caused it has been handled (the model must not reset mid-click)."""
        if not self._dirty:
            self._dirty = True
            QTimer.singleShot(0, self._rebuild)

    def _rebuild(self) -> None:
        self._dirty = False
        self.pview.clear()
        if self.pinned:
            self.pview.append(np.array(sorted(self.pinned), dtype=np.int64))
        self.pmodel.reset_rows(self.pview.n)
        shown = self.pview.n > 0
        self.strip.setVisible(shown)
        self.rule.setVisible(shown)
        if shown:
            self._resize_strip()
            self.sync_widths()
        self.pins_changed.emit()

    def _resize_strip(self) -> None:
        rows = min(self.pview.n, MAX_STRIP_ROWS)
        h = rows * self.strip.verticalHeader().defaultSectionSize() + 2 * self.strip.frameWidth()
        self.strip.setFixedHeight(max(h, 1))
        self._apply_vbar_policy()

    def _apply_vbar_policy(self) -> None:
        """Same scroll-bar gutter as the table, so the columns line up exactly."""
        need = self._main_vbar or self.pview.n > MAX_STRIP_ROWS
        self.strip.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn if need
                                              else Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

    def _main_vbar_range(self, _lo: int, hi: int) -> None:
        self._main_vbar = hi > 0
        self._apply_vbar_policy()
        QTimer.singleShot(0, self._follow_hscroll)

    # ------------------------------------------------------- stay in step with the table
    def _section_resized(self, logical: int, _old: int, new: int) -> None:
        if logical < self.pmodel.columnCount():
            self.strip.setColumnWidth(logical, new)

    def sync_widths(self) -> None:
        for c in range(min(self.table.model().columnCount(), self.pmodel.columnCount())):
            self.strip.setColumnWidth(c, self.table.columnWidth(c))
        self._follow_hscroll()

    def _follow_hscroll(self, *_args) -> None:
        self.strip.horizontalScrollBar().setValue(self.table.horizontalScrollBar().value())

    # ------------------------------------------------------------ called by the tab
    def set_columnizer(self, columnizer) -> None:
        self.pmodel.set_columnizer(columnizer)
        self.strip.reset_columns()
        self.sync_widths()

    def refresh(self) -> None:
        self.pmodel.refresh_all()

    def apply_theme(self, font, spec, selection, grid, marker: str) -> None:
        self.strip.apply_theme(font, spec, selection, grid)
        self.pmodel.marker = marker
        self.pmodel.refresh_all()
        if self.strip.isVisible():
            self._resize_strip()
        self.sync_widths()

    def set_row_style(self, fn, source_col: int) -> None:
        self.pmodel.row_style = fn
        self.pmodel.source_col = source_col
        self.pmodel.refresh_all()
