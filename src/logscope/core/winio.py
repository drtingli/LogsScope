"""Platform file access helpers (Windows-optimised, portable fallback).

Why this module exists
----------------------
A log viewer must read a file that another process is still writing, renaming
or deleting.  On Windows Python's normal ``open()`` does not pass
``FILE_SHARE_DELETE``, so while we have a log open the writer cannot rotate
(rename/delete) it.  We therefore open files with ``CreateFileW`` and the full
share mask ``READ | WRITE | DELETE`` and hand the OS a caching hint:

* ``FILE_FLAG_SEQUENTIAL_SCAN`` for the indexer / filter / search scans
* ``FILE_FLAG_RANDOM_ACCESS``   for the handle that serves visible rows

Change notification uses ``FindFirstChangeNotificationW`` on the containing
directory.  NTFS reports size changes of a file that is still open lazily, so
the watcher is only a *wake-up hint*: callers must always re-check the file
(``poll``) and the wait timeout doubles as a polling fallback.  This also keeps
network shares (where notifications are unreliable) working.
"""

from __future__ import annotations

import os
import sys
import time
from typing import BinaryIO

IS_WINDOWS = sys.platform == "win32"

SEQUENTIAL = "sequential"
RANDOM = "random"

if IS_WINDOWS:  # pragma: no cover - exercised on Windows only
    import ctypes
    import msvcrt
    from ctypes import wintypes

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)

    GENERIC_READ = 0x80000000
    FILE_SHARE_READ = 0x1
    FILE_SHARE_WRITE = 0x2
    FILE_SHARE_DELETE = 0x4
    OPEN_EXISTING = 3
    FILE_ATTRIBUTE_NORMAL = 0x80
    FILE_FLAG_SEQUENTIAL_SCAN = 0x08000000
    FILE_FLAG_RANDOM_ACCESS = 0x10000000
    FILE_NOTIFY_CHANGE_FILE_NAME = 0x1
    FILE_NOTIFY_CHANGE_SIZE = 0x8
    FILE_NOTIFY_CHANGE_LAST_WRITE = 0x10
    WAIT_OBJECT_0 = 0
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    _k32.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    _k32.CreateFileW.restype = wintypes.HANDLE
    _k32.FindFirstChangeNotificationW.argtypes = [wintypes.LPCWSTR, wintypes.BOOL, wintypes.DWORD]
    _k32.FindFirstChangeNotificationW.restype = wintypes.HANDLE
    _k32.FindNextChangeNotification.argtypes = [wintypes.HANDLE]
    _k32.FindNextChangeNotification.restype = wintypes.BOOL
    _k32.FindCloseChangeNotification.argtypes = [wintypes.HANDLE]
    _k32.FindCloseChangeNotification.restype = wintypes.BOOL
    _k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    _k32.WaitForSingleObject.restype = wintypes.DWORD


def open_shared(path: str, hint: str = SEQUENTIAL) -> BinaryIO:
    """Open ``path`` read-only, unbuffered, without blocking writers/renamers."""
    if IS_WINDOWS:  # pragma: no cover
        flag = FILE_FLAG_SEQUENTIAL_SCAN if hint == SEQUENTIAL else FILE_FLAG_RANDOM_ACCESS
        handle = _k32.CreateFileW(
            os.path.abspath(path),
            GENERIC_READ,
            FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
            None,
            OPEN_EXISTING,
            FILE_ATTRIBUTE_NORMAL | flag,
            None,
        )
        if handle is None or handle == INVALID_HANDLE_VALUE:
            raise ctypes.WinError(ctypes.get_last_error())
        fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
        return os.fdopen(fd, "rb", buffering=0)

    fh = open(path, "rb", buffering=0)
    advise = getattr(os, "posix_fadvise", None)
    if advise is not None:
        try:
            advise(fh.fileno(), 0, 0,
                   os.POSIX_FADV_SEQUENTIAL if hint == SEQUENTIAL else os.POSIX_FADV_RANDOM)
        except OSError:
            pass
    return fh


class ChangeWatcher:
    """Wake-up helper: ``wait(timeout)`` returns early when the folder changes.

    Always treat the result as a hint; the caller must still poll the file.
    """

    def __init__(self, path: str) -> None:
        self._handle = None
        self._dir = os.path.dirname(os.path.abspath(path)) or "."
        if IS_WINDOWS:  # pragma: no cover
            mask = (FILE_NOTIFY_CHANGE_FILE_NAME | FILE_NOTIFY_CHANGE_SIZE
                    | FILE_NOTIFY_CHANGE_LAST_WRITE)
            h = _k32.FindFirstChangeNotificationW(self._dir, False, mask)
            if h is not None and h != INVALID_HANDLE_VALUE:
                self._handle = h

    @property
    def native(self) -> bool:
        return self._handle is not None

    def wait(self, timeout: float) -> bool:
        if self._handle is None:
            time.sleep(timeout)
            return False
        if IS_WINDOWS:  # pragma: no cover
            r = _k32.WaitForSingleObject(self._handle, int(timeout * 1000))
            if r == WAIT_OBJECT_0:
                _k32.FindNextChangeNotification(self._handle)
                return True
        return False

    def close(self) -> None:
        if self._handle is not None and IS_WINDOWS:  # pragma: no cover
            _k32.FindCloseChangeNotification(self._handle)
        self._handle = None
