"""Filtering and searching (FilterParams / SearchParams equivalents)."""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass
from typing import Callable

import numpy as np

from .reader import LogFileReader

BLOCK = 8192


@dataclass
class FilterParams:
    text: str = ""
    regex: bool = False
    case_sensitive: bool = False
    invert: bool = False
    context: int = 0           # "spread": extra lines around matches


class LineMatcher:
    def __init__(self, text: str, regex: bool = False, case_sensitive: bool = False,
                 invert: bool = False) -> None:
        self.text, self.regex, self.case, self.invert = text, regex, case_sensitive, invert
        if regex:
            self._rx = re.compile(text, 0 if case_sensitive else re.IGNORECASE)
            self._needle = None
        else:
            self._rx = None
            self._needle = text if case_sensitive else text.lower()

    @classmethod
    def from_params(cls, p: FilterParams) -> "LineMatcher":
        return cls(p.text, p.regex, p.case_sensitive, p.invert)

    def match(self, line: str) -> bool:
        if self._rx is not None:
            hit = self._rx.search(line) is not None
        elif self.case:
            hit = self._needle in line
        else:
            hit = self._needle in line.lower()
        return hit != self.invert

    def indices(self, lines: list[str]) -> list[int]:
        inv = self.invert
        if self._rx is not None:
            s = self._rx.search
            return [i for i, l in enumerate(lines) if (s(l) is not None) != inv]
        n = self._needle
        if self.case:
            return [i for i, l in enumerate(lines) if (n in l) != inv]
        return [i for i, l in enumerate(lines) if (n in l.lower()) != inv]

    def fast_needle(self, codec: str) -> tuple[bytes, bool] | None:
        """``(needle_bytes, ascii_case_insensitive)`` when a byte-level pre-search
        is safe, else None.  Candidates are always re-checked on decoded text."""
        if self.regex or self.invert or not self.text:
            return None
        try:
            if self.case:
                return self.text.encode(codec), False
            if self.text.isascii() and not codec.startswith("utf-16"):
                return self.text.lower().encode(codec), True
        except (UnicodeEncodeError, LookupError):
            pass
        return None


def match_block(matcher: LineMatcher, blk) -> list[int]:
    """Indices (within the block) of matching lines.

    Plain-text filters search the raw bytes and decode only candidate lines,
    which is several times faster than decoding every line."""
    fast = matcher.fast_needle(blk.codec)
    if fast is None:
        return matcher.indices(blk.lines())
    needle, ci = fast
    raw = blk.raw.lower() if ci else blk.raw
    pos = raw.find(needle)
    if pos < 0:
        return []
    unit = 2 if blk.codec.startswith("utf-16") else 1
    starts = np.asarray(blk.starts, dtype=np.int64) - blk.lo
    cand: list[int] = []
    while pos >= 0:
        if pos % unit:
            pos = raw.find(needle, pos + 1)
            continue
        li = int(np.searchsorted(starts, pos, side="right")) - 1
        cand.append(li)
        nxt = int(starts[li + 1]) if li + 1 < starts.size else len(raw)
        pos = raw.find(needle, nxt)
    bounds = blk.starts + [blk.end]
    out = []
    for li in cand:
        piece = blk.raw[bounds[li] - blk.lo: bounds[li + 1] - blk.lo]
        if matcher.match(piece.decode(blk.codec, "replace").rstrip("\r\n")):
            out.append(li)
    return out


def scan_matches(reader: LogFileReader, matcher: LineMatcher, start: int, stop: int,
                 cancel: threading.Event, emit: Callable[[np.ndarray], None],
                 context: int = 0, block: int = BLOCK, emit_from: int | None = None) -> bool:
    """Scan lines ``[start, stop)`` and ``emit`` ascending arrays of matching
    line numbers (expanded by ``context`` lines).  Lines below ``emit_from``
    (default: ``start``) are never emitted.  Returns False if cancelled."""
    fh = reader.new_scan_handle()
    pending: list[np.ndarray] = []
    pending_n = 0
    last_emit = time.monotonic()
    last_out = (start if emit_from is None else emit_from) - 1   # highest line already emitted
    try:
        for a in range(start, stop, block):
            if cancel.is_set():
                return False
            b = min(a + block, stop)
            blk = reader.fetch(a, b, fh)
            if blk is None:
                continue
            hits = match_block(matcher, blk)
            if hits:
                found = np.asarray(hits, dtype=np.int64) + a
                if context > 0:
                    found = _expand(found, context, last_out, stop)
                    if found.size:
                        last_out = int(found[-1])
                else:
                    found = found[found > last_out]
                    if found.size:
                        last_out = int(found[-1])
                if found.size:
                    pending.append(found)
                    pending_n += found.size
            if pending and (pending_n >= 5000 or time.monotonic() - last_emit > 0.1):
                emit(np.concatenate(pending))
                pending, pending_n, last_emit = [], 0, time.monotonic()
        if pending:
            emit(np.concatenate(pending))
        return True
    finally:
        fh.close()


