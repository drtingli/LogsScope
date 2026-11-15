"""Incremental, index-based log file reader (the LogfileReader equivalent).

* Never loads the file into memory: only a numpy array of line start offsets
  is kept (8 bytes per line) and visible lines are fetched on demand through
  a small block cache.
* ``poll()`` is incremental: it only scans bytes appended since the last call
  and detects truncation and rotation (file replaced).
* The newest line may still be incomplete (no newline yet).  It is shown as a
  *provisional* line and re-read on the next poll.
"""

from __future__ import annotations

import codecs
import os
import threading
from collections import OrderedDict
from typing import NamedTuple

import numpy as np

from . import winio
from .lineindex import LineIndex

CHUNK_BYTES = 4 * 1024 * 1024
BLOCK_LINES = 256
CACHE_BLOCKS = 256


class PollResult(NamedTuple):
    kind: str          # "none" | "appended" | "reset"
    first_changed: int  # first line number whose content is new/changed
    count: int         # total number of lines after the poll
    more: bool         # stopped early because max_bytes was reached


def detect_encoding(sample: bytes) -> tuple[str, int]:
    """Return ``(codec, bom_length)`` for the beginning of a file."""
    if sample.startswith(b"\xef\xbb\xbf"):
        return "utf-8", 3
    if sample.startswith(b"\xff\xfe") and not sample.startswith(b"\xff\xfe\x00\x00"):
        return "utf-16-le", 2
    if sample.startswith(b"\xfe\xff"):
        return "utf-16-be", 2
    for codec in ("utf-8", "gbk"):
        try:
            codecs.getincrementaldecoder(codec)().decode(sample, final=False)
            return codec, 0
        except UnicodeDecodeError:
            continue
    return "latin-1", 0


class Block:
    """Raw bytes of a run of lines plus the offsets needed to split them."""

    __slots__ = ("raw", "starts", "lo", "end", "codec")

    def __init__(self, raw: bytes, starts: list[int], lo: int, end: int, codec: str) -> None:
        self.raw, self.starts, self.lo, self.end, self.codec = raw, starts, lo, end, codec

    def lines(self) -> list[str]:
        raw, lo, codec = self.raw, self.lo, self.codec
        bounds = self.starts + [self.end]
        out = []
        for i in range(len(self.starts)):
            piece = raw[bounds[i] - lo: bounds[i + 1] - lo]
            out.append(piece.decode(codec, "replace").rstrip("\r\n"))
        return out


