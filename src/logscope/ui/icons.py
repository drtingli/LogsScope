"""Qt side of ``core.iconset``: QIcons for the toolbar and the program logo."""

from __future__ import annotations

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from ..core import iconset

SIZES = (16, 20, 24, 32, 40, 48, 64)
LOGO_SIZES = (16, 24, 32, 48, 64, 128, 256)


def _render(svg: str, size: int) -> QPixmap:
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    img = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    renderer.render(p)
    p.end()
    return QPixmap.fromImage(img)


def icon(name: str, color: QColor | str = "#444444", disabled: QColor | str | None = None) -> QIcon:
    """A toolbar icon in ``color`` (``disabled`` for the greyed-out state)."""
    col = QColor(color).name()
    dis = QColor(disabled).name() if disabled is not None else QColor(col).lighter(210).name()
    ic = QIcon()
    for size in SIZES:
        ic.addPixmap(_render(iconset.svg_for(name, col), size), QIcon.Mode.Normal)
        ic.addPixmap(_render(iconset.svg_for(name, dis), size), QIcon.Mode.Disabled)
    return ic


def app_icon() -> QIcon:
    """The program logo at the usual window / taskbar sizes."""
    ic = QIcon()
    for size in LOGO_SIZES:
        ic.addPixmap(_render(iconset.logo_svg(size), size))
    return ic


def logo_pixmap(size: int = 96) -> QPixmap:
    return _render(iconset.logo_svg(size), size)
