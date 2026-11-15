"""A tab taken out of the main window and shown in a window of its own.

The window reuses the main window's actions (find, filter panel, bookmarks, follow,
time difference ...): they act on the tab of whichever window is active.  Closing the
window puts the tab back into the main window; "Close tab" really closes it.
"""

from __future__ import annotations

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QLabel, QMainWindow, QToolBar, QVBoxLayout, QWidget

from .bookmark_panel import BookmarkPanel

MENUS = ("&File", "&View", "&Search", "&Bookmarks", "&Analyze", "&Tab", "&Help")


class DetachedWindow(QMainWindow):
    def __init__(self, main, tab) -> None:
        super().__init__(None)                      # no parent: a real top-level window
        self.main, self.tab = main, tab
        self._final = False                         # True: closing really closes (no re-attach)
        self.resize(980, 640)

        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.strip = QLabel("")
        self.strip.setFixedHeight(5)
        lay.addWidget(self.strip)
        lay.addWidget(tab, 1)
        tab.show()
        self.setCentralWidget(body)

        self.bookmark_panel = BookmarkPanel(main.settings, self)
        self.bookmark_panel.all_tabs = main.all_tabs
        self.bookmark_panel.setObjectName("DetachedBookmarksDock")
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.bookmark_panel)
        self.bookmark_panel.hide()
        self.bookmark_panel.set_tab(tab)
        self.bookmark_panel.apply_theme(main.settings.theme())

        self.reattach_act = QAction("&Re-attach to main window", self)
        self.reattach_act.triggered.connect(lambda: main.reattach(self))   # Ctrl+Shift+D: shared action
        self._build_menus()
        self._build_toolbar()

        self.status = QLabel("")
        self.statusBar().addPermanentWidget(self.status, 1)
        tab.status_message.connect(self._show_status)
        tab.look_changed.connect(self.refresh_look)
        tab.title_changed.connect(self._on_title)
        self._timer = QTimer(self)
        self._timer.timeout.connect(lambda: self.status.setText(self.tab.status_text()))
        self._timer.start(500)
        self.refresh_look()

    # ----------------------------------------------------------------------- UI
    def _build_menus(self) -> None:
        bar = self.menuBar()
        shared = self.main.menu_actions()
        for title in MENUS:
            m = bar.addMenu(title)
            if title == "&File":
                m.addAction(self.reattach_act)
            if title == "&View":
                m.addAction(self.bookmark_panel.toggleViewAction())
                self.bookmark_panel.toggleViewAction().setText("&Bookmarks panel")
                self.bookmark_panel.toggleViewAction().setShortcut(QKeySequence("Ctrl+B"))
            for a in shared.get(title, []):
                if a is None:
                    m.addSeparator()
                else:
                    m.addAction(a)

    def _build_toolbar(self) -> None:
        tb = QToolBar("Window", self)
        tb.setMovable(False)
        tb.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.addToolBar(tb)
        tb.addAction(self.reattach_act)
        tb.addSeparator()
        m = self.main
        self.refresh_icons()
        for a in (m.follow_act, m.autofit_act, m.wrap_act, m.fit_act):
            tb.addAction(a)
        tb.addSeparator()
        for a in (m.find_act, m.panel_act, self.bookmark_panel.toggleViewAction(), m.delta_act):
            tb.addAction(a)
        tb.addSeparator()
        for a in (m.sync_act, m.link_dlg_act, m.merge_act):
            tb.addAction(a)
        self.toolbar = tb
        m.style_toolbar(tb)

    def refresh_icons(self) -> None:
        """Icons of this window's own actions, in the text colour of the current window theme."""
        self.main.decorate(self.reattach_act, "reattach", "Re-attach")
        self.main.decorate(self.bookmark_panel.toggleViewAction(), "bookmark", "Bookmarks")

    def _show_status(self, msg: str) -> None:
        self.statusBar().showMessage(msg, 5000)

    def _on_title(self, _title: str) -> None:
        self.refresh_look()

    def refresh_look(self) -> None:
        color = self.tab.tab_color
        self.strip.setStyleSheet(f"background: {color};" if color else "")
        self.strip.setVisible(bool(color))
        link = "\U0001F517 " if self.tab.linked else ""
        self.setWindowTitle(f"{link}{self.tab.title} \u2014 LogScope")

    # ------------------------------------------------------------------ closing
    def take_tab(self):
        """Hand the tab widget back (to re-attach or close it)."""
        self.centralWidget().layout().removeWidget(self.tab)
        self.tab.setParent(None)
        self.bookmark_panel.set_tab(None)
        self._timer.stop()
        for sig, slot in ((self.tab.status_message, self._show_status),
                          (self.tab.look_changed, self.refresh_look),
                          (self.tab.title_changed, self._on_title)):
            try:
                sig.disconnect(slot)
            except (RuntimeError, TypeError):
                pass
        return self.tab

    def force_close(self) -> None:
        self._final = True
        self.close()

    def closeEvent(self, e) -> None:
        if self._final:
            e.accept()
            return
        e.ignore()                                   # the main window decides (re-attach)
        QTimer.singleShot(0, lambda: self.main.reattach(self))
