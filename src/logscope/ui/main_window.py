"""Main window: tabbed log views (LogTabWindow)."""

from __future__ import annotations

import os
import re
import subprocess
import sys

from PySide6.QtCore import QEvent, QSize, QTimer, Qt, QUrl
from PySide6.QtGui import (
    QAction, QActionGroup, QColor, QDesktopServices, QGuiApplication, QIcon, QKeySequence, QPalette, QPixmap,
)
from PySide6.QtWidgets import (
    QApplication, QColorDialog, QFileDialog, QInputDialog, QLabel, QMainWindow, QMenu, QMessageBox,
    QComboBox, QToolBar, QToolButton,
)

import numpy as np

from ..core import derive
from ..core.clipfile import save_text_file, suggest_name
from ..core.columnizers import ColumnizerRegistry
from ..core.highlight import HighlightEngine, HighlightRule
from ..core.mergeinfo import CODES, MergeInfo, MergeSource
from ..core.session import load_session_file, sanitize_session, save_session_file, session_actions, strip_actions
from ..core.theme import nudge_row_spacing, reset_row_spacing
from ..core.timestamps import format_stamp
from ..core.timesync import effective_ms, format_offset, is_time_only, merge_order, parse_offset, scan_times
from ..core.uitheme import MODE_LABELS, MODES, sanitize_mode
from .bookmark_panel import BookmarkPanel
from .appearance import AppearanceDialog
from .detached import DetachedWindow
from .dialogs import HighlightDialog
from .findbar import focused_finder, selected_text
from .linkdialog import LinkDialog
from .log_tab import LogTab
from . import icons
from . import uitheme
from .aboutdialog import AboutDialog
from .arrangedialog import ArrangeDialog
from .ctxmenu_dialog import ContextMenuDialog
from .regexdialog import RegexColumnizerDialog
from ..core import layout as layout_core
from .tabhost import LAYOUTS, TabHost
from .timedialogs import DeltaDialog, TimestampDialog
from .workers import FuncWorker

SESSION_TICK_MS = 10_000        # also remembers scroll position / filter edits
SESSION_DEBOUNCE_MS = 500
TAB_COLORS = [("Red", "#e74c3c"), ("Orange", "#f39c12"), ("Yellow", "#f1c40f"), ("Green", "#2ecc71"),
              ("Teal", "#1abc9c"), ("Blue", "#3498db"), ("Purple", "#9b59b6"), ("Pink", "#fd79a8"),
              ("Grey", "#95a5a6")]


TOOLBAR_STYLES = [
    ("icon", "Icons only", Qt.ToolButtonStyle.ToolButtonIconOnly),
    ("text", "Text only", Qt.ToolButtonStyle.ToolButtonTextOnly),
    ("icon_text_beside", "Icons and text (text beside the icon)", Qt.ToolButtonStyle.ToolButtonTextBesideIcon),
    ("icon_text_under", "Icons and text (text below the icon)", Qt.ToolButtonStyle.ToolButtonTextUnderIcon),
]
ICON_SIZES = [("Small (16 px)", 16), ("Medium (20 px)", 20), ("Large (24 px)", 24), ("Extra large (32 px)", 32)]


