"""Time alignment between several log files (Qt-free).

* :func:`nearest_line`  -- the line of a file whose timestamp is closest to a given time
  (binary search that parses only a few dozen lines, so it is instant on a 500 MB file);
* :func:`scan_times` / :func:`effective_ms` -- timestamps of *every* line, filled in for
  lines without one (continuation lines, stack traces) and made non-decreasing;
* :func:`merge_order` -- the interleaving of several files by time (millisecond resolution);
  lines with the same millisecond stay together in one block per source file;
* :func:`parse_offset` -- clock offsets such as ``+01:00`` or ``-250ms`` for logs written
  with different clocks / zones.
"""

from __future__ import annotations

import re
import threading
from typing import Callable

import numpy as np

SCAN = 256                      # lines looked at to find the nearest stamped line
BLOCK = 8192
TIME_ONLY_LIMIT = 86_400 * 366  # smaller values are "time of day" (formats without a date)
MAX_OFFSET = 10 * 366 * 86_400.0


def is_time_only(seconds: float) -> bool:
    return seconds < TIME_ONLY_LIMIT


# ------------------------------------------------------------------ offsets
_CLOCK = re.compile(r"^([+-])?(\d{1,4}):(\d{1,2})(?::(\d{1,2}(?:\.\d+)?))?$")
_UNITS = re.compile(r"^([+-]?\d+(?:\.\d+)?)\s*(ms|s|m|h|d)?$", re.IGNORECASE)
_FACTOR = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0, "d": 86400.0, None: 1.0}


def parse_offset(text: str) -> float:
    """'' -> 0; '+01:00', '-1:30:15.5', '3600', '-250ms', '1.5h' -> seconds (raises ValueError)."""
    t = (text or "").strip().replace(",", ".")
    if not t:
        return 0.0
    m = _CLOCK.match(t)
    if m:
        sign = -1.0 if m.group(1) == "-" else 1.0
        h, mi, s = int(m.group(2)), int(m.group(3)), float(m.group(4) or 0)
        if mi > 59 or s >= 60:
            raise ValueError("minutes and seconds must be below 60")
        v = sign * (h * 3600 + mi * 60 + s)
    else:
        m = _UNITS.match(t)
        if not m:
            raise ValueError("use e.g. +01:00, -00:00:02.5, 3600, -250ms, 1.5h")
        v = float(m.group(1)) * _FACTOR[(m.group(2) or "").lower() or None]
    if abs(v) > MAX_OFFSET:
        raise ValueError("offset is too large")
    return v


