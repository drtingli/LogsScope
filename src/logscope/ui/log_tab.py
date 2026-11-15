"""One log window: table + find bar + filter / search-result panels (LogWindow)."""

from __future__ import annotations

import os
import re
import time
import weakref
from functools import partial

import numpy as np
from PySide6.QtCore import QItemSelectionModel, QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QFont, QFontDatabase, QPalette
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QFileDialog, QMessageBox, QHBoxLayout, QInputDialog, QLabel, QMenu,
    QPushButton, QSpinBox, QSplitter, QTabWidget, QToolButton, QVBoxLayout, QWidget,
)

from ..core import actions, derive
from ..core.bookmarks import Bookmark, bookmarks_from_list, bookmarks_to_list, clean_marker, normalize_tags
from ..core.columnizers import MergedColumnizer, TimestampColumnizer
from ..core.filters import has_actions, read_filters_file, write_filters_file
from ..core.reader import LogFileReader
from ..core.triggers import scan_and_run
from ..core.search import (
    FilterParams, LineMatcher, find_next, plan_incremental, scan_matches, selection_to_query,
)
from ..core.session import sanitize_filter, sanitize_find
from ..core.timestamps import MAX_LINES, DeltaReport, TimestampParser, compute_delta
from ..core.timesync import is_time_only, nearest_line, time_near
from ..core.views import FullView, ListView
from ..core.widths import measure_columns
from .dialogs import ActionDialog
from .findbar import MainFinder, PanelFindBar, RowsBackend
from .look import area_font
from .models import GUTTER, LogTableModel
from .contextmenu import show_context_menu
from .sticky import StickyBox
from .tableview import LogTableView
from .widgets import HistoryCombo
from .workers import FuncWorker, ReaderWorker

SAMPLE_LINES = 200
FILTER_TAB, RESULTS_TAB = 0, 1


def make_table(model: LogTableModel, font: QFont) -> LogTableView:
    return LogTableView(model, font)


class PanelFit:
    """Measures the widest text of one table in a background thread and feeds the table.
    Requests are coalesced; widths only grow until ``reset``."""

    def __init__(self, tab: "LogTab", table: LogTableView) -> None:
        self.tab, self.table = tab, table
        self.worker: FuncWorker | None = None
        self.gen = 0
        self._parts: list[np.ndarray] = []
        self._reset = self._deep = self._force = False
        self.timer = QTimer(tab)
        self.timer.setSingleShot(True)
        self.timer.setInterval(250)
        self.timer.timeout.connect(self._run)

    def reset(self) -> None:
        """Forget everything (new columnizer / new result set / file reset)."""
        self.gen += 1
        self._parts, self._reset, self._deep, self._force = [], False, False, False
        self.table.reset_columns()

    def request(self, lines: np.ndarray, reset: bool = False, deep: bool = False, force: bool = False) -> None:
        if reset:
            self._parts, self._reset = [], True
            self.gen += 1
        self._parts.append(np.asarray(lines, dtype=np.int64))
        self._deep |= deep
        self._force |= force
        self.timer.start()

    def _run(self) -> None:
        if self.worker is not None:
            return                                    # _done runs again
        if not self._parts and not self._reset:
            return
        lines = np.concatenate(self._parts) if self._parts else np.empty(0, dtype=np.int64)
        reset, deep, force, gen = self._reset, self._deep, self._force, self.gen
        self._parts, self._reset, self._deep, self._force = [], False, False, False
        reader, columnizer = self.tab.reader, self.tab.columnizer

        def job(cancel, emit):
            return measure_columns(reader, columnizer, lines, cancel, deep)

        w = FuncWorker(job, self.tab)
        w.done.connect(partial(self._done, w, gen, reset, force))
        self.worker = w
        w.start()

    def _done(self, w, gen, reset, force, result) -> None:
        if w is not self.worker:
            return
        self.worker = None
        if not isinstance(result, Exception) and result is not False and gen == self.gen:
            self.table.set_field_units(result, reset=reset, force=force)
        if self._parts or self._reset:
            self.timer.start()

    def stop(self) -> None:
        self.timer.stop()
        if self.worker is not None:
            self.worker.cancel.set()
            self.worker.wait(2000)


