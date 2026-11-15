"""Help > About: version, authoring information and the runtime details useful in a bug report."""

from __future__ import annotations

from PySide6.QtGui import QFontDatabase, QGuiApplication
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QTabWidget, QTextBrowser, QVBoxLayout, QWidget,
)

from .. import about
from .icons import logo_pixmap


class AboutDialog(QDialog):
    def __init__(self, settings_dir: str = "", parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"About {about.APP_NAME}")
        self.resize(560, 520)
        self._settings_dir = settings_dir

        logo = QLabel()
        logo.setPixmap(logo_pixmap(72))
        title = QLabel(f"<span style='font-size:20pt;font-weight:600'>{about.APP_NAME}</span> "
                       f"<span style='font-size:12pt'>v{about.__version__}</span><br>{about.TAGLINE}")
        title.setWordWrap(True)
        head = QWidget()
        hl = QHBoxLayout(head)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.addWidget(logo)
        hl.addWidget(title, 1)

        page = QTextBrowser()
        page.setOpenExternalLinks(False)
        page.setHtml(about.about_html())

        sysinfo = QPlainTextEdit()
        sysinfo.setReadOnly(True)
        sysinfo.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        sysinfo.setPlainText(about.info_text(settings_dir))

        tabs = QTabWidget()
        tabs.addTab(page, "About")
        tabs.addTab(sysinfo, "System info")

        copy = QPushButton("Copy info")
        copy.clicked.connect(self._copy)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        box.addButton(copy, QDialogButtonBox.ButtonRole.ActionRole)
        box.rejected.connect(self.reject)
        box.accepted.connect(self.accept)

        lay = QVBoxLayout(self)
        lay.addWidget(head)
        lay.addWidget(tabs, 1)
        lay.addWidget(box)

    def _copy(self) -> None:
        QGuiApplication.clipboard().setText(about.info_text(self._settings_dir))
