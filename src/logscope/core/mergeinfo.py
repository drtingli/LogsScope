"""What a merged tab knows about where its lines came from (Qt-free)."""

from __future__ import annotations

import weakref

import numpy as np

from .theme import blend, contrast_fg

SOFT = 0.28            # how much of the tab colour tints the lines of that file
CODES = ("#e74c3c", "#3498db", "#2ecc71", "#f39c12", "#9b59b6", "#1abc9c", "#fd79a8", "#95a5a6",
         "#e67e22", "#16a085")      # given to linked tabs that have no colour yet


class MergeSource:
    """One file that took part in a merge.  ``color`` follows the live tab while it is open."""

    def __init__(self, path: str, title: str, color: str, tab=None) -> None:
        self.path, self.title, self.color = path, title, color
        self.tab_ref = weakref.ref(tab) if tab is not None else None

    def tab(self):
        return self.tab_ref() if self.tab_ref is not None else None

    def refresh(self) -> None:
        t = self.tab()
        if t is not None:
            self.color, self.title = t.tab_color, t.title


class MergeInfo:
    """``file_idx[n]`` = index into ``sources`` of merged line ``n`` (``origin[n]`` is its line there)."""

    def __init__(self, sources: list[MergeSource], file_idx: np.ndarray) -> None:
        self.sources = sources
        self.file_idx = file_idx
        self._styles: list[tuple[str, str, str] | None] = [None] * len(sources)

    def source_of(self, line: int) -> MergeSource | None:
        if 0 <= line < self.file_idx.size:
            return self.sources[int(self.file_idx[line])]
        return None

    def path_of(self, line: int) -> str:
        s = self.source_of(line)
        return s.path if s is not None else ""

    def counts(self) -> list[int]:
        return np.bincount(self.file_idx.astype(np.int64), minlength=len(self.sources)).tolist()

    def update_styles(self, base_bg: str, base_fg: str) -> None:
        """Colours of the lines of each file: (text, soft background, solid background).

        The text colour is the panel's own unless the tinted background would make it unreadable."""
        for i, s in enumerate(self.sources):
            s.refresh()
            if not s.color:
                self._styles[i] = None
                continue
            soft = blend(s.color, base_bg, SOFT)
            self._styles[i] = (contrast_fg(soft, base_fg), soft, s.color)

    def style(self, line: int):
        if 0 <= line < self.file_idx.size:
            st = self._styles[int(self.file_idx[line])]
            if st is not None:
                return st
        return None

    def solid_text(self, source_index: int) -> str:
        s = self.sources[source_index]
        return contrast_fg(s.color) if s.color else ""
