"""Column-width measuring (Qt-free).  Widths are in *character cells*; a CJK character
counts as two cells.  The UI multiplies by the font's cell width.

A whole 500 MB file is never decoded just to size columns: byte lengths come from the
line index (free), the longest lines plus an even sample are decoded and split, and
lines that are new since the last measurement are always included."""

from __future__ import annotations

import threading
import unicodedata

import numpy as np

MAX_CELL = 4000            # the table shows at most this many characters per cell
TOP_LONGEST = 1500
SAMPLES = 8000
DEEP_SAMPLES = 150_000


def display_width(s: str) -> int:
    if s.isascii():
        return len(s)
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)


def merge_max(a: list[int], b: list[int]) -> list[int]:
    n = max(len(a), len(b))
    return [max(a[i] if i < len(a) else 0, b[i] if i < len(b) else 0) for i in range(n)]


def pick_candidates(lengths: np.ndarray, top_k: int = TOP_LONGEST, samples: int = SAMPLES) -> np.ndarray:
    """Indices (ascending) of the lines worth decoding: all of them when there are few,
    else the ``top_k`` longest plus ``samples`` evenly spread ones."""
    n = int(lengths.size)
    if n <= top_k + samples:
        return np.arange(n, dtype=np.int64)
    top = np.argpartition(lengths, n - top_k)[n - top_k:]
    spread = np.linspace(0, n - 1, samples).astype(np.int64)
    return np.union1d(top, spread)


def measure_columns(reader, columnizer, lines: np.ndarray, cancel: threading.Event | None = None,
                    deep: bool = False) -> list[int]:
    """Max display width (cells) of every field of ``columnizer`` over ``lines``."""
    lines = np.asarray(lines, dtype=np.int64)
    ncols = len(columnizer.column_names())
    widths = [0] * ncols
    if lines.size == 0:
        return widths
    lengths = reader.line_lengths(lines)
    samples = DEEP_SAMPLES if deep else SAMPLES
    chosen = lines[pick_candidates(lengths, TOP_LONGEST, samples)]
    fh = reader.new_scan_handle()
    try:
        for i, ln in enumerate(chosen.tolist()):
            if cancel is not None and (i & 255) == 0 and cancel.is_set():
                break
            blk = reader.fetch(ln, ln + 1, fh)
            if blk is None:
                continue
            text = blk.lines()[0] if blk.starts else ""
            try:
                cells = columnizer.split_at(ln, text)
            except Exception:
                cells = [text]
            for c, cell in enumerate(cells[:ncols]):
                w = display_width(cell[:MAX_CELL])
                if w > widths[c]:
                    widths[c] = w
    finally:
        fh.close()
    return widths


def fit_to_width(desired: list[int], total: int, min_w: int = 48) -> list[int]:
    """Pixel widths for the fields so they fit into ``total`` pixels.  Narrow fields keep what
    they need; the remaining space is shared equally by the wide ones (they will wrap).
    If everything fits, the spare room goes to the last field."""
    n = len(desired)
    if n == 0:
        return []
    want = [max(min_w, d) for d in desired]
    total = max(total, min_w * n)
    if sum(want) <= total:
        out = list(want)
        out[-1] += total - sum(want)
        return out
    out = [0] * n
    remaining, left = total, n
    for i in sorted(range(n), key=lambda k: want[k]):
        share = remaining // left
        out[i] = min(want[i], max(share, min_w))
        remaining -= out[i]
        left -= 1
    return out
