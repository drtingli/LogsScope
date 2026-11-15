"""Dockable bookmark list for the current tab (bookmark window), with tags."""

from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDockWidget, QFileDialog, QHBoxLayout, QInputDialog, QLabel,
    QMenu, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QToolButton, QVBoxLayout,
    QWidget,
)

from ..core.bookmarks import all_tags, normalize_tags, read_bookmark_file, tags_text, write_bookmark_file
from ..core.theme import DEFAULT_MARKER, MARKER_PRESETS
from .findbar import ItemRowsBackend, PanelFindBar
from .tagdialog import TagManagerDialog
from .look import GridDelegate, area_font, style_table

LINE_ROLE = Qt.ItemDataRole.UserRole
ALL_TAGS = "(all tags)"
UNTAGGED = "(untagged)"
COL_LINE, COL_TAGS, COL_COMMENT, COL_TEXT = range(4)


class BookmarkPanel(QDockWidget):
    def __init__(self, settings, parent=None) -> None:
        super().__init__("Bookmarks", parent)
        self.setObjectName("BookmarksDock")
        self.settings = settings
        self._tab = None
        self._busy = False
        self._marker = DEFAULT_MARKER

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Line", "Tags", "Comment", "Text"])
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)   # edit via the buttons
        self._grid = GridDelegate(self.table)
        self.table.setItemDelegate(self._grid)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(COL_LINE, 80)
        self.table.setColumnWidth(COL_TAGS, 160)
        self.table.setColumnWidth(COL_COMMENT, 240)
        self.table.setToolTip("Double-click or Enter: jump to the line.")
        self.table.cellDoubleClicked.connect(lambda r, _c: self._jump(r))
        self.table.itemActivated.connect(lambda it: self._jump(it.row()))

        self.finder = PanelFindBar(settings, ItemRowsBackend(self.table))      # Ctrl+F / F3 in this list
        self.table.panel_finder = self.finder

        # --- tag filter ----------------------------------------------------------
        self.tag_filter = QComboBox()
        self.tag_filter.setToolTip("Show only bookmarks with this tag")
        self.tag_filter.addItem(ALL_TAGS)
        self.tag_filter.currentTextChanged.connect(lambda _t: self._on_tab_changed())
        frow = QHBoxLayout()
        frow.addWidget(QLabel("Tag:"))
        frow.addWidget(self.tag_filter, 1)

        comment_b, del_b, clear_b = QPushButton("Comment…"), QPushButton("Delete"), QPushButton("Clear")
        marker_b = QPushButton("Marker…")
        marker_b.setToolTip("Choose the emoji / text shown in the first column for the selected bookmarks")
        marker_b.clicked.connect(self._edit_marker)
        tab_b = QPushButton("To tab")
        tab_b.setToolTip("Show the selected bookmarks (or all shown ones) as a new tab, with comments and tags")
        tab_b.clicked.connect(self._to_tab)
        save_b, load_b = QPushButton("Save…"), QPushButton("Load…")
        load_b.setToolTip("Merge bookmarks (with comments and tags) from a file into this tab")
        comment_b.clicked.connect(self._edit_comment)
        del_b.clicked.connect(self._delete_selected)
        clear_b.clicked.connect(self._clear)
        save_b.clicked.connect(self._save)
        load_b.clicked.connect(self._load)

        self.tags_btn = QToolButton()
        self.tags_btn.setText("Tags")
        self.tags_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.tags_menu = QMenu(self.tags_btn)
        self.tags_btn.setMenu(self.tags_menu)
        self.tags_menu.aboutToShow.connect(self._rebuild_tags_menu)

        bar = QHBoxLayout()
        bar.addWidget(comment_b)
        bar.addWidget(self.tags_btn)
        bar.addWidget(marker_b)
        for b in (tab_b, del_b, clear_b, save_b, load_b):
            bar.addWidget(b)
        bar.addStretch(1)
        self.info = QLabel("")
        self.delta_label = QLabel("")
        self.delta_label.setToolTip("Time between the selected bookmarks")
        self.table.itemSelectionChanged.connect(self._update_delta)

        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.addLayout(frow)
        lay.addWidget(self.table, 1)
        lay.addWidget(self.finder)
        lay.addLayout(bar)
        lay.addWidget(self.info)
        lay.addWidget(self.delta_label)
        self.setWidget(body)
        self.apply_theme(settings.theme())

    def apply_theme(self, theme: dict) -> None:
        font = area_font(self.settings, theme, "bookmarks")
        style_table(self.table, self._grid, font, theme["areas"]["bookmarks"], theme["selection"],
                    theme["grid"], QFontMetrics(font).height() + int(theme["areas"]["bookmarks"]["row_spacing"]))
        self._marker = theme["bookmark_marker"]
        if self._tab is not None:
            self.refresh()

    # ------------------------------------------------------------- tab binding
    def set_tab(self, tab) -> None:
        if self._tab is not None:
            try:
                self._tab.bookmarks_changed.disconnect(self._on_tab_changed)
            except (RuntimeError, TypeError):
                pass
        self._tab = tab
        if tab is not None:
            tab.bookmarks_changed.connect(self._on_tab_changed)
        self.refresh()

    def _on_tab_changed(self, *_args) -> None:
        if not self._busy:
            self.refresh()

    def _rebuild_tag_filter(self, tags: list[str]) -> str:
        """Refill the filter combo; returns the tag that is selected afterwards."""
        current = self.tag_filter.currentText() or ALL_TAGS
        entries = [ALL_TAGS, UNTAGGED] + tags
        if current not in entries:
            current = ALL_TAGS
        self.tag_filter.blockSignals(True)
        self.tag_filter.clear()
        self.tag_filter.addItems(entries)
        self.tag_filter.setCurrentText(current)
        self.tag_filter.blockSignals(False)
        return current

    def refresh(self) -> None:
        self._busy = True
        try:
            self.table.setRowCount(0)
            tab = self._tab
            if tab is None:
                self._rebuild_tag_filter([])
                self.info.setText("")
                return
            selected = self._rebuild_tag_filter(all_tags(tab.bookmarks))
            marks = []
            for ln in sorted(tab.bookmarks):
                tags = tab.bookmarks[ln].tags
                if selected == ALL_TAGS or (selected == UNTAGGED and not tags) \
                        or selected.lower() in (t.lower() for t in tags):
                    marks.append(ln)
            self.table.setRowCount(len(marks))
            count = tab.reader.count
            for row, ln in enumerate(marks):
                bm = tab.bookmarks[ln]
                num = QTableWidgetItem(f"{bm.marker or self._marker} {ln + 1:,}")
                num.setData(LINE_ROLE, ln)
                num.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                cells = (num, QTableWidgetItem(tags_text(bm.tags)), QTableWidgetItem(bm.comment),
                         QTableWidgetItem(tab.reader.get_line(ln) if ln < count else ""))
                for col, it in enumerate(cells):
                    it.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                    self.table.setItem(row, col, it)
            shown = f"{len(marks)} of " if len(marks) != len(tab.bookmarks) else ""
            self.info.setText(f"{shown}{len(tab.bookmarks)} bookmark(s)")
        finally:
            self._busy = False

    # ---------------------------------------------------------------- actions
    def _line_of_row(self, row: int) -> int | None:
        it = self.table.item(row, COL_LINE)
        return None if it is None else it.data(LINE_ROLE)

    def _jump(self, row: int) -> None:
        ln = self._line_of_row(row)
        if self._tab is not None and ln is not None:
            self._tab.goto_line(ln)

    def _update_delta(self) -> None:
        text, tab = "", self._tab
        if tab is not None and not self._busy:
            lines = self._selected_lines()
            if len(lines) >= 2 and tab.get_ts_parser().usable:
                rep = tab.delta_for(lines, limit=20_000)
                text = rep.summary() if rep.ok else \
                    f"\u0394t: {rep.stamped} of {rep.selected} selected lines have a timestamp"
        self.delta_label.setText(text)

    def _to_tab(self) -> None:
        tab = self._tab
        if tab is None:
            return
        lines = self._selected_lines()
        if not lines:
            lines = [ln for ln in (self._line_of_row(r) for r in range(self.table.rowCount())) if ln is not None]
        if lines:
            tab.bookmarks_to_tab(lines)
        else:
            self.info.setText("No bookmarks to show")

    def _selected_lines(self) -> list[int]:
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        return [ln for ln in (self._line_of_row(r) for r in rows) if ln is not None]

    def _edit_comment(self) -> None:
        tab, lines = self._tab, self._selected_lines()
        if tab is None or not lines:
            self.info.setText("Select a bookmark first")
            return
        current = tab.bookmarks[lines[0]].comment if lines[0] in tab.bookmarks else ""
        text, ok = QInputDialog.getText(self, "Bookmark comment", "Comment:", text=current)
        if ok:
            for ln in lines:
                tab.set_bookmark_comment(ln, text)
            self.refresh()

    def _edit_marker(self) -> None:
        tab, lines = self._tab, self._selected_lines()
        if tab is None or not lines:
            self.info.setText("Select a bookmark first")
            return
        current = tab.bookmarks[lines[0]].marker if lines[0] in tab.bookmarks else ""
        items = ["(default)"] + MARKER_PRESETS
        text, ok = QInputDialog.getItem(
            self, "Bookmark marker",
            "Pick an emoji or type your own (Windows: Win + . opens the emoji picker):",
            items, items.index(current) if current in items else 0, True)
        if ok:
            text = text.strip()
            tab.set_bookmark_marker(lines, "" if text in ("", "(default)") else text)
            self.refresh()

    # ------------------------------------------------------------------- tags
    def _known_tags(self) -> list[str]:
        used = all_tags(self._tab.bookmarks) if self._tab is not None else []
        return normalize_tags(used + list(self.settings["bookmark_tags"]))

    def _ask_tags(self, title: str, default: str = "") -> list[str] | None:
        hint = ""
        known = self._known_tags()
        if known:
            hint = "\nKnown: " + ", ".join(known[:15])
        text, ok = QInputDialog.getText(self, title, "Tags, separated by commas:" + hint, text=default)
        tags = normalize_tags(text)
        return tags if ok and tags else None

    def manage_tags(self) -> None:
        """Dialog to create / rename / delete tags of all open files."""
        provider = getattr(self, "all_tabs", None)
        tabs = list(provider()) if callable(provider) else ([self._tab] if self._tab is not None else [])
        if not tabs:
            self.info.setText("Open a file first")
            return
        TagManagerDialog(tabs, self._tab, self.settings, self).exec()
        self.refresh()

    def _rebuild_tags_menu(self) -> None:
        m = self.tags_menu
        m.clear()
        m.addAction("Manage tags…").triggered.connect(self.manage_tags)
        m.addSeparator()
        have = bool(self._selected_lines())
        for text, fn in (("Set tags…", self._set_tags), ("Add tags…", self._add_tags),
                         ("Remove tags…", self._remove_tags)):
            a = m.addAction(text)
            a.setEnabled(have)
            a.triggered.connect(fn)
        known = self._known_tags()
        if known:
            m.addSeparator()
            quick = m.addMenu("Quick add")
            quick.setEnabled(have)
            for t in known:
                a = quick.addAction(t)
                a.triggered.connect(lambda _=False, tag=t: self._quick_add(tag))
            forget = m.addMenu("Forget suggestion")
            for t in list(self.settings["bookmark_tags"]):
                a = forget.addAction(t)
                a.triggered.connect(lambda _=False, tag=t: self._forget(tag))

    def _set_tags(self) -> None:
        tab, lines = self._tab, self._selected_lines()
        if tab is None or not lines:
            return
        first = tab.bookmarks.get(lines[0])
        tags = self._ask_tags("Set tags", tags_text(first.tags) if first else "")
        if tags is None:
            return
        for ln in lines:
            tab.set_bookmark_tags(ln, tags)
        self.refresh()

    def _add_tags(self) -> None:
        tags = self._ask_tags("Add tags")
        if tags is not None and self._tab is not None:
            self._tab.add_tags(self._selected_lines(), tags)

    def _remove_tags(self) -> None:
        tags = self._ask_tags("Remove tags")
        if tags is not None and self._tab is not None:
            self._tab.remove_tags(self._selected_lines(), tags)

    def _quick_add(self, tag: str) -> None:
        if self._tab is not None:
            self._tab.add_tags(self._selected_lines(), [tag])

    def _forget(self, tag: str) -> None:
        self.settings["bookmark_tags"] = [t for t in self.settings["bookmark_tags"] if t != tag]

    # ------------------------------------------------------------ delete/save/load
    def _delete_selected(self) -> None:
        if self._tab is not None:
            lines = self._selected_lines()
            if lines:
                self._tab.remove_bookmarks(lines)

    def _clear(self) -> None:
        if self._tab is None or not self._tab.bookmarks:
            return
        ans = QMessageBox.question(self, "Bookmarks", "Remove all bookmarks of this tab?")
        if ans == QMessageBox.StandardButton.Yes:
            self._tab.clear_bookmarks()

    def _save(self) -> None:
        tab = self._tab
        if tab is None or not tab.bookmarks:
            self.info.setText("No bookmarks to save")
            return
        default = os.path.basename(tab.path) + ".bookmarks.json"
        path, _ = QFileDialog.getSaveFileName(self, "Save bookmarks", default,
                                              "Bookmark files (*.json);;All files (*)")
        if not path:
            return
        try:
            write_bookmark_file(path, tab.path, tab.bookmarks, tab.reader.get_line)
            self.info.setText(f"Saved {len(tab.bookmarks)} bookmark(s) to {os.path.basename(path)}")
        except OSError as exc:
            QMessageBox.warning(self, "Bookmarks", f"Cannot save:\n{exc}")

    def _load(self) -> None:
        tab = self._tab
        if tab is None:
            return
        path, _ = QFileDialog.getOpenFileName(self, "Load bookmarks", "",
                                              "Bookmark files (*.json);;All files (*)")
        if not path:
            return
        try:
            for_file, marks = read_bookmark_file(path)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Bookmarks", f"Cannot load bookmarks:\n{exc}")
            return
        if for_file and os.path.basename(for_file).lower() != os.path.basename(tab.path).lower():
            ans = QMessageBox.question(
                self, "Bookmarks",
                f"These bookmarks were saved for\n{for_file}\nLoad them into {os.path.basename(tab.path)} anyway?")
            if ans != QMessageBox.StandardButton.Yes:
                return
        tab.add_bookmarks(marks)
        beyond = sum(1 for ln in marks if ln >= tab.reader.count)
        note = f" ({beyond} beyond the current end of the file)" if beyond else ""
        self.info.setText(f"Loaded {len(marks)} bookmark(s){note}")
