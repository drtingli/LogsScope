"""Manage bookmark tags (create / read / rename / delete) for all open files."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QHBoxLayout, QInputDialog, QLabel, QMessageBox, QPushButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout,
)

from ..core import tagmgr


class TagManagerDialog(QDialog):
    """Changes take effect at once (like a list editor); ``Close`` only closes the window."""

    def __init__(self, tabs: list, current, settings, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Manage bookmark tags")
        self.resize(640, 460)
        self.tabs, self.settings = tabs, settings
        self.current = tabs.index(current) if current in tabs else None

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Tag", "Bookmarks in this file", "Bookmarks in all open files",
                                              "In suggestions"])
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0, 200)
        self.table.setColumnWidth(1, 150)
        self.table.setColumnWidth(2, 170)
        self.table.itemDoubleClicked.connect(lambda _it: self._rename())
        self.table.itemSelectionChanged.connect(self._buttons)

        self.new_b, self.rename_b = QPushButton("New tag…"), QPushButton("Rename…")
        self.delete_b, self.unused_b = QPushButton("Delete…"), QPushButton("Forget unused suggestions")
        self.new_b.clicked.connect(self._new)
        self.rename_b.clicked.connect(self._rename)
        self.delete_b.clicked.connect(self._delete)
        self.unused_b.clicked.connect(self._forget_unused)
        self.new_b.setToolTip("Add a tag to the suggestions, so it is offered under Tags > Quick add")
        self.rename_b.setToolTip("Rename the tag on all bookmarks of all open files; an existing name merges the two")
        self.delete_b.setToolTip("Remove the tag from all bookmarks of all open files and from the suggestions")

        self.status = QLabel("")
        self.status.setWordWrap(True)
        note = QLabel("Covers the bookmarks of all open files. Files that are closed keep their tags until you "
                      "open them and rename or delete the tag there.")
        note.setWordWrap(True)
        bar = QHBoxLayout()
        for b in (self.new_b, self.rename_b, self.delete_b):
            bar.addWidget(b)
        bar.addStretch(1)
        bar.addWidget(self.unused_b)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        box.rejected.connect(self.reject)
        box.accepted.connect(self.accept)
        lay = QVBoxLayout(self)
        lay.addWidget(self.table, 1)
        lay.addLayout(bar)
        lay.addWidget(self.status)
        lay.addWidget(note)
        lay.addWidget(box)
        self._reload()

    # ------------------------------------------------------------------ data
    def _files(self) -> list:
        return [t.bookmarks for t in self.tabs]

    def _suggestions(self) -> list[str]:
        return list(self.settings["bookmark_tags"])

    def _reload(self, select: str = "") -> None:
        rows = tagmgr.usage(self._files(), self._suggestions(), self.current)
        self.table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            cells = (r.name, str(r.here), str(r.total), "yes" if r.suggested else "")
            for c, text in enumerate(cells):
                it = QTableWidgetItem(text)
                if c:
                    it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.table.setItem(i, c, it)
            if select and r.name.lower() == select.lower():
                self.table.selectRow(i)
        self._buttons()

    def _selected(self) -> str:
        r = self.table.currentRow()
        it = self.table.item(r, 0) if r >= 0 else None
        return it.text() if it is not None else ""

    def _buttons(self) -> None:
        have = bool(self._selected())
        self.rename_b.setEnabled(have)
        self.delete_b.setEnabled(have)

    def _finish(self, change: tagmgr.TagChange, select: str = "") -> None:
        self.settings["bookmark_tags"] = change.suggestions
        for fi in change.files:
            self.tabs[fi]._bookmarks_modified()                 # repaint markers, refresh the bookmark panel
        self._reload(select)

    # --------------------------------------------------------------- actions
    def _new(self) -> None:
        text, ok = QInputDialog.getText(self, "New tag", "Name of the new tag:")
        if not ok:
            return
        try:
            tags = tagmgr.add(self._suggestions(), text)
        except ValueError as exc:
            QMessageBox.warning(self, "Tags", str(exc))
            return
        self.settings["bookmark_tags"] = tags
        self.status.setText("Tag added to the suggestions.")
        self._reload(tagmgr.clean_tag(text))

    def _rename(self) -> None:
        old = self._selected()
        if not old:
            return
        text, ok = QInputDialog.getText(self, "Rename tag", f"New name for \u201c{old}\u201d:", text=old)
        if not ok or text.strip() == old:
            return
        try:
            new = tagmgr.clean_tag(text)
        except ValueError as exc:
            QMessageBox.warning(self, "Tags", str(exc))
            return
        existing = {r.name.lower() for r in tagmgr.usage(self._files(), self._suggestions())}
        if new.lower() in existing and new.lower() != old.lower():
            ans = QMessageBox.question(self, "Tags", f"\u201c{new}\u201d exists already. Merge \u201c{old}\u201d into it?")
            if ans != QMessageBox.StandardButton.Yes:
                return
        change = tagmgr.rename(self._files(), self._suggestions(), old, new)
        self.status.setText(f"Renamed on {change.bookmarks:,} bookmark(s) in {len(change.files)} file(s).")
        self._finish(change, new)

    def _delete(self) -> None:
        tag = self._selected()
        if not tag:
            return
        row = next((r for r in tagmgr.usage(self._files(), self._suggestions()) if r.name.lower() == tag.lower()), None)
        n = row.total if row is not None else 0
        ans = QMessageBox.question(
            self, "Delete tag",
            f"Remove \u201c{tag}\u201d from {n:,} bookmark(s) in the open files and from the suggestions?\n"
            "The bookmarks themselves stay.")
        if ans != QMessageBox.StandardButton.Yes:
            return
        change = tagmgr.delete(self._files(), self._suggestions(), tag)
        self.status.setText(f"Removed from {change.bookmarks:,} bookmark(s).")
        self._finish(change)

    def _forget_unused(self) -> None:
        before = self._suggestions()
        after = tagmgr.forget_unused(self._files(), before)
        self.settings["bookmark_tags"] = after
        self.status.setText(f"{len(before) - len(after)} unused suggestion(s) removed.")
        self._reload()
