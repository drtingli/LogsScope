"""Row -> line-number mappings used by the table models."""

from __future__ import annotations

import numpy as np


class FullView:
    """Every line of the file, in order."""

    is_full = True

    def line_number(self, row: int) -> int:
        return row

    def row_of_line(self, line: int) -> int:
        return line


class ListView:
    """A subset of lines (filter result)."""

    is_full = False

    def __init__(self, lines: np.ndarray | None = None) -> None:
        self._a = np.empty(1024, dtype=np.int64)
        self.n = 0
        if lines is not None:
            self.append(lines)

    def clear(self) -> None:
        self.n = 0

    def append(self, arr: np.ndarray) -> None:
        k = int(arr.size)
        if k == 0:
            return
        need = self.n + k
        if need > self._a.size:
            cap = self._a.size
            while cap < need:
                cap *= 2
            g = np.empty(cap, dtype=np.int64)
            g[: self.n] = self._a[: self.n]
            self._a = g
        self._a[self.n:need] = arr
        self.n = need

    def drop_from(self, line: int) -> int:
        """Remove entries with line number >= ``line``; return how many."""
        keep = int(np.searchsorted(self._a[: self.n], line, side="left"))
        removed = self.n - keep
        self.n = keep
        return removed

    def line_number(self, row: int) -> int:
        return int(self._a[row])

    def row_of_line(self, line: int) -> int:
        """Row of the first entry >= line (clamped), -1 if empty."""
        if self.n == 0:
            return -1
        r = int(np.searchsorted(self._a[: self.n], line, side="left"))
        return min(r, self.n - 1)

    def snapshot(self) -> np.ndarray:
        return self._a[: self.n].copy()

    def slice(self, a: int, b: int) -> np.ndarray:
        """Line numbers of rows ``[a, b)`` (clamped), as a copy."""
        return self._a[max(a, 0): min(b, self.n)].copy()
