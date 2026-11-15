"""Choose which open files are linked (time sync + merging) and give each its clock offset."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QMessageBox, QPushButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout,
)

from ..core.timesync import format_offset, parse_offset

C_TITLE, C_PATH, C_FORMAT, C_OFFSET = range(4)


class LinkDialog(QDialog):
    def __init__(self, tabs: list, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Link files - time sync and merge")
        self.resize(900, 420)
        self._tabs = tabs
        info = QLabel(
            "Linked files follow each other: scrolling or jumping to a line in one of them moves the others "
            "to the line with the closest timestamp (toolbar button <b>Sync time</b>), and "
            "<b>Analyze &gt; Merge linked files</b> interleaves their lines by time in a new tab.<br>"
            "Only files whose lines carry a timestamp can be linked. <b>Clock offset</b> is added to a "
            "file's timestamps (e.g. <code>+01:00</code>, <code>-00:00:02.5</code>, <code>-250ms</code>) "
            "when its machine's clock or time zone differs.")
        info.setWordWrap(True)
        self.table = QTableWidget(len(tabs), 4)
        self.table.setHorizontalHeaderLabels(["File", "Path", "Timestamp format", "Clock offset"])
        self.table.verticalHeader().hide()
        self.table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        for r, t in enumerate(tabs):
            usable = t.can_link()
            title = QTableWidgetItem(t.title)
            title.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled if usable
                           else Qt.ItemFlag.NoItemFlags)
            title.setCheckState(Qt.CheckState.Checked if (t.linked and usable) else Qt.CheckState.Unchecked)
            if t.tab_color:
                title.setData(Qt.ItemDataRole.DecorationRole, _swatch(t.tab_color))
            path = QTableWidgetItem(t.saved_path or t.path)
            fmt = QTableWidgetItem(t.get_ts_parser().label if usable else "no timestamps found")
            off = QTableWidgetItem(format_offset(t.ts_offset))
            for it in (path, fmt):
                it.setFlags(Qt.ItemFlag.ItemIsEnabled if usable else Qt.ItemFlag.NoItemFlags)
            off.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsEditable if usable else Qt.ItemFlag.NoItemFlags)
            for c, it in enumerate((title, path, fmt, off)):
                self.table.setItem(r, c, it)
        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setStretchLastSection(True)
        all_b, none_b = QPushButton("Link all with timestamps"), QPushButton("Unlink all")
        all_b.clicked.connect(lambda: self._set_all(True))
        none_b.clicked.connect(lambda: self._set_all(False))
        row = QHBoxLayout()
        row.addWidget(all_b)
        row.addWidget(none_b)
        row.addStretch(1)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        box.accepted.connect(self._accept)
        box.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(info)
        lay.addWidget(self.table, 1)
        lay.addLayout(row)
        lay.addWidget(box)
        self._result: list[tuple] = []

    def _set_all(self, on: bool) -> None:
        for r, t in enumerate(self._tabs):
            it = self.table.item(r, C_TITLE)
            if it is not None and (it.flags() & Qt.ItemFlag.ItemIsUserCheckable):
                it.setCheckState(Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)

    def _accept(self) -> None:
        out = []
        for r, t in enumerate(self._tabs):
            if not t.can_link():
                out.append((t, False, t.ts_offset))
                continue
            try:
                off = parse_offset(self.table.item(r, C_OFFSET).text())
            except ValueError as exc:
                self.table.setCurrentCell(r, C_OFFSET)
                QMessageBox.warning(self, "Clock offset", f"{t.title}: {exc}")
                return
            out.append((t, self.table.item(r, C_TITLE).checkState() == Qt.CheckState.Checked, off))
        self._result = out
        self.accept()

    def result_rows(self) -> list[tuple]:
        """``[(tab, linked, offset_seconds)]``"""
        return list(self._result)


def _swatch(color: str):
    from PySide6.QtGui import QColor, QIcon, QPixmap
    pix = QPixmap(14, 14)
    pix.fill(QColor(color))
    return QIcon(pix)