class MainWindow(QMainWindow):
    def __init__(self, settings, registry: ColumnizerRegistry) -> None:
        super().__init__()
        self.settings, self.registry = settings, registry
        self.highlight = HighlightEngine(settings.highlight_rules())
        self._closing = False
        self._applying_ui_theme = False
        app = QApplication.instance()
        self._ui_scheme = uitheme.apply(app, sanitize_mode(settings["ui_theme"]))   # before any widget is styled
        app.styleHints().colorSchemeChanged.connect(self._on_system_scheme)
        self.setWindowTitle("LogScope")
        self.resize(1200, 760)
        self.setAcceptDrops(True)

        self._merge_worker: FuncWorker | None = None
        self.detached: list[DetachedWindow] = []        # tabs living in windows of their own
        self._menu_actions: dict[str, list] = {}        # shared with the detached windows
        self.tabs = TabHost()
        self.tabs.tabCloseRequested.connect(self.close_tab)
        self.tabs.currentChanged.connect(self._on_tab_changed)
        self.tabs.tabContextRequested.connect(self._tab_context_menu)
        self.tabs.detachRequested.connect(self.detach_tab)
        self.setCentralWidget(self.tabs)

        self._session_timer = QTimer(self)                 # debounced "something changed"
        self._session_timer.setSingleShot(True)
        self._session_timer.timeout.connect(self._save_session_now)

        self.bookmark_panel = BookmarkPanel(settings, self)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.bookmark_panel)
        self.bookmark_panel.hide()                      # opened on demand (Ctrl+B / toolbar)

        self.status = QLabel("")
        self.statusBar().addPermanentWidget(self.status, 1)
        self._build_menus()
        self._build_toolbar()
        self.tabs.set_leds_visible(bool(settings["activity_led"]))
        self.tabs.layoutChanged.connect(self._on_layout_changed)
        self.tabs.setLayoutMode(settings["layout"])
        QApplication.instance().focusChanged.connect(self._sync_follow_action)

        self._status_timer = QTimer(self)
        self._status_timer.timeout.connect(self._update_status)
        self._status_timer.start(500)
        self._tick = QTimer(self)                          # periodic safety net
        self._tick.timeout.connect(self._save_session_now)
        self._tick.start(SESSION_TICK_MS)

    # ------------------------------------------------------------------ menus
    def _act(self, menu, text, slot, shortcut=None, checkable=False):
        a = QAction(text, self)
        a.setCheckable(checkable)
        if shortcut:
            a.setShortcut(QKeySequence(shortcut))
        a.triggered.connect(slot)
        menu.addAction(a)
        self._menu_actions.setdefault(menu.title(), []).append(a)
        return a

    def menu_actions(self) -> dict[str, list]:
        """Actions per menu title (shared with the menus of detached windows)."""
        return self._menu_actions

    def _build_menus(self) -> None:
        m = self.menuBar().addMenu("&File")
        self.open_act = self._act(m, "&Open…", self.open_dialog, "Ctrl+O")
        self.paste_act = self._act(m, "&Paste clipboard as new file…", self.paste_as_file, "Ctrl+Shift+V")
        self.recent_menu = m.addMenu("Open &recent")
        self._act(m, "&Close tab", self.close_current, "Ctrl+W")
        self.save_act = self._act(m, "Save tab &as…", self.save_as, "Ctrl+S")
        self.save_act.setToolTip("Save the lines of this tab to a file (for a filter result: the filtered lines)")
        m.addSeparator()
        self._act(m, "&Save session…", self.save_session_dialog)
        self._act(m, "&Load session…", self.load_session_dialog)
        m.addSeparator()
        self._act(m, "E&xit", self.close, "Ctrl+Q")
        self._refresh_recent()

        v = self.menuBar().addMenu("&View")
        self.follow_act = self._act(v, "&Follow tail", self._follow_toggled, "Ctrl+T", True)
        self.follow_act.setChecked(bool(self.settings["follow_tail"]))
        self.panel_act = self._act(v, "Filter / results &panel",
                                   lambda: self._cur(lambda t: t.toggle_bottom()))
        self.panel_act.setShortcuts([QKeySequence("F4"), QKeySequence("Ctrl+Shift+F")])
        v.addSeparator()
        self.autofit_act = self._act(v, "&Auto-fit column widths", self._autofit_toggled, checkable=True)
        self.autofit_act.setChecked(bool(self.settings["autofit_columns"]))
        self.autofit_act.setToolTip("Column widths follow the longest text, also when new lines arrive")
        self.wrap_act = self._act(v, "&Wrap text to window width", self._wrap_toggled, checkable=True)
        self.wrap_act.setChecked(bool(self.settings["wrap_text"]))
        self.wrap_act.setToolTip("Fit all fields to the window width; long fields wrap onto several lines")
        rs = v.addMenu("Row &spacing")
        self._act(rs, "&Looser (+1 px in every panel)", lambda: self._nudge_row_spacing(1))
        self._act(rs, "&Tighter (-1 px in every panel)", lambda: self._nudge_row_spacing(-1))
        self._act(rs, "&Reset to default", lambda: self._nudge_row_spacing(None))
        rs.addSeparator()
        self._act(rs, "&Per panel…", self._edit_appearance)
        self._menu_actions["&View"].append(rs.menuAction())
        self.fit_act = self._act(v, "&Fit columns now", lambda: self._each_tab(lambda t: t.fit_columns_now()))
        self.fit_act.setToolTip("Measure the widest text once (deeper scan), even when auto-fit is off")
        v.addSeparator()
        bm_toggle = self.bookmark_panel.toggleViewAction()
        bm_toggle.setText("&Bookmarks panel")
        bm_toggle.setShortcut(QKeySequence("Ctrl+B"))
        v.addAction(bm_toggle)
        self._act(v, "&Reload", self._reload, "F5")
        self.led_act = self._act(v, "Activity &LED in the tabs", self._led_toggled, checkable=True)
        self.led_act.setChecked(bool(self.settings["activity_led"]))
        self.led_act.setToolTip("A small LED in front of each tab name flashes when new lines arrive while "
                                "Follow tail is on; it is grey when the tab does not follow")
        self.layout_menu = v.addMenu("&Layout")
        grp = QActionGroup(self)
        grp.setExclusive(True)
        self._layout_actions: dict[str, QAction] = {}
        for key, label in LAYOUTS:
            a = QAction(label, self, checkable=True)
            a.setChecked(key == self.settings["layout"])
            a.triggered.connect(lambda _=False, k=key: self.set_layout(k))
            grp.addAction(a)
            self.layout_menu.addAction(a)
            self._layout_actions[key] = a
        self.layout_custom_act = QAction("Custom (you rearranged the areas)", self, checkable=True)
        self.layout_custom_act.setEnabled(False)
        self.layout_custom_act.setChecked(self.settings["layout"] == "custom")
        grp.addAction(self.layout_custom_act)
        self.layout_menu.addAction(self.layout_custom_act)
        self.layout_menu.addSeparator()
        arrange = self.layout_menu.addAction("&Arrange in columns \u00d7 rows\u2026")
        arrange.setToolTip("Any number of columns and rows of areas; the tabs are spread over them")
        arrange.triggered.connect(self.arrange_dialog)
        merge_areas = self.layout_menu.addAction("&Merge all areas into one")
        merge_areas.triggered.connect(lambda: self.set_layout("tabs"))
        self.layout_menu.addSeparator()
        hint = self.layout_menu.addAction("Drag a tab onto another area to move it, onto an edge to split")
        hint.setEnabled(False)
        self._menu_actions["&View"].append(self.layout_menu.menuAction())
        self.sync_act = self._act(v, "&Sync time between linked files", self._sync_toggled, "Ctrl+Shift+Y", True)
        self.sync_act.setChecked(bool(self.settings["time_sync"]))
        self.sync_act.setText("Sync time")
        self.sync_act.setToolTip("When on, scrolling or jumping in a linked file moves the other linked files to "
                                 "the line with the closest timestamp")
        self.col_menu = v.addMenu("&Columnizer")
        self._build_columnizer_menu()
        self._menu_actions["&View"].append(self.col_menu.menuAction())

        s = self.menuBar().addMenu("&Search")
        self.find_act = self._act(s, "&Find in this panel…", lambda: self._find_cmd("show"), "Ctrl+F")
        self.find_act.setToolTip("Search the panel that has the focus (main view, filter result, search results, "
                                 "bookmarks); F3 / Shift+F3 jump to the next / previous match and wrap around")
        self._act(s, "Find &next", lambda: self._find_cmd("next"), "F3")
        self._act(s, "Find &previous", lambda: self._find_cmd("prev"), "Shift+F3")
        self._act(s, "Find &all", lambda: self._cur(lambda t: t.find_all()), "Ctrl+Shift+A")
        self._act(s, "&Go to line…", self._goto_line, "Ctrl+G")

        b = self.menuBar().addMenu("&Bookmarks")
        self._act(b, "&Toggle bookmark", lambda: self._cur(lambda t: t.toggle_bookmark()), "Ctrl+F2")
        self._act(b, "&Next bookmark", lambda: self._cur(lambda t: t.goto_bookmark(True)), "F2")
        self._act(b, "&Previous bookmark", lambda: self._cur(lambda t: t.goto_bookmark(False)), "Shift+F2")
        b.addSeparator()
        self._act(b, "Manage &tags…", lambda: self._manage_tags())
        self.bookmark_panel.all_tabs = self.all_tabs

        an = self.menuBar().addMenu("&Analyze")
        self.delta_act = self._act(an, "&Time difference of selected lines…", self.show_delta, "Ctrl+D")
        self.delta_act.setToolTip("Select two or more lines (Shift/Ctrl-click) to see the time between them")
        self._act(an, "Timestamp &format…", self.timestamp_dialog)
        self._act(an, "&Filter result → new tab", lambda: self._cur(lambda t: t.filter_to_tab()))
        self._act(an, "&Search results → new tab", lambda: self._cur(lambda t: t.results_to_tab()))
        self._act(an, "&Bookmarks → new tab", lambda: self._cur(lambda t: t.bookmarks_to_tab()), "Ctrl+Shift+B")
        self._act(an, "Selected &lines → new tab", lambda: self._cur(lambda t: t.selection_to_tab()), "Ctrl+Shift+T")
        an.addSeparator()
        self._menu_actions["&Analyze"].append(None)
        self.merge_act = self._act(an, "&Merge linked files → new tab", self.merge_linked, "Ctrl+Shift+M")
        self.merge_act.setToolTip("Interleave the lines of all linked files by time (millisecond resolution) in a new tab")

        tm = self.menuBar().addMenu("&Tab")
        self._act(tm, "&Rename tab…", lambda: self.rename_tab(self.current_tab()), "F6")
        self.link_act = self._act(tm, "&Linked (time sync / merge)", self._link_toggled, "Ctrl+L", True)
        self.link_act.setToolTip("Link this file with the others: they follow each other by timestamp")
        self.link_dlg_act = self._act(tm, "Link &files…", self.link_dialog, "Ctrl+Shift+L")
        self._act(tm, "Clock &offset…", self.time_offset_dialog)
        self.color_menu = QMenu("Tab &color", self)
        self.color_menu.aboutToShow.connect(lambda: self._fill_color_menu(self.color_menu, self.current_tab()))
        tm.addMenu(self.color_menu)
        self._menu_actions["&Tab"].append(self.color_menu.menuAction())
        self.detach_act = self._act(tm, "&Detach tab to window / re-attach", self.detach_or_reattach, "Ctrl+Shift+D")
        self._act(tm, "Jump to &source line", lambda: self.jump_to_source(self.current_tab()), "Ctrl+Shift+J")
        self._act(tm, "Copy full &path", lambda: self.copy_path(self.current_tab()))
        self._act(tm, "Show in &folder", lambda: self.show_in_folder(self.current_tab()))

        o = self.menuBar().addMenu("&Options")
        self._build_ui_theme_menu(o)
        self._act(o, "&Appearance (fonts, colours, grid, marker)…", self._edit_appearance)
        self._act(o, "&Highlight rules…", self._edit_highlight)
        self.group_menu = o.addMenu("Highlight &group")
        self.group_menu.aboutToShow.connect(self._rebuild_group_menu)
        self._act(o, "&Context menu (right-click)…", self.edit_context_menu)
        self._build_toolbar_menu(o)
        o.addSeparator()
        self.restore_act = self._act(o, "&Restore last session on start",
                                     lambda on: self.settings.__setitem__("restore_session", bool(on)),
                                     checkable=True)
        self.restore_act.setChecked(bool(self.settings["restore_session"]))

        h = self.menuBar().addMenu("&Help")
        self._act(h, "&About LogScope…", self.show_about)

    def _build_toolbar(self) -> None:
        tb = QToolBar("Main", self)
        tb.setObjectName("MainToolbar")
        tb.setMovable(False)
        tb.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.addToolBar(tb)
        for a in (self.open_act, self.paste_act, self.save_act):
            tb.addAction(a)
        tb.addSeparator()
        for a in (self.follow_act, self.autofit_act, self.wrap_act, self.fit_act):
            tb.addAction(a)
        tb.addSeparator()
        tb.addAction(self.find_act)
        tb.addAction(self.panel_act)
        tb.addAction(self.bookmark_panel.toggleViewAction())
        tb.addSeparator()
        tb.addAction(self.delta_act)
        tb.addSeparator()
        for a in (self.sync_act, self.link_dlg_act, self.merge_act):
            tb.addAction(a)
        tb.addSeparator()
        tb.addAction(self.detach_act)
        layout_btn = QToolButton()
        layout_btn.setText("Layout")
        layout_btn.setToolTip("Show the tabs one at a time, side by side, stacked or as a grid")
        layout_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        layout_btn.setMenu(self.layout_menu)
        tb.addWidget(layout_btn)
        self.layout_btn = layout_btn
        tb.addSeparator()
        tb.addWidget(QLabel(" Columnizer: "))
        self.col_combo = QComboBox()
        self.col_combo.setToolTip("How the lines of the current tab are split into columns "
                                  "(Automatic = detected from the first lines or from a file-name rule)")
        self.col_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.col_combo.setMinimumWidth(190)
        self.col_combo.activated.connect(self._col_combo_picked)
        tb.addWidget(self.col_combo)
        self.toolbar = tb
        self._refresh_col_combo()
        self._assign_icons()
        self.apply_toolbar_look()

    # ------------------------------------------------ toolbar icons / look
    def decorate(self, action: QAction, icon_name: str, short_text: str) -> None:
        """Give a toolbar action its generated icon and a short caption."""
        pal = self.palette()
        normal = pal.color(QPalette.ColorGroup.Active, QPalette.ColorRole.ButtonText)
        grey = pal.color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText)
        action.setIcon(icons.icon(icon_name, normal, grey))
        action.setIconText(short_text)

    def _assign_icons(self) -> None:
        table = [
            (self.open_act, "open", "Open"), (self.paste_act, "paste", "Paste"), (self.save_act, "save", "Save"),
            (self.follow_act, "follow", "Follow"), (self.autofit_act, "autofit", "Auto-fit"),
            (self.wrap_act, "wrap", "Wrap"), (self.fit_act, "fit", "Fit now"), (self.find_act, "find", "Find"),
            (self.panel_act, "panel", "Filter"), (self.bookmark_panel.toggleViewAction(), "bookmark", "Bookmarks"),
            (self.delta_act, "delta", "\u0394t"), (self.sync_act, "sync", "Sync"),
            (self.link_dlg_act, "link", "Link"), (self.merge_act, "merge", "Merge"),
            (self.detach_act, "detach", "Detach"),
        ]
        for action, name, short in table:
            self.decorate(action, name, short)
        pal = self.palette()
        self.layout_btn.setIcon(icons.icon("layout", pal.color(QPalette.ColorGroup.Active,
                                                               QPalette.ColorRole.ButtonText)))
        self.layout_btn.setText("Layout")

    def _toolbar_style_key(self) -> str:
        v = self.settings["toolbar_style"]
        return v if v in {k for k, _l, _s in TOOLBAR_STYLES} else "icon_text_under"

    def _toolbar_icon_px(self) -> int:
        v = self.settings["toolbar_icon_size"]
        return v if v in {px for _l, px in ICON_SIZES} else 24

    def style_toolbar(self, tb: QToolBar) -> None:
        """Apply the chosen look (icons / text, icon size) to a toolbar of this program."""
        style = {k: s for k, _l, s in TOOLBAR_STYLES}[self._toolbar_style_key()]
        px = self._toolbar_icon_px()
        tb.setToolButtonStyle(style)
        tb.setIconSize(QSize(px, px))
        for w in tb.findChildren(QToolButton):
            if w is getattr(self, "layout_btn", None):
                w.setToolButtonStyle(style)
                w.setIconSize(QSize(px, px))

    def apply_toolbar_look(self) -> None:
        bars = [self.toolbar] + [d.toolbar for d in self.detached if getattr(d, "toolbar", None) is not None]
        for tb in bars:
            self.style_toolbar(tb)

    def _build_toolbar_menu(self, options_menu) -> None:
        m = options_menu.addMenu("&Toolbar")
        grp = QActionGroup(self)
        grp.setExclusive(True)
        for key, label, _style in TOOLBAR_STYLES:
            a = QAction(label, self, checkable=True)
            a.setChecked(key == self._toolbar_style_key())
            a.triggered.connect(lambda _=False, k=key: self._set_toolbar_style(k))
            grp.addAction(a)
            m.addAction(a)
        m.addSeparator()
        sizes = QActionGroup(self)
        sizes.setExclusive(True)
        for label, px in ICON_SIZES:
            a = QAction(label, self, checkable=True)
            a.setChecked(px == self._toolbar_icon_px())
            a.triggered.connect(lambda _=False, p=px: self._set_toolbar_icon_size(p))
            sizes.addAction(a)
            m.addAction(a)

    def _set_toolbar_style(self, key: str) -> None:
        self.settings["toolbar_style"] = key
        self.apply_toolbar_look()

    def _set_toolbar_icon_size(self, px: int) -> None:
        self.settings["toolbar_icon_size"] = int(px)
        self.apply_toolbar_look()

    # ------------------------------------------------------------ window theme
    def _build_ui_theme_menu(self, options_menu) -> None:
        m = options_menu.addMenu("Window &theme")
        grp = QActionGroup(self)
        grp.setExclusive(True)
        self._ui_theme_actions: dict[str, QAction] = {}
        current = sanitize_mode(self.settings["ui_theme"])
        for mode in MODES:
            a = QAction(MODE_LABELS[mode], self, checkable=True)
            a.setChecked(mode == current)
            a.triggered.connect(lambda _checked=False, md=mode: self.set_ui_theme(md))
            grp.addAction(a)
            m.addAction(a)
            self._ui_theme_actions[mode] = a

    def set_ui_theme(self, mode: str) -> None:
        """Options > Window theme: follow Windows, light or dark."""
        mode = sanitize_mode(mode)
        self.settings["ui_theme"] = mode
        a = self._ui_theme_actions.get(mode)
        if a is not None:
            a.setChecked(True)
        self._reapply_ui_theme()

    def _on_system_scheme(self, _scheme=None) -> None:
        if not self._closing and sanitize_mode(self.settings["ui_theme"]) == "system":
            self._reapply_ui_theme()

    def _reapply_ui_theme(self) -> None:
        if self._applying_ui_theme:
            return                                      # unsetting the scheme can announce a change itself
        self._applying_ui_theme = True
        try:
            self._ui_scheme = uitheme.apply(QApplication.instance(), sanitize_mode(self.settings["ui_theme"]))
        finally:
            self._applying_ui_theme = False
        self._apply_theme()                             # log panels copy the window palette: restyle them
        self._assign_icons()
        for win in self.detached:
            win.refresh_icons()

    def changeEvent(self, e) -> None:
        super().changeEvent(e)
        if e.type() == QEvent.Type.PaletteChange and hasattr(self, "layout_btn"):
            self._assign_icons()                          # light / dark theme: redraw the icons in the text colour

    def all_tabs(self) -> list[LogTab]:
        """Tabs of the main window followed by the tabs in detached windows."""
        tabs = [self.tabs.widget(i) for i in range(self.tabs.count())]
        tabs += [w.tab for w in self.detached]
        return [t for t in tabs if isinstance(t, LogTab)]

    def _each_tab(self, fn) -> None:
        for t in self.all_tabs():
            fn(t)

    def _autofit_toggled(self, on: bool) -> None:
        self.settings["autofit_columns"] = bool(on)
        self._apply_view_options()

    def _wrap_toggled(self, on: bool) -> None:
        self.settings["wrap_text"] = bool(on)
        self._apply_view_options()

    def _apply_view_options(self) -> None:
        autofit, wrap = bool(self.settings["autofit_columns"]), bool(self.settings["wrap_text"])
        self._each_tab(lambda t: t.set_view_options(autofit, wrap))

    def paste_as_file(self) -> None:
        """Clipboard text -> Save dialog -> file on disk -> opened as a new tab."""
        text = QGuiApplication.clipboard().text()
        if not text.strip():
            self.statusBar().showMessage("The clipboard holds no text", 5000)
            return
        folder = self.settings["paste_dir"]
        if not folder or not os.path.isdir(folder):
            folder = os.path.expanduser("~")
        path, _ = QFileDialog.getSaveFileName(
            self, "Save clipboard as new file", os.path.join(folder, suggest_name()),
            "Log files (*.log *.txt);;All files (*)")
        if not path:
            return
        try:
            lines = save_text_file(path, text)
        except OSError as exc:
            QMessageBox.warning(self, "LogScope", f"Cannot save {path}:\n{exc}")
            return
        self.settings["paste_dir"] = os.path.dirname(os.path.abspath(path))
        self.open_file(path)
        self.statusBar().showMessage(f"Saved {lines:,} line(s) to {path}", 8000)

    def _build_columnizer_menu(self) -> None:
        self.col_menu.clear()
        group = QActionGroup(self)
        group.setExclusive(True)
        self._col_actions: dict[str | None, QAction] = {}
        auto = QAction("Automatic", self, checkable=True)
        auto.setChecked(True)
        auto.triggered.connect(lambda: self._cur(lambda t: t.set_columnizer(None)))
        group.addAction(auto)
        self.col_menu.addAction(auto)
        self._col_actions[None] = auto
        self.col_menu.addSeparator()
        for name in self.registry.names():
            a = QAction(name, self, checkable=True)
            a.triggered.connect(lambda _=False, n=name: self._cur(lambda t: t.set_columnizer(n)))
            group.addAction(a)
            self.col_menu.addAction(a)
            self._col_actions[name] = a
        self.col_menu.addSeparator()
        self.col_menu.addAction("&Parsing rules (regex columnizers)…").triggered.connect(self.edit_regex_columnizers)
        self._refresh_col_combo()

    # ---------------------------------------------- columnizer drop-down (toolbar)
    def _refresh_col_combo(self, *_args) -> None:
        """Fill the toolbar drop-down and tick the menu entry for the current tab's columnizer."""
        if self._closing:
            return
        combo = getattr(self, "col_combo", None)
        t = self.current_tab()
        chosen, in_use = t.columnizer_choice() if t is not None and t.reader.count > 0 else (None, "")
        acts = getattr(self, "_col_actions", {})
        a = acts.get(chosen if chosen in acts else None)
        if a is not None:
            a.setChecked(True)
        if combo is None:
            return
        key = (id(t), chosen, in_use, tuple(self.registry.names()))
        if key == getattr(self, "_col_combo_key", None):
            return                                   # unchanged: do not rebuild (an open list would close)
        self._col_combo_key = key
        combo.blockSignals(True)
        combo.clear()
        combo.addItem(f"Automatic ({in_use})" if in_use else "Automatic", None)
        for name in self.registry.names():
            combo.addItem(name, name)
        idx = combo.findData(chosen) if chosen is not None else 0
        combo.setCurrentIndex(max(0, idx))
        combo.setEnabled(t is not None)
        combo.blockSignals(False)

    def _col_combo_picked(self, index: int) -> None:
        t = self.current_tab()
        if t is not None and index >= 0:
            t.set_columnizer(self.col_combo.itemData(index))

    def _rebuild_group_menu(self) -> None:
        self.group_menu.clear()
        grp = QActionGroup(self.group_menu)
        grp.setExclusive(True)
        active = self.settings.active_group
        for name in self.settings.highlight_groups():
            a = QAction(name, self.group_menu, checkable=True)
            a.setChecked(name == active)
            a.triggered.connect(lambda _=False, n=name: self._select_group(n))
            grp.addAction(a)
            self.group_menu.addAction(a)

    def _refresh_recent(self) -> None:
        self.recent_menu.clear()
        for p in self.settings["recent_files"]:
            a = QAction(p, self)
            a.triggered.connect(lambda _=False, path=p: self.open_file(path))
            self.recent_menu.addAction(a)

    # ------------------------------------------------------------------ tabs
    def _active_detached(self) -> DetachedWindow | None:
        w = QApplication.activeWindow()
        while w is not None:
            if isinstance(w, DetachedWindow) and w in self.detached:
                return w
            w = w.parentWidget()
        return None

    def _dialog_parent(self):
        return self._active_detached() or self

    def current_tab(self) -> LogTab | None:
        """The tab the user is working in: of the active detached window, else the main window's."""
        d = self._active_detached()
        if d is not None:
            return d.tab
        w = self.tabs.currentWidget()
        return w if isinstance(w, LogTab) else None

    def _cur(self, fn) -> None:
        t = self.current_tab()
        if t is not None:
            fn(t)

    def open_dialog(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(self, "Open log file(s) - each opens in its own tab", "",
                                                "All files (*)")
        for f in files:
            self.open_file(f)

    def open_file(self, path: str, state: dict | None = None, remember: bool = True) -> LogTab | None:
        path = os.path.abspath(path)
        try:
            tab = LogTab(path, self.settings, self.registry, self.highlight)
        except OSError as exc:
            QMessageBox.warning(self, "LogScope", f"Cannot open {path}:\n{exc}")
            return None
        self._wire_tab(tab)
        if state is not None:
            tab.apply_state(state)
        self._attach_tab(tab, path)
        if remember:
            self.settings.add_recent(path)
            self._refresh_recent()
        return tab

    def _wire_tab(self, tab: LogTab) -> None:
        """Connect a new tab's signals (once; they stay connected when it changes windows)."""
        tab.follow_changed.connect(self._tab_follow_changed)
        tab.bookmarks_changed.connect(self._schedule_session_save)
        tab.status_message.connect(lambda m: self.statusBar().showMessage(m, 5000))
        tab.title_changed.connect(lambda title, t=tab: self._on_tab_title(t, title))
        tab.look_changed.connect(self.tabs.refresh_chrome)
        tab.look_changed.connect(self._on_look_changed)
        tab.derive_ready.connect(self._on_derive_ready)
        tab.time_sync_requested.connect(self._on_time_sync)
        tab.link_changed.connect(lambda t=tab: self._on_link_changed(t))
        tab.columnizer_changed.connect(self._refresh_col_combo)
        tab.request.connect(lambda name, payload, t=tab: self._on_tab_request(t, name, payload))

    def _attach_tab(self, tab: LogTab, tooltip: str | None = None) -> None:
        idx = self.tabs.addTab(tab, self._tab_label(tab))
        self.tabs.setTabToolTip(idx, tooltip or tab.path)
        self.tabs.setCurrentIndex(idx)
        self._schedule_session_save()

    def _tab_label(self, tab: LogTab) -> str:
        return ("\U0001F517 " if tab.linked else "") + tab.title

    def _on_tab_title(self, tab: LogTab, title: str) -> None:
        i = self.tabs.indexOf(tab)
        label = self._tab_label(tab)
        if i >= 0 and self.tabs.tabText(i) != label:
            self.tabs.setTabText(i, label)

    def _on_derive_ready(self, source, dest: str, origin, title: str, marks) -> None:
        """A filter result / bookmark list / selection was written to a temporary file: open it."""
        try:
            tab = LogTab(dest, self.settings, self.registry, self.highlight, title=title,
                         origin=origin, source=source, encoding="utf-8")
        except OSError as exc:
            QMessageBox.warning(self, "LogScope", f"Cannot open the new tab:\n{exc}")
            derive.remove_file(dest)
            return
        self._wire_tab(tab)
        if marks:
            tab.bookmarks.update(marks)
            tab._bookmarks_modified()
        self._attach_tab(tab, f"{title}\nTemporary file - use File > Save tab as… to keep it")
        self.statusBar().showMessage(
            f"{origin.size:,} line(s) in a new tab. Filter or search it like any file; "
            f"Ctrl+S saves it; Ctrl+Shift+J jumps to the source line.", 10000)

    def close_current(self) -> None:
        d = self._active_detached()
        if d is not None:
            self.close_detached(d)
        else:
            self.close_tab(self.tabs.currentIndex())

    def close_tab(self, index: int) -> None:
        w = self.tabs.widget(index)
        if isinstance(w, LogTab):
            if self.bookmark_panel._tab is w:
                self.bookmark_panel.set_tab(None)
            self.tabs.removeTab(index)
            w.shutdown()
            w.deleteLater()
            self._schedule_session_save()

    def _on_tab_changed(self, _i: int) -> None:
        w = self.tabs.currentWidget()
        t = w if isinstance(w, LogTab) else None
        self.bookmark_panel.set_tab(t)
        if t is not None and self._active_detached() is None:
            self.follow_act.blockSignals(True)
            self.follow_act.setChecked(t.follow)
            self.follow_act.blockSignals(False)
        self._refresh_tab_actions()
        self._refresh_col_combo()
        self._update_status()

    def _sync_follow_action(self, *_args) -> None:
        if self._closing:
            return
        t = self.current_tab()
        if t is not None and self.follow_act.isChecked() != t.follow:
            self.follow_act.blockSignals(True)
            self.follow_act.setChecked(t.follow)
            self.follow_act.blockSignals(False)
        self._refresh_tab_actions()
        self._refresh_col_combo()

    # ------------------------------------------------------- layout / detach
    def set_layout(self, mode: str) -> None:
        self.tabs.setLayoutMode(mode)
        self.settings["layout"] = self.tabs.layoutMode()

    def _led_toggled(self, on: bool) -> None:
        self.settings["activity_led"] = bool(on)
        self.tabs.set_leds_visible(bool(on))

    def arrange_dialog(self) -> None:
        n = self.tabs.count()
        cols, rows = layout_core.preset_shape("grid", n) if n > 1 else (2, 1)
        dlg = ArrangeDialog(int(self.settings["arrange_cols"] or cols), int(self.settings["arrange_rows"] or rows),
                            n, self._dialog_parent())
        if dlg.exec():
            cols, rows = dlg.shape()
            self.settings["arrange_cols"], self.settings["arrange_rows"] = cols, rows
            self.tabs.arrange(cols, rows)

    def _on_layout_changed(self, mode: str) -> None:
        """The areas changed (preset chosen, tab dragged, area added or removed ...)."""
        self.settings["layout"] = mode
        for key, a in self._layout_actions.items():
            a.setChecked(key == mode)
        self.layout_custom_act.setChecked(mode == "custom")
        self._schedule_session_save()

    def detach_tab(self, index: int) -> None:
        tab = self.tabs.widget(index)
        if not isinstance(tab, LogTab):
            return
        if self.bookmark_panel._tab is tab:
            self.bookmark_panel.set_tab(None)
        self.tabs.removeTab(index)
        win = DetachedWindow(self, tab)
        self.detached.append(win)
        g = self.geometry()
        win.move(g.x() + 50, g.y() + 50)
        win.show()
        win.raise_()
        win.activateWindow()
        self._schedule_session_save()

    def reattach(self, win: DetachedWindow) -> None:
        if win not in self.detached:
            return
        self.detached.remove(win)
        tab = win.take_tab()
        win.force_close()
        win.deleteLater()
        self._attach_tab(tab, tab.path)

    def close_detached(self, win: DetachedWindow) -> None:
        if win not in self.detached:
            return
        self.detached.remove(win)
        tab = win.take_tab()
        win.force_close()
        win.deleteLater()
        tab.shutdown()
        tab.deleteLater()
        self._schedule_session_save()

    def detach_or_reattach(self) -> None:
        d = self._active_detached()
        if d is not None:
            self.reattach(d)
        else:
            self.detach_tab(self.tabs.currentIndex())

    # ------------------------------------------------------------- tab menu
    def _swatch(self, color: str) -> QIcon:
        pix = QPixmap(14, 14)
        pix.fill(QColor(color))
        return QIcon(pix)

    def _fill_color_menu(self, menu: QMenu, tab: LogTab | None) -> None:
        menu.clear()
        if tab is None:
            return
        for name, c in TAB_COLORS:
            a = menu.addAction(self._swatch(c), name)
            a.setCheckable(True)
            a.setChecked(tab.tab_color == c)
            a.triggered.connect(lambda _=False, col=c: tab.set_tab_color(col))
        menu.addSeparator()
        menu.addAction("Custom…").triggered.connect(lambda: self._custom_color(tab))
        menu.addAction("No colour").triggered.connect(lambda: tab.set_tab_color(""))

    def _custom_color(self, tab: LogTab) -> None:
        c = QColorDialog.getColor(QColor(tab.tab_color or "#3498db"), self._dialog_parent(), "Tab colour")
        if c.isValid():
            tab.set_tab_color(c.name())

    def _tab_context_menu(self, index: int, pos) -> None:
        tab = self.tabs.widget(index)
        if not isinstance(tab, LogTab):
            return
        m = QMenu(self)
        m.addAction("Rename…").triggered.connect(lambda: self.rename_tab(tab))
        self._fill_color_menu(m.addMenu("Tab colour"), tab)
        link = m.addAction("Linked (time sync / merge)")
        link.setCheckable(True)
        link.setChecked(tab.linked)
        link.triggered.connect(lambda on: self._set_tab_linked(tab, on))
        m.addAction("Clock offset…").triggered.connect(lambda: self.time_offset_dialog(tab))
        self._fill_area_menu(m.addMenu("Area"), tab)
        m.addAction("Detach to window").triggered.connect(lambda: self.detach_tab(self.tabs.indexOf(tab)))
        m.addAction("Save as…").triggered.connect(lambda: self.save_as(tab))
        if tab.derived:
            m.addAction("Jump to source line").triggered.connect(lambda: self.jump_to_source(tab))
        m.addSeparator()
        m.addAction("Copy full path").triggered.connect(lambda: self.copy_path(tab))
        m.addAction("Show in folder").triggered.connect(lambda: self.show_in_folder(tab))
        m.addSeparator()
        m.addAction("Close").triggered.connect(lambda: self.close_tab(self.tabs.indexOf(tab)))
        m.addAction("Close others").triggered.connect(lambda: self._close_others(tab))
        m.exec(pos)

    def _fill_area_menu(self, menu: QMenu, tab: LogTab) -> None:
        """Tab menu > Area: the keyboard-free alternative to dragging the tab."""
        new = menu.addMenu("Move to a new area")
        new.setEnabled(self.tabs.area_tab_count(tab) > 1)
        for label, zone in (("on the right", "right"), ("below", "bottom"), ("on the left", "left"), ("above", "top")):
            new.addAction(label).triggered.connect(lambda _=False, z=zone: self.tabs.move_tab_to_new_area(tab, z))
        others = self.tabs.areas(tab)
        to = menu.addMenu("Move to another area")
        to.setEnabled(bool(others))
        for label, grp in others:
            to.addAction(label).triggered.connect(lambda _=False, g=grp: self.tabs.move_tab_to_area(tab, g))
        menu.addSeparator()
        menu.addAction("Arrange in columns \u00d7 rows\u2026").triggered.connect(self.arrange_dialog)

    def _close_others(self, keep: LogTab) -> None:
        for i in reversed(range(self.tabs.count())):
            if self.tabs.widget(i) is not keep:
                self.close_tab(i)

    def rename_tab(self, tab: LogTab | None) -> None:
        if tab is None:
            return
        text, ok = QInputDialog.getText(self._dialog_parent(), "Rename tab", "Tab title:", text=tab.title)
        if ok:
            tab.set_title(text)
            self._schedule_session_save()

    def copy_path(self, tab: LogTab | None) -> None:
        if tab is not None:
            QGuiApplication.clipboard().setText(tab.saved_path or tab.path)
            self.statusBar().showMessage("Path copied to the clipboard", 4000)

    def show_in_folder(self, tab: LogTab | None) -> None:
        if tab is None:
            return
        path = tab.saved_path or tab.path
        if sys.platform == "win32" and os.path.exists(path):
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
        else:
            QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(path)))

    # ------------------------------------------------------- save / source
    def save_as(self, tab: LogTab | None = None) -> None:
        tab = tab if isinstance(tab, LogTab) else self.current_tab()
        if tab is None:
            return
        folder = self.settings["save_dir"]
        if not folder or not os.path.isdir(folder):
            folder = os.path.expanduser("~")
        name = re.sub(r'[\\/:*?"<>|]+', "_", tab.title).strip() if tab.derived else os.path.basename(tab.path)
        if tab.derived and not os.path.splitext(name)[1]:
            name += ".log"
        path, _ = QFileDialog.getSaveFileName(self._dialog_parent(), "Save tab as", os.path.join(folder, name),
                                              "Log files (*.log *.txt);;All files (*)")
        if not path:
            return
        if os.path.abspath(path) == os.path.abspath(tab.path):
            QMessageBox.warning(self, "LogScope", "That is the file shown in this tab.")
            return
        try:
            tab.save_as(path)
        except OSError as exc:
            QMessageBox.warning(self, "LogScope", f"Cannot save {path}:\n{exc}")
            return
        self.settings["save_dir"] = os.path.dirname(os.path.abspath(path))
        self.statusBar().showMessage(f"Saved {tab.reader.count:,} line(s) to {path}", 8000)

    def jump_to_source(self, tab: LogTab | None, line: int | None = None) -> None:
        """From a line of a derived / merged tab to the same line in the file it came from."""
        if tab is None or not tab.derived:
            self.statusBar().showMessage("Only tabs made with 'to new tab' or 'merge' have a source line", 5000)
            return
        info = tab.source_info(tab.current_line() if line is None else line)
        if info is None:
            self.statusBar().showMessage("Select a line first", 5000)
            return
        src, path, line = info
        if src is not None and src in self.all_tabs():
            i = self.tabs.indexOf(src)
            if i >= 0:
                self.tabs.setCurrentIndex(i)
            else:
                for w in self.detached:
                    if w.tab is src:
                        w.raise_()
                        w.activateWindow()
            src.goto_line(line)
        elif path and os.path.isfile(path) and not derive.is_derived_path(path):
            self.open_file(path, state={"follow": False, "line": line + 1}, remember=False)
        else:
            self.statusBar().showMessage("The source tab was closed", 5000)

    # ------------------------------------------------------ timestamps / Δt
    def show_delta(self) -> None:
        t = self.current_tab()
        if t is None:
            return
        if not t.get_ts_parser().usable:
            self.statusBar().showMessage("No timestamps recognised - see Analyze > Timestamp format…", 7000)
            return
        rep = t.delta_report()
        if rep is None:
            self.statusBar().showMessage("Select two or more lines first (Shift / Ctrl + click)", 6000)
            return
        DeltaDialog(rep, self._dialog_parent()).exec()

    def timestamp_dialog(self) -> None:
        t = self.current_tab()
        if t is None:
            return
        n = min(200, t.reader.count)
        dlg = TimestampDialog(t.ts_spec, [t.reader.get_line(i) for i in range(n)], self._dialog_parent())
        if dlg.exec():
            t.set_timestamp_spec(dlg.spec())
            if dlg.make_default():
                self.settings["timestamp_format"] = dlg.spec()
            self._schedule_session_save()

    # ------------------------------------------------------------ find in panel
    def _find_cmd(self, cmd: str) -> None:
        """Ctrl+F / F3 / Shift+F3: act on the panel that has the keyboard focus."""
        f = focused_finder(QApplication.focusWidget())
        if f is None:
            t = self.current_tab()
            f = t.main_finder if t is not None else None
        if f is None:
            return
        if cmd == "show":
            f.show_bar(selected_text(QApplication.focusWidget()))      # Ctrl+F: selection -> search box
        else:
            f.step(cmd == "next")

    # ------------------------------------------ right-click menu of the log panels
    HIGHLIGHT_COLOURS = ["#fff2a8", "#c8f7c5", "#bde0fe", "#ffd6e0", "#e0c3fc", "#ffd8a8"]

    def edit_context_menu(self) -> None:
        dlg = ContextMenuDialog(self.settings.context_menu(), self._dialog_parent())
        if dlg.exec():
            self.settings.set_context_menu(dlg.menu())
            self.statusBar().showMessage("Context menu saved - it applies the next time you right-click", 6000)

    def _on_tab_request(self, tab: LogTab, name: str, payload: dict) -> None:
        """Context-menu items that need the main window."""
        if name == "customize_menu":
            self.edit_context_menu()
        elif name == "time_diff":
            self._show_delta_for(tab, payload.get("lines") or [])
        elif name == "jump_source":
            self.jump_to_source(tab, payload.get("line"))
        elif name == "highlight_text":
            self._highlight_text(str(payload.get("text") or ""))

    def _show_delta_for(self, tab: LogTab, lines) -> None:
        if len(lines) < 2:
            self.statusBar().showMessage("Select two or more lines first (Shift / Ctrl + click)", 6000)
            return
        if not tab.get_ts_parser().usable:
            self.statusBar().showMessage("No timestamps recognised - see Analyze > Timestamp format…", 7000)
            return
        DeltaDialog(tab.delta_for(np.asarray(sorted(set(lines)), dtype=np.int64)), self._dialog_parent()).exec()

    def _highlight_text(self, text: str) -> None:
        """Add a rule that colours this text (only the matching words) to the active highlight group."""
        text = text.strip()
        if not text:
            return
        groups, active = self.settings.highlight_groups(), self.settings.active_group
        rules = groups[active]
        if any(r.pattern == text and not r.regex for r in rules):
            self.statusBar().showMessage("That text is highlighted already (Options > Highlight rules…)", 5000)
            return
        rules.append(HighlightRule(pattern=text, regex=False, case_sensitive=True, fg="#000000",
                                   bg=self.HIGHLIGHT_COLOURS[len(rules) % len(self.HIGHLIGHT_COLOURS)],
                                   whole_line=False))
        self.settings.set_highlight_groups(groups, active)
        self._apply_active_group()
        self.statusBar().showMessage(f"Highlighting \u201c{text[:40]}\u201d - change the colours under "
                                     "Options > Highlight rules…", 7000)

    def _manage_tags(self) -> None:
        d = self._active_detached()
        (d.bookmark_panel if d is not None else self.bookmark_panel).manage_tags()

    def show_about(self) -> None:
        AboutDialog(str(self.settings.dir), self._dialog_parent()).exec()

    # ---------------------------------------------------- regex columnizers
    def edit_regex_columnizers(self) -> None:
        t = self.current_tab()
        sample = [t.reader.get_line(i) for i in range(min(60, t.reader.count))] if t is not None else []
        dlg = RegexColumnizerDialog(self.settings["regex_columnizers"], sample, self._dialog_parent(),
                                    current_path=t.path if t is not None and not t.derived else "")
        if not dlg.exec():
            return
        specs = dlg.specs()
        self.settings["regex_columnizers"] = specs
        errors = self.registry.set_user_regex(specs)
        self._build_columnizer_menu()
        self._each_tab(lambda tab: tab.refresh_columnizer())
        msg = f"{len(specs)} parsing rule(s) saved; rules with file patterns apply automatically"
        self.statusBar().showMessage(msg + (f"; skipped: {'; '.join(errors)}" if errors else ""), 9000)

    # --------------------------------------------- linked files / time sync
    def _sync_toggled(self, on: bool) -> None:
        self.settings["time_sync"] = bool(on)
        if on:
            t = self.current_tab()
            if t is not None and t.linked:               # align the others to what is shown now
                line = t.center_line()
                if line is not None:
                    self._on_time_sync(t, line)

    def _refresh_tab_actions(self) -> None:
        t = self.current_tab()
        self.link_act.blockSignals(True)
        self.link_act.setChecked(bool(t is not None and t.linked))
        self.link_act.setEnabled(t is not None)
        self.link_act.blockSignals(False)

    def _link_toggled(self, on: bool) -> None:
        self._set_tab_linked(self.current_tab(), on)

    def _set_tab_linked(self, tab: LogTab | None, on: bool) -> None:
        if tab is None:
            return
        if not tab.set_linked(on):
            self.statusBar().showMessage(
                f"No timestamps recognised in {tab.title} - set the format under Analyze > Timestamp format…", 8000)
        self._refresh_tab_actions()

    def link_dialog(self) -> None:
        tabs = self.all_tabs()
        if not tabs:
            return
        dlg = LinkDialog(tabs, self._dialog_parent())
        if dlg.exec():
            for tab, on, off in dlg.result_rows():
                tab.set_time_offset(off)
                tab.set_linked(on)
            n = sum(1 for t in tabs if t.linked)
            self.statusBar().showMessage(
                f"{n} file(s) linked" + ("" if n >= 2 else " - link at least two to sync or merge them"), 6000)

    def time_offset_dialog(self, tab: LogTab | None = None) -> None:
        tab = tab if isinstance(tab, LogTab) else self.current_tab()
        if tab is None:
            return
        text, ok = QInputDialog.getText(
            self._dialog_parent(), "Clock offset",
            f"Added to the timestamps of {tab.title} when syncing / merging.\n"
            "Examples: +01:00   -00:00:02.5   -250ms   3600", text=format_offset(tab.ts_offset))
        if not ok:
            return
        try:
            tab.set_time_offset(parse_offset(text))
        except ValueError as exc:
            QMessageBox.warning(self._dialog_parent(), "Clock offset", str(exc))

    def _on_link_changed(self, tab: LogTab) -> None:
        i = self.tabs.indexOf(tab)
        if i >= 0:
            self.tabs.setTabText(i, self._tab_label(tab))
        for w in self.detached:
            if w.tab is tab:
                w.refresh_look()
        self._refresh_tab_actions()
        self._schedule_session_save()

    def _on_look_changed(self) -> None:
        for t in self.all_tabs():                           # merged tabs wear the colours of their sources
            if t.merge is not None:
                t.refresh_merge_style()

    def _on_time_sync(self, tab: LogTab, line: int) -> None:
        """``tab`` scrolled / jumped to ``line``: move every other linked tab to the closest time."""
        if not self.sync_act.isChecked() or not tab.linked or self._closing:
            return
        universal = tab.time_at(line)
        if universal is None:
            return
        time_only = is_time_only(universal - tab.ts_offset)
        moved = 0
        for other in self.all_tabs():
            if other is not tab and other.linked and other.show_time(universal, time_only) is not None:
                moved += 1
        if moved:
            self.statusBar().showMessage(f"Time sync: {moved} linked file(s) moved to {format_stamp(universal)}", 4000)

    # ------------------------------------------------------------ merge view
    def merge_linked(self) -> None:
        """All linked files -> one new tab, lines interleaved by time."""
        tabs = [t for t in self.all_tabs() if t.linked and t.can_link()]
        if len(tabs) < 2:
            self.statusBar().showMessage("Link at least two files with timestamps first (Tab > Link files…)", 8000)
            self.link_dialog()
            tabs = [t for t in self.all_tabs() if t.linked and t.can_link()]
            if len(tabs) < 2:
                return
        if self._merge_worker is not None and self._merge_worker.isRunning():
            self.statusBar().showMessage("Still merging…", 4000)
            return
        taken = {t.tab_color for t in tabs if t.tab_color}
        spare = [c for c in CODES if c not in taken]
        for t in tabs:                                       # every file needs a colour to be told apart
            if not t.tab_color:
                t.set_tab_color(spare.pop(0) if spare else CODES[len(taken) % len(CODES)])
        jobs = [(t.reader, t.get_ts_parser(), t.ts_offset, t.reader.count) for t in tabs]
        sources = [MergeSource(t.path, t.title, t.tab_color, t) for t in tabs]
        readers = [j[0] for j in jobs]
        title = "Merged: " + ", ".join(t.title for t in tabs)
        title = title if len(title) <= 80 else title[:77] + "…"
        dest = derive.new_path("merged")
        total = sum(j[3] for j in jobs)

        def job(cancel, emit):
            ms_list, base = [], 0
            for reader, parser, offset, count in jobs:
                times = scan_times(reader, parser, upto=count, cancel=cancel,
                                   progress=lambda c, b=base: emit(f"Merging: reading timestamps {b + c:,} / {total:,}"))
                ms_list.append(effective_ms(times, offset))
                base += count
            emit("Merging: ordering the lines…")
            fi, li = merge_order(ms_list)
            del ms_list
            derive.write_merged(readers, fi, li, dest, cancel,
                                progress=lambda c: emit(f"Merging: writing {c:,} / {fi.size:,} lines"))
            return fi, li

        w = FuncWorker(job, self)
        w.batch.connect(lambda msg: self.statusBar().showMessage(msg, 3000))
        w.done.connect(lambda res, w=w: self._on_merge_done(w, dest, sources, title, res))
        self._merge_worker = w
        self.statusBar().showMessage(f"Merging {len(tabs)} files ({total:,} lines)…", 5000)
        w.start()

    def _on_merge_done(self, w, dest: str, sources: list, title: str, result) -> None:
        if w is not self._merge_worker:
            return
        self._merge_worker = None
        if self._closing:
            derive.remove_file(dest)
            return
        if isinstance(result, BaseException):
            derive.remove_file(dest)
            if not isinstance(result, InterruptedError):
                msg = str(result)
                if isinstance(result, ValueError):
                    msg += " - check Analyze > Timestamp format… of each linked file"
                QMessageBox.warning(self._dialog_parent(), "Merge", f"Cannot merge the linked files:\n{msg}")
            return
        fi, li = result
        info = MergeInfo(sources, fi)
        try:
            tab = LogTab(dest, self.settings, self.registry, self.highlight, title=title,
                         origin=li, merge=info, encoding="utf-8")
        except OSError as exc:
            derive.remove_file(dest)
            QMessageBox.warning(self._dialog_parent(), "Merge", f"Cannot open the merged tab:\n{exc}")
            return
        self._wire_tab(tab)
        self._attach_tab(tab, f"{title}\nTemporary file - use File > Save tab as… to keep it")
        counts = ", ".join(f"{s.title}: {n:,}" for s, n in zip(sources, info.counts()))
        self.statusBar().showMessage(
            f"Merged {fi.size:,} line(s) ({counts}). Lines are coloured like their file's tab; "
            f"Ctrl+Shift+J jumps to the original line.", 12000)

    # --------------------------------------------------------------- actions
    def _follow_toggled(self, on: bool) -> None:
        self.settings["follow_tail"] = bool(on)
        self._cur(lambda t: t.set_follow(on))

    def _tab_follow_changed(self, on: bool) -> None:
        if self.sender() is self.current_tab():
            self.follow_act.blockSignals(True)
            self.follow_act.setChecked(on)
            self.follow_act.blockSignals(False)
        self._schedule_session_save()

    def _goto_line(self) -> None:
        t = self.current_tab()
        if t is None:
            return
        n, ok = QInputDialog.getInt(self, "Go to line", "Line number:", max(t.current_line() + 1, 1),
                                    1, max(t.reader.count, 1))
        if ok:
            t.goto_line(n - 1)

    def _reload(self) -> None:
        t = self.current_tab()
        if t is None or t.derived:
            return
        idx = self.tabs.indexOf(t)
        if idx < 0:
            self.statusBar().showMessage("Re-attach the tab to the main window first", 5000)
            return
        path, state = t.path, t.capture_state()
        self.close_tab(idx)
        self.open_file(path, state=state)

    # ------------------------------------------------------------- highlight
    def _apply_active_group(self) -> None:
        self.highlight.set_rules(self.settings.highlight_rules())
        self._each_tab(lambda w: w.refresh_highlight())

    def _select_group(self, name: str) -> None:
        self.settings.set_active_group(name)
        self._apply_active_group()

    def _edit_highlight(self) -> None:
        dlg = HighlightDialog(self.settings.highlight_groups(), self.settings.active_group, self)
        if dlg.exec():
            self.settings.set_highlight_groups(dlg.groups(), dlg.active())
            self._apply_active_group()

    # ------------------------------------------------------------ appearance
    def _edit_appearance(self) -> None:
        dlg = AppearanceDialog(self.settings.theme(), self)
        if dlg.exec():
            self.settings.set_theme(dlg.theme())
            self._apply_theme()

    def _nudge_row_spacing(self, delta: int | None) -> None:
        """View > Row spacing: +1 / -1 pixel in every panel, or back to the defaults."""
        theme = self.settings.theme()
        theme = reset_row_spacing(theme) if delta is None else nudge_row_spacing(theme, delta)
        self.settings.set_theme(theme)
        self._apply_theme()
        a = theme["areas"]
        self.statusBar().showMessage(
            "Row spacing: main %d px, filter %d px, search %d px, bookmarks %d px (Options > Appearance sets each panel)"
            % (a["main"]["row_spacing"], a["filter"]["row_spacing"], a["search"]["row_spacing"],
               a["bookmarks"]["row_spacing"]), 6000)

    def _apply_theme(self) -> None:
        theme = self.settings.theme()
        self._each_tab(lambda t: t.apply_theme(theme))
        self.bookmark_panel.apply_theme(theme)
        for win in self.detached:
            win.bookmark_panel.apply_theme(theme)

    # --------------------------------------------------------------- session
    def _capture_session(self) -> dict:
        tabs, current, numbers = [], 0, {}
        for i in range(self.tabs.count()):
            w = self.tabs.widget(i)
            if isinstance(w, LogTab) and not w.derived:
                if i == self.tabs.currentIndex():
                    current = len(tabs)
                numbers[w] = len(tabs)
                tabs.append(w.capture_state())
        tabs += [w.tab.capture_state() for w in self.detached if not w.tab.derived]   # come back as normal tabs
        return sanitize_session({"tabs": tabs, "current": current,
                                 "layout": self.tabs.capture_layout(lambda t: numbers.get(t))})

    def _schedule_session_save(self, *_args) -> None:
        if not self._closing:
            self._session_timer.start(SESSION_DEBOUNCE_MS)

    def _save_session_now(self) -> None:
        if not self._closing:
            self.settings["session"] = self._capture_session()    # written only if changed

    def restore_session(self, session: dict, remember: bool = False) -> int:
        """Open the session's files (skipping missing ones).  Returns how many opened."""
        opened, missing, current_tab, by_number = 0, [], None, {}
        for i, st in enumerate(session["tabs"]):
            if not os.path.exists(st["path"]):
                missing.append(os.path.basename(st["path"]))
                continue
            tab = self.open_file(st["path"], state=st, remember=remember)
            if tab is not None:
                opened += 1
                by_number[i] = tab
                if i == session["current"]:
                    current_tab = tab
        if session.get("layout") and by_number:
            self.tabs.apply_layout(session["layout"], by_number.get)
        if current_tab is not None:
            self.tabs.setCurrentWidget(current_tab)
        if missing:
            self.statusBar().showMessage("Not found, skipped: " + ", ".join(missing), 8000)
        return opened

    def restore_last_session(self) -> int:
        return self.restore_session(self.settings.last_session())

    def save_session_dialog(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Save session", "session.logscope.json",
                                              "Session files (*.json);;All files (*)")
        if path:
            try:
                save_session_file(path, self._capture_session())
            except OSError as exc:
                QMessageBox.warning(self, "Session", f"Cannot save:\n{exc}")

    def load_session_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Load session", "",
                                              "Session files (*.json);;All files (*)")
        if not path:
            return
        try:
            session = load_session_file(path)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Session", f"Cannot load session:\n{exc}")
            return
        cmds = session_actions(session)
        if cmds:
            ans = QMessageBox.question(
                self, "Session",
                "This session would start programs when its filters match:\n\n  " + "\n  ".join(cmds) +
                "\n\nYes = load including the programs\nNo = load without the programs",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel)
            if ans == QMessageBox.StandardButton.Cancel:
                return
            if ans == QMessageBox.StandardButton.No:
                strip_actions(session)
        self.restore_session(session, remember=True)

    # ---------------------------------------------------------------- status
    def _update_status(self) -> None:
        w = self.tabs.currentWidget()
        self.status.setText(w.status_text() if isinstance(w, LogTab)
                            else "Drop a log file here or press Ctrl+O")

    # -------------------------------------------------------- drag and drop
    def dragEnterEvent(self, e) -> None:
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e) -> None:
        for url in e.mimeData().urls():
            if url.isLocalFile():
                self.open_file(url.toLocalFile())

    def closeEvent(self, e) -> None:
        self._save_session_now()          # before tabs disappear
        self._closing = True
        if self._merge_worker is not None:
            self._merge_worker.cancel.set()
            self._merge_worker.wait(3000)
        self.bookmark_panel.set_tab(None)
        for win in list(self.detached):
            self.detached.remove(win)
            tab = win.take_tab()
            win.force_close()
            tab.shutdown()
        while self.tabs.count():
            self.close_tab(0)
        super().closeEvent(e)
