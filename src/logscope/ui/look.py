"""Qt helpers for the appearance settings: fonts, palettes and a delegate that draws grid lines."""

from __future__ import annotations

from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette, QPen
from PySide6.QtWidgets import QApplication, QStyledItemDelegate

from ..core.uitheme import grid_colour


def area_font(settings, theme: dict, area: str) -> QFont:
    """Log views: fixed-width system font unless the theme says otherwise; bookmarks: UI font."""
    spec = theme["areas"][area]
    if area == "bookmarks":
        font = QFont(QApplication.font())
        family, size = spec["font_family"], spec["font_size"]
    else:
        font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        family = spec["font_family"] or settings["font_family"]          # v0.1 global settings
        size = spec["font_size"] or int(settings["font_size"])
    if family:
        font.setFamily(family)
    if size:
        font.setPointSize(int(size))
    return font


def make_palette(spec: dict, selection: dict) -> QPalette:
    """Default text / background of an area plus the colours of the selected row."""
    pal = QPalette(QApplication.palette())
    if spec["bg"]:
        pal.setColor(QPalette.ColorRole.Base, QColor(spec["bg"]))
        pal.setColor(QPalette.ColorRole.AlternateBase, QColor(spec["bg"]))
    if spec["fg"]:
        pal.setColor(QPalette.ColorRole.Text, QColor(spec["fg"]))
    for group in (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive):    # selection stays visible
        pal.setColor(group, QPalette.ColorRole.Highlight, QColor(selection["bg"]))
        pal.setColor(group, QPalette.ColorRole.HighlightedText, QColor(selection["fg"]))
    return pal


class GridDelegate(QStyledItemDelegate):
    """Normal item painting plus a line under and to the right of every cell."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.grid_h, self.grid_v = True, True
        self.grid_color = QColor("#d9d9d9")

    def set_grid(self, horizontal: bool, vertical: bool, colour: str) -> None:
        self.grid_h, self.grid_v, self.grid_color = bool(horizontal), bool(vertical), QColor(colour)

    def draw_grid(self, painter, rect) -> None:
        if not (self.grid_h or self.grid_v):
            return
        painter.save()
        painter.setPen(QPen(self.grid_color, 1))
        if self.grid_h:
            painter.drawLine(rect.left(), rect.bottom(), rect.right(), rect.bottom())
        if self.grid_v:
            painter.drawLine(rect.right(), rect.top(), rect.right(), rect.bottom())
        painter.restore()

    def paint(self, painter, option, index) -> None:
        super().paint(painter, option, index)
        self.draw_grid(painter, option.rect)


def style_table(table, delegate: GridDelegate, font: QFont, spec: dict, selection: dict, grid: dict,
                row_height: int) -> None:
    """Shared by every table (also the bookmark list)."""
    table.setFont(font)
    pal = make_palette(spec, selection)
    table.setPalette(pal)
    table.setShowGrid(False)                               # the delegate draws the lines
    dark = pal.color(QPalette.ColorRole.Base).lightness() < 128
    delegate.set_grid(grid["horizontal"], grid["vertical"], grid_colour(grid["color"], dark))
    table.verticalHeader().setDefaultSectionSize(row_height)
    table.viewport().update()
