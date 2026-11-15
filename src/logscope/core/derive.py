"""Derived files: "filter result / bookmarks / selection -> new tab" (pipe tabs).

The chosen lines are written to a temporary UTF-8 file, which is then opened as a
perfectly normal tab -- so filtering again, searching, bookmarks and saving work
exactly as for any other file.  ``origin`` (a line-number array) remembers which
line of the source every line of the new file came from.
"""

from __future__ import annotations

import atexit
import os
import re
import shutil
import tempfile
import threading
from pathlib import Path
from typing import Callable

import numpy as np

_lock = threading.Lock()
_dir: Path | None = None
_counter = 0
RUN_GAP = 512               # lines closer than this are fetched with one read


def temp_dir() -> Path:
    global _dir
    with _lock:
        if _dir is None or not _dir.is_dir():
            _dir = Path(tempfile.mkdtemp(prefix="logscope-"))
            atexit.register(shutil.rmtree, str(_dir), True)
        return _dir


def new_path(title: str) -> str:
    """Unique file name for a derived file, e.g. ``.../filter-of-app-3.log``."""
    global _counter
    safe = re.sub(r"[^\w.-]+", "-", title, flags=re.UNICODE).strip("-.")[:60] or "lines"
    with _lock:
        _counter += 1
        n = _counter
    return str(temp_dir() / f"{safe}-{n}.log")


def remove_file(path: str) -> bool:
    """Delete a derived file; only files inside our temp folder are ever touched."""
    try:
        p = Path(path).resolve()
        if p.parent != temp_dir().resolve():
            return False
        p.unlink()
        return True
    except OSError:
        return False


def is_derived_path(path: str) -> bool:
    try:
        return Path(path).resolve().parent == temp_dir().resolve()
    except OSError:
        return False


def normalize_lines(lines) -> np.ndarray:
    """Sorted, unique, non-negative line numbers as int64."""
    a = np.unique(np.asarray(list(lines) if not isinstance(lines, np.ndarray) else lines, dtype=np.int64))
    return a[a >= 0]


def fetch_sorted(reader, fh, arr) -> list[str]:
    """Texts of the given line numbers (ascending) -- nearby lines are read with one block read."""
    lst = arr.tolist() if hasattr(arr, "tolist") else list(arr)
    out: list[str] = []
    i, n = 0, len(lst)
    while i < n:
        j = i + 1
        while j < n and lst[j] - lst[j - 1] < RUN_GAP and j - i < 4096:
            j += 1
        a, b = lst[i], lst[j - 1] + 1
        block = reader.fetch(a, b, fh)
        texts = block.lines() if block is not None else []
        for ln in lst[i:j]:
            k = ln - a
            out.append(texts[k] if k < len(texts) else "")
        i = j
    return out


def _close_quietly(*handles) -> None:
    for h in handles:
        try:
            h.close()
        except OSError:
            pass


def write_lines(reader, lines, dest: str, cancel: threading.Event | None = None,
                progress: Callable[[int], None] | None = None) -> np.ndarray:
    """Write the given source lines to ``dest`` (UTF-8, ``\\n``).

    Returns the line numbers actually written (the origin map); cancelling removes ``dest``.
    """
    arr = normalize_lines(lines)
    arr = arr[arr < reader.count]
    fh = reader.new_scan_handle()
    written = 0
    try:
        with open(dest, "wb") as out:
            for i in range(0, int(arr.size), 4096):
                if cancel is not None and cancel.is_set():
                    raise InterruptedError("cancelled")
                part = arr[i:i + 4096]
                out.write(("\n".join(fetch_sorted(reader, fh, part)) + "\n").encode("utf-8", "replace"))
                written += int(part.size)
                if progress is not None:
                    progress(written)
    except BaseException:
        _close_quietly(fh)
        try:
            os.unlink(dest)
        except OSError:
            pass
        raise
    _close_quietly(fh)
    return arr


CHUNK = 50_000


def write_merged(readers, file_idx, line_idx, dest: str, cancel: threading.Event | None = None,
                 progress: Callable[[int], None] | None = None) -> None:
    """Write the lines ``readers[file_idx[i]]`` line ``line_idx[i]`` (i = 0..n) to ``dest``, in that order.

    Within one file the line numbers must ascend (``timesync.merge_order`` guarantees it)."""
    n = int(len(file_idx))
    handles = [r.new_scan_handle() for r in readers]
    try:
        with open(dest, "wb") as out:
            for c0 in range(0, n, CHUNK):
                if cancel is not None and cancel.is_set():
                    raise InterruptedError("cancelled")
                f_seg, l_seg = file_idx[c0:c0 + CHUNK], line_idx[c0:c0 + CHUNK]
                texts = [""] * len(f_seg)
                for f in np.unique(f_seg).tolist():
                    pos = np.flatnonzero(f_seg == f)
                    got = fetch_sorted(readers[f], handles[f], l_seg[pos])
                    for p, t in zip(pos.tolist(), got):
                        texts[p] = t
                out.write(("\n".join(texts) + "\n").encode("utf-8", "replace"))
                if progress is not None:
                    progress(min(n, c0 + CHUNK))
    except BaseException:
        _close_quietly(*handles)
        try:
            os.unlink(dest)
        except OSError:
            pass
        raise
    _close_quietly(*handles)


def save_copy(src: str, dest: str) -> None:
    """Copy ``src`` to ``dest`` atomically (the destination is never half written)."""
    directory = os.path.dirname(os.path.abspath(dest))
    fd, tmp = tempfile.mkstemp(prefix=".save-", dir=directory)
    os.close(fd)
    try:
        shutil.copyfile(src, tmp)
        os.replace(tmp, dest)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