def format_offset(seconds: float) -> str:
    sign = "-" if seconds < 0 else "+"
    ms = int(round(abs(seconds) * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{sign}{h:02d}:{m:02d}:{s:02d}" + (f".{ms:03d}" if ms else "")


# --------------------------------------------------------- nearest line
def nearest_line(get_line: Callable[[int], str], count: int, parser, target: float,
                 scan: int = SCAN) -> tuple[int, float] | None:
    """``(line, its_time)`` of the stamped line closest to ``target`` (seconds, as the parser
    reports them), or None when no line near the probed positions carries a timestamp.

    Assumes the log is roughly in time order; out-of-order stamps only make the answer approximate."""
    if count <= 0:
        return None
    memo: dict[int, float | None] = {}

    def stamp(i: int) -> float | None:
        if i not in memo:
            memo[i] = parser.seconds(get_line(i))
        return memo[i]

    def forward(i: int, limit: int):
        for k in range(i, min(limit, i + scan)):
            t = stamp(k)
            if t is not None:
                return k, t
        return None

    def backward(i: int):
        for k in range(i, max(-1, i - scan), -1):
            t = stamp(k)
            if t is not None:
                return k, t
        return None

    lo, hi = 0, count
    while lo < hi:
        mid = (lo + hi) // 2
        r = forward(mid, hi)
        if r is None:
            hi = mid
        elif r[1] < target:
            lo = r[0] + 1
        else:
            hi = mid
    nxt = forward(lo, count) if lo < count else None
    prv = backward(lo - 1) if lo > 0 else None
    if nxt is None:
        return prv
    if prv is None:
        return nxt
    return nxt if abs(nxt[1] - target) < abs(target - prv[1]) else prv


# ----------------------------------------------------------- all timestamps
def scan_times(reader, parser, upto: int | None = None, cancel: threading.Event | None = None,
               progress: Callable[[int], None] | None = None) -> np.ndarray:
    """Timestamp (seconds, float64) of every line; NaN where a line has none."""
    n = reader.count if upto is None else min(upto, reader.count)
    out = np.full(n, np.nan, dtype=np.float64)
    seconds = parser.seconds
    fh = reader.new_scan_handle()
    try:
        for a in range(0, n, BLOCK):
            if cancel is not None and cancel.is_set():
                raise InterruptedError("cancelled")
            blk = reader.fetch(a, min(a + BLOCK, n), fh)
            if blk is None:
                continue
            for i, text in enumerate(blk.lines()):
                v = seconds(text)
                if v is not None:
                    out[a + i] = v
            if progress is not None:
                progress(min(a + BLOCK, n))
    finally:
        try:
            fh.close()
        except OSError:
            pass
    return out


def effective_ms(times: np.ndarray, offset: float = 0.0) -> np.ndarray:
    """Millisecond time of every line, never decreasing: lines without a timestamp take the one
    of the line before them (leading ones the first stamp), out-of-order stamps are lifted to the
    running maximum.  Raises ValueError when no line has a timestamp."""
    n = int(times.size)
    if n == 0:
        return np.empty(0, dtype=np.int64)
    ok = ~np.isnan(times)
    if not ok.any():
        raise ValueError("no line has a timestamp")
    idx = np.maximum.accumulate(np.where(ok, np.arange(n), 0))
    filled = times[idx]
    first = int(np.argmax(ok))
    filled[:first] = times[first]
    ms = np.rint((filled + offset) * 1000.0).astype(np.int64)
    return np.maximum.accumulate(ms)


def merge_order(ms_list: list[np.ndarray]) -> tuple[np.ndarray, np.ndarray]:
    """Interleave several files by time.

    ``ms_list[k]`` are the (non-decreasing) millisecond times of file ``k``'s lines.  Returns
    ``(file_index, line_number)`` arrays in merged order.  Each file's own line order is kept.
    Lines with the same millisecond are grouped into one block per file; the file that supplied the
    previous line continues first, so blocks are split as rarely as possible."""
    k = len(ms_list)
    sizes = [int(m.size) for m in ms_list]
    total = sum(sizes)
    if total == 0:
        return np.empty(0, dtype=np.int16), np.empty(0, dtype=np.int64)
    ms = np.concatenate(ms_list).astype(np.int64, copy=False)
    fi = np.repeat(np.arange(k, dtype=np.int16), sizes)
    li = np.concatenate([np.arange(s, dtype=np.int64) for s in sizes])
    order = np.lexsort((li, fi, ms))            # by time, then file, then line
    ms, fi, li = ms[order], fi[order], li[order]
    del order
    change = np.flatnonzero(np.diff(ms)) + 1
    starts = np.concatenate(([0], change))
    ends = np.concatenate((change, [total]))
    for g in np.flatnonzero(fi[starts] != fi[ends - 1]).tolist():       # groups with several files
        s, e = int(starts[g]), int(ends[g])
        if s == 0:
            continue
        seg = fi[s:e]
        mine = seg == fi[s - 1]
        if seg[0] == fi[s - 1] or not mine.any():
            continue
        idx = np.concatenate((np.flatnonzero(mine), np.flatnonzero(~mine))) + s
        fi[s:e] = fi[idx]
        li[s:e] = li[idx]
    return fi, li


def time_near(get_line: Callable[[int], str], count: int, parser, line: int,
              scan: int = SCAN) -> float | None:
    """Timestamp of ``line``, or of the closest stamped line just before (else just after) it."""
    if not parser.usable or not 0 <= line < count:
        return None
    for k in range(line, max(-1, line - scan), -1):
        t = parser.seconds(get_line(k))
        if t is not None:
            return t
    for k in range(line + 1, min(count, line + scan)):
        t = parser.seconds(get_line(k))
        if t is not None:
            return t
    return None