class LogFileReader:
    def __init__(self, path: str, encoding: str | None = None,
                 chunk_bytes: int = CHUNK_BYTES) -> None:
        self.path = os.path.abspath(path)
        self._forced_encoding = encoding
        self._chunk = max(2, chunk_bytes - chunk_bytes % 2)
        self._index = LineIndex()
        self._lock = threading.RLock()        # index state
        self._poll_lock = threading.Lock()    # serialises poll()
        self._rlock = threading.Lock()        # random-access handle
        self._cache_lock = threading.Lock()
        self._cache: OrderedDict[int, list[str]] = OrderedDict()
        self._refs = 1
        self.missing = False
        self._sfh = self._rfh = None
        self._open()

    # ------------------------------------------------------------------ setup
    def _open(self) -> None:
        self._close_handles()
        self._sfh = winio.open_shared(self.path, winio.SEQUENTIAL)
        self._rfh = winio.open_shared(self.path, winio.RANDOM)
        st = os.stat(self.path)  # same API as the per-poll check -> consistent ids
        self._ident = (st.st_dev, st.st_ino)
        self._enc_locked = False
        self._reset_state()

    def _reset_state(self) -> None:
        with self._lock:
            self._index.clear()
            self._data_end = 0
            self._provisional = False
            if not self._enc_locked:
                self._set_encoding(self._forced_encoding, b"")
            self._scan_pos = self._bom
            self._deferred: int | None = self._bom
        with self._cache_lock:
            self._cache.clear()

    def _set_encoding(self, forced: str | None, sample: bytes) -> None:
        detected, bom = detect_encoding(sample)
        codec = forced or detected
        low = codec.lower().replace("_", "-")
        if low == "utf-8-sig":
            codec = "utf-8"
        elif low in ("utf-16", "utf16"):
            codec = detected if detected.startswith("utf-16") else "utf-16-le"
        self.codec = codec
        self._bom = bom if codec == detected else 0
        if codec.startswith("utf-16"):
            self._unit = 2
            self._dtype = np.dtype("<u2" if codec.endswith("le") else ">u2")
        else:
            self._unit = 1
            self._dtype = np.dtype("u1")

    def _lock_encoding_from_file(self) -> None:
        """Detect the encoding from the first bytes once the file has content."""
        if self._enc_locked:
            return
        self._sfh.seek(0)
        sample = self._sfh.read(65536)
        if not sample:
            return
        self._enc_locked = True
        self._set_encoding(self._forced_encoding, sample)
        self._scan_pos = self._bom
        self._deferred = self._bom

    # ---------------------------------------------------------- ref counting
    def retain(self) -> None:
        self._refs += 1

    def release(self) -> None:
        self._refs -= 1
        if self._refs <= 0:
            self.close()

    def close(self) -> None:
        self._close_handles()

    def _close_handles(self) -> None:
        for fh in (self._sfh, self._rfh):
            try:
                if fh is not None:
                    fh.close()
            except OSError:
                pass
        self._sfh = self._rfh = None

    def new_scan_handle(self):
        return winio.open_shared(self.path, winio.SEQUENTIAL)

    # ------------------------------------------------------------- properties
    @property
    def count(self) -> int:
        return self._index.count

    @property
    def complete_count(self) -> int:
        """Lines that are certainly complete (the last one may still be growing)."""
        with self._lock:
            return self._index.count - (1 if self._provisional and self._index.count else 0)

    @property
    def size(self) -> int:
        return self._data_end

    @property
    def encoding(self) -> str:
        return self.codec

    # ------------------------------------------------------------------- poll
    def poll(self, max_bytes: int | None = None) -> PollResult:
        """Scan bytes appended since the last call (thread-safe)."""
        with self._poll_lock:
            reset = False
            try:
                pst = os.stat(self.path)
                self.missing = False
            except OSError:
                pst = None
                self.missing = True
            if pst is not None and pst.st_ino != 0 and (pst.st_dev, pst.st_ino) != self._ident:
                # Rotation: path now points to a different file.  Re-open.
                try:
                    self._open()
                    reset = True
                except OSError:
                    self.missing = True
            if self._sfh is None:
                return PollResult("none", self.count, self.count, False)
            size = os.fstat(self._sfh.fileno()).st_size
            if size < self._data_end:               # truncated in place
                self._enc_locked = False
                self._reset_state()
                reset = True
            self._lock_encoding_from_file()
            if not reset and size - self._data_end < self._unit:
                return PollResult("none", self.count, self.count, False)
            return self._scan(size, reset, max_bytes)

    def _scan(self, size: int, reset: bool, max_bytes: int | None) -> PollResult:
        unit = self._unit
        with self._lock:
            if self._provisional and self._index.count:
                last = self._index.pop()           # re-read the incomplete line
                self._scan_pos = last
                self._deferred = last
                self._provisional = False
            first = self._index.count
        done = 0
        more = False
        while self._scan_pos < size:
            if max_bytes is not None and done >= max_bytes:
                more = True
                break
            n = min(self._chunk, size - self._scan_pos)
            n -= n % unit
            if n <= 0:
                break
            self._sfh.seek(self._scan_pos)
            buf = self._sfh.read(n)
            buf = buf[: len(buf) - len(buf) % unit]
            if not buf:
                break
            self._index_chunk(buf)
            done += len(buf)
        with self._lock:
            self._provisional = (not more) and self._deferred is None and self._index.count > 0
            count = self._index.count
        self._invalidate_cache(first)
        if reset:
            return PollResult("reset", 0, count, more)
        if count == first and not more:
            return PollResult("none", first, count, False)
        return PollResult("appended", first, count, more)

    def _index_chunk(self, buf: bytes) -> None:
        unit = self._unit
        base = self._scan_pos
        end = base + len(buf)
        arr = np.frombuffer(buf, dtype=self._dtype)
        nl = np.flatnonzero(arr == 10)
        nxt = base + (nl + 1) * unit               # start of the line after each "\n"
        starts = nxt[nxt < end]
        with self._lock:
            if self._deferred is not None and self._deferred == base:
                starts = np.concatenate((np.array([base], dtype=np.int64), starts))
            self._deferred = end if (nxt.size and nxt[-1] == end) else None
            self._index.append(starts.astype(np.int64, copy=False))
            self._scan_pos = end
            self._data_end = end

    # ------------------------------------------------------------ line access
    def fetch(self, a: int, b: int, fh=None) -> Block | None:
        """Raw data for lines ``[a, b)`` (clamped).  ``fh``: private handle."""
        with self._lock:
            count = self._index.count
            b = min(b, count)
            if a < 0 or a >= b:
                return None
            starts = self._index.slice(a, min(b + 1, count)).tolist()
            end = starts.pop() if len(starts) == b - a + 1 else self._data_end
        lo = starts[0]
        raw = self._read_at(lo, end - lo, fh)
        return Block(raw, starts, lo, end, self.codec)

    def line_lengths(self, lines: np.ndarray) -> np.ndarray:
        """Byte length of each given line (terminator included), straight from the index."""
        lines = np.asarray(lines, dtype=np.int64)
        with self._lock:
            count = self._index.count
            if count == 0 or lines.size == 0:
                return np.zeros(lines.size, dtype=np.int64)
            starts = self._index.slice(0, count)
            lines = np.clip(lines, 0, count - 1)
            nxt = np.where(lines + 1 < count, starts[np.minimum(lines + 1, count - 1)], self._data_end)
            return (nxt - starts[lines]).astype(np.int64)

    def _read_at(self, offset: int, length: int, fh=None) -> bytes:
        try:
            if fh is not None:
                fh.seek(offset)
                return fh.read(length)
            with self._rlock:
                if self._rfh is None:
                    return b""
                self._rfh.seek(offset)
                return self._rfh.read(length)
        except (OSError, ValueError):
            return b""

    def _block(self, blk: int) -> list[str]:
        with self._cache_lock:
            hit = self._cache.get(blk)
            if hit is not None:
                self._cache.move_to_end(blk)
                return hit
        data = self.fetch(blk * BLOCK_LINES, (blk + 1) * BLOCK_LINES)
        lines = data.lines() if data is not None else []
        with self._cache_lock:
            self._cache[blk] = lines
            while len(self._cache) > CACHE_BLOCKS:
                self._cache.popitem(last=False)
        return lines

    def get_line(self, i: int) -> str:
        if i < 0 or i >= self._index.count:
            return ""
        blk, j = divmod(i, BLOCK_LINES)
        lines = self._block(blk)
        if j >= len(lines):                       # stale partial block: refetch once
            with self._cache_lock:
                self._cache.pop(blk, None)
            lines = self._block(blk)
        return lines[j] if j < len(lines) else ""

    def _invalidate_cache(self, first_changed: int) -> None:
        blk = first_changed // BLOCK_LINES
        with self._cache_lock:
            for k in [k for k in self._cache if k >= blk]:
                del self._cache[k]
