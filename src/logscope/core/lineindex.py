"""Growable int64 array holding the byte offset at which every line starts."""

from __future__ import annotations

import numpy as np


class LineIndex:
    def __init__(self, capacity: int = 1 << 16) -> None:
        self._a = np.empty(capacity, dtype=np.int64)
        self.count = 0

    def clear(self) -> None:
        self.count = 0

    def append(self, offsets: np.ndarray) -> None:
        n = int(offsets.size)
        if n == 0:
            return
        need = self.count + n
        if need > self._a.size:
            cap = self._a.size
            while cap < need:
                cap *= 2
            grown = np.empty(cap, dtype=np.int64)
            grown[: self.count] = self._a[: self.count]
            self._a = grown
        self._a[self.count:need] = offsets
        self.count = need

    def pop(self) -> int:
        self.count -= 1
        return int(self._a[self.count])

    def get(self, i: int) -> int:
        return int(self._a[i])

    def slice(self, a: int, b: int) -> np.ndarray:
        return self._a[a:b]
