"""Highlight-rule editor with named groups, import and export."""

from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox, QColorDialog, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout,
    QInputDialog, QLabel, QLineEdit, QMenu, QMessageBox, QPushButton, QRadioButton, QTableWidget,
    QTableWidgetItem, QToolButton, QVBoxLayout,
)

from ..core.actions import launch, sanitize_action, split_command
from ..core.highlight import HighlightRule, groups_from_json, groups_to_json, validate_rule
from ..core.jsonio import atomic_write_json, read_json

COLS = ["Pattern", "Regex", "Case", "Whole line", "Text colour", "Background", "Preview"]
C_PAT, C_RX, C_CASE, C_WHOLE, C_FG, C_BG, C_PREV = range(7)
PRESETS = [("Red", "#ffffff", "#c0392b"), ("Orange", "#000000", "#f5cd79"), ("Yellow", "#000000", "#ffff99"),
           ("Green", "#000000", "#b8e994"), ("Blue", "#ffffff", "#2d6cdf"), ("Grey", "#000000", "#d0d0d0"),
           ("Purple", "#ffffff", "#8e44ad")]


class ActionDialog(QDialog):
    """Program to start when the filter matches a new line."""

    def __init__(self, action: dict | None, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Filter action - run a program on match")
        self.resize(640, 300)
        action = action or {}
        self.enabled = QCheckBox("Enabled")
        self.enabled.setChecked(action.get("enabled", True) is not False)
        self.command = QLineEdit(action.get("command", ""))
        self.command.setPlaceholderText(r'e.g.  C:\tools\notify.exe   or   "C:\Program Files\x\y.exe" --flag   or   python alert.py')
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        test = QPushButton("Test")
        test.setToolTip("Start the program once with a sample line")
        test.clicked.connect(self._test)
        row = QHBoxLayout()
        row.addWidget(self.command, 1)
        row.addWidget(browse)
        row.addWidget(test)
        self.first = QRadioButton("First match only (once, until the filter is applied again)")
        self.every = QRadioButton("Every match")
        (self.every if action.get("mode") == "every" else self.first).setChecked(True)
        info = QLabel(
            "The program starts when a <b>new</b> line matches the filter (lines already in the file "
            "are not considered).<br>The <b>complete log line is passed as the last argument</b> "
            "(also in the environment variables LOGSCOPE_LINE, LOGSCOPE_LINE_NO, LOGSCOPE_FILE). "
            ".py scripts run with the Python that runs LogScope. For .bat / .cmd files the characters "
            "&amp; | &lt; &gt; ^ % \" ! are replaced by spaces in the argument; use LOGSCOPE_LINE for the exact text.")
        info.setWordWrap(True)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        box.accepted.connect(self._validate_and_accept)
        box.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(self.enabled)
        lay.addWidget(QLabel("Program and fixed arguments (leave empty for no action):"))
        lay.addLayout(row)
        lay.addWidget(self.first)
        lay.addWidget(self.every)
        lay.addWidget(info)
        lay.addStretch(1)
        lay.addWidget(box)

    def action(self) -> dict | None:
        return sanitize_action({"command": self.command.text(),
                                "mode": "every" if self.every.isChecked() else "first",
                                "enabled": self.enabled.isChecked()})

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose program or script", "", "All files (*)")
        if path:
            self.command.setText(f'"{path}"' if " " in path else path)

    def _check(self) -> bool:
        text = self.command.text().strip()
        if not text:
            return True
        try:
            split_command(text)
        except ValueError as exc:
            QMessageBox.warning(self, "Filter action", str(exc))
            return False
        return True

    def _test(self) -> None:
        if not self._check() or not self.command.text().strip():
            return
        try:
            launch(self.command.text(), "LogScope test line", 0, "")
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Filter action", f"Cannot start the program:\n{exc}")

    def _validate_and_accept(self) -> None:
        if self._check():
            self.accept()


def unique_name(name: str, existing) -> str:
    if name not in existing:
        return name
    n = 2
    while f"{name} ({n})" in existing:
        n += 1
    return f"{name} ({n})"


class HighlightDialog(QDialog):
    def __init__(self, groups: dict[str, list[HighlightRule]], active: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Highlight rules (first matching rule wins)")
        self.resize(920, 480)
        self._groups = {n: list(rs) for n, rs in groups.items()}
        self._current = active if active in self._groups else next(iter(self._groups))

        # --- group bar ---------------------------------------------------------
        self.group_combo = QComboBox()
        self.group_combo.addItems(list(self._groups))
        self.group_combo.setCurrentText(self._current)
        self.group_combo.currentTextChanged.connect(self._switch_group)
        new_b, ren_b, del_b = QPushButton("New"), QPushButton("Rename"), QPushButton("Delete")
        imp_b, exp_b = QPushButton("Import…"), QPushButton("Export…")
        new_b.clicked.connect(self._new_group)
        ren_b.clicked.connect(self._rename_group)
        del_b.clicked.connect(self._delete_group)
        imp_b.clicked.connect(self._import)
        exp_b.clicked.connect(self._export)
        gbar = QHBoxLayout()
        gbar.addWidget(QLabel("Group:"))
        gbar.addWidget(self.group_combo, 1)
        for b in (new_b, ren_b, del_b, imp_b, exp_b):
            gbar.addWidget(b)

        # --- rules table -------------------------------------------------------
        self.table = QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0, 280)
        self.table.cellDoubleClicked.connect(self._pick_colour)
        self.table.setToolTip("Whole line: colour the entire row.  Unchecked: colour only the matching words.\n"
                              "Select rows, then use the colour buttons - or double-click a colour cell.")
        add, rem = QPushButton("Add rule"), QPushButton("Remove rule")
        up, down = QPushButton("Up"), QPushButton("Down")
        add.clicked.connect(lambda: self._add_row(HighlightRule("")))
        rem.clicked.connect(self._remove)
        up.clicked.connect(lambda: self._move(-1))
        down.clicked.connect(lambda: self._move(1))
        fg_b, bg_b = QPushButton("Text colour…"), QPushButton("Background…")
        fg_b.setToolTip("Set the text colour of the selected rules")
        bg_b.setToolTip("Set the background colour of the selected rules")
        fg_b.clicked.connect(lambda: self._pick_for_selection(C_FG))
        bg_b.clicked.connect(lambda: self._pick_for_selection(C_BG))
        preset_b = QToolButton()
        preset_b.setText("Preset colours")
        preset_b.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        pm = QMenu(preset_b)
        for name, fg, bg in PRESETS:
            act = pm.addAction(name)
            act.triggered.connect(lambda _=False, f=fg, b=bg: self._apply_preset(f, b))
        preset_b.setMenu(pm)
        bar = QHBoxLayout()
        for b in (add, rem, up, down, fg_b, bg_b, preset_b):
            bar.addWidget(b)
        bar.addStretch(1)

        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        box.accepted.connect(self._validate_and_accept)
        box.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addLayout(gbar)
        lay.addWidget(self.table, 1)
        lay.addLayout(bar)
        lay.addWidget(box)
        self._fill(self._groups[self._current])

    # ----------------------------------------------------------------- results
    def groups(self) -> dict[str, list[HighlightRule]]:
        self._store()
        return {n: list(rs) for n, rs in self._groups.items()}

    def active(self) -> str:
        return self._current

    # ------------------------------------------------------------------ groups
    def _store(self) -> None:
        self._groups[self._current] = self.rules()

    def _fill(self, rules: list[HighlightRule]) -> None:
        self.table.setRowCount(0)
        for r in rules:
            self._add_row(r)

    def _switch_group(self, name: str) -> None:
        if not name or name == self._current or name not in self._groups:
            return
        self._store()
        self._current = name
        self._fill(self._groups[name])

    def _rebuild_combo(self, select: str) -> None:
        self.group_combo.blockSignals(True)
        self.group_combo.clear()
        self.group_combo.addItems(list(self._groups))
        self.group_combo.setCurrentText(select)
        self.group_combo.blockSignals(False)

    def _ask_name(self, title: str, default: str = "") -> str | None:
        name, ok = QInputDialog.getText(self, title, "Group name:", text=default)
        name = name.strip()
        return name if ok and name else None

    def _new_group(self) -> None:
        name = self._ask_name("New group")
        if name is None:
            return
        self._store()
        name = unique_name(name, self._groups)
        self._groups[name] = []
        self._current = name
        self._rebuild_combo(name)
        self._fill([])

    def _rename_group(self) -> None:
        name = self._ask_name("Rename group", self._current)
        if name is None or name == self._current:
            return
        self._store()
        if name in self._groups:
            QMessageBox.warning(self, "Highlight groups", f"A group named '{name}' already exists.")
            return
        self._groups = {(name if n == self._current else n): rs for n, rs in self._groups.items()}
        self._current = name
        self._rebuild_combo(name)

    def _delete_group(self) -> None:
        if len(self._groups) <= 1:
            QMessageBox.information(self, "Highlight groups", "At least one group must remain.")
            return
        ans = QMessageBox.question(self, "Highlight groups", f"Delete group '{self._current}'?")
        if ans != QMessageBox.StandardButton.Yes:
            return
        names = list(self._groups)
        idx = names.index(self._current)
        del self._groups[self._current]
        self._current = list(self._groups)[min(idx, len(self._groups) - 1)]
        self._rebuild_combo(self._current)
        self._fill(self._groups[self._current])

    def _export(self) -> None:
        self._store()
        path, _ = QFileDialog.getSaveFileName(self, "Export highlight group", f"{self._current}.highlight.json",
                                              "Highlight files (*.json);;All files (*)")
        if not path:
            return
        try:
            atomic_write_json(path, groups_to_json({self._current: self._groups[self._current]}))
        except OSError as exc:
            QMessageBox.warning(self, "Export", f"Cannot write file:\n{exc}")

    def _import(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Import highlight groups", "",
                                              "Highlight files (*.json);;All files (*)")
        if not path:
            return
        try:
            imported = groups_from_json(read_json(path))
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Import", f"Cannot import:\n{exc}")
            return
        self._store()
        last = self._current
        for name, rules in imported.items():
            last = unique_name(name, self._groups)
            self._groups[last] = rules
        self._current = last
        self._rebuild_combo(last)
        self._fill(self._groups[last])

    # ------------------------------------------------------------------- rules
    def _add_row(self, r: HighlightRule) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setItem(row, 0, QTableWidgetItem(r.pattern))
        for col, flag in ((C_RX, r.regex), (C_CASE, r.case_sensitive), (C_WHOLE, r.whole_line)):
            it = QTableWidgetItem()
            it.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            it.setCheckState(Qt.CheckState.Checked if flag else Qt.CheckState.Unchecked)
            self.table.setItem(row, col, it)
        self._set_colour(row, C_FG, r.fg)
        self._set_colour(row, C_BG, r.bg)
        self._update_preview(row)

    def _set_colour(self, row: int, col: int, hexcol: str) -> None:
        it = QTableWidgetItem(hexcol)
        it.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        it.setBackground(QColor(hexcol))
        it.setForeground(QColor("#000000" if QColor(hexcol).lightness() > 128 else "#ffffff"))
        self.table.setItem(row, col, it)

    def _update_preview(self, row: int) -> None:
        it = QTableWidgetItem("Sample text 示例 Aa 123")
        it.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        it.setForeground(QColor(self.table.item(row, C_FG).text()))
        it.setBackground(QColor(self.table.item(row, C_BG).text()))
        self.table.setItem(row, C_PREV, it)

    def _pick_colour(self, row: int, col: int) -> None:
        if col in (C_FG, C_BG):
            self._pick_for_rows([row], col)

    def _selected_rows(self) -> list[int]:
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        return rows or ([self.table.currentRow()] if self.table.currentRow() >= 0 else [])

    def _pick_for_selection(self, col: int) -> None:
        rows = self._selected_rows()
        if not rows:
            QMessageBox.information(self, "Highlight rules", "Select one or more rules first.")
            return
        self._pick_for_rows(rows, col)

    def _pick_for_rows(self, rows: list[int], col: int) -> None:
        title = "Text colour" if col == C_FG else "Background colour"
        c = QColorDialog.getColor(QColor(self.table.item(rows[0], col).text()), self, title)
        if c.isValid():
            for r in rows:
                self._set_colour(r, col, c.name())
                self._update_preview(r)

    def _apply_preset(self, fg: str, bg: str) -> None:
        for r in self._selected_rows():
            self._set_colour(r, C_FG, fg)
            self._set_colour(r, C_BG, bg)
            self._update_preview(r)

    def _remove(self) -> None:
        for row in sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True):
            self.table.removeRow(row)

    def _move(self, delta: int) -> None:
        rules = self.rules()
        sel = self.table.currentRow()
        tgt = sel + delta
        if sel < 0 or not 0 <= tgt < len(rules):
            return
        rules[sel], rules[tgt] = rules[tgt], rules[sel]
        self._fill(rules)
        self.table.selectRow(tgt)

    def rules(self) -> list[HighlightRule]:
        out = []
        for row in range(self.table.rowCount()):
            pat = self.table.item(row, 0).text()
            if not pat:
                continue
            out.append(HighlightRule(
                pat,
                self.table.item(row, C_RX).checkState() == Qt.CheckState.Checked,
                self.table.item(row, C_CASE).checkState() == Qt.CheckState.Checked,
                self.table.item(row, C_FG).text(),
                self.table.item(row, C_BG).text(),
                self.table.item(row, C_WHOLE).checkState() == Qt.CheckState.Checked,
            ))
        return out

    def _validate_and_accept(self) -> None:
        self._store()
        problems = [f"{g}: '{r.pattern}' - {err}"
                    for g, rs in self._groups.items() for r in rs
                    if (err := validate_rule(r)) is not None]
        if problems:
            QMessageBox.warning(self, "Invalid regular expression",
                                "Please fix these rules first:\n\n" + "\n".join(problems[:10]))
            return
        self.accept()
