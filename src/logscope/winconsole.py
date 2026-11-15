"""Get rid of a black console window that belongs only to this program (Windows).

A console-subsystem launcher (``logscope.exe``, ``python.exe``) started by a double-click opens a
console window.  ``FreeConsole`` removes it, but only when no *interactive shell* shares that
console: started from cmd / PowerShell / Windows Terminal the console is the user's own and stays.
The decision is a pure function (:func:`should_free`) so it can be tested on any platform.
"""

from __future__ import annotations

import os
import sys

OUR_LAUNCHERS = {"logscope.exe", "logscope-gui.exe", "python.exe", "pythonw.exe", "py.exe", "uv.exe", "uvx.exe"}


def should_free(image_names: list[str], own_names: set[str] | None = None) -> bool:
    """True when every program attached to the console is one of our own launchers."""
    names = {n.lower() for n in image_names if n}
    return bool(names) and names <= (own_names or OUR_LAUNCHERS)


def _image_name(pid: int) -> str:
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.windll.kernel32
    h = k32.OpenProcess(0x1000, False, pid)             # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(len(buf))
        if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return os.path.basename(buf.value)
        return ""
    finally:
        k32.CloseHandle(h)


def hide_own_console() -> bool:
    """Close the console window if it is ours alone.  Returns True when it was released."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        k32 = ctypes.windll.kernel32
        if not k32.GetConsoleWindow():
            return False                                  # already windowless (pythonw / GUI launcher)
        pids = (ctypes.c_uint * 16)()
        n = k32.GetConsoleProcessList(pids, 16)
        if n == 0 or n > 16:
            return False
        names = [_image_name(pids[i]) for i in range(n)]
        if "" in names or not should_free(names):
            return False                                  # a shell (or something unknown) shares it: keep
        return bool(k32.FreeConsole())
    except Exception:
        return False


def set_app_user_model_id(app_id: str) -> None:
    """Give the process its own taskbar identity, so the taskbar shows our icon, not python's."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
    except Exception:
        pass
