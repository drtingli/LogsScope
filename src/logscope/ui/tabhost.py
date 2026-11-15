"""The tab area: any number of groups of tabs, arranged side by side and stacked without limit.

The area is a tree of splitters whose leaves are *groups* (``Group``).  Every group has its own tab bar
and shows one of its tabs.  Tabs are moved with the mouse:

* drag a tab onto the tab bar of any group -> the tab joins that group (at the position you drop it);
* drag a tab onto the page area of a group -> the nearer edge splits the group (left / right / top /
  bottom: a new column or row appears), the middle adds the tab to the group.

Ready-made layouts (one page at a time, side by side, stacked, grid) and ``arrange(columns, rows)`` build
the same tree.  The splitter handles resize every area; areas can be nested as deep as needed.

``TabHost`` keeps the small part of the ``QTabWidget`` API the main window uses (``count``, ``widget``,
``addTab``, ``removeTab`` ...).  Tab numbers count the tabs in layout order (group after group).
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QDrag, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel, QMenu, QPushButton, QSplitter, QStackedWidget, QTabBar,
    QToolButton, QVBoxLayout, QWidget,
)

from PySide6.QtCore import QMimeData

from ..core import layout as lay
from .led import LedIndicator

LAYOUTS = (("tabs", "Tabs (one area)"),
           ("columns", "Side by side"),
           ("rows", "Stacked"),
           ("grid", "Grid"))
LAYOUT_IDS = tuple(k for k, _ in LAYOUTS) + ("custom",)
STRIP_PX = 4
MIME = "application/x-logscope-tab"
DRAG_ALPHA = 70


def readable_on(color: str) -> str:
    """Black or white text, whichever is readable on ``color``."""
    c = QColor(color)
    return "#000000" if (c.red() * 299 + c.green() * 587 + c.blue() * 114) / 1000 > 150 else "#ffffff"


class ColorTabBar(QTabBar):
    """Paints the colour of every tab (a strip on top and a light tint)."""

    context_requested = Signal(int, QPoint)

    def __init__(self, color_of, parent=None) -> None:
        super().__init__(parent)
        self._color_of = color_of

    def paintEvent(self, e) -> None:
        super().paintEvent(e)
        p = QPainter(self)
        for i in range(self.count()):
            c = self._color_of(i)
            if not c:
                continue
            r = self.tabRect(i)
            tint = QColor(c)
            tint.setAlpha(55)
            p.fillRect(r.adjusted(0, STRIP_PX, 0, 0), tint)
            p.fillRect(QRect(r.left(), r.top(), r.width(), STRIP_PX), QColor(c))
        p.end()

    def contextMenuEvent(self, e) -> None:
        i = self.tabAt(e.pos())
        if i >= 0:
            self.context_requested.emit(i, e.globalPos())


class GroupTabBar(ColorTabBar):
    """A tab bar whose tabs can be dragged to any other group (and re-ordered by dragging)."""

    drag_started = Signal(int)
    drag_finished = Signal()
    dropped_at = Signal(int)                  # insert position among the tabs of this bar
    clicked = Signal()

    def __init__(self, color_of, parent=None) -> None:
        super().__init__(color_of, parent)
        self.setMovable(False)
        self.setAcceptDrops(True)
        self._press: tuple[QPoint, int] | None = None
        self._drop_at = -1

    # ----------------------------------------------------------------- dragging out
    def mousePressEvent(self, e) -> None:
        self.clicked.emit()
        self._press = None
        if e.button() == Qt.MouseButton.LeftButton:
            i = self.tabAt(e.pos())
            if i >= 0 and not self._on_close_button(i, e.pos()):
                self._press = (e.pos(), i)
        super().mousePressEvent(e)

    def _on_close_button(self, i: int, pos: QPoint) -> bool:
        b = self.tabButton(i, QTabBar.ButtonPosition.RightSide)         # the LED (left) passes clicks through
        return b is not None and b.geometry().contains(pos)

    def mouseMoveEvent(self, e) -> None:
        if self._press is not None and e.buttons() & Qt.MouseButton.LeftButton:
            pos, i = self._press
            if (e.pos() - pos).manhattanLength() >= QApplication.startDragDistance() and 0 <= i < self.count():
                self._press = None
                self._start_drag(i, pos)
                return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e) -> None:
        self._press = None
        super().mouseReleaseEvent(e)

    def _start_drag(self, i: int, pos: QPoint) -> None:
        rect = self.tabRect(i)
        drag = QDrag(self)
        md = QMimeData()
        md.setData(MIME, b"tab")
        drag.setMimeData(md)
        pix = self.grab(rect)
        drag.setPixmap(pix)
        drag.setHotSpot(pos - rect.topLeft())
        self.drag_started.emit(i)
        try:
            drag.exec(Qt.DropAction.MoveAction)
        finally:
            try:
                self.drag_finished.emit()
            except RuntimeError:               # this bar was deleted while the drop was handled
                pass

    # ------------------------------------------------------------------- dropping in
    def _index_at(self, x: int) -> int:
        return lay.insert_index([self.tabRect(i).center().x() for i in range(self.count())], x)

    def dragEnterEvent(self, e) -> None:
        if e.mimeData().hasFormat(MIME):
            e.acceptProposedAction()
            self._drop_at = self._index_at(e.position().toPoint().x())
            self.update()

    def dragMoveEvent(self, e) -> None:
        if e.mimeData().hasFormat(MIME):
            e.acceptProposedAction()
            at = self._index_at(e.position().toPoint().x())
            if at != self._drop_at:
                self._drop_at = at
                self.update()

    def dragLeaveEvent(self, e) -> None:
        self._drop_at = -1
        self.update()

    def dropEvent(self, e) -> None:
        if e.mimeData().hasFormat(MIME):
            at = self._index_at(e.position().toPoint().x())
            self._drop_at = -1
            self.update()
            e.acceptProposedAction()
            self.dropped_at.emit(at)

    def paintEvent(self, e) -> None:
        super().paintEvent(e)
        if self._drop_at >= 0:
            if self.count() == 0:
                x = 2
            elif self._drop_at >= self.count():
                x = self.tabRect(self.count() - 1).right()
            else:
                x = self.tabRect(self._drop_at).left()
            p = QPainter(self)
            pen = QPen(QColor("#3b82f6"))
            pen.setWidth(3)
            p.setPen(pen)
            p.drawLine(x, 2, x, self.height() - 2)
            p.end()


class DropOverlay(QWidget):
    """Transparent layer over the pages of a group while a tab is being dragged: shows where it would go."""

    dropped = Signal(str)

    def __init__(self, parent) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)
        self._zone = ""
        self.hide()

    def _zone_for(self, pos) -> str:
        return lay.zone_at(pos.x(), pos.y(), self.width(), self.height())

    def dragEnterEvent(self, e) -> None:
        if e.mimeData().hasFormat(MIME):
            e.acceptProposedAction()
            self._zone = self._zone_for(e.position().toPoint())
            self.update()

    def dragMoveEvent(self, e) -> None:
        if e.mimeData().hasFormat(MIME):
            e.acceptProposedAction()
            z = self._zone_for(e.position().toPoint())
            if z != self._zone:
                self._zone = z
                self.update()

    def dragLeaveEvent(self, e) -> None:
        self._zone = ""
        self.update()

    def dropEvent(self, e) -> None:
        zone = self._zone_for(e.position().toPoint())
        self._zone = ""
        self.update()
        e.acceptProposedAction()
        self.dropped.emit(zone)

    def paintEvent(self, e) -> None:
        if not self._zone:
            return
        x, y, w, h = lay.zone_rect(self._zone, self.width(), self.height())
        p = QPainter(self)
        c = QColor("#3b82f6")
        c.setAlpha(DRAG_ALPHA)
        p.fillRect(x, y, w, h, c)
        pen = QPen(QColor("#3b82f6"))
        pen.setWidth(2)
        p.setPen(pen)
        p.drawRect(x + 1, y + 1, w - 2, h - 2)
        p.end()


class PageArea(QWidget):
    """Holds the stack of pages of a group; keeps the drop overlay the size of the page area."""

    def __init__(self) -> None:
        super().__init__()
        self.overlay = DropOverlay(self)
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(0, 0, 0, 0)

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self.overlay.setGeometry(self.rect())
        self.overlay.raise_()


class Group(QFrame):
    """One area: tab bar (with a menu button) above the page of the current tab."""

    current_changed = Signal(object)          # tab widget or None
    activated = Signal(object)                # this group
    close_requested = Signal(object)          # tab widget
    context_requested = Signal(object, QPoint)
    drag_started = Signal(object, object)     # this group, tab widget
    drag_finished = Signal()
    bar_dropped = Signal(object, int)         # this group, insert position
    page_dropped = Signal(object, str)        # this group, zone
    remove_empty_requested = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self._tabs: list[QWidget] = []
        self.leds_visible = True
        self._active = False
        self._multi = False
        self.bar = GroupTabBar(self._color_of)
        self.bar.setTabsClosable(True)
        self.bar.setDocumentMode(True)
        self.bar.setExpanding(False)
        self.bar.setUsesScrollButtons(True)
        self.bar.setElideMode(Qt.TextElideMode.ElideRight)
        self.bar.currentChanged.connect(self._on_bar_current)
        self.bar.tabCloseRequested.connect(lambda i: self.close_requested.emit(self._tabs[i]) if 0 <= i < len(self._tabs) else None)
        self.bar.context_requested.connect(lambda i, pos: self.context_requested.emit(self._tabs[i], pos) if 0 <= i < len(self._tabs) else None)
        self.bar.drag_started.connect(lambda i: self.drag_started.emit(self, self._tabs[i]) if 0 <= i < len(self._tabs) else None)
        self.bar.drag_finished.connect(self.drag_finished)
        self.bar.dropped_at.connect(lambda at: self.bar_dropped.emit(self, at))
        self.bar.clicked.connect(lambda: self.activated.emit(self))

        self.menu_btn = QToolButton()
        self.menu_btn.setText("\u22ee")
        self.menu_btn.setAutoRaise(True)
        self.menu_btn.setToolTip("Areas: move this tab to a new area, add or remove areas")
        self.menu_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.menu = QMenu(self.menu_btn)
        self.menu_btn.setMenu(self.menu)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(0)
        head.addWidget(self.bar, 1)
        head.addWidget(self.menu_btn)

        self.stack = QStackedWidget()
        self.placeholder = QWidget()
        pl = QVBoxLayout(self.placeholder)
        pl.addStretch(1)
        msg = QLabel("Empty area\n\nDrag a tab here")
        msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
        msg.setEnabled(False)
        self.remove_btn = QPushButton("Remove this area")
        self.remove_btn.clicked.connect(lambda: self.remove_empty_requested.emit(self))
        pl.addWidget(msg)
        pl.addWidget(self.remove_btn, 0, Qt.AlignmentFlag.AlignCenter)
        pl.addStretch(1)
        self.stack.addWidget(self.placeholder)

        self.area = PageArea()
        self.area.layout().addWidget(self.stack)
        self.area.overlay.dropped.connect(lambda zone: self.page_dropped.emit(self, zone))

        lyt = QVBoxLayout(self)
        lyt.setContentsMargins(1, 1, 1, 1)
        lyt.setSpacing(0)
        lyt.addLayout(head)
        lyt.addWidget(self.area, 1)
        self._update_empty()

    # ------------------------------------------------------------------ content
    def _color_of(self, i: int) -> str:
        return getattr(self._tabs[i], "tab_color", "") if 0 <= i < len(self._tabs) else ""

    @property
    def tabs(self) -> list[QWidget]:
        return list(self._tabs)

    def current_tab(self):
        i = self.bar.currentIndex()
        return self._tabs[i] if 0 <= i < len(self._tabs) else None

    def index_of(self, tab) -> int:
        return self._tabs.index(tab) if tab in self._tabs else -1

    def insert(self, tab, title: str, tooltip: str = "", at: int | None = None) -> None:
        at = len(self._tabs) if at is None else max(0, min(at, len(self._tabs)))
        self._tabs.insert(at, tab)
        self.stack.addWidget(tab)
        self.bar.blockSignals(True)
        self.bar.insertTab(at, title)
        self.bar.setTabToolTip(at, tooltip)
        self.bar.blockSignals(False)
        self._add_led(tab, at)
        self._update_empty()

    def take(self, tab) -> tuple[str, str]:
        """Remove ``tab`` (the widget stays alive, without parent); returns its title and tooltip."""
        i = self._tabs.index(tab)
        title, tip = self.bar.tabText(i), self.bar.tabToolTip(i)
        was_current = i == self.bar.currentIndex()
        self._tabs.pop(i)
        self.stack.removeWidget(tab)
        tab.setParent(None)
        self.bar.blockSignals(True)
        self.bar.removeTab(i)
        self.bar.blockSignals(False)
        self._update_empty()
        if was_current or not self._tabs:
            self._sync_page()
            self.current_changed.emit(self.current_tab())
        return title, tip

    def select(self, tab) -> None:
        i = self.index_of(tab)
        if i >= 0:
            self.bar.setCurrentIndex(i)
            self._sync_page()

    def set_title(self, tab, text: str) -> None:
        i = self.index_of(tab)
        if i >= 0:
            self.bar.setTabText(i, text)

    def title(self, tab) -> str:
        i = self.index_of(tab)
        return self.bar.tabText(i) if i >= 0 else ""

    def set_tooltip(self, tab, text: str) -> None:
        i = self.index_of(tab)
        if i >= 0:
            self.bar.setTabToolTip(i, text)

    def _sync_page(self) -> None:
        cur = self.current_tab()
        self.stack.setCurrentWidget(cur if cur is not None else self.placeholder)

    def _add_led(self, tab, index: int) -> None:
        """The activity LED in front of the file name (blinks when new lines arrive while following)."""
        led = LedIndicator(bool(getattr(tab, "follow", False)))
        led.setVisible(self.leds_visible)
        follow_sig = getattr(tab, "follow_changed", None)
        if follow_sig is not None:
            follow_sig.connect(led.set_following)
        activity = getattr(tab, "activity", None)
        if activity is not None:
            activity.connect(led.flash)
        self.bar.setTabButton(index, QTabBar.ButtonPosition.LeftSide, led)

    def set_leds_visible(self, on: bool) -> None:
        self.leds_visible = bool(on)
        for i in range(self.bar.count()):
            b = self.bar.tabButton(i, QTabBar.ButtonPosition.LeftSide)
            if b is not None:
                b.setVisible(self.leds_visible)

    def _on_bar_current(self, _i: int) -> None:
        self._sync_page()
        self.current_changed.emit(self.current_tab())

    def _update_empty(self) -> None:
        self.remove_btn.setVisible(not self._tabs and self._multi)
        if not self._tabs:
            self.stack.setCurrentWidget(self.placeholder)

    # ------------------------------------------------------------------ looks
    def set_multi(self, multi: bool) -> None:
        self._multi = multi
        self.remove_btn.setVisible(not self._tabs and multi)
        self.update()

    def set_active(self, on: bool) -> None:
        self._active = on
        self.update()

    def show_drop_zones(self, on: bool) -> None:
        ov = self.area.overlay
        if on:
            ov.setGeometry(self.area.rect())
            ov.show()
            ov.raise_()
        else:
            ov.hide()

    def paintEvent(self, e) -> None:
        super().paintEvent(e)
        if self._multi:
            p = QPainter(self)
            c = QColor("#3b82f6")
            if not self._active:
                c.setAlpha(70)
            pen = QPen(c)
            pen.setWidth(2 if self._active else 1)
            p.setPen(pen)
            p.drawRect(self.rect().adjusted(0, 0, -1, -1))
            p.end()

    def mousePressEvent(self, e) -> None:
        self.activated.emit(self)
        super().mousePressEvent(e)


class TabHost(QWidget):
    currentChanged = Signal(int)
    tabCloseRequested = Signal(int)
    tabContextRequested = Signal(int, QPoint)
    detachRequested = Signal(int)
    layoutChanged = Signal(str)               # the arrangement changed (mode name)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._groups: list[Group] = []
        self._root: QWidget | None = None
        self._active: Group | None = None
        self._mode = "tabs"
        self._drag: tuple[Group, QWidget] | None = None
        self._leds_visible = True
        self._quiet = 0                           # >0: restructuring, do not announce intermediate current tabs
        self._holder = QVBoxLayout(self)
        self._holder.setContentsMargins(0, 0, 0, 0)
        self._holder.setSpacing(0)
        first = self._new_group()
        self._set_root(first)
        self._set_active(first, announce=False)
        QApplication.instance().focusChanged.connect(self._on_focus_changed)

    # ------------------------------------------------------------ groups: creating, finding
    def _new_group(self) -> Group:
        g = Group()
        self._wire(g)
        self._groups.append(g)
        return g

    def _ordered_groups(self, node=None) -> list[Group]:
        node = self._root if node is None else node
        if isinstance(node, Group):
            return [node]
        out: list[Group] = []
        if isinstance(node, QSplitter):
            for i in range(node.count()):
                out += self._ordered_groups(node.widget(i))
        return out

    def _flat(self) -> list[tuple[Group, QWidget]]:
        return [(g, t) for g in self._ordered_groups() for t in g.tabs]

    def _group_of(self, tab) -> Group | None:
        for g in self._groups:
            if tab in g.tabs:
                return g
        return None

    def _emit_index(self, signal, tab) -> None:
        i = self.indexOf(tab)
        if i >= 0:
            signal.emit(i)

    def _emit_context(self, tab, pos) -> None:
        i = self.indexOf(tab)
        if i >= 0:
            self.tabContextRequested.emit(i, pos)

    # ----------------------------------------------------------- QTabWidget-like API
    def count(self) -> int:
        return sum(len(g.tabs) for g in self._groups)

    def widget(self, i: int):
        flat = self._flat()
        return flat[i][1] if 0 <= i < len(flat) else None

    def indexOf(self, w) -> int:
        for i, (_g, t) in enumerate(self._flat()):
            if t is w:
                return i
        return -1

    def currentWidget(self):
        return self._active.current_tab() if self._active is not None else None

    def currentIndex(self) -> int:
        w = self.currentWidget()
        return self.indexOf(w) if w is not None else -1

    def setCurrentIndex(self, i: int) -> None:
        flat = self._flat()
        if 0 <= i < len(flat):
            g, t = flat[i]
            g.select(t)
            self._set_active(g)

    def setCurrentWidget(self, w) -> None:
        g = self._group_of(w)
        if g is not None:
            g.select(w)
            self._set_active(g)

    def addTab(self, tab, title: str) -> int:
        """The tab joins the active area (the ready-made layouts give every tab an area of its own)."""
        g = self._active if self._active in self._groups else self._groups[0]
        g.insert(tab, title)
        if self._mode in lay.PRESETS:
            self._apply_preset()
        self._emit_layout()
        return self.indexOf(tab)

    def removeTab(self, i: int):
        """Take page ``i`` out; returns its widget (still alive, no parent)."""
        flat = self._flat()
        g, tab = flat[i]
        self._quiet += 1
        try:
            g.take(tab)
            if not g.tabs and len(self._groups) > 1:       # an emptied area goes away unless it is the last one
                self._remove_group(g)
            if self._mode in lay.PRESETS:
                self._apply_preset()
        finally:
            self._quiet -= 1
        self._announce_current()
        self._emit_layout()
        return tab

    def setTabText(self, i: int, text: str) -> None:
        w = self.widget(i)
        g = self._group_of(w) if w is not None else None
        if g is not None:
            g.set_title(w, text)

    def tabText(self, i: int) -> str:
        w = self.widget(i)
        g = self._group_of(w) if w is not None else None
        return g.title(w) if g is not None else ""

    def setTabToolTip(self, i: int, text: str) -> None:
        w = self.widget(i)
        g = self._group_of(w) if w is not None else None
        if g is not None:
            g.set_tooltip(w, text)

    def refresh_chrome(self) -> None:
        """Tab colours or titles changed: redraw the tab bars."""
        for g in self._groups:
            g.bar.update()

    # ------------------------------------------------------------------- active area
    def _set_active(self, g: Group, announce: bool = True) -> None:
        if g is self._active or g not in self._groups:
            return
        if self._active is not None:
            self._active.set_active(False)
        self._active = g
        g.set_active(True)
        if announce:
            self._announce_current()

    def _announce_current(self) -> None:
        if not self._quiet:
            self.currentChanged.emit(self.currentIndex())

    def _on_group_current(self, g: Group, _tab) -> None:
        if g is self._active:
            self._announce_current()

    def _on_focus_changed(self, _old, new) -> None:
        w = new
        while w is not None:
            if isinstance(w, Group):
                if w in self._groups:
                    self._set_active(w)
                return
            w = w.parentWidget()

    def _refresh_multi(self) -> None:
        multi = len(self._groups) > 1
        for g in self._groups:
            g.set_multi(multi)

    # --------------------------------------------------------------------- tree surgery
    def _set_root(self, w: QWidget) -> None:
        if self._root is not None:
            self._holder.removeWidget(self._root)
        self._root = w
        self._holder.addWidget(w)
        w.show()

    def _replace(self, old: QWidget, new: QWidget) -> None:
        """Put ``new`` where ``old`` is in the tree (``old`` stays alive, detached by the caller)."""
        parent = old.parentWidget()
        if isinstance(parent, QSplitter):
            idx, sizes = parent.indexOf(old), parent.sizes()
            parent.insertWidget(idx, new)
            old.setParent(None)
            parent.setSizes(sizes)
        else:
            self._set_root(new)
            old.setParent(None)

    @staticmethod
    def _splitter(orient: str) -> QSplitter:
        sp = QSplitter(Qt.Orientation.Horizontal if orient == "h" else Qt.Orientation.Vertical)
        sp.setChildrenCollapsible(False)
        sp.setHandleWidth(5)
        return sp

    @staticmethod
    def _orient(sp: QSplitter) -> str:
        return "h" if sp.orientation() == Qt.Orientation.Horizontal else "v"

    def _split_group(self, dest: Group, orient: str, before: bool) -> Group:
        """A new empty area on one side of ``dest``."""
        new = self._new_group()
        parent = dest.parentWidget()
        if isinstance(parent, QSplitter) and self._orient(parent) == orient:
            idx, sizes = parent.indexOf(dest), parent.sizes()
            total = sizes[idx]
            parent.insertWidget(idx if before else idx + 1, new)
            half = max(total // 2, 1)
            sizes[idx:idx + 1] = [half, max(total - half, 1)] if not before else [max(total - half, 1), half]
            parent.setSizes(sizes)
        else:
            sp = self._splitter(orient)
            sizes = parent.sizes() if isinstance(parent, QSplitter) else []
            idx = parent.indexOf(dest) if isinstance(parent, QSplitter) else -1
            if isinstance(parent, QSplitter):
                parent.insertWidget(idx, sp)
            else:
                self._set_root(sp)
            if before:
                sp.addWidget(new)
                sp.addWidget(dest)
            else:
                sp.addWidget(dest)
                sp.addWidget(new)
            sp.setSizes([1000, 1000])
            if isinstance(parent, QSplitter) and sizes:
                parent.setSizes(sizes)
        new.show()
        return new

    def _remove_group(self, g: Group) -> None:
        """Take an (empty) area out of the tree; a splitter left with one child collapses."""
        if g not in self._groups or len(self._groups) <= 1:
            return
        parent = g.parentWidget()
        self._groups.remove(g)
        was_active = g is self._active
        g.setParent(None)
        g.deleteLater()
        if isinstance(parent, QSplitter):
            self._collapse(parent)
        if was_active:
            self._active = None
            self._set_active(self._ordered_groups()[0])
        self._refresh_multi()

    def _collapse(self, sp: QSplitter) -> None:
        if sp.count() != 1:
            return
        child = sp.widget(0)
        gp = sp.parentWidget()
        if isinstance(gp, QSplitter):
            idx, sizes = gp.indexOf(sp), gp.sizes()
            gp.insertWidget(idx, child)
            sp.setParent(None)
            sp.deleteLater()
            gp.setSizes(sizes)
            if isinstance(child, QSplitter) and self._orient(child) == self._orient(gp):
                self._flatten(gp, child)
        else:
            child.setParent(None)
            sp.setParent(None)
            sp.deleteLater()
            self._set_root(child)

    def _flatten(self, outer: QSplitter, inner: QSplitter) -> None:
        """``inner`` (a child of ``outer``) has the same direction: merge its children into ``outer``."""
        idx, sizes, inner_sizes = outer.indexOf(inner), outer.sizes(), inner.sizes()
        share, total = sizes[idx], max(sum(inner_sizes), 1)
        kids = [inner.widget(i) for i in range(inner.count())]
        for n, w in enumerate(kids):
            outer.insertWidget(idx + n, w)
        inner.setParent(None)
        inner.deleteLater()
        sizes[idx:idx + 1] = [max(int(share * s / total), 1) for s in inner_sizes]
        outer.setSizes(sizes)

    def _remove_empty_group(self, g: Group) -> None:
        if not g.tabs:
            self._remove_group(g)
            self._mode = "custom"
            self._emit_layout()

    def _emit_layout(self) -> None:
        self._refresh_multi()
        self.layoutChanged.emit(self._mode)

    # ---------------------------------------------------------------- dragging tabs
    def _begin_drag(self, g: Group, tab) -> None:
        self._drag = (g, tab)
        for grp in self._groups:
            grp.show_drop_zones(True)

    def _end_drag(self) -> None:
        self._drag = None
        for grp in list(self._groups):
            try:
                grp.show_drop_zones(False)
            except RuntimeError:
                pass

    def _drop_on_bar(self, dest: Group, at: int) -> None:
        if self._drag is None:
            return
        src, tab = self._drag
        QTimer.singleShot(0, lambda: self._move_tab(tab, dest, at))

    def _drop_on_page(self, dest: Group, zone: str) -> None:
        if self._drag is None:
            return
        src, tab = self._drag
        QTimer.singleShot(0, lambda: self._drop_zone(tab, dest, zone))

    def _drop_zone(self, tab, dest: Group, zone: str) -> None:
        edge = lay.zone_split(zone)
        if edge is None:
            self._move_tab(tab, dest, None)
            return
        src = self._group_of(tab)
        if src is None or dest not in self._groups:
            return
        if src is dest and len(src.tabs) == 1:
            return                                   # splitting a group off its only tab changes nothing
        if len(self._groups) >= lay.MAX_GROUPS:
            return
        orient, before = edge
        new = self._split_group(dest, orient, before)
        self._move_tab(tab, new, None)

    def _move_tab(self, tab, dest: Group, at: int | None) -> None:
        src = self._group_of(tab)
        if src is None or dest not in self._groups:
            return
        if src is dest:
            old = src.index_of(tab)
            if at is None or at in (old, old + 1):
                src.select(tab)
                self._set_active(src)
                return
        self._quiet += 1
        try:
            if src is dest:
                title, tip = src.take(tab)
                src.insert(tab, title, tip, at - 1 if at > old else at)
            else:
                title, tip = src.take(tab)
                dest.insert(tab, title, tip, at)
                if not src.tabs and len(self._groups) > 1:
                    self._remove_group(src)
            dest.select(tab)
            self._set_active(dest, announce=False)
        finally:
            self._quiet -= 1
        self._announce_current()
        self._mode = "custom"
        self._emit_layout()

    # ------------------------------------------------------------ commands (menus)
    def move_tab_to_new_area(self, tab, zone: str) -> None:
        """``zone``: left / right / top / bottom of the tab's current area."""
        g = self._group_of(tab)
        if g is not None and len(g.tabs) > 1:
            self._drop_zone(tab, g, zone)

    def add_empty_area(self, group: Group, zone: str) -> None:
        edge = lay.zone_split(zone)
        if edge is not None and len(self._groups) < lay.MAX_GROUPS:
            self._split_group(group, edge[0], edge[1])
            self._mode = "custom"
            self._emit_layout()

    def areas(self, exclude_tab=None) -> list[tuple[str, Group]]:
        """(label, area) of every area except the one holding ``exclude_tab``, in layout order."""
        out = []
        skip = self._group_of(exclude_tab) if exclude_tab is not None else None
        for n, g in enumerate(self._ordered_groups(), 1):
            if g is skip:
                continue
            cur = g.current_tab()
            name = g.title(cur) if cur is not None else "empty"
            out.append((f"Area {n}: {name}", g))
        return out

    def move_tab_to_area(self, tab, group: Group) -> None:
        self._move_tab(tab, group, None)

    def area_count(self) -> int:
        return len(self._groups)

    def area_tab_count(self, tab) -> int:
        """Number of tabs in the area that holds ``tab``."""
        g = self._group_of(tab)
        return len(g.tabs) if g is not None else 0

    def merge_all(self) -> None:
        self.arrange(1, 1, "tabs")

    def _fill_group_menu(self, g: Group) -> None:
        m = g.menu
        m.clear()
        tab = g.current_tab()
        multi_tabs = len(g.tabs) > 1
        move = m.addMenu("Move this tab to a new area")
        move.setEnabled(multi_tabs and tab is not None)
        for label, zone in (("on the right", "right"), ("below", "bottom"), ("on the left", "left"), ("above", "top")):
            move.addAction(label).triggered.connect(lambda _=False, z=zone, t=tab: self.move_tab_to_new_area(t, z))
        others = self.areas(tab) if tab is not None else []
        to = m.addMenu("Move this tab to another area")
        to.setEnabled(bool(others) and tab is not None)
        for label, grp in others:
            to.addAction(label).triggered.connect(lambda _=False, gr=grp, t=tab: self.move_tab_to_area(t, gr))
        add = m.addMenu("Add an empty area")
        for label, zone in (("to the right", "right"), ("below", "bottom"), ("to the left", "left"), ("above", "top")):
            add.addAction(label).triggered.connect(lambda _=False, z=zone, grp=g: self.add_empty_area(grp, z))
        m.addSeparator()
        close = m.addAction("Close this area (its tabs move to a neighbour)")
        close.setEnabled(len(self._groups) > 1)
        close.triggered.connect(lambda _=False, grp=g: self._close_area(grp))
        merge = m.addAction("Merge all areas into one")
        merge.setEnabled(len(self._groups) > 1)
        merge.triggered.connect(self.merge_all)

    def _close_area(self, g: Group) -> None:
        """Remove an area; its tabs go to the next area in layout order."""
        order = self._ordered_groups()
        if g not in order or len(order) < 2:
            return
        i = order.index(g)
        target = order[i + 1] if i + 1 < len(order) else order[i - 1]
        self._quiet += 1
        try:
            for tab in g.tabs:
                title, tip = g.take(tab)
                target.insert(tab, title, tip)
            cur = target.current_tab()
            self._remove_group(g)
            if cur is not None:
                target.select(cur)
            self._set_active(target, announce=False)
        finally:
            self._quiet -= 1
        self._announce_current()
        self._mode = "custom"
        self._emit_layout()

    # -------------------------------------------------------------------- layouts
    def layoutMode(self) -> str:
        return self._mode

    def setLayoutMode(self, mode: str) -> None:
        if mode not in LAYOUT_IDS:
            mode = "tabs"
        if mode == "custom":
            self._mode = "custom"                    # nothing to rebuild: the areas stay as they are
            self.layoutChanged.emit(mode)
            return
        if mode == "tabs":
            self.arrange(1, 1, "tabs")
        else:
            self._mode = mode
            self._apply_preset()
            self._emit_layout()

    def _apply_preset(self) -> None:
        cols, rows = lay.preset_shape(self._mode, self.count())
        self._rebuild(lay.arrange_tree(self.count(), cols, rows))

    def arrange(self, cols: int, rows: int, mode: str = "custom") -> None:
        """``rows`` x ``cols`` areas (any numbers); the tabs are spread over them in order."""
        self._mode = mode if mode in ("tabs", "custom") else "custom"
        self._rebuild(lay.arrange_tree(self.count(), cols, rows))
        self._emit_layout()

    def _rebuild(self, tree: dict, current=None) -> None:
        """Replace all areas by ``tree`` (tab numbers = positions in the present tab order)."""
        self._quiet += 1
        try:
            self._rebuild_quiet(tree, current)
        finally:
            self._quiet -= 1
        self._announce_current()

    def _rebuild_quiet(self, tree: dict, current=None) -> None:
        flat = self._flat()
        tabs = [t for _g, t in flat]
        meta = {t: (g.title(t), g.bar.tabToolTip(g.index_of(t))) for g, t in flat}
        cur = current if current is not None else self.currentWidget()
        for g, t in flat:
            g.take(t)
        old_root, old_groups = self._root, self._groups
        self._groups = []
        self._active = None
        for g in old_groups:
            g.setParent(None)
            g.deleteLater()
        if old_root is not None and not isinstance(old_root, Group):
            old_root.setParent(None)
            old_root.deleteLater()
        self._root = None
        placed: set = set()
        root = self._build(tree, tabs, meta, placed)
        for t in tabs:                                   # tabs the tree did not mention: into the first area
            if t not in placed:
                g0 = self._ordered_groups(root)[0]
                g0.insert(t, *meta[t])
        self._set_root(root)
        self._groups = self._ordered_groups(root)
        for g in self._groups:
            g.show()
        self._refresh_multi()
        target = self._group_of(cur) if cur is not None else None
        self._active = None
        self._set_active(target or self._groups[0], announce=False)
        if cur is not None and target is not None:
            target.select(cur)

    def _build(self, node: dict, tabs: list, meta: dict, placed: set):
        if node["t"] == "g":
            g = Group()
            self._wire(g)
            for i in node["tabs"]:
                if 0 <= i < len(tabs) and tabs[i] not in placed:
                    placed.add(tabs[i])
                    g.insert(tabs[i], *meta[tabs[i]])
            if g.tabs:
                g.select(g.tabs[node["cur"]] if 0 <= node["cur"] < len(g.tabs) else g.tabs[0])
            return g
        sp = self._splitter(node["o"])
        for k in node["kids"]:
            sp.addWidget(self._build(k, tabs, meta, placed))
        sp.setSizes(list(node["sizes"]))
        return sp

    def set_leds_visible(self, on: bool) -> None:
        """Show or hide the activity LEDs of all tabs."""
        self._leds_visible = bool(on)
        for g in self._groups:
            g.set_leds_visible(self._leds_visible)

    def _wire(self, g: Group) -> None:
        """Connect a new group: its signals, and the LED setting."""
        g.leds_visible = self._leds_visible
        g.current_changed.connect(lambda tab, grp=g: self._on_group_current(grp, tab))
        g.activated.connect(self._set_active)
        g.close_requested.connect(lambda tab: self._emit_index(self.tabCloseRequested, tab))
        g.context_requested.connect(lambda tab, pos: self._emit_context(tab, pos))
        g.drag_started.connect(self._begin_drag)
        g.drag_finished.connect(self._end_drag)
        g.bar_dropped.connect(self._drop_on_bar)
        g.page_dropped.connect(self._drop_on_page)
        g.remove_empty_requested.connect(self._remove_empty_group)
        g.menu.aboutToShow.connect(lambda grp=g: self._fill_group_menu(grp))

    # -------------------------------------------------------------------- saving
    def capture_layout(self, number_of) -> dict | None:
        """The arrangement as a plain dictionary; ``number_of(tab)`` gives a tab's number in the session or
        None for tabs that are not saved.  An area that held only unsaved tabs is left out; an area that was
        empty on purpose stays."""
        def walk(node):
            if isinstance(node, Group):
                numbers = [number_of(t) for t in node.tabs]
                kept = [n for n in numbers if n is not None]
                if node.tabs and not kept:
                    return None
                cur = node.current_tab()
                cur_no = number_of(cur) if cur is not None else None
                return lay.group(kept, kept.index(cur_no) if cur_no in kept else 0)
            kids, sizes = [], []
            for i in range(node.count()):
                k = walk(node.widget(i))
                if k is not None:
                    kids.append(k)
                    sizes.append(max(int(node.sizes()[i]), 1))
            if not kids:
                return None
            return kids[0] if len(kids) == 1 else lay.split(self._orient(node), kids, sizes)
        root = walk(self._root)
        return {"mode": self._mode, "root": root} if root is not None else None

    def apply_layout(self, saved: dict, tab_of) -> None:
        """Arrange the open tabs as in ``saved`` (``tab_of(number)`` gives the tab widget or None)."""
        present = [t for _g, t in self._flat()]
        numbers: dict[int, int] = {}
        for n in lay.tab_numbers(saved["root"]):
            w = tab_of(n)
            if w is not None and w in present:
                numbers[n] = present.index(w)
        root = lay.prune(saved["root"], lambda n: n in numbers)
        if root is None:
            return
        self._mode = saved["mode"] if saved.get("mode") in lay.MODES else "custom"
        self._rebuild(lay.renumber(root, numbers))
        self._emit_layout()
