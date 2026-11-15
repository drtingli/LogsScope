"""Filter actions: run an external program when a filter matches a *new* line (Qt-free).

The full log line is passed as the first argument after the command.  It is also
available in the environment (LOGSCOPE_LINE, LOGSCOPE_LINE_NO, LOGSCOPE_FILE) because
command lines are limited to ~32k characters on Windows."""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import sys
import threading

MODES = ("first", "every")
MAX_COMMAND = 1000
MAX_ARG = 30_000                    # Windows: whole command line < 32767 characters
BATCH_UNSAFE = re.compile(r'[&|<>^%"!\r\n]')
CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_PROCESS_GROUP = 0x00000200


def sanitize_action(d) -> dict | None:
    if not isinstance(d, dict) or not isinstance(d.get("command"), str):
        return None
    command = d["command"].strip()[:MAX_COMMAND]
    if not command:
        return None
    mode = d.get("mode")
    return {"command": command, "mode": mode if mode in MODES else "first",
            "enabled": d.get("enabled") is not False}


def is_active(action: dict | None) -> bool:
    return bool(action) and bool(action.get("enabled", True)) and bool(action.get("command"))


def split_command(command: str, windows: bool | None = None) -> list[str]:
    """``C:\\Program Files\\x.exe`` (an existing file, no quotes) is kept whole."""
    windows = (sys.platform == "win32") if windows is None else windows
    command = command.strip()
    if not command:
        raise ValueError("empty command")
    if os.path.isfile(command):
        return [command]
    try:
        parts = shlex.split(command, posix=not windows)
    except ValueError as exc:
        raise ValueError(f"cannot parse command: {exc}") from None
    if windows:                      # posix=False keeps the quotes
        parts = [p[1:-1] if len(p) >= 2 and p[0] == p[-1] == '"' else p for p in parts]
    if not parts:
        raise ValueError("empty command")
    return parts


def build_argv(command: str, line: str, windows: bool | None = None) -> list[str]:
    windows = (sys.platform == "win32") if windows is None else windows
    argv = split_command(command, windows)
    exe = argv[0].lower()
    if exe.endswith(".py"):                          # convenience: run scripts with this Python
        argv = [sys.executable] + argv
    arg = line.rstrip("\r\n")
    if windows and exe.endswith((".bat", ".cmd")):
        arg = BATCH_UNSAFE.sub(" ", arg)             # cmd.exe would interpret these characters
    room = max(1000, MAX_ARG - sum(len(a) + 1 for a in argv))
    return argv + [arg[:room]]


def launch(command: str, line: str, line_no: int = 0, log_path: str = "",
           windows: bool | None = None, popen=subprocess.Popen):
    """Start the program without waiting for it.  Raises ValueError / OSError on failure."""
    windows = (sys.platform == "win32") if windows is None else windows
    argv = build_argv(command, line, windows)
    env = dict(os.environ)
    env["LOGSCOPE_LINE"] = line.rstrip("\r\n")[:MAX_ARG]
    env["LOGSCOPE_LINE_NO"] = str(line_no)
    env["LOGSCOPE_FILE"] = log_path
    kw: dict = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    env=env, shell=False)
    if windows:
        kw["creationflags"] = CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True
    proc = popen(argv, **kw)
    if hasattr(proc, "wait"):                        # reap it, never block the caller
        threading.Thread(target=proc.wait, daemon=True).start()
    return proc


def run_matches(action: dict, matches, log_path: str, burst_limit: int = 25,
                launcher=launch) -> dict:
    """Launch for ``matches`` = ``[(line_no, text), ...]`` according to the action's mode.

    Returns ``{"launched": n, "skipped": n, "errors": [str], "fired": bool}``.  ``skipped``
    counts matches beyond ``burst_limit`` (protection against a runaway log)."""
    res = {"launched": 0, "skipped": 0, "errors": [], "fired": False}
    if not is_active(action) or not matches:
        return res
    first_only = action.get("mode", "first") == "first"
    todo = matches[:1] if first_only else matches
    for i, (ln, text) in enumerate(todo):
        if i >= burst_limit:
            res["skipped"] = len(todo) - i
            break
        try:
            launcher(action["command"], text, ln + 1, log_path)
            res["launched"] += 1
        except (OSError, ValueError) as exc:
            res["errors"].append(str(exc))
        res["fired"] = True                           # "first match" is used up even if it failed
    return res