class LogTab(QWidget):
    follow_changed = Signal(bool)
    title_changed = Signal(str)
    bookmarks_changed = Signal()
    status_message = Signal(str)
    delta_changed = Signal(str)             # time difference of the selected lines ("" = none)
    look_changed = Signal()                 # tab colour / title changed -> host redraws its tab bar
    derive_ready = Signal(object, str, object, str, object)   # tab, file, origin lines, title, bookmarks
    time_sync_requested = Signal(object, int)   # this tab scrolled / jumped to a line: align the linked tabs
    link_changed = Signal()                     # linked on/off or time offset changed
    columnizer_changed = Signal()               # another columnizer is in use (chosen, detected or redefined)
    activity = Signal()                         # new lines arrived after the initial load (drives the tab LED)
    request = Signal(str, dict)                 # context-menu items the main window carries out (time_diff ...)

    def __init__(self, path: str, settings, registry, highlight, *, title: str | None = None,
                 origin: np.ndarray | None = None, source=None, encoding: str | None = None,
                 merge=None, parent=None) -> None:
        super().__init__(parent)
        self.settings, self.registry, self.highlight = settings, registry, highlight
        self.merge = merge                             # MergeInfo when this tab merges several files
        self.linked = False                            # takes part in time sync / merging
        self._linked_wanted = False                    # restore the link once timestamps can be read
        self.ts_offset = 0.0                           # seconds added to this file's timestamps
        self._sync_line: int | None = None
        self._silent_until = 0.0                       # ignore our own scrolling after a sync
        self._pending_reveal: tuple[int, bool] | None = None
        self._base_col = registry.default              # the columnizer before a merge wraps it
        self._find_from, self._find_fwd = -1, True
        self.path = path
        self.bookmarks: dict[int, Bookmark] = {}       # 0-based line -> comment + tags
        self.derived = origin is not None              # "to new tab" result: a temporary file
        self.origin_lines = origin                     # new line n came from source line origin_lines[n]
        self._source_ref = weakref.ref(source) if source is not None else None
        self.source_path = source.path if source is not None else ""
        self.saved_path = ""                           # where "Save as" last copied a derived file
        self.tab_color = "" if self.derived else settings.tab_color(path)
        self.ts_spec = settings["timestamp_format"]    # "" = detect automatically
        self.ts_parser = TimestampParser.none()
        self._ts_probed = -1                           # line count at the last failed detection
        self._closed = False
        self._delta_text = ""
        self._delta_src = None
        self._derive_worker: FuncWorker | None = None
        self._follow = bool(settings["follow_tail"]) and not self.derived
        self._worker: ReaderWorker | None = None
        self._filter_worker: FuncWorker | None = None
        self._search_worker: FuncWorker | None = None
        self._results_worker: FuncWorker | None = None
        self._filter_active = False
        self._filter_params = FilterParams()
        self._filter_scanned = 0
        self._filter_dirty_from: int | None = None
        self._columnizer_forced = False
        self._pending_line: int | None = None          # restore position once indexed
        self._pending_filter: dict | None = None       # restore filter once there are lines
        self._results_scanned = 0
        self._results_matcher: LineMatcher | None = None   # set while 'Find all' results are live
        self._results_dirty_from: int | None = None
        self._filter_settled = False       # first full scan done -> auto-scroll on growth
        self._results_settled = False
        self._filter_busy = False          # a scan's batches/done may still be queued
        self._action: dict | None = None   # program to start when the filter matches a new line
        self._trigger_params: FilterParams | None = None
        self._trigger_armed = self._trigger_wait_arm = self._trigger_busy = False
        self._trigger_scanned = 0
        self._trigger_worker: FuncWorker | None = None
        self._caught_up = False
        self._results_busy = False
        self.title = title or os.path.basename(path)

        reader = LogFileReader(path, encoding or settings["encoding"] or None)
        self.reader = reader

        font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        if settings["font_family"]:
            font.setFamily(settings["font_family"])
        font.setPointSize(int(settings["font_size"]))

        # --- main view -------------------------------------------------------
        self.view = FullView()
        self.model = LogTableModel(reader, self.view, registry.default, highlight,
                                   self.bookmarks, initial_rows=0)
        self.columnizer = registry.default
        self.table = make_table(self.model, font)
        self.table.verticalScrollBar().actionTriggered.connect(self._on_user_scroll)

        # --- find bar --------------------------------------------------------
        self.find_bar = QWidget()
        self.find_bar.is_find_bar = True                     # selections in it are not search terms
        fb = QHBoxLayout(self.find_bar)
        fb.setContentsMargins(4, 2, 4, 2)
        self.find_edit = HistoryCombo("Find…")
        self.find_edit.set_history(settings["find_history"])
        self.find_edit.submitted.connect(lambda: self.find(True))
        self.find_regex = QCheckBox("Regex")
        self.find_case = QCheckBox("Case")
        prev_b, next_b = QPushButton("Prev"), QPushButton("Next")
        all_b = QPushButton("Find all")
        all_b.setToolTip("List every match in the 'Search results' panel")
        prev_b.clicked.connect(lambda: self.find(False))
        next_b.clicked.connect(lambda: self.find(True))
        all_b.clicked.connect(self.find_all)
        self.find_label = QLabel("")
        fb.addWidget(QLabel("Find:"))
        fb.addWidget(self.find_edit, 1)
        for w in (self.find_regex, self.find_case, prev_b, next_b, all_b, self.find_label):
            fb.addWidget(w)
        self.find_bar.hide()

        # --- bottom panel: Filter | Search results ----------------------------
        self.fview = ListView()
        self.fmodel = LogTableModel(reader, self.fview, registry.default, highlight, self.bookmarks)
        self.ftable = make_table(self.fmodel, font)
        self.ftable.doubleClicked.connect(lambda ix: self._jump_from(self.fview, ix))
        self.ftable.activated.connect(lambda ix: self._jump_from(self.fview, ix))

        self.sview = ListView()
        self.smodel = LogTableModel(reader, self.sview, registry.default, highlight, self.bookmarks)
        self.stable = make_table(self.smodel, font)
        self.stable.doubleClicked.connect(lambda ix: self._jump_from(self.sview, ix))
        self.stable.activated.connect(lambda ix: self._jump_from(self.sview, ix))

        # sticky lines: a strip above each panel holds the lines ticked in the Pin column
        self.box_main = StickyBox(self, self.table, font)
        self.box_filter = StickyBox(self, self.ftable, font)
        self.box_results = StickyBox(self, self.stable, font)
        self.boxes = (self.box_main, self.box_filter, self.box_results)
        for tbl, vw in ((self.table, self.view), (self.ftable, self.fview), (self.stable, self.sview)):
            tbl.mark_double_clicked.connect(lambda row, v=vw: self._toggle_bookmark_line(v.line_number(row)))
        for panel, tbl, vw in (("main", self.table, self.view), ("filter", self.ftable, self.fview),
                               ("search", self.stable, self.sview)):
            tbl.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            tbl.customContextMenuRequested.connect(
                lambda pos, p=panel, t=tbl, v=vw: show_context_menu(self, p, t, v, pos))
        for panel, box in zip(("main", "filter", "search"), self.boxes):
            box.strip.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            box.strip.customContextMenuRequested.connect(
                lambda pos, p=panel, b=box: show_context_menu(self, p, b.strip, b.pview, pos, True))
        for box in self.boxes:
            box.strip.mark_double_clicked.connect(
                lambda row, b=box: self._toggle_bookmark_line(b.pview.line_number(row)))
            box.strip.doubleClicked.connect(
                lambda ix, b=box: self.goto_line(b.pview.line_number(ix.row())) if ix.isValid() else None)

        self.main_finder = MainFinder(self)                  # Ctrl+F / F3 work in every panel
        self.table.panel_finder = self.main_finder
        self.find_bar.panel_finder = self.main_finder
        self.ffind = PanelFindBar(settings, RowsBackend(self, self.ftable, self.fview))
        self.sfind = PanelFindBar(settings, RowsBackend(self, self.stable, self.sview))
        self.ftable.panel_finder = self.ffind
        self.stable.panel_finder = self.sfind
        for box, tbl in zip(self.boxes, (self.table, self.ftable, self.stable)):
            box.strip.panel_finder = tbl.panel_finder        # Ctrl+F also works with the focus in a strip

        self.fit_main = PanelFit(self, self.table)
        self.fit_filter = PanelFit(self, self.ftable)
        self.fit_results = PanelFit(self, self.stable)
        for t in (self.table, self.ftable, self.stable):
            t.copied.connect(self.status_message)
            t.configure(bool(settings["autofit_columns"]), bool(settings["wrap_text"]))
        self.apply_theme(settings.theme())

        self._delta_timer = QTimer(self)                 # time difference follows the selection
        self._delta_timer.setSingleShot(True)
        self._delta_timer.setInterval(200)
        self._delta_timer.timeout.connect(self._update_delta)
        for tbl, vw in ((self.table, self.view), (self.ftable, self.fview), (self.stable, self.sview)):
            tbl.selectionModel().selectionChanged.connect(partial(self._on_selection, tbl, vw))

        self._sync_timer = QTimer(self)                  # scrolling / jumping -> tell the linked tabs
        self._sync_timer.setSingleShot(True)
        self._sync_timer.setInterval(90)
        self._sync_timer.timeout.connect(self._emit_sync)
        self.table.verticalScrollBar().valueChanged.connect(self._on_scrolled)
        if self.merge is not None:
            self.refresh_merge_style()

        self.bottom = QTabWidget()
        self.bottom.addTab(self._build_filter_page(settings), "Filter")
        self.bottom.addTab(self._build_results_page(), "Search results")
        self.bottom.hide()

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(self.box_main)
        splitter.addWidget(self.bottom)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(splitter, 1)
        lay.addWidget(self.find_bar)

        self._worker = ReaderWorker(reader, self)
        self._worker.lines_changed.connect(self._on_lines_changed)
        self._worker.reset.connect(self._on_reset)
        self._worker.caught_up.connect(self._on_caught_up)
        self._worker.failed.connect(lambda m: self.filter_label.setText(f"Reader error: {m}"))
        self._worker.start()

    def _build_filter_page(self, settings) -> QWidget:
        page = QWidget()
        fl = QVBoxLayout(page)
        fl.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        self.filter_edit = HistoryCombo("Filter (show only matching lines)…", apply_on_pick=True)
        self.filter_edit.set_history(settings["filter_history"])
        self.filter_edit.submitted.connect(self.apply_filter)
        self.filter_regex = QCheckBox("Regex")
        self.filter_case = QCheckBox("Case")
        self.filter_invert = QCheckBox("Invert")
        self.filter_ctx = QSpinBox()
        self.filter_ctx.setRange(0, 50)
        self.filter_ctx.setPrefix("±")
        self.filter_ctx.setToolTip("Spread: extra lines around each match")
        self.filter_tail = QCheckBox("Live")
        self.filter_tail.setChecked(bool(settings["filter_tail"]))
        self.filter_tail.setToolTip("Keep applying the filter to new lines as the file grows")
        self.filter_tail.toggled.connect(self._on_filter_live_toggled)
        go, stop, to_tab = QPushButton("Apply"), QPushButton("Stop"), QPushButton("To tab")
        self.action_btn = QPushButton("Action…")
        self.action_btn.setToolTip("Run a program when this filter matches a new line")
        self.action_btn.clicked.connect(self._edit_action)
        go.clicked.connect(self.apply_filter)
        stop.clicked.connect(self.cancel_filter)
        to_tab.setToolTip("Show the filter result in a new tab (then save or filter it further)")
        to_tab.clicked.connect(self.filter_to_tab)

        self.saved_btn = QToolButton()
        self.saved_btn.setText("Saved filters")
        self.saved_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.saved_menu = QMenu(self.saved_btn)
        self.saved_btn.setMenu(self.saved_menu)
        self.saved_menu.aboutToShow.connect(self._rebuild_saved_menu)

        self.filter_label = QLabel("")
        row.addWidget(self.filter_edit, 1)
        for w in (self.filter_regex, self.filter_case, self.filter_invert, self.filter_ctx,
                  self.filter_tail, go, stop, to_tab, self.action_btn, self.saved_btn, self.filter_label):
            row.addWidget(w)
        fl.addLayout(row)
        fl.addWidget(self.box_filter)
        fl.addWidget(self.ffind)
        return page

    def _build_results_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        top = QHBoxLayout()
        self.results_label = QLabel("Use 'Find all' in the find bar (Ctrl+F). Double-click a result to jump to it.")
        self.results_live = QCheckBox("Live")
        self.results_live.setChecked(bool(self.settings["search_live"]))
        self.results_live.setToolTip("Keep adding matches from new lines as the file grows")
        self.results_live.toggled.connect(self._on_results_live_toggled)
        res_tab = QPushButton("To tab")
        res_tab.setToolTip("Show these search results in a new tab (then save or filter them further)")
        res_tab.clicked.connect(self.results_to_tab)
        top.addWidget(self.results_label, 1)
        top.addWidget(self.results_live)
        top.addWidget(res_tab)
        lay.addLayout(top)
        lay.addWidget(self.box_results)
        lay.addWidget(self.sfind)
        return page

    # ------------------------------------------------------------- lifecycle
    def shutdown(self) -> None:
        self._closed = True
        workers = (self._worker, self._filter_worker, self._search_worker, self._results_worker,
                   self._trigger_worker, self._derive_worker)
        for w in workers:
            if w is not None:
                if hasattr(w, "stop"):
                    w.stop()
                if hasattr(w, "cancel"):
                    w.cancel.set()
        for w in workers:
            if w is not None:
                w.wait(2000)
        for f in (self.fit_main, self.fit_filter, self.fit_results):
            f.stop()
        self.reader.release()
        if self.derived:
            derive.remove_file(self.path)           # temporary file; "Save as" copies are untouched

    # ------------------------------------------------------- reader signals
    def _on_reset(self) -> None:
        for b in self.boxes:
            b.set_pins([])
        self.bookmarks.clear()
        self.bookmarks_changed.emit()
        self._pending_line = None
        self._trigger_scanned = 0              # the new file's lines are all new
        self.model.reset_rows(0)
        self.fit_main.reset()
        self.fit_results.reset()
        self.sview.clear()
        self.smodel.reset_rows(0)
        if self._results_matcher is not None and self.results_live.isChecked():
            self._restart_results()
        else:
            self._results_matcher = None
            self.results_label.setText("File was reset; run 'Find all' again.")
        if self._filter_active:
            self._restart_filter()
        else:
            self.fmodel.reset_rows(0)
            self.fit_filter.reset()

    def _on_lines_changed(self, first: int, total: int) -> None:
        had_rows = self.model.rowCount()
        if self._caught_up and total > had_rows:
            self.activity.emit()
        if any(b.pinned for b in self.boxes):
            for b in self.boxes:
                b.prune(total)                                       # the file may have become shorter
        self.model.lines_changed(first, total)
        if had_rows == 0 and total > 0 and not self._columnizer_forced:
            self._apply_columnizer(self._auto_columnizer())
        if self._linked_wanted and total > 0:
            if self.set_linked(True) or total >= SAMPLE_LINES:
                self._linked_wanted = False
        for t in (self.table, self.ftable, self.stable):
            t.set_line_digits(total)
        if total > first:
            self.fit_main.request(np.arange(first, total, dtype=np.int64))
        if self._pending_filter is not None and total > 0:
            spec, self._pending_filter = self._pending_filter, None
            self.set_filter_spec(spec, apply=True)
        if self._follow:
            self.table.scrollToBottom()
        elif self._pending_line is not None and total > self._pending_line:
            line, self._pending_line = self._pending_line, None
            self.goto_line(line)
        if self._filter_active and self.filter_tail.isChecked():
            self._filter_dirty_from = first if self._filter_dirty_from is None \
                else min(self._filter_dirty_from, first)
            self._filter_pump()
        if self._results_matcher is not None and self.results_live.isChecked():
            self._results_dirty_from = first if self._results_dirty_from is None \
                else min(self._results_dirty_from, first)
            self._results_pump()
        self._trigger_pump()
        self.title_changed.emit(self.title)

    def _auto_columnizer(self):
        n = min(SAMPLE_LINES, self.reader.count)
        sample = [self.reader.get_line(i) for i in range(n)]
        self.ts_parser, self._ts_probed = TimestampParser.none(), -1
        self.get_ts_parser()
        if not self.derived and self.merge is None:      # a rule bound to this file name wins over detection
            bound = self.registry.for_path(self.path)
            if bound is not None:
                return bound
        return self.registry.auto_detect(sample)

    # ------------------------------------------------------ timestamps / Δt
    def get_ts_parser(self) -> TimestampParser:
        """The timestamp format of this tab (detected from the first lines unless one is set)."""
        count = self.reader.count
        if not self.ts_parser.usable and count > 0 and count != self._ts_probed:
            self._ts_probed = count                     # do not retry until the file has grown
            n = min(SAMPLE_LINES, count)
            self.ts_parser = TimestampParser.from_spec(
                self.ts_spec, [self.reader.get_line(i) for i in range(n)])
        return self.ts_parser

    def set_timestamp_spec(self, spec: str) -> None:
        """'' = automatic, 'builtin:<name>' or a strptime format such as %d.%m.%Y %H:%M:%S,%f."""
        self.ts_spec = spec or ""
        self.ts_parser, self._ts_probed = TimestampParser.none(), -1
        self.get_ts_parser()
        if self.linked and not self.ts_parser.usable:
            self.linked = False                          # without timestamps a tab cannot stay linked
        if isinstance(self._base_col, TimestampColumnizer):
            self._apply_columnizer(self._base_col)
        self._delta_timer.start()
        self.link_changed.emit()

    def delta_for(self, lines, limit: int = MAX_LINES) -> DeltaReport:
        return compute_delta(lines, self.reader.get_line, self.get_ts_parser(), limit)

    def _active_table(self):
        for tbl, vw in ((self.table, self.view), (self.ftable, self.fview), (self.stable, self.sview)):
            if tbl.hasFocus() or tbl.viewport().hasFocus():
                return tbl, vw
        return self._delta_src or (self.table, self.view)

    def selected_lines(self, source=None) -> np.ndarray:
        """Line numbers (sorted, unique) selected in a panel (default: the active one)."""
        tbl, vw = source or self._active_table()
        rows = tbl.model().rowCount()
        parts = []
        try:
            ranges = [(r.top(), r.bottom()) for r in tbl.selectionModel().selection()]
        except TypeError:                                   # binding without iteration support
            ranges = [(i.row(), i.row()) for i in tbl.selectionModel().selectedRows()]
        for first, last in ranges:
            top, bot = max(first, 0), min(last, rows - 1)
            if bot < top:
                continue
            if vw.is_full:
                parts.append(np.arange(top, bot + 1, dtype=np.int64))
            else:
                parts.append(vw.slice(top, bot + 1))
        return np.unique(np.concatenate(parts)) if parts else np.empty(0, dtype=np.int64)

    def _on_selection(self, tbl, vw, *_args) -> None:
        self._delta_src = (tbl, vw)
        self._delta_timer.start()

    def _update_delta(self) -> None:
        text = ""
        if self._delta_src is not None:
            lines = self.selected_lines(self._delta_src)
            if lines.size >= 2 and self.get_ts_parser().usable:
                rep = self.delta_for(lines, limit=20_000)
                text = rep.summary() if rep.ok else \
                    f"\u0394t: {rep.stamped} of {rep.selected} selected lines have a timestamp"
        if text != self._delta_text:
            self._delta_text = text
            self.delta_changed.emit(text)

    def delta_report(self) -> DeltaReport | None:
        """Full report for the current selection (None if fewer than two lines are selected)."""
        lines = self.selected_lines()
        if lines.size < 2:
            return None
        return self.delta_for(lines)

    # ------------------------------------------------------------- tab look
    def set_tab_color(self, color: str) -> None:
        self.tab_color = color if color else ""
        if not self.derived:
            self.settings.set_tab_color(self.path, self.tab_color)
        self.look_changed.emit()

    def set_title(self, title: str) -> None:
        title = title.strip() or os.path.basename(self.path)
        if title != self.title:
            self.title = title
            self.title_changed.emit(title)
            self.look_changed.emit()

    # ------------------------------------------------------- derived (to tab)
    def source_tab(self):
        return self._source_ref() if self._source_ref is not None else None

    def source_line(self, line: int) -> int | None:
        """Line number in the source tab for ``line`` of this derived tab."""
        o = self.origin_lines
        return int(o[line]) if o is not None and 0 <= line < o.size else None

    def source_info(self, line: int):
        """``(tab or None, file path, line in that file)`` for a line of a derived / merged tab."""
        n = self.source_line(line)
        if n is None:
            return None
        if self.merge is not None:
            src = self.merge.source_of(line)
            return None if src is None else (src.tab(), src.path, n)
        return self.source_tab(), self.source_path, n

    def lines_to_tab(self, lines, title: str, with_bookmarks: bool = False) -> None:
        """Write the given lines to a temporary file; the host opens it as a new tab."""
        arr = derive.normalize_lines(lines)
        arr = arr[arr < self.reader.count]
        if arr.size == 0:
            self.status_message.emit("Nothing to show in a new tab")
            return
        if self._derive_worker is not None and self._derive_worker.isRunning():
            self.status_message.emit("Still writing the previous new tab…")
            return
        dest = derive.new_path(title)
        reader = self.reader
        w = FuncWorker(lambda cancel, emit: derive.write_lines(reader, arr, dest, cancel), self)
        w.done.connect(partial(self._on_derive_done, w, dest, title, with_bookmarks))
        self._derive_worker = w
        self.status_message.emit(f"Writing {arr.size:,} line(s) to a new tab…")
        w.start()

    def _on_derive_done(self, w, dest: str, title: str, with_bookmarks: bool, result) -> None:
        if w is not self._derive_worker:
            return
        if self._closed:                                  # the tab was closed while writing
            derive.remove_file(dest)
            return
        if isinstance(result, BaseException):
            if not isinstance(result, InterruptedError):
                self.status_message.emit(f"Cannot create the new tab: {result}")
            return
        marks = None
        if with_bookmarks:
            marks = {i: Bookmark(self.bookmarks[ln].comment, list(self.bookmarks[ln].tags),
                                 self.bookmarks[ln].marker)
                     for i, ln in enumerate(result.tolist()) if ln in self.bookmarks}
        self.derive_ready.emit(self, dest, result, title, marks)

    def results_to_tab(self) -> None:
        self.lines_to_tab(self.sview.snapshot(), f"{self.title} [{self.find_edit.text() or 'search'}]")

    def bookmarks_to_tab(self, lines=None) -> None:
        """Bookmarked lines (all, or the given subset) -> new tab; comments and tags come along."""
        use = sorted(self.bookmarks) if lines is None else list(lines)
        self.lines_to_tab(use, f"{self.title} [bookmarks]", with_bookmarks=True)

    def selection_to_tab(self) -> None:
        self.lines_to_tab(self.selected_lines(), f"{self.title} [selection]")

    def save_as(self, dest: str) -> None:
        """Copy this tab's file to ``dest`` (for a derived tab: save the filtered lines)."""
        derive.save_copy(self.path, dest)
        if self.derived:
            self.saved_path = dest

    def _apply_columnizer(self, c) -> None:
        if isinstance(c, TimestampColumnizer):
            c = c.with_parser(self.get_ts_parser())
        self._base_col = c
        if self.merge is not None:                       # merged tab: a Source column in front
            c = MergedColumnizer(c, self.merge)
        for m in (self.model, self.fmodel, self.smodel):
            m.set_columnizer(c)
        for box in self.boxes:
            box.set_columnizer(c)
        self.columnizer = c
        for t in (self.table, self.ftable, self.stable):
            t.reset_columns()
        self.refit_columns()
        self.columnizer_changed.emit()

    def columnizer_choice(self) -> tuple[str | None, str]:
        """(name chosen by hand or None = automatic, name of the columnizer in use)."""
        in_use = self._base_col.name
        return (in_use if self._columnizer_forced else None), in_use

    def refit_columns(self, deep: bool = False, force: bool = False) -> None:
        """Re-measure the widest text of the main table and both panels."""
        main = np.arange(self.reader.count, dtype=np.int64)
        self.fit_main.request(main, reset=True, deep=deep, force=force)
        self.fit_filter.request(self.fview.snapshot(), reset=True, deep=deep, force=force)
        self.fit_results.request(self.sview.snapshot(), reset=True, deep=deep, force=force)

    def fit_columns_now(self) -> None:
        """Toolbar button: fit once, with a deeper scan, even when auto-fit is off."""
        self.refit_columns(deep=True, force=True)

    def set_view_options(self, autofit: bool, wrap: bool) -> None:
        for t in (self.table, self.ftable, self.stable):
            t.configure(autofit, wrap)
        if autofit or wrap:
            self.refit_columns()

    def set_columnizer(self, name: str | None) -> None:
        """``None`` = automatic detection."""
        if name is None:
            self._columnizer_forced = False
            self._apply_columnizer(self._auto_columnizer())
        else:
            self._columnizer_forced = True
            self._apply_columnizer(self.registry.get(name))

    def refresh_columnizer(self) -> None:
        """The set of columnizers changed (regex columnizers edited): pick up the new definitions."""
        if self.reader.count == 0:
            return
        if self._columnizer_forced:
            name = self._base_col.name
            if name in self.registry.names():
                self._apply_columnizer(self.registry.get(name))
                return
            self._columnizer_forced = False            # the one in use was deleted: back to automatic
        self._apply_columnizer(self._auto_columnizer())

    def apply_theme(self, theme: dict) -> None:
        """Fonts, default colours, selected-row colours, grid lines and bookmark marker."""
        for area, table, model in (("main", self.table, self.model), ("filter", self.ftable, self.fmodel),
                                   ("search", self.stable, self.smodel)):
            table.apply_theme(area_font(self.settings, theme, area), theme["areas"][area],
                              theme["selection"], theme["grid"])
            model.marker = theme["bookmark_marker"]
            model.refresh_all()
        for box, area in zip(self.boxes, ("main", "filter", "search")):
            box.apply_theme(area_font(self.settings, theme, area), theme["areas"][area],
                            theme["selection"], theme["grid"], theme["bookmark_marker"])
        if self.merge is not None:
            self.refresh_merge_style()

    def refresh_highlight(self) -> None:
        for m in (self.model, self.fmodel, self.smodel):
            m.refresh_all()
        for box in self.boxes:
            box.refresh()

    # ------------------------------------------------------------ follow tail
    @property
    def follow(self) -> bool:
        return self._follow

    def set_follow(self, on: bool) -> None:
        self._follow = on and not self.derived
        if self._follow:
            self._pending_line = None
            self.table.scrollToBottom()
        self.follow_changed.emit(self._follow)

    def _on_user_scroll(self, _action) -> None:
        # Scrolling away from the end turns follow-tail off,
        # scrolling back to the end turns it on again.
        QTimer.singleShot(0, self._sync_follow_with_scrollbar)

    def _sync_follow_with_scrollbar(self) -> None:
        self._pending_line = None                    # the user took over navigation
        sb = self.table.verticalScrollBar()
        at_end = sb.value() >= sb.maximum() - 2
        if at_end != self._follow:
            self._follow = at_end
            self.follow_changed.emit(at_end)

    # ------------------------------------------------------------ navigation
    def current_line(self) -> int:
        idx = self.table.currentIndex()
        return self.view.line_number(idx.row()) if idx.isValid() else -1

    def goto_line(self, line: int, focus: bool = True) -> None:
        """Select and reveal ``line`` (0-based) in the main table; linked tabs follow (time sync)."""
        if self.model.rowCount() == 0:
            return
        self._reveal(line, keep_selection=False, focus=focus)
        if self.linked:
            self._sync_line = line
            self._sync_timer.start()

    def _reveal(self, line: int, keep_selection: bool, focus: bool = False) -> None:
        if not self.isVisible() and not focus:           # a hidden tab page cannot scroll yet
            self._pending_reveal = (line, keep_selection)
            return
        self._pending_reveal = None
        row = self.view.row_of_line(line)
        row = max(0, min(row, self.model.rowCount() - 1))
        if self._follow:
            self._follow = False
            self.follow_changed.emit(False)
        idx = self.model.index(row, 0)
        if keep_selection:
            self.table.selectionModel().setCurrentIndex(idx, QItemSelectionModel.SelectionFlag.NoUpdate)
        else:
            self.table.selectRow(row)
        self.table.scrollTo(idx, QAbstractItemView.ScrollHint.PositionAtCenter)
        if focus:
            self.table.setFocus()

    def showEvent(self, e) -> None:
        super().showEvent(e)
        if self._pending_reveal is not None:
            QTimer.singleShot(0, self._apply_pending_reveal)

    def _apply_pending_reveal(self) -> None:
        if self._pending_reveal is not None and self.isVisible() and self.model.rowCount():
            line, keep = self._pending_reveal
            self._silent_until = time.monotonic() + 0.4
            self._reveal(line, keep)

    # ------------------------------------------------------ time sync / links
    def _on_scrolled(self, _value: int) -> None:
        if self.linked:
            self._sync_timer.start()

    def center_line(self) -> int | None:
        """File line shown in the middle of the main view."""
        rows = self.model.rowCount()
        if rows == 0:
            return None
        idx = self.table.indexAt(QPoint(8, self.table.viewport().height() // 2))
        row = idx.row() if idx.isValid() else rows - 1
        return self.view.line_number(row)

    def _emit_sync(self) -> None:
        line, self._sync_line = self._sync_line, None
        if not self.linked or time.monotonic() < self._silent_until:
            return
        if line is None:
            line = self.center_line()
        if line is not None and line >= 0:
            self.time_sync_requested.emit(self, line)

    def can_link(self) -> bool:
        return self.get_ts_parser().usable

    def set_linked(self, on: bool) -> bool:
        """Link / unlink this tab (only tabs whose lines carry a timestamp can be linked)."""
        if on and not self.can_link():
            return False
        if on != self.linked:
            self.linked = bool(on)
            self.link_changed.emit()
        return self.linked == bool(on)

    def set_time_offset(self, seconds: float) -> None:
        if seconds != self.ts_offset:
            self.ts_offset = float(seconds)
            self.link_changed.emit()

    def time_at(self, line: int) -> float | None:
        """Time of ``line`` (clock offset included); unstamped lines use the nearest stamped one."""
        t = time_near(self.reader.get_line, self.reader.count, self.get_ts_parser(), line)
        return None if t is None else t + self.ts_offset

    def show_time(self, universal: float, time_only: bool) -> int | None:
        """Scroll to the line closest to ``universal`` time (same clock as :meth:`time_at`).

        Returns that line, or None if this tab has no comparable timestamps."""
        parser = self.get_ts_parser()
        if not parser.usable or self.reader.count == 0 or self.model.rowCount() == 0:
            return None
        res = nearest_line(self.reader.get_line, self.reader.count, parser, universal - self.ts_offset)
        if res is None or is_time_only(res[1]) != time_only:
            return None
        self._silent_until = time.monotonic() + 0.4
        self._sync_line = None
        self._reveal(res[0], keep_selection=self._has_multi_selection())
        return res[0]

    def _has_multi_selection(self) -> bool:
        sm = self.table.selectionModel()
        try:
            sel = sm.selection()
            return sel.count() > 1 or (sel.count() == 1 and sel[0].height() > 1)
        except (TypeError, IndexError, AttributeError):
            return len(sm.selectedRows()) > 1

    # ---------------------------------------------------------- merged tabs
    def refresh_merge_style(self) -> None:
        """Lines of each source file take the colour of that file's tab."""
        if self.merge is None:
            return
        area = self.settings.theme()["areas"]["main"]
        pal = self.table.palette()
        base_bg = area["bg"] or pal.color(QPalette.ColorRole.Base).name()
        base_fg = area["fg"] or pal.color(QPalette.ColorRole.Text).name()
        self.merge.update_styles(base_bg, base_fg)
        for m in (self.model, self.fmodel, self.smodel):
            m.row_style = self.merge.style
            m.source_col = GUTTER
            m.refresh_all()
        for box in self.boxes:
            box.set_row_style(self.merge.style, GUTTER)

    def _jump_from(self, view: ListView, index) -> None:
        """Double-click / Enter on a filter or search result row."""
        if index.isValid() and index.row() < view.n:
            self.goto_line(view.line_number(index.row()))

    # -------------------------------------------------------------- bookmarks
    def toggle_bookmark(self) -> None:
        self._toggle_bookmark_line(self.current_line())

    def _toggle_bookmark_line(self, ln: int) -> None:
        """Set or remove the bookmark of a file line (Ctrl+F2, or double-click in the Bookmark column)."""
        if ln < 0:
            return
        if ln in self.bookmarks:
            del self.bookmarks[ln]
        else:
            self.bookmarks[ln] = Bookmark()
        self._bookmarks_modified()

    def goto_bookmark(self, forward: bool) -> None:
        if not self.bookmarks:
            return
        cur = self.current_line()
        marks = sorted(self.bookmarks)
        if forward:
            nxt = next((m for m in marks if m > cur), marks[0])
        else:
            nxt = next((m for m in reversed(marks) if m < cur), marks[-1])
        self.goto_line(nxt)

    def set_bookmark_comment(self, line: int, comment: str) -> None:
        bm = self.bookmarks.get(line)
        if bm is not None and bm.comment != comment:
            bm.comment = comment
            self.bookmarks_changed.emit()

    def set_bookmark_tags(self, line: int, tags) -> None:
        bm = self.bookmarks.get(line)
        tags = normalize_tags(tags)
        if bm is not None and bm.tags != tags:
            bm.tags = tags
            self.settings.remember_tags(tags)
            self._bookmarks_modified()

    def add_tags(self, lines, tags) -> None:
        """Add ``tags`` to every bookmark in ``lines`` (existing tags are kept)."""
        tags = normalize_tags(tags)
        if not tags:
            return
        for ln in lines:
            bm = self.bookmarks.get(ln)
            if bm is not None:
                bm.tags = normalize_tags(bm.tags + tags)
        self.settings.remember_tags(tags)
        self._bookmarks_modified()

    def remove_tags(self, lines, tags) -> None:
        drop = {t.lower() for t in normalize_tags(tags)}
        for ln in lines:
            bm = self.bookmarks.get(ln)
            if bm is not None:
                bm.tags = [t for t in bm.tags if t.lower() not in drop]
        self._bookmarks_modified()

    def set_bookmark_marker(self, lines, marker: str) -> None:
        """``""`` = back to the default marker."""
        marker = clean_marker(marker, "")
        for ln in lines:
            bm = self.bookmarks.get(ln)
            if bm is not None:
                bm.marker = marker
        self._bookmarks_modified()

    def remove_bookmarks(self, lines) -> None:
        for ln in lines:
            self.bookmarks.pop(ln, None)
        self._bookmarks_modified()

    def clear_bookmarks(self) -> None:
        self.bookmarks.clear()
        self._bookmarks_modified()

    def add_bookmarks(self, marks: dict[int, Bookmark]) -> None:
        """Merge: an existing bookmark keeps its comment when the new one has none; tags are united."""
        for ln, new in marks.items():
            old = self.bookmarks.get(ln)
            if old is None:
                self.bookmarks[ln] = Bookmark(new.comment, list(new.tags), new.marker)
            else:
                old.comment = new.comment or old.comment
                old.marker = new.marker or old.marker
                old.tags = normalize_tags(old.tags + new.tags)
            self.settings.remember_tags(new.tags)
        self._bookmarks_modified()

    def _bookmarks_modified(self) -> None:
        self.refresh_highlight()
        self.bookmarks_changed.emit()

    # ------------------------------------------------------------------- find
    def show_find(self, text: str = "") -> None:
        """Open the find bar; ``text`` (the selection on screen) replaces what is in the box."""
        self.find_bar.show()
        query = selection_to_query(text, self.find_regex.isChecked())
        if query:
            self.find_edit.set_text(query)
        self.find_edit.focus_and_select()

    def _make_find_matcher(self) -> LineMatcher | None:
        text = self.find_edit.text()
        if not text:
            self.show_find()
            return None
        try:
            matcher = LineMatcher(text, self.find_regex.isChecked(), self.find_case.isChecked())
        except re.error as exc:
            self.find_label.setText(f"Invalid regex: {exc}")
            return None
        self.find_edit.set_history(self.settings.push_history("find_history", text))
        return matcher

    def find(self, forward: bool) -> None:
        matcher = self._make_find_matcher()
        if matcher is None:
            return
        if self._search_worker is not None and self._search_worker.isRunning():
            self._search_worker.cancel.set()
            self._search_worker.wait(1000)
        start = self.current_line()
        if start < 0:
            start = -1 if forward else self.reader.count
        self._find_from, self._find_fwd = start, forward
        reader = self.reader
        w = FuncWorker(lambda cancel, emit: find_next(reader, matcher, start, forward, cancel), self)
        w.done.connect(self._on_find_done)
        self.find_label.setText("Searching…")
        self._search_worker = w
        w.start()

    def _on_find_done(self, result) -> None:
        if isinstance(result, Exception):
            self.find_label.setText(str(result))
        elif result < 0:
            self.find_label.setText("Not found")
        else:
            wrapped = result <= self._find_from if self._find_fwd else result >= self._find_from
            self.find_label.setText(f"Line {result + 1:,}" + ("  (wrapped around)" if wrapped else ""))
            self.goto_line(result)

    def find_all(self) -> None:
        """Collect every match into the 'Search results' panel (double-click to jump)."""
        matcher = self._make_find_matcher()
        if matcher is None:
            return
        self._results_matcher = matcher
        self.bottom.setCurrentIndex(RESULTS_TAB)
        self.bottom.show()
        self._restart_results()

    def _restart_results(self) -> None:
        if self._results_worker is not None and self._results_worker.isRunning():
            self._results_worker.cancel.set()
            self._results_worker.wait(2000)
        self._results_busy = False
        self.sview.clear()
        self.smodel.reset_rows(0)
        self._results_scanned = 0
        self._results_dirty_from = None
        self._results_settled = False
        self.fit_results.reset()
        self.results_label.setText("Searching…")
        self._results_pump()

    def _results_pump(self) -> None:
        """Scan the part of the file the results have not seen yet."""
        matcher = self._results_matcher
        if matcher is None:
            return
        if self._results_busy:
            return                      # _on_results_done pumps again
        total = self.reader.count
        if total <= self._results_scanned and self._results_dirty_from is None:
            return
        drop, scan_from, _emit_from = plan_incremental(self._results_scanned, self._results_dirty_from, 0)
        self._results_dirty_from = None
        removed = self.sview.drop_from(drop)
        self.smodel.remove_tail(removed)
        self._results_scanned = total
        reader = self.reader

        def job(cancel, emit):
            return scan_matches(reader, matcher, scan_from, total, cancel, emit, 0)

        w = FuncWorker(job, self)
        w.batch.connect(partial(self._on_results_batch, w))
        w.done.connect(partial(self._on_results_done, w))
        self._results_worker = w
        self._results_busy = True
        w.start()

    def _at_bottom(self, table) -> bool:
        sb = table.verticalScrollBar()
        return sb.value() >= sb.maximum() - 2

    def _on_results_batch(self, w, arr) -> None:
        if w is not self._results_worker:
            return                      # stale batch from a cancelled scan
        stick = self._results_settled and self._at_bottom(self.stable)
        self.sview.append(arr)
        self.smodel.append_rows(int(arr.size))
        self.fit_results.request(arr)
        self.results_label.setText(f"{self.sview.n:,} matches…")
        if stick:
            QTimer.singleShot(0, self.stable.scrollToBottom)

    def _on_results_done(self, w, result) -> None:
        if w is not self._results_worker:
            return
        self._results_busy = False
        if isinstance(result, Exception):
            self.results_label.setText(f"Search failed: {result}")
            self._results_matcher = None
            return
        if result is False:
            self.results_label.setText(f"{self.sview.n:,} matches (stopped)")
            return
        self._results_settled = True
        live = " - live" if self.results_live.isChecked() else ""
        self.results_label.setText(
            f"{self.sview.n:,} matches in {self._results_scanned:,} lines{live} "
            f"- double-click a row to jump")
        if self.results_live.isChecked() and self.reader.count > self._results_scanned:
            self._results_pump()

    def _on_results_live_toggled(self, on: bool) -> None:
        self.settings["search_live"] = bool(on)
        if on and self._results_matcher is not None:
            self._results_pump()

    # ----------------------------------------------------------------- filter
    def show_filter(self, on: bool = True) -> None:
        if on:
            self.bottom.setCurrentIndex(FILTER_TAB)
            self.bottom.show()
            self.filter_edit.focus_and_select()
        else:
            self.bottom.hide()

    def toggle_bottom(self) -> None:
        self.show_filter(not self.bottom.isVisible())

    def cancel_filter(self) -> None:
        if self._filter_worker is not None and self._filter_worker.isRunning():
            self._filter_worker.cancel.set()
        self._filter_active = False
        self._disarm_trigger()

    def filter_spec(self) -> dict:
        spec = {"text": self.filter_edit.text(), "regex": self.filter_regex.isChecked(),
                "case": self.filter_case.isChecked(), "invert": self.filter_invert.isChecked(),
                "context": self.filter_ctx.value()}
        if self._action:
            spec["action"] = dict(self._action)
        return spec

    def set_filter_spec(self, spec: dict, apply: bool = False) -> None:
        self.filter_edit.set_text(spec.get("text", ""))
        self.filter_regex.setChecked(bool(spec.get("regex", False)))
        self.filter_case.setChecked(bool(spec.get("case", False)))
        self.filter_invert.setChecked(bool(spec.get("invert", False)))
        self.filter_ctx.setValue(int(spec.get("context", 0)))
        self._set_action(spec.get("action"))
        if apply:
            self.apply_filter()

    def apply_filter(self) -> None:
        text = self.filter_edit.text()
        if not text:
            self.cancel_filter()
            self.fview.clear()
            self.fmodel.reset_rows(0)
            self.fit_filter.reset()
            self.filter_label.setText("")
            return
        spec = self.filter_spec()
        params = FilterParams(text, spec["regex"], spec["case"], spec["invert"], spec["context"])
        try:
            LineMatcher.from_params(params)
        except re.error as exc:
            self.filter_label.setText(f"Invalid regex: {exc}")
            return
        self.filter_edit.set_history(self.settings.push_history("filter_history", text))
        self._filter_params = params
        self._filter_active = True
        self._setup_trigger(params)
        self._restart_filter()

    def _restart_filter(self) -> None:
        if self._filter_worker is not None and self._filter_worker.isRunning():
            self._filter_worker.cancel.set()
            self._filter_worker.wait(2000)
        self._filter_busy = False       # whatever the old scan still has queued is ignored
        self.fview.clear()
        self.fmodel.reset_rows(0)
        self._filter_scanned = 0
        self._filter_dirty_from = None
        self._filter_settled = False
        self.fit_filter.reset()
        self._filter_pump()

    def _filter_pump(self) -> None:
        """Scan whatever part of the file the filter has not seen yet.

        With spread (±N lines) the entries near the old end are recomputed, because new
        matches add leading context there and the last matches' trailing context was cut off."""
        if not self._filter_active:
            return
        if self._filter_busy:
            return                      # _on_filter_done pumps again
        total = self.reader.count
        if total <= self._filter_scanned and self._filter_dirty_from is None:
            return
        params, reader = self._filter_params, self.reader
        drop, scan_from, emit_from = plan_incremental(
            self._filter_scanned, self._filter_dirty_from, params.context)
        self._filter_dirty_from = None
        removed = self.fview.drop_from(drop)
        self.fmodel.remove_tail(removed)
        self._filter_scanned = total
        matcher = LineMatcher.from_params(params)

        def job(cancel, emit):
            return scan_matches(reader, matcher, scan_from, total, cancel, emit,
                                params.context, emit_from=emit_from)

        w = FuncWorker(job, self)
        w.batch.connect(partial(self._on_filter_batch, w))
        w.done.connect(partial(self._on_filter_done, w))
        self.filter_label.setText("Filtering…")
        self._filter_worker = w
        self._filter_busy = True
        w.start()

    def _on_filter_batch(self, w, arr) -> None:
        if w is not self._filter_worker:
            return                      # stale batch from a cancelled scan
        stick = self._filter_settled and self._at_bottom(self.ftable)
        self.fview.append(arr)
        self.fmodel.append_rows(int(arr.size))
        self.fit_filter.request(arr)
        self.filter_label.setText(f"{self.fview.n:,} lines…")
        if stick:                       # live update: keep the newest lines in view
            QTimer.singleShot(0, self.ftable.scrollToBottom)

    def _on_filter_done(self, w, result) -> None:
        if w is not self._filter_worker:
            return
        self._filter_busy = False
        if isinstance(result, Exception):
            self.filter_label.setText(f"Filter failed: {result}")
            return
        if result is False:             # cancelled: keep what was found
            self.filter_label.setText(f"{self.fview.n:,} lines (stopped)")
            return
        self._filter_settled = True
        live = " - live" if self.filter_tail.isChecked() else ""
        self.filter_label.setText(f"{self.fview.n:,} lines{live}")
        if self.filter_tail.isChecked() and self.reader.count > self._filter_scanned:
            self._filter_pump()

    def _on_filter_live_toggled(self, on: bool) -> None:
        self.settings["filter_tail"] = bool(on)
        if on and self._filter_active:
            self._filter_dirty_from = None
            self._filter_pump()

    # ----------------------------------------------------------- filter action
    def _set_action(self, action) -> None:
        self._action = actions.sanitize_action(action)
        on = actions.is_active(self._action)
        self.action_btn.setText("Action \u2713" if on else "Action…")
        if self._action:
            mode = "first match only" if self._action["mode"] == "first" else "every match"
            state = "" if on else " (disabled)"
            self.action_btn.setToolTip(f"{self._action['command']}\n{mode}{state}")
        else:
            self.action_btn.setToolTip("Run a program when this filter matches a new line")

    def _edit_action(self) -> None:
        dlg = ActionDialog(self._action, self)
        if not dlg.exec():
            return
        self._set_action(dlg.action())
        if self._filter_active:
            self._setup_trigger(self._filter_params)          # re-arm with the new settings

    def _on_caught_up(self) -> None:
        self._caught_up = True
        if self._trigger_wait_arm:
            self._arm_trigger()

    def _setup_trigger(self, params: FilterParams) -> None:
        """Called whenever a filter is applied: arm the action for lines that arrive from now on."""
        self._disarm_trigger()
        if self.derived or not actions.is_active(self._action):    # a derived file never grows
            return
        self._trigger_params = params
        if self._caught_up:
            self._arm_trigger()
        else:
            self._trigger_wait_arm = True                     # wait until the file is fully indexed

    def _arm_trigger(self) -> None:
        self._trigger_wait_arm = False
        self._trigger_scanned = self.reader.complete_count   # existing lines never fire
        self._trigger_armed = True
        mode = "first match" if self._action["mode"] == "first" else "every match"
        self.status_message.emit(f"Filter action armed ({mode}) for new lines")

    def _disarm_trigger(self) -> None:
        self._trigger_armed = self._trigger_wait_arm = False

    def _trigger_pump(self) -> None:
        """Look at the lines that arrived since the last look; start the program for matches."""
        if not self._trigger_armed or self._trigger_busy or self._trigger_params is None:
            return
        stop = self.reader.complete_count
        start = self._trigger_scanned
        if stop <= start:
            return
        self._trigger_scanned = stop
        action, reader, path = dict(self._action), self.reader, self.path
        limit = max(1, int(self.settings["trigger_burst_limit"]))
        params = self._trigger_params

        def job(cancel, emit):
            return scan_and_run(reader, params, action, path, start, stop, limit, cancel)

        w = FuncWorker(job, self)
        w.done.connect(partial(self._on_trigger_done, w))
        self._trigger_worker = w
        self._trigger_busy = True
        w.start()

    def _on_trigger_done(self, w, result) -> None:
        if w is not self._trigger_worker:
            return
        self._trigger_busy = False
        if isinstance(result, Exception):
            self.status_message.emit(f"Filter action failed: {result}")
        else:
            if result["fired"] and self._action and self._action["mode"] == "first":
                self._trigger_armed = False                   # used up until the filter is applied again
            if result["errors"]:
                self.status_message.emit(f"Filter action error: {result['errors'][0]}")
            elif result["launched"]:
                extra = f" ({result['skipped']} more matches skipped)" if result["skipped"] else ""
                self.status_message.emit(
                    f"Filter action started {result['launched']}\u00d7, last for line {result['last_line']:,}{extra}")
        self._trigger_pump()

    def filter_to_tab(self) -> None:
        """Show the current filter result as a new tab (a real file: save it, filter it again)."""
        if self.fview.n == 0:
            self.status_message.emit("The filter result is empty")
            return
        self.lines_to_tab(self.fview.snapshot(), f"{self.title} [{self._filter_params.text}]")

    # ------------------------------------------------------ saved filters menu
    def _rebuild_saved_menu(self) -> None:
        m = self.saved_menu
        m.clear()
        save = m.addAction("Save current filter as…")
        save.setEnabled(bool(self.filter_edit.text()))
        save.triggered.connect(self._save_current_filter)
        saved = self.settings.saved_filters()
        if saved:
            m.addSeparator()
            for spec in saved:
                label = f"{spec['name']}    ({spec['text']})" + ("   \u26a1" if spec.get("action") else "")
                act = m.addAction(label)
                act.triggered.connect(lambda _=False, s=spec: self._use_saved_filter(s))
            m.addSeparator()
            dm = m.addMenu("Delete saved filter")
            for spec in saved:
                act = dm.addAction(spec["name"])
                act.triggered.connect(lambda _=False, n=spec["name"]: self.settings.delete_filter(n))
        m.addSeparator()
        exp = m.addAction("Export saved filters…")
        exp.setEnabled(bool(saved))
        exp.triggered.connect(self._export_filters)
        m.addAction("Import saved filters…").triggered.connect(self._import_filters)

    def _save_current_filter(self) -> None:
        spec = self.filter_spec()
        if not spec["text"]:
            return
        name, ok = QInputDialog.getText(self, "Save filter", "Name:", text=spec["text"][:40])
        name = name.strip()
        if ok and name:
            self.settings.save_filter({"name": name, **spec})

    def _use_saved_filter(self, spec: dict) -> None:
        self.show_filter(True)
        self.set_filter_spec(sanitize_filter(spec) or {"text": spec["text"]}, apply=True)

    def _export_filters(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export saved filters", "logscope.filters.json",
                                              "Filter files (*.json);;All files (*)")
        if path:
            try:
                write_filters_file(path, self.settings.saved_filters())
            except OSError as exc:
                QMessageBox.warning(self, "Export", f"Cannot write file:\n{exc}")

    def _import_filters(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Import saved filters", "",
                                              "Filter files (*.json);;All files (*)")
        if not path:
            return
        try:
            filters = read_filters_file(path)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Import", f"Cannot import:\n{exc}")
            return
        if has_actions(filters):
            cmds = "\n".join(f"  {f['name']}: {f['action']['command']}" for f in filters if f.get("action"))
            ans = QMessageBox.question(
                self, "Import saved filters",
                "These filters would start programs when they match:\n\n" + cmds +
                "\n\nYes = import including the programs\nNo = import without the programs",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel)
            if ans == QMessageBox.StandardButton.Cancel:
                return
            if ans == QMessageBox.StandardButton.No:
                for f in filters:
                    f.pop("action", None)
        for f in filters:
            self.settings.save_filter(f)

    # --------------------------------------------------------------- session
    def capture_state(self) -> dict:
        """Everything needed to bring this tab back after a restart."""
        if self._pending_line is not None:
            line = self._pending_line + 1               # restore not finished yet: keep the old target
        else:
            ln = self.current_line()
            line = ln + 1 if ln >= 0 else 0
        spec = self._pending_filter
        if spec is None and self.filter_edit.text():
            spec = self.filter_spec()
        return {
            "path": self.path,
            "bookmarks": bookmarks_to_list(self.bookmarks, self.reader.get_line),
            "follow": self._follow,
            "line": line,
            "columnizer": self.columnizer.name if self._columnizer_forced else None,
            "filter": spec,
            "find": ({"text": self.find_edit.text(), "regex": self.find_regex.isChecked(),
                      "case": self.find_case.isChecked()} if self.find_edit.text() else None),
            "filter_visible": self.bottom.isVisible() and self.bottom.currentIndex() == FILTER_TAB,
            "color": self.tab_color,
            "title": self.title if self.title != os.path.basename(self.path) else "",
            "ts_format": self.ts_spec,
            "linked": self.linked,
            "ts_offset": self.ts_offset,
            "pinned": sorted(self.box_main.pinned),
        }

    def apply_state(self, state: dict) -> None:
        self.bookmarks.update(bookmarks_from_list(state.get("bookmarks")))
        if state.get("pinned"):
            self.box_main.set_pins(state["pinned"])
        if state.get("color"):
            self.set_tab_color(state["color"])
        if state.get("title"):
            self.set_title(state["title"])
        if state.get("ts_format"):
            self.set_timestamp_spec(state["ts_format"])
        if state.get("ts_offset"):
            self.ts_offset = float(state["ts_offset"])
        self._linked_wanted = bool(state.get("linked"))      # applied once timestamps are known
        name = state.get("columnizer")
        if name and name in self.registry.names():
            self.set_columnizer(name)
        self.set_follow(bool(state.get("follow", True)))
        if not self._follow and state.get("line", 0) > 0:
            self._pending_line = state["line"] - 1
        self._pending_filter = sanitize_filter(state.get("filter"))
        if self._pending_filter is not None:
            self.set_filter_spec(self._pending_filter, apply=False)   # fills the controls now
        find = sanitize_find(state.get("find"))
        if find is not None:
            self.find_edit.set_text(find["text"])
            self.find_regex.setChecked(find["regex"])
            self.find_case.setChecked(find["case"])
        if state.get("filter_visible"):
            self.show_filter(True)
        self._bookmarks_modified()

    # ---------------------------------------------------------------- status
    def status_text(self) -> str:
        r = self.reader
        parts = [f"{r.count:,} lines", f"{r.size / 1048576:,.1f} MB", r.encoding]
        parts.append(self.columnizer.name)
        if r.missing:
            parts.append("FILE NOT FOUND")
        if self.merge is not None:
            info = self.source_info(self.current_line())
            names = ", ".join(s.title for s in self.merge.sources)
            parts.append(f"merged from {len(self.merge.sources)} files ({names})")
            if info is not None:
                parts.append(f"{os.path.basename(info[1])}: line {info[2] + 1:,}")
        elif self.derived:
            src = self.source_tab()
            cur = self.source_line(self.current_line())
            parts.append("from " + (src.title if src is not None else "a closed tab")
                         + (f", source line {cur + 1:,}" if cur is not None else ""))
        if self.linked:
            parts.append("linked" + (f" (offset {self.ts_offset:+g} s)" if self.ts_offset else ""))
        if self.bookmarks:
            parts.append(f"{len(self.bookmarks)} bookmark(s)")
        if self._delta_text:
            parts.append(self._delta_text)
        return "  |  ".join(parts)
