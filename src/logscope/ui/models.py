"""Virtualised table model: only visible rows are ever read from disk."""

from __future__ import annotations

from collections import OrderedDict

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import QBrush, QColor

from ..core.bookmarks import tags_text
from ..core.theme import contrast_fg

COL_MARK, COL_PIN, COL_LINE = 0, 1, 2     # the three gutter columns in front of the log fields
GUTTER = 3                                  # index of the first field column
FULL_ROLE = Qt.ItemDataRole.UserRole + 1     # complete cell text (see ui.tableview)
SPANS_ROLE = Qt.ItemDataRole.UserRole + 2    # [(start, end, fg, bg)] of highlighted words in a cell
BOOKMARK_TINT = ("#dbe9ff", "#000000")        # first-column colours of a bookmarked line
ROW_CACHE = 4096
MAX_CELL = 4000          # characters shown per cell (full text in the tooltip)


class LogTableModel(QAbstractTableModel):
    def __init__(self, reader, view, columnizer, highlight, bookmarks: dict,
                 initial_rows: int = 0, parent=None) -> None:
        super().__init__(parent)
        self.reader = reader
        self.view = view
        self.columnizer = columnizer
        self.highlight = highlight
        self.bookmarks = bookmarks
        self._rows = initial_rows
        self.marker = "\u2605"                 # default bookmark marker (theme)
        self.pinned: set[int] = set()          # sticky lines of this panel (shared with its sticky strip)
        self.on_pin = None                     # callable(line, on) -> the panel applies a tick in the Pin column
        self.row_style = None                  # callable(line) -> (fg, soft bg, solid bg) | None (merged tabs)
        self.source_col = -1                   # column that gets the solid background
        self._cols = columnizer.column_names()
        self._cache: OrderedDict[int, tuple] = OrderedDict()

    # ------------------------------------------------------------ Qt model API
    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else self._rows

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else GUTTER + len(self._cols)

    def flags(self, index):
        base = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if not index.isValid():
            return base
        if index.column() == COL_PIN:
            return base | Qt.ItemFlag.ItemIsUserCheckable
        if index.column() == COL_MARK:
            return base                                                        # double-click toggles the bookmark
        return base | Qt.ItemFlag.ItemIsEditable                               # editor = read-only viewer

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation != Qt.Orientation.Horizontal:
            return None
        if role == Qt.ItemDataRole.TextAlignmentRole:
            if section < COL_LINE:
                return int(Qt.AlignmentFlag.AlignCenter)
            return int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)     # "Message" stays visible
        if role == Qt.ItemDataRole.DisplayRole:
            if section == COL_MARK:
                return self.marker
            if section == COL_PIN:
                return "\u2191"
            return "Line" if section == COL_LINE else self._cols[section - GUTTER]
        if role == Qt.ItemDataRole.ToolTipRole:
            if section == COL_MARK:
                return "Bookmark - double-click a cell to set or remove a bookmark on that line"
            if section == COL_PIN:
                return "Sticky - tick a line to keep it at the top of this panel while you scroll"
        return None

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole) -> bool:
        """A tick in the Pin column makes the line sticky (the panel owns the set)."""
        if role != Qt.ItemDataRole.CheckStateRole or not index.isValid() or index.column() != COL_PIN \
                or index.row() >= self._rows or self.on_pin is None:
            return False
        on = value == Qt.CheckState.Checked or value == Qt.CheckState.Checked.value
        self.on_pin(self.view.line_number(index.row()), bool(on))
        return True

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        row, col = index.row(), index.column()
        if row >= self._rows:
            return None
        ln = self.view.line_number(row)

        if role == Qt.ItemDataRole.CheckStateRole:
            if col != COL_PIN:
                return None
            return Qt.CheckState.Checked if ln in self.pinned else Qt.CheckState.Unchecked
        k = col - GUTTER                                   # index of the field, negative in the gutter
        if role == Qt.ItemDataRole.DisplayRole:
            if col == COL_MARK:
                bm = self.bookmarks.get(ln)
                return "" if bm is None else (bm.marker or self.marker)
            if col == COL_PIN:
                return ""
            if col == COL_LINE:
                return str(ln + 1)
            cells = self._info(ln)[1]
            return cells[k][:MAX_CELL] if k < len(cells) else ""
        if role == SPANS_ROLE:
            if k < 0 or not self.highlight.has_word_rules:
                return None
            cells = self._info(ln)[1]
            return (self.highlight.spans(cells[k][:MAX_CELL]) or None) if k < len(cells) else None
        if role == FULL_ROLE:
            if col == COL_LINE:
                return str(ln + 1)
            if k < 0:
                return ""
            cells = self._info(ln)[1]
            return cells[k] if k < len(cells) else ""
        if role == Qt.ItemDataRole.BackgroundRole:
            if col in (COL_MARK, COL_LINE) and ln in self.bookmarks:
                return QBrush(QColor(BOOKMARK_TINT[0]))
            rule = self._info(ln)[2]
            if rule is not None:
                return QBrush(QColor(rule.bg))
            style = self.row_style(ln) if self.row_style is not None else None
            if style is not None:
                return QBrush(QColor(style[2] if col == self.source_col else style[1]))
            return None
        if role == Qt.ItemDataRole.ForegroundRole:
            if col in (COL_MARK, COL_LINE) and ln in self.bookmarks:
                return QBrush(QColor(BOOKMARK_TINT[1]))
            rule = self._info(ln)[2]
            if rule is not None:
                return QBrush(QColor(rule.fg))
            style = self.row_style(ln) if self.row_style is not None else None
            if style is not None:
                return QBrush(QColor(contrast_fg(style[2]) if col == self.source_col else style[0]))
            return None
        if role == Qt.ItemDataRole.TextAlignmentRole:
            if col == COL_LINE:
                return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            if col < COL_LINE:
                return int(Qt.AlignmentFlag.AlignCenter)
            return None
        if role == Qt.ItemDataRole.ToolTipRole and col == COL_MARK:
            bm = self.bookmarks.get(ln)
            if bm is None:
                return "Double-click to bookmark this line"
            parts = [f"Bookmark: {bm.comment}" if bm.comment else "Bookmark"]
            if bm.tags:
                parts.append("Tags: " + tags_text(bm.tags))
            return "\n".join(parts)
        if role == Qt.ItemDataRole.ToolTipRole and col == COL_PIN:
            return "Sticky: stays at the top of this panel" if ln in self.pinned else "Tick to keep this line at the top"
        if role == Qt.ItemDataRole.ToolTipRole and col >= GUTTER:
            return self._info(ln)[0][:MAX_CELL]
        return None

    # ------------------------------------------------------------- row cache
    def _info(self, ln: int) -> tuple:
        hit = self._cache.get(ln)
        if hit is not None:
            return hit
        text = self.reader.get_line(ln)
        try:
            cells = self.columnizer.split_at(ln, text)
        except Exception:                      # a faulty plugin must not break painting
            cells = [text]
        info = (text, cells, self.highlight.match(text))
        self._cache[ln] = info
        if len(self._cache) > ROW_CACHE:
            self._cache.popitem(last=False)
        return info

    def line_text(self, row: int) -> str:
        return self._info(self.view.line_number(row))[0]

    # ------------------------------------------------------- change notifiers
    def lines_changed(self, first_line: int, total: int) -> None:
        """Reader reported: lines >= first_line are new/changed; total lines now."""
        old = self._rows
        self._cache.clear()
        if first_line < old and old > 0:
            self.dataChanged.emit(self.index(first_line, 0),
                                  self.index(old - 1, self.columnCount() - 1))
        if total > old:
            self.beginInsertRows(QModelIndex(), old, total - 1)
            self._rows = total
            self.endInsertRows()

    def append_rows(self, count: int) -> None:
        if count <= 0:
            return
        self.beginInsertRows(QModelIndex(), self._rows, self._rows + count - 1)
        self._rows += count
        self.endInsertRows()

    def remove_tail(self, count: int) -> None:
        if count <= 0:
            return
        self._cache.clear()
        self.beginRemoveRows(QModelIndex(), self._rows - count, self._rows - 1)
        self._rows -= count
        self.endRemoveRows()

    def reset_rows(self, rows: int = 0) -> None:
        self.beginResetModel()
        self._cache.clear()
        self._rows = rows
        self.endResetModel()

    def refresh_all(self) -> None:
        """Highlight rules / bookmarks changed: repaint everything visible."""
        self._cache.clear()
        if self._rows:
            self.dataChanged.emit(self.index(0, 0),
                                  self.index(self._rows - 1, self.columnCount() - 1))

    def set_columnizer(self, columnizer) -> None:
        self.beginResetModel()
        self.columnizer = columnizer
        self._cols = columnizer.column_names()
        self._cache.clear()
        self.endResetModel()
