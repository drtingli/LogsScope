"""View > Layout > Arrange: any number of columns and rows of areas."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel, QPushButton, QSpinBox, QVBoxLayout,
)

from ..core import layout as lay


class ArrangeDialog(QDialog):
    def __init__(self, cols: int, rows: int, n_tabs: int, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Arrange the view")
        self.n_tabs = n_tabs
        self.cols = QSpinBox()
        self.rows = QSpinBox()
        for sp, v in ((self.cols, cols), (self.rows, rows)):
            sp.setRange(1, lay.MAX_CELLS_PER_SIDE)
            sp.setValue(max(1, min(v, lay.MAX_CELLS_PER_SIDE)))
            sp.valueChanged.connect(self._update)
        form = QFormLayout()
        form.addRow("Columns (side by side)", self.cols)
        form.addRow("Rows (stacked)", self.rows)

        presets = QHBoxLayout()
        for text, c, r in (("3 columns", 3, 1), ("4 rows", 1, 4), ("2 \u00d7 2", 2, 2), ("3 \u00d7 2", 3, 2)):
            b = QPushButton(text)
            b.clicked.connect(lambda _=False, cc=c, rr=r: (self.cols.setValue(cc), self.rows.setValue(rr)))
            presets.addWidget(b)

        self.info = QLabel("")
        self.info.setWordWrap(True)
        note = QLabel("The open tabs are spread over the areas in order. Afterwards drag tabs between areas, "
                      "drop a tab on the edge of an area to split it further, or use the \u22ee menu of an area.")
        note.setWordWrap(True)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        lyt = QVBoxLayout(self)
        lyt.addLayout(form)
        lyt.addLayout(presets)
        lyt.addWidget(self.info)
        lyt.addWidget(note)
        lyt.addWidget(box)
        self._update()

    def _update(self, *_a) -> None:
        cells = self.cols.value() * self.rows.value()
        extra = ""
        if self.n_tabs > cells:
            extra = f" Some areas get several tabs (shown with their own tab bar)."
        elif self.n_tabs < cells:
            extra = f" {cells - self.n_tabs} area(s) stay empty, ready to receive tabs."
        self.info.setText(f"{cells} area(s) for {self.n_tabs} tab(s).{extra}")

    def shape(self) -> tuple[int, int]:
        return self.cols.value(), self.rows.value()
