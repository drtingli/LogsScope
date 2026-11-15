"""Dialogs for timestamps: time difference report and timestamp format chooser."""

from __future__ import annotations

from PySide6.QtGui import QFontDatabase, QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QLabel, QLineEdit, QPlainTextEdit,
    QPushButton, QRadioButton, QVBoxLayout,
)

from ..core.timestamps import FORMAT_NAMES, TimestampParser


class DeltaDialog(QDialog):
    """Time difference of the selected lines (copyable text)."""

    def __init__(self, report, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Time difference of the selected lines")
        self.resize(640, 260)
        lay = QVBoxLayout(self)
        self.text = QPlainTextEdit(report.details())
        self.text.setReadOnly(True)
        self.text.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        lay.addWidget(self.text, 1)
        copy = QPushButton("Copy")
        copy.clicked.connect(lambda: QGuiApplication.clipboard().setText(self.text.toPlainText()))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.addButton(copy, QDialogButtonBox.ButtonRole.ActionRole)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)


class TimestampDialog(QDialog):
    """Auto-detect, pick a built-in format, or give a strptime format."""

    def __init__(self, spec: str, sample: list[str], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Timestamp format")
        self.resize(560, 330)
        self._sample = [s for s in sample if s.strip()][:200]
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("How timestamps are recognised in the lines of this tab\n"
                             "(used for the time difference of selected lines and the Timestamp column):"))
        self.auto = QRadioButton("Detect automatically")
        self.builtin = QRadioButton("Built-in format:")
        self.custom = QRadioButton("Custom format (strptime):")
        self.combo = QComboBox()
        self.combo.addItems(FORMAT_NAMES)
        self.edit = QLineEdit()
        self.edit.setPlaceholderText("%d.%m.%Y %H:%M:%S,%f")
        self.edit.setToolTip("Directives: %Y %y %m %d %H %M %S %f %b %B %z %p %I %j")
        self.preview = QLabel("")
        self.preview.setWordWrap(True)
        self.default_cb = QCheckBox("Also use this for tabs opened later")
        for w in (self.auto, self.builtin, self.combo, self.custom, self.edit, self.preview, self.default_cb):
            lay.addWidget(w)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

        if spec.startswith("builtin:") and spec[8:] in FORMAT_NAMES:
            self.builtin.setChecked(True)
            self.combo.setCurrentText(spec[8:])
        elif spec:
            self.custom.setChecked(True)
            self.edit.setText(spec)
        else:
            self.auto.setChecked(True)
        for w in (self.auto, self.builtin, self.custom):
            w.toggled.connect(self._update_preview)
        self.combo.currentTextChanged.connect(self._update_preview)
        self.edit.textChanged.connect(self._update_preview)
        self._update_preview()

    def spec(self) -> str:
        if self.builtin.isChecked():
            return "builtin:" + self.combo.currentText()
        if self.custom.isChecked():
            return self.edit.text().strip()
        return ""

    def make_default(self) -> bool:
        return self.default_cb.isChecked()

    def _parser(self):
        if self.custom.isChecked():
            return TimestampParser.from_format(self.edit.text().strip())       # may raise ValueError
        return TimestampParser.from_spec(self.spec(), self._sample)

    def _update_preview(self, *_args) -> None:
        self.combo.setEnabled(self.builtin.isChecked())
        self.edit.setEnabled(self.custom.isChecked())
        try:
            p = self._parser()
        except ValueError as exc:
            self.preview.setText(f"Invalid format: {exc}")
            return
        hits = [(s, p.parse(s)) for s in self._sample]
        ok = [(s, r) for s, r in hits if r is not None]
        if not p.usable:
            self.preview.setText("No timestamp format recognised in the first lines.")
        elif not ok:
            self.preview.setText(f"{p.label}: matches none of the first {len(hits)} lines.")
        else:
            s, r = ok[0]
            self.preview.setText(f"{p.label}: {len(ok)} of {len(hits)} lines match. "
                                 f"First hit: \u201c{s[r[1]:r[2]].strip()}\u201d")

    def _accept(self) -> None:
        try:
            self._parser()
        except ValueError as exc:
            self.preview.setText(f"Invalid format: {exc}")
            return
        self.accept()
