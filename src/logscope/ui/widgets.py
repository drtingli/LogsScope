"""Small reusable widgets."""

from __future__ import annotations

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import QComboBox, QSizePolicy


class HistoryCombo(QComboBox):
    """Editable combo box = text field + drop-down list of previous entries.

    ``submitted`` fires when Enter is pressed (and, if ``apply_on_pick``, when an
    entry is picked from the list).  Fires at most once per event-loop turn."""

    submitted = Signal()

    def __init__(self, placeholder: str = "", apply_on_pick: bool = False, parent=None) -> None:
        super().__init__(parent)
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumWidth(200)
        le = self.lineEdit()
        le.setPlaceholderText(placeholder)
        le.setClearButtonEnabled(True)
        le.returnPressed.connect(self._fire)
        if apply_on_pick:
            self.activated.connect(lambda _i: self._fire())
        self._pending = False
        self.setCurrentIndex(-1)
        self.clearEditText()

    def _fire(self) -> None:
        if not self._pending:
            self._pending = True
            QTimer.singleShot(0, self._deliver)

    def _deliver(self) -> None:
        self._pending = False
        self.submitted.emit()

    def text(self) -> str:
        return self.currentText()

    def set_text(self, text: str) -> None:
        self.setEditText(text)

    def set_history(self, items: list[str]) -> None:
        current = self.currentText()
        self.blockSignals(True)
        self.clear()
        self.addItems(items)
        self.blockSignals(False)
        self.setEditText(current)

    def focus_and_select(self) -> None:
        self.setFocus()
        self.lineEdit().selectAll()
