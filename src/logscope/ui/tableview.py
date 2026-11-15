"""The table used for the main view and for the filter / search-result panels.

* column widths follow the longest text (auto-fit) or, in wrap mode, are fitted to the
  window width while every field wraps onto as many lines as it needs;
* double-clicking a field opens a read-only editor with the whole text selected and copied
  to the clipboard; the text can then be re-selected and copied by hand;
* Ctrl+C copies the selected lines."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtCore import QPointF
from PySide6.QtGui import (
    QColor, QFont, QFontMetrics, QGuiApplication, QPalette, QTextCharFormat, QTextLayout, QTextOption,
)
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QFrame, QHeaderView, QLineEdit, QPlainTextEdit, QStyle,
    QStyleOptionViewItem, QTableView,
)

from ..core.widths import fit_to_width, merge_max
from .look import GridDelegate, style_table
from .models import COL_LINE, COL_MARK, COL_PIN, FULL_ROLE, GUTTER, SPANS_ROLE

DEFAULT_FIELD_PX = 160
MIN_FIELD_PX = 48
MAX_FIELD_PX = 100_000
CELL_PAD_PX = 18                             # item margins + a little air
MAX_COPY_ROWS = 100_000
WHEEL_PX_PER_NOTCH = 64                      # sideways pixels per wheel notch (for an 8 px character cell)


def wrapped_line_count(text: str, font: QFont, width: int) -> int:
    """Number of lines ``text`` takes when wrapped like a table cell (words, or anywhere)."""
    if not text:
        return 1
    lay = QTextLayout(text, font)
    opt = QTextOption()
    opt.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
    lay.setTextOption(opt)
    n = 0
    lay.beginLayout()
    while True:
        line = lay.createLine()
        if not line.isValid():
            break
        line.setLineWidth(max(width, 10))
        n += 1
    lay.endLayout()
    return max(n, 1)


class FieldDelegate(GridDelegate):
    """Double-click -> read-only editor, text fully selected and put on the clipboard."""

    def __init__(self, view: "LogTableView") -> None:
        super().__init__(view)
        self._view = view

    def createEditor(self, parent, option, index):
        if self._view.wrap:
            ed = QPlainTextEdit(parent)
            ed.setReadOnly(True)
            ed.setFrameShape(QFrame.Shape.NoFrame)
            ed.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
            ed.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        else:
            ed = QLineEdit(parent)
            ed.setReadOnly(True)
            ed.setFrame(False)
        ed.setFont(self._view.font())
        return ed

    def setEditorData(self, editor, index) -> None:
        text = index.data(FULL_ROLE) or ""
        if isinstance(editor, QPlainTextEdit):
            editor.setPlainText(text)
        else:
            editor.setText(text)
            editor.setCursorPosition(0)
        editor.selectAll()
        QTimer.singleShot(0, editor.selectAll)            # survive the focus change
        QGuiApplication.clipboard().setText(text)
        self._view.copied.emit(f"Copied {len(text):,} characters to the clipboard")

    def setModelData(self, editor, model, index) -> None:
        pass                                              # read-only

    def updateEditorGeometry(self, editor, option, index) -> None:
        editor.setGeometry(option.rect)

    def paint(self, painter, option, index) -> None:
        """Cells with matching-words-only highlights are drawn word by word; selected cells
        and everything else use the normal painting."""
        spans = None
        if index.column() >= GUTTER and not (option.state & QStyle.StateFlag.State_Selected):
            spans = index.data(SPANS_ROLE)
        if not spans:
            super().paint(painter, option, index)
            return
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        text, opt.text = opt.text, ""
        widget = option.widget
        style = widget.style() if widget is not None else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, widget)   # background, focus
        margin = style.pixelMetric(QStyle.PixelMetric.PM_FocusFrameHMargin, opt, widget) + 1
        rect = option.rect.adjusted(margin, 0, -margin, 0)
        lay = QTextLayout(text, opt.font)
        topt = QTextOption()
        topt.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere if self._view.wrap
                         else QTextOption.WrapMode.NoWrap)
        lay.setTextOption(topt)
        marks = []
        for start, end, fg, bg in spans:
            fr = QTextLayout.FormatRange()
            fr.start, fr.length = int(start), int(end) - int(start)
            fmt = QTextCharFormat()
            fmt.setForeground(QColor(fg))
            fmt.setBackground(QColor(bg))
            fr.format = fmt
            marks.append(fr)
        y = 0.0
        lay.beginLayout()
        while True:
            line = lay.createLine()
            if not line.isValid():
                break
            line.setLineWidth(rect.width() if self._view.wrap else 1.0e7)
            line.setPosition(QPointF(0.0, y))
            y += line.height()
        lay.endLayout()
        top = rect.top() + max(0.0, (rect.height() - y) / 2)          # centred: spacing above and below
        painter.save()
        painter.setClipRect(rect)
        painter.setFont(opt.font)
        painter.setPen(opt.palette.color(QPalette.ColorRole.Text))
        lay.draw(painter, QPointF(rect.left(), top), marks)
        painter.restore()
        self.draw_grid(painter, option.rect)


class LogTableView(QTableView):
    copied = Signal(str)
    mark_double_clicked = Signal(int)          # row whose bookmark-marker cell was double-clicked

    def __init__(self, model, font: QFont, parent=None) -> None:
        super().__init__(parent)
        self.setModel(model)
        self.setFont(font)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setWordWrap(False)
        self.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.setShowGrid(False)
        self._delegate = FieldDelegate(self)
        self.setItemDelegate(self._delegate)

        self.wrap = False
        self.autofit = True
        self._units: list[int] = []        # measured max width (cells) of every field
        self._digits = 6                   # digits of the largest line number
        self._applying = False
        self._touched: set[int] = set()    # rows whose height we changed (wrap mode)
        self.hscroll_target = None         # scroll bar that Shift + wheel moves (the strip uses its table's)
        self._spacing = 4                  # extra pixels per row (theme: "row spacing")
        self._row_h = self.fontMetrics().height() + self._spacing

        vh = self.verticalHeader()
        vh.hide()
        vh.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)     # fixed rows = fast layout
        vh.setDefaultSectionSize(self._row_h)
        hh = self.horizontalHeader()
        hh.setMinimumSectionSize(14)                              # the Bookmark / Pin columns are narrow
        hh.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)   # text starts at the left
        hh.setStretchLastSection(True)
        hh.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.reset_columns()

        self._layout_timer = QTimer(self)
        self._layout_timer.setSingleShot(True)
        self._layout_timer.setInterval(30)
        self._layout_timer.timeout.connect(self._relayout)
        self._rows_timer = QTimer(self)
        self._rows_timer.setSingleShot(True)
        self._rows_timer.setInterval(25)
        self._rows_timer.timeout.connect(self._refit_rows)
        self.verticalScrollBar().valueChanged.connect(self._schedule_rows)
        hh.sectionResized.connect(self._on_section_resized)
        model.rowsInserted.connect(self._schedule_rows)
        model.modelReset.connect(lambda: self._touched.clear())
        model.modelReset.connect(self._schedule_rows)
        model.dataChanged.connect(self._schedule_rows)

    # ----------------------------------------------------------------- modes
    def configure(self, autofit: bool, wrap: bool) -> None:
        self.autofit = bool(autofit)
        self._set_wrap(bool(wrap))
        self._apply_widths()

    def _set_wrap(self, on: bool) -> None:
        changed = on != self.wrap
        self.wrap = on
        self.setWordWrap(on)
        self.setTextElideMode(Qt.TextElideMode.ElideNone if on else Qt.TextElideMode.ElideRight)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff if on
                                          else Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.horizontalHeader().setStretchLastSection(not on and not self.autofit)
        if changed:
            if not on:
                self._restore_rows()
            self._schedule_rows()

    # ----------------------------------------------------------------- theme
    def apply_theme(self, font: QFont, spec: dict, selection: dict, grid: dict) -> None:
        """Font, default colours, selected-row colours and grid lines of this table."""
        self._spacing = int(spec.get("row_spacing", 4))
        self._row_h = QFontMetrics(font).height() + self._spacing
        self._restore_rows()                       # forget wrapped heights: fonts changed
        style_table(self, self._delegate, font, spec, selection, grid, self._row_h)
        self._apply_widths()
        if not (self.autofit or self.wrap):
            self._set_gutter_widths()
        self._schedule_rows()

    # ---------------------------------------------------------------- widths
    def set_line_digits(self, count: int) -> None:
        digits = max(len(str(max(count, 1))), 4)
        if digits != self._digits:
            self._digits = digits
            self._apply_widths()

    def reset_columns(self) -> None:
        """Forget measured widths (new columnizer / new result set): defaults until re-measured."""
        self._units = []
        self._applying = True
        try:
            self._set_gutter_widths()
            for c in range(GUTTER, self.model().columnCount()):
                self.setColumnWidth(c, DEFAULT_FIELD_PX)
        finally:
            self._applying = False
        self._schedule_rows()

    def set_field_units(self, units: list[int], reset: bool = False, force: bool = False) -> None:
        new = list(units) if reset else merge_max(self._units, list(units))
        if new == self._units and not force:
            return
        self._units = new
        self._apply_widths(force)

    def _line_px(self) -> int:
        return self.fontMetrics().horizontalAdvance("0") * (self._digits + 1) + CELL_PAD_PX

    def _mark_px(self) -> int:
        """The Bookmark column is just wide enough for the marker (an emoji is about twice as wide as it is
        tall at the table's font size) plus some air."""
        fm = self.fontMetrics()
        marker = getattr(self.model(), "marker", "") or "\u2605"
        return max(fm.horizontalAdvance(marker) + 18, int(fm.height() * 1.9) + 10, 36)

    def _pin_px(self) -> int:
        return max(self.fontMetrics().height() + 12, 28)

    def _col0_px(self) -> int:
        """Total width of the gutter (Bookmark + Pin + Line)."""
        return self._mark_px() + self._pin_px() + self._line_px()

    def _set_gutter_widths(self) -> None:
        self.setColumnWidth(COL_MARK, self._mark_px())
        self.setColumnWidth(COL_PIN, self._pin_px())
        self.setColumnWidth(COL_LINE, self._line_px())

    def _cell_px(self) -> int:
        fm = self.fontMetrics()
        return max(fm.horizontalAdvance("0"), fm.averageCharWidth(), 1)

    def _apply_widths(self, force: bool = False) -> None:
        if self._applying or not (self.autofit or self.wrap or force):
            return
        nfields = self.model().columnCount() - GUTTER
        if nfields < 1 or not self._units:
            return
        cw = self._cell_px()
        want = []
        for i in range(nfields):
            u = self._units[i] if i < len(self._units) else 0
            want.append(min(MAX_FIELD_PX, max(MIN_FIELD_PX, u * cw + CELL_PAD_PX)))
        col0 = self._col0_px()
        avail = max(self.viewport().width() - col0, MIN_FIELD_PX * nfields)
        if self.wrap:
            widths = fit_to_width(want, avail, MIN_FIELD_PX)
        else:
            widths = want
            widths[-1] = max(widths[-1], avail - sum(widths[:-1]))     # fill the window
        self._applying = True
        try:
            self._set_gutter_widths()
            for i, w in enumerate(widths):
                self.setColumnWidth(i + GUTTER, w)
        finally:
            self._applying = False
        if self.wrap:
            self._schedule_rows()

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._layout_timer.start()

    def _relayout(self) -> None:
        self._apply_widths()
        self._schedule_rows()

    def _on_section_resized(self, *_args) -> None:
        if self.wrap and not self._applying:
            self._schedule_rows()

    # -------------------------------------------------------- wrapped rows
    def _schedule_rows(self, *_args) -> None:
        if self.wrap:
            self._rows_timer.start()

    def _refit_rows(self) -> None:
        """Wrap mode: size the visible rows (plus a margin) to their wrapped text."""
        if not self.wrap:
            return
        model, vh = self.model(), self.verticalHeader()
        total = model.rowCount()
        if total == 0:
            return
        sb = self.verticalScrollBar()
        at_bottom = sb.value() >= sb.maximum() - 2
        fm = self.fontMetrics()
        font = self.font()
        widths = [self.columnWidth(c) - 8 for c in range(model.columnCount())]
        for _pass in range(2):
            first = self.rowAt(0)
            last = self.rowAt(self.viewport().height() - 1)
            first = 0 if first < 0 else first
            last = min(total - 1, first + 60) if last < 0 else last
            changed = False
            for row in range(max(0, first - 4), min(total - 1, last + 4) + 1):
                lines = 1
                for c in range(GUTTER, model.columnCount()):
                    text = model.data(model.index(row, c), Qt.ItemDataRole.DisplayRole) or ""
                    lines = max(lines, wrapped_line_count(text, font, widths[c]))
                h = lines * fm.lineSpacing() + self._spacing
                if vh.sectionSize(row) != h:
                    vh.resizeSection(row, h)
                    self._touched.add(row)
                    changed = True
            if not changed:
                break
        if at_bottom:
            self.scrollToBottom()

    def _restore_rows(self) -> None:
        vh, total = self.verticalHeader(), self.model().rowCount()
        for row in self._touched:
            if row < total:
                vh.resizeSection(row, self._row_h)
        self._touched.clear()

    # ------------------------------------------- scrolling: horizontal = user only
    def scrollTo(self, index, hint=QAbstractItemView.ScrollHint.EnsureVisible) -> None:
        """Bring the row into view vertically, but never move the horizontal scroll bar.

        Qt scrolls sideways to show the clicked / current cell; with wide log lines that makes the view
        jump on every click.  Only the user moves the horizontal scroll bar (bar, wheel, Left / Right)."""
        hbar = self.horizontalScrollBar()
        x = hbar.value()
        super().scrollTo(index, hint)
        if hbar.value() != x:
            hbar.setValue(x)

    def wheelEvent(self, e) -> None:
        """Shift + mouse wheel scrolls sideways (wheel up = left, wheel down = right)."""
        if e.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            bar = self.hscroll_target or self.horizontalScrollBar()
            pixels = e.pixelDelta().y() or e.pixelDelta().x()
            if not pixels:                                   # a normal wheel: 120 units per notch
                units = e.angleDelta().y() or e.angleDelta().x()
                pixels = int(units / 120 * WHEEL_PX_PER_NOTCH * self._cell_px() / 8)
            if pixels:
                bar.setValue(bar.value() - pixels)
            e.accept()
            return
        super().wheelEvent(e)

    def mouseDoubleClickEvent(self, e) -> None:
        idx = self.indexAt(e.position().toPoint())
        if idx.isValid() and idx.column() == COL_MARK:
            self.mark_double_clicked.emit(idx.row())
            e.accept()
            return
        super().mouseDoubleClickEvent(e)

    # ----------------------------------------------------------------- copy
    def keyPressEvent(self, e) -> None:
        if e.key() == Qt.Key.Key_C and e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.copy_selected_lines()
            return
        if e.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right) and e.modifiers() == Qt.KeyboardModifier.NoModifier \
                and self.state() != QAbstractItemView.State.EditingState:
            bar = self.horizontalScrollBar()                      # the cursor keys scroll sideways, on request only
            step = bar.singleStep() * 4
            bar.setValue(bar.value() + (step if e.key() == Qt.Key.Key_Right else -step))
            e.accept()
            return
        super().keyPressEvent(e)

    def copy_selected_lines(self) -> None:
        rows = sorted({i.row() for i in self.selectedIndexes()})[:MAX_COPY_ROWS]
        model = self.model()
        if not rows or not hasattr(model, "line_text"):
            return
        QGuiApplication.clipboard().setText("\n".join(model.line_text(r) for r in rows))
        self.copied.emit(f"Copied {len(rows):,} line(s) to the clipboard")
