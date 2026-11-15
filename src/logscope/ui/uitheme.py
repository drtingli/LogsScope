"""Qt side of ``core.uitheme``: applies the window theme to the application."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

from ..core import uitheme


def build_palette(scheme: str) -> QPalette:
    colours = uitheme.palette_colours(scheme)
    pal = QPalette()
    for role, value in colours["normal"].items():
        pal.setColor(getattr(QPalette.ColorRole, role), QColor(value))
    for role, value in colours["disabled"].items():
        pal.setColor(QPalette.ColorGroup.Disabled, getattr(QPalette.ColorRole, role), QColor(value))
    return pal


def system_is_dark(app: QApplication) -> bool:
    return app.styleHints().colorScheme() == Qt.ColorScheme.Dark


def apply(app: QApplication, mode: str) -> str:
    """Use ``mode`` for every window; returns the scheme shown (``"light"`` / ``"dark"``)."""
    hints = app.styleHints()
    mode = uitheme.sanitize_mode(mode)
    if mode == "system":
        hints.unsetColorScheme()                  # report (and follow) the Windows setting again
        scheme = uitheme.effective_scheme(mode, system_is_dark(app))
    else:
        scheme = mode
        # also switches native parts such as the Windows title bar
        hints.setColorScheme(Qt.ColorScheme.Dark if scheme == "dark" else Qt.ColorScheme.Light)
    app.setPalette(build_palette(scheme))
    return scheme


def is_dark() -> bool:
    """Does the application currently show a dark window theme?"""
    return QApplication.palette().color(QPalette.ColorRole.Base).lightness() < 128