def plan_incremental(scanned: int, dirty_from: int | None, context: int) -> tuple[int, int, int]:
    """How to extend a live filter / search result over lines appended (or rewritten) after
    the first ``scanned`` lines were processed.

    Returns ``(drop_from, scan_from, emit_from)``: delete result entries >= drop_from, then
    scan from ``scan_from`` emitting only line numbers >= ``emit_from`` (== drop_from).
    With context lines, lines near the old end are re-evaluated because new matches add
    leading context there and the last matches' trailing context was cut off."""
    base = scanned if dirty_from is None else min(scanned, max(dirty_from, 0))
    drop = max(0, base - context)
    return drop, max(0, drop - context), drop


def _expand(found: np.ndarray, ctx: int, last_out: int, stop: int) -> np.ndarray:
    out: list[np.ndarray] = []
    for m in found.tolist():
        lo = max(m - ctx, last_out + 1)
        hi = min(m + ctx, stop - 1)
        if lo <= hi:
            out.append(np.arange(lo, hi + 1, dtype=np.int64))
            last_out = hi
    return np.concatenate(out) if out else np.empty(0, dtype=np.int64)


def find_next(reader: LogFileReader, matcher: LineMatcher, from_line: int, forward: bool,
              cancel: threading.Event, block: int = BLOCK) -> int:
    """First matching line strictly after/before ``from_line`` (wraps around).

    Returns -1 when nothing matches or the search was cancelled."""
    total = reader.count
    if total == 0:
        return -1
    fh = reader.new_scan_handle()
    try:
        if forward:
            first = min(max(from_line + 1, 0), total)
            ranges = [(first, total), (0, min(first, total))]
            for lo, hi in ranges:
                for a in range(lo, hi, block):
                    if cancel.is_set():
                        return -1
                    r = _first_in(reader, matcher, a, min(a + block, hi), fh, True)
                    if r >= 0:
                        return r
        else:
            last = min(max(from_line, 0), total)
            ranges = [(0, last), (last, total)]
            for lo, hi in ranges:
                b = hi
                while b > lo:
                    if cancel.is_set():
                        return -1
                    a = max(lo, b - block)
                    r = _first_in(reader, matcher, a, b, fh, False)
                    if r >= 0:
                        return r
                    b = a
        return -1
    finally:
        fh.close()


def _first_in(reader, matcher, a, b, fh, forward) -> int:
    blk = reader.fetch(a, b, fh)
    if blk is None:
        return -1
    hits = match_block(matcher, blk)
    if not hits:
        return -1
    return a + (hits[0] if forward else hits[-1])


ROW_BLOCK = 4096


def find_in_rows(reader: LogFileReader, matcher: LineMatcher, lines: np.ndarray, start_row: int,
                 forward: bool, cancel: threading.Event) -> tuple[int, bool]:
    """Next / previous *row* of a result list whose line matches, wrapping around.

    ``lines`` are the line numbers of the rows; ``start_row`` is the current row (-1 = none).
    Returns ``(row, wrapped)``; ``row`` is -1 when nothing matches or the search was cancelled."""
    from .derive import fetch_sorted                      # local import: derive needs no search code
    lines = np.asarray(lines, dtype=np.int64)
    n = int(lines.size)
    if n == 0:
        return -1, False
    fh = reader.new_scan_handle()

    def texts(a: int, b: int) -> list[str]:
        part = lines[a:b]
        if part.size > 1 and not np.all(np.diff(part) > 0):          # not ascending: line by line
            return [reader.get_line(int(x)) for x in part.tolist()]
        return fetch_sorted(reader, fh, part)

    try:
        if forward:
            first = min(max(start_row + 1, 0), n)
            spans = [(first, n, False), (0, first, True)]
            for lo, hi, wrapped in spans:
                for a in range(lo, hi, ROW_BLOCK):
                    if cancel.is_set():
                        return -1, False
                    hits = matcher.indices(texts(a, min(a + ROW_BLOCK, hi)))
                    if hits:
                        return a + hits[0], wrapped
        else:
            last = min(max(start_row, 0), n) if start_row >= 0 else n
            spans = [(0, last, False), (last, n, True)]
            for lo, hi, wrapped in spans:
                b = hi
                while b > lo:
                    if cancel.is_set():
                        return -1, False
                    a = max(lo, b - ROW_BLOCK)
                    hits = matcher.indices(texts(a, b))
                    if hits:
                        return a + hits[-1], wrapped
                    b = a
        return -1, False
    finally:
        try:
            fh.close()
        except OSError:
            pass


MAX_QUERY = 500


def selection_to_query(text: str, regex: bool = False) -> str:
    """Text selected on screen -> what Ctrl+F puts in the find box.

    A selection over several lines gives its first non-empty line; the text is trimmed and capped.
    With the Regex option on it is escaped, so special characters such as ``[+]`` are found literally."""
    import re

    for line in (text or "").replace("\u2029", "\n").replace("\u2028", "\n").replace("\r", "\n").split("\n"):
        line = line.strip()
        if line:
            q = line[:MAX_QUERY]
            return re.escape(q) if regex else q
    return ""
