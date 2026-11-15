"""Options > Context menu: choose what the right-click menu of the log panels contains."""

from __future__ import annotations

import copy

from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QMessageBox, QPushButton, QVBoxLayout,
)

from ..core import ctxmenu


class ContextMenuDialog(QDialog):
    def __init__(self, menu: list[dict], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Context menu of the log panels")
        self.resize(900, 560)
        self._items = ctxmenu.sanitize_menu(menu)

        self.listw = QListWidget()
        up, down, rem = QPushButton("Up"), QPushButton("Down"), QPushButton("Remove")
        reset = QPushButton("Reset to defaults")
        up.clicked.connect(lambda: self._move(-1))
        down.clicked.connect(lambda: self._move(1))
        rem.clicked.connect(self._remove)
        reset.clicked.connect(self._reset)
        left = QVBoxLayout()
        left.addWidget(QLabel("Menu items (top to bottom)"))
        left.addWidget(self.listw, 1)
        row = QHBoxLayout()
        for b in (up, down, rem):
            row.addWidget(b)
        left.addLayout(row)
        left.addWidget(reset)

        # --- add a built-in item ------------------------------------------------------
        add_box = QGroupBox("Add")
        self.builtin = QComboBox()
        add_b, sep_b = QPushButton("Add item"), QPushButton("Add separator")
        add_b.clicked.connect(self._add_builtin)
        sep_b.clicked.connect(self._add_separator)
        self.hint = QLabel("")
        self.hint.setWordWrap(True)
        self.builtin.currentIndexChanged.connect(self._show_hint)
        ab = QVBoxLayout(add_box)
        ab.addWidget(self.builtin)
        ab.addWidget(self.hint)
        r2 = QHBoxLayout()
        r2.addWidget(add_b)
        r2.addWidget(sep_b)
        ab.addLayout(r2)

        # --- custom command ----------------------------------------------------------------
        cmd_box = QGroupBox("Run a program (custom command)")
        self.c_label, self.c_command, self.c_args = QLineEdit(), QLineEdit(), QLineEdit()
        self.c_label.setPlaceholderText("Menu text, e.g. Open in editor")
        self.c_command.setPlaceholderText(r"Program, e.g. C:\Program Files\Notepad++\notepad++.exe")
        self.c_args.setPlaceholderText('Arguments, e.g. -n{lineno} "{file}"')
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        self.c_add = QPushButton("Add command")
        self.c_update = QPushButton("Update selected command")
        self.c_add.clicked.connect(self._add_command)
        self.c_update.clicked.connect(self._update_command)
        form = QFormLayout()
        form.addRow("Text", self.c_label)
        crow = QHBoxLayout()
        crow.addWidget(self.c_command, 1)
        crow.addWidget(browse)
        form.addRow("Program", crow)
        form.addRow("Arguments", self.c_args)
        note = QLabel("Placeholders: <code>{line}</code> the whole line, <code>{field}</code> the clicked field, "
                      "<code>{lineno}</code> its line number, <code>{file}</code> the log file, "
                      "<code>{selection}</code> all selected lines. They are filled in after the arguments "
                      "are split, so quotes in the log text cannot break the command. No shell is used.")
        note.setWordWrap(True)
        cb = QVBoxLayout(cmd_box)
        cb.addLayout(form)
        cb.addWidget(note)
        r3 = QHBoxLayout()
        r3.addWidget(self.c_add)
        r3.addWidget(self.c_update)
        cb.addLayout(r3)

        right = QVBoxLayout()
        right.addWidget(add_box)
        right.addWidget(cmd_box)
        right.addStretch(1)
        body = QHBoxLayout()
        body.addLayout(left, 1)
        body.addLayout(right, 1)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addLayout(body, 1)
        lay.addWidget(box)

        self.listw.currentRowChanged.connect(self._selected)
        self._refill(0)

    # ------------------------------------------------------------------ list
    def _refill(self, row: int) -> None:
        self.listw.blockSignals(True)
        self.listw.clear()
        for it in self._items:
            self.listw.addItem(ctxmenu.item_label(it))
        self.listw.blockSignals(False)
        if self._items:
            self.listw.setCurrentRow(max(0, min(row, len(self._items) - 1)))
        self._fill_builtin_combo()
        self._selected(self.listw.currentRow())

    def _fill_builtin_combo(self) -> None:
        used = {it["id"] for it in self._items if it["type"] == "builtin"}
        self.builtin.blockSignals(True)
        self.builtin.clear()
        for iid, (label, _needs, _hint) in ctxmenu.BUILTINS.items():
            if iid not in used:
                self.builtin.addItem(label, iid)
        self.builtin.blockSignals(False)
        self._show_hint()

    def _show_hint(self, *_a) -> None:
        iid = self.builtin.currentData()
        self.hint.setText(ctxmenu.BUILTINS[iid][2] if iid else "All built-in items are in the menu already.")

    def _selected(self, row: int) -> None:
        it = self._items[row] if 0 <= row < len(self._items) else None
        is_cmd = it is not None and it["type"] == "command"
        self.c_update.setEnabled(is_cmd)
        if is_cmd:
            self.c_label.setText(it["label"])
            self.c_command.setText(it["command"])
            self.c_args.setText(it["args"])

    def _move(self, delta: int) -> None:
        r = self.listw.currentRow()
        t = r + delta
        if 0 <= r < len(self._items) and 0 <= t < len(self._items):
            self._items[r], self._items[t] = self._items[t], self._items[r]
            self._refill(t)

    def _remove(self) -> None:
        r = self.listw.currentRow()
        if 0 <= r < len(self._items):
            del self._items[r]
            self._items = ctxmenu.tidy(self._items)
            self._refill(r)

    def _reset(self) -> None:
        self._items = ctxmenu.default_menu()
        self._refill(0)

    def _insert(self, item: dict) -> None:
        r = self.listw.currentRow()
        at = r + 1 if 0 <= r < len(self._items) else len(self._items)
        self._items.insert(at, item)
        self._items = ctxmenu.tidy(self._items)
        self._refill(min(at, len(self._items) - 1))

    def _add_builtin(self) -> None:
        iid = self.builtin.currentData()
        if iid:
            self._insert({"type": "builtin", "id": iid})

    def _add_separator(self) -> None:
        self._insert({"type": "separator"})

    # ---------------------------------------------------------------- commands
    def _command_from_fields(self) -> dict | None:
        item = ctxmenu.sanitize_item({"type": "command", "label": self.c_label.text(),
                                      "command": self.c_command.text(), "args": self.c_args.text()})
        if item is None:
            QMessageBox.warning(self, "Context menu", "Enter the menu text and the program.")
            return None
        try:
            ctxmenu.split_command(item["command"])
            ctxmenu.split_args(item["args"])
        except ValueError as exc:
            QMessageBox.warning(self, "Context menu", str(exc))
            return None
        return item

    def _add_command(self) -> None:
        item = self._command_from_fields()
        if item is not None:
            self._insert(item)

    def _update_command(self) -> None:
        r = self.listw.currentRow()
        item = self._command_from_fields()
        if item is not None and 0 <= r < len(self._items) and self._items[r]["type"] == "command":
            self._items[r] = item
            self._refill(r)

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose a program", "", "Programs (*.exe *.bat *.cmd *.py);;All files (*)")
        if path:
            self.c_command.setText(path)

    # ------------------------------------------------------------------ result
    def menu(self) -> list[dict]:
        return copy.deepcopy(self._items)
