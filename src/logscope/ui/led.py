"""The activity LED shown in front of the file name in each tab."""

from __future__ import annotations

from PySide6.QtCore import QPointF, Qt, QTimer
from PySide6.QtGui import QBrush, QColor, QPainter, QPen, QRadialGradient
from PySide6.QtWidgets import QWidget

from ..core.ledstate import TICK_MS, LedState

SIZE = 16


class LedIndicator(QWidget):
    """A small round LED.  Mouse clicks pass through to the tab, so the tab can still be clicked and dragged."""

    def __init__(self, following: bool = False, parent=None) -> None:
        super().__init__(parent)
        self.state = LedState(following)
        self.setFixedSize(SIZE, SIZE)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._timer = QTimer(self)
        self._timer.setInterval(TICK_MS)
        self._timer.timeout.connect(self._tick)
        self._update_tip()

    # ------------------------------------------------------------------ state
    def set_following(self, on: bool) -> None:
        self.state.set_following(on)
        if not self.state.animating:
            self._timer.stop()
        self._update_tip()
        self.update()

    def flash(self) -> None:
        """New lines arrived (a no-op unless the tab follows the tail)."""
        if self.state.flash():
            if not self._timer.isActive():
                self._timer.start()
            self.update()

    def _tick(self) -> None:
        if not self.state.tick():
            self._timer.stop()
        self.update()

    def _update_tip(self) -> None:
        self.setToolTip("Following the tail: flashes when new lines arrive" if self.state.following
                        else "Not following the tail (Follow tail is off)")

    # ----------------------------------------------------------------- drawing
    def paintEvent(self, e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        c = QPointF(self.width() / 2, self.height() / 2)
        fill = QColor(self.state.color())
        glow = self.state.glow()
        if glow > 0.05:                                              # halo while it is bright
            halo = QColor(fill)
            halo.setAlpha(int(150 * glow))
            g = QRadialGradient(c, self.width() / 2)
            g.setColorAt(0.45, halo)
            g.setColorAt(1.0, QColor(0, 0, 0, 0))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(g))
            p.drawEllipse(c, self.width() / 2 - 0.5, self.height() / 2 - 0.5)
        r = self.width() / 2 - 4.0
        lens = QRadialGradient(QPointF(c.x() - r * 0.35, c.y() - r * 0.35), r * 1.6)
        lens.setColorAt(0.0, fill.lighter(150))
        lens.setColorAt(1.0, fill)
        p.setBrush(QBrush(lens))
        p.setPen(QPen(QColor(0, 0, 0, 90), 1))
        p.drawEllipse(c, r, r)
        p.end()
