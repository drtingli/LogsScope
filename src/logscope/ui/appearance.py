"""Appearance dialog: fonts and colours per area, selected row, grid lines, bookmark marker."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QCheckBox, QColorDialog, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFontComboBox,
    QGridLayout, QGroupBox, QHBoxLayout, QLabel, QMessageBox, QPushButton, QSpinBox, QToolButton,
    QVBoxLayout, QWidget,
)

from ..core.jsonio import atomic_write_json, read_json
from ..core.theme import AREA_LABELS, AREAS, MARKER_PRESETS, MAX_ROW_SPACING, default_theme, sanitize_theme, theme_from_json

DEFAULT_SIZE = 3            # the smallest spin-box value stands for "default size"


class ColorField(QWidget):
    """A colour swatch button; the small cross goes back to the default colour."""

    changed = Signal()

    def __init__(self, value: str = "", allow_default: bool = True, parent=None) -> None:
        super().__init__(parent)
        self._value = ""
        self._allow_default = allow_default
        self.btn = QPushButton()
        self.btn.setMinimumWidth(96)
        self.btn.clicked.connect(self._pick)
        self.clear_btn = QToolButton()
        self.clear_btn.setText("\u2715")
        self.clear_btn.setToolTip("Use the default colour")
        self.clear_btn.clicked.connect(lambda: self.set_value(""))
        self.clear_btn.setVisible(allow_default)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.btn, 1)
        lay.addWidget(self.clear_btn)
        self.set_value(value)

    def value(self) -> str:
        return self._value

    def set_value(self, value: str) -> None:
        self._value = value or ""
        if self._value:
            fg = "#000000" if QColor(self._value).lightness() > 128 else "#ffffff"
            self.btn.setText(self._value)
            self.btn.setStyleSheet(f"background-color: {self._value}; color: {fg};")
        else:
            self.btn.setText("default")
            self.btn.setStyleSheet("")
        self.changed.emit()

    def _pick(self) -> None:
        c = QColorDialog.getColor(QColor(self._value or "#ffffff"), self, "Choose colour")
        if c.isValid():
            self.set_value(c.name())


class AppearanceDialog(QDialog):
    def __init__(self, theme: dict, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Appearance")
        self.resize(860, 540)
        lay = QVBoxLayout(self)

        # --- areas ---------------------------------------------------------------
        box = QGroupBox("Fonts and default colours of each area")
        g = QGridLayout(box)
        for col, head in enumerate(("Area", "Font", "Size", "Text colour", "Background", "Row spacing")):
            g.addWidget(QLabel(f"<b>{head}</b>"), 0, col)
        self._area: dict[str, tuple] = {}
        for row, area in enumerate(AREAS, start=1):
            font = QFontComboBox()
            font.setEditable(True)
            font.lineEdit().setPlaceholderText("default")
            font.setMinimumWidth(190)
            size = QSpinBox()
            size.setRange(DEFAULT_SIZE, 72)
            size.setSpecialValueText("default")
            fg, bg = ColorField(), ColorField()
            spacing = QSpinBox()
            spacing.setRange(0, MAX_ROW_SPACING)
            spacing.setSuffix(" px")
            spacing.setToolTip("Extra space added to the height of every row (0 = rows as tight as the text)")
            g.addWidget(QLabel(AREA_LABELS[area]), row, 0)
            for col, w in enumerate((font, size, fg, bg, spacing), start=1):
                g.addWidget(w, row, col)
            self._area[area] = (font, size, fg, bg, spacing)
        g.setColumnStretch(1, 1)
        lay.addWidget(box)

        # --- selection / grid / marker ----------------------------------------------
        sel = QGroupBox("Selected row")
        sl = QHBoxLayout(sel)
        self.sel_fg, self.sel_bg = ColorField("", False), ColorField("", False)
        sl.addWidget(QLabel("Text colour"))
        sl.addWidget(self.sel_fg)
        sl.addWidget(QLabel("Background"))
        sl.addWidget(self.sel_bg)
        sl.addStretch(1)
        lay.addWidget(sel)

        grid = QGroupBox("Grid lines between rows and columns")
        gl = QHBoxLayout(grid)
        self.grid_h = QCheckBox("Horizontal (between rows)")
        self.grid_v = QCheckBox("Vertical (between columns)")
        self.grid_color = ColorField("", False)
        gl.addWidget(self.grid_h)
        gl.addWidget(self.grid_v)
        gl.addWidget(QLabel("Colour"))
        gl.addWidget(self.grid_color)
        gl.addStretch(1)
        lay.addWidget(grid)

        mk = QGroupBox("Bookmark marker (first column)")
        ml = QHBoxLayout(mk)
        self.marker = QComboBox()
        self.marker.setEditable(True)
        self.marker.addItems(MARKER_PRESETS)
        self.marker.setMinimumWidth(120)
        ml.addWidget(self.marker)
        hint = QLabel("Pick one or type any emoji - on Windows press Win + . to open the emoji picker. "
                      "Single bookmarks can have their own marker in the bookmark panel.")
        hint.setWordWrap(True)
        ml.addWidget(hint, 1)
        lay.addWidget(mk)

        # --- buttons -------------------------------------------------------------------
        imp, exp, reset = QPushButton("Import…"), QPushButton("Export…"), QPushButton("Reset to defaults")
        imp.clicked.connect(self._import)
        exp.clicked.connect(self._export)
        reset.clicked.connect(lambda: self._load(default_theme()))
        bar = QHBoxLayout()
        for b in (imp, exp, reset):
            bar.addWidget(b)
        bar.addStretch(1)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        bar.addWidget(bb)
        lay.addLayout(bar)
        self._load(theme)

    # ------------------------------------------------------------------- values
    def _load(self, theme: dict) -> None:
        theme = sanitize_theme(theme)
        for area, (font, size, fg, bg, spacing) in self._area.items():
            spec = theme["areas"][area]
            spacing.setValue(spec["row_spacing"])
            if spec["font_family"]:
                font.setCurrentFont(QFont(spec["font_family"]))
                font.setEditText(spec["font_family"])
            else:
                font.setCurrentIndex(-1)
                font.setEditText("")
            size.setValue(spec["font_size"] or DEFAULT_SIZE)
            fg.set_value(spec["fg"])
            bg.set_value(spec["bg"])
        self.sel_fg.set_value(theme["selection"]["fg"])
        self.sel_bg.set_value(theme["selection"]["bg"])
        self.grid_h.setChecked(theme["grid"]["horizontal"])
        self.grid_v.setChecked(theme["grid"]["vertical"])
        self.grid_color.set_value(theme["grid"]["color"])
        self.marker.setEditText(theme["bookmark_marker"])

    def theme(self) -> dict:
        areas = {}
        for area, (font, size, fg, bg, spacing) in self._area.items():
            areas[area] = {"row_spacing": spacing.value(), "font_family": font.currentText().strip(),
                           "font_size": 0 if size.value() <= DEFAULT_SIZE else size.value(),
                           "fg": fg.value(), "bg": bg.value()}
        return sanitize_theme({
            "areas": areas,
            "selection": {"fg": self.sel_fg.value(), "bg": self.sel_bg.value()},
            "grid": {"horizontal": self.grid_h.isChecked(), "vertical": self.grid_v.isChecked(),
                     "color": self.grid_color.value()},
            "bookmark_marker": self.marker.currentText(),
        })

    # ------------------------------------------------------------------ files
    def _export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export appearance", "logscope.theme.json",
                                              "Theme files (*.json);;All files (*)")
        if path:
            try:
                atomic_write_json(path, self.theme())
            except OSError as exc:
                QMessageBox.warning(self, "Export", f"Cannot write file:\n{exc}")

    def _import(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Import appearance", "",
                                              "Theme files (*.json);;All files (*)")
        if not path:
            return
        try:
            self._load(theme_from_json(read_json(path)))
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Import", f"Cannot import:\n{exc}")
