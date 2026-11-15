"""Right-click menu of the log panels: the built-in items, the default layout and custom commands (Qt-free).

The menu is a list stored in the settings (``context_menu``)::

    {"type": "builtin",   "id": "copy_lines"}
    {"type": "separator"}
    {"type": "command",   "label": "Open in editor", "command": "C:\\\\Tools\\\\edit.exe", "args": "--line {lineno} \\"{file}\\""}

Custom commands may use these placeholders in their arguments: ``{line}`` (the full text of the line),
``{field}`` (the clicked field), ``{lineno}`` (1-based), ``{file}`` and ``{selection}`` (all selected lines).
The arguments are split *before* the placeholders are filled in, so text from the log can never add or
break arguments, and no shell is involved.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
import threading

from .actions import BATCH_UNSAFE, CREATE_NEW_PROCESS_GROUP, CREATE_NO_WINDOW, MAX_ARG, split_command

MAX_ITEMS = 60
MAX_LABEL = 60
MAX_COMMAND = 1000
MAX_ARGS = 2000
PLACEHOLDERS = ("{line}", "{field}", "{lineno}", "{file}", "{selection}")

# id -> (label, needs, hint).  needs: "row" a line under the cursor, "field" a text field under the cursor,
# "multi" two or more selected lines, "derived" a tab made by 'to new tab' / merge, "any" always available
BUILTINS: dict[str, tuple[str, str, str]] = {
    "copy_lines": ("Copy line(s)", "row", "Copy the selected lines (Ctrl+C)"),
    "copy_field": ("Copy this field", "field", "Copy the text of the field that was clicked"),
    "copy_numbered": ("Copy line(s) with line numbers", "row", "Copy the selected lines, each after its line number"),
    "copy_location": ("Copy file path and line number", "row", "Copy 'path:line' of the selected lines"),
    "find_text": ("Find this text", "field", "Open the find bar of this panel with the field text"),
    "filter_include": ("Filter: only lines containing this text", "field", "Show only lines that contain the field text"),
    "filter_exclude": ("Filter: hide lines containing this text", "field", "Hide the lines that contain the field text"),
    "highlight_text": ("Highlight this text", "field", "Colour this text wherever it appears"),
    "bookmark": ("Toggle bookmark", "row", "Set or remove a bookmark on the selected lines"),
    "bookmark_comment": ("Bookmark with comment…", "row", "Bookmark the selected lines and give them a comment"),
    "pin": ("Pin to top (sticky)", "row", "Keep the selected lines at the top of this panel"),
    "goto_main": ("Show in main view", "row", "Jump to this line in the main log view"),
    "time_diff": ("Time difference of selected lines…", "multi", "Time between the first and last selected line"),
    "selection_to_tab": ("Selected lines \u2192 new tab", "row", "Open the selected lines as a new tab"),
    "jump_source": ("Jump to source line", "derived", "Go to this line in the file the tab was made from"),
    "customize": ("Customize this menu…", "any", "Add or remove items of this menu"),
}

DEFAULT_ITEMS = [
    "copy_lines", "copy_field", "-",
    "find_text", "filter_include", "filter_exclude", "highlight_text", "-",
    "bookmark", "bookmark_comment", "pin", "-",
    "goto_main", "time_diff", "selection_to_tab", "jump_source", "-",
    "customize",
]


def default_menu() -> list[dict]:
    return [{"type": "separator"} if x == "-" else {"type": "builtin", "id": x} for x in DEFAULT_ITEMS]


def sanitize_item(d) -> dict | None:
    if not isinstance(d, dict):
        return None
    kind = d.get("type")
    if kind == "separator":
        return {"type": "separator"}
    if kind == "builtin":
        return {"type": "builtin", "id": d["id"]} if d.get("id") in BUILTINS else None
    if kind == "command":
        label, command, args = d.get("label"), d.get("command"), d.get("args", "")
        if not isinstance(label, str) or not isinstance(command, str):
            return None
        label, command = label.strip()[:MAX_LABEL], command.strip()[:MAX_COMMAND]
        if not label or not command:
            return None
        return {"type": "command", "label": label, "command": command,
                "args": args.strip()[:MAX_ARGS] if isinstance(args, str) else ""}
    return None


def tidy(items: list[dict]) -> list[dict]:
    """No leading / trailing / doubled separators, each built-in at most once."""
    out: list[dict] = []
    seen: set[str] = set()
    for it in items:
        if it["type"] == "separator":
            if out and out[-1]["type"] != "separator":
                out.append(it)
            continue
        if it["type"] == "builtin":
            if it["id"] in seen:
                continue
            seen.add(it["id"])
        out.append(it)
    while out and out[-1]["type"] == "separator":
        out.pop()
    return out


def sanitize_menu(obj) -> list[dict]:
    """The menu from a settings file.  Anything that is not a list gives the default menu;
    an empty list stays empty (the user removed everything)."""
    if not isinstance(obj, list):
        return default_menu()
    items = [x for x in (sanitize_item(i) for i in obj[:MAX_ITEMS * 2]) if x is not None]
    return tidy(items)[:MAX_ITEMS]


def item_label(item: dict) -> str:
    if item["type"] == "separator":
        return "\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500  separator  \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500"
    if item["type"] == "builtin":
        return BUILTINS[item["id"]][0]
    return f"\u2699 {item['label']}   [{item['command']}]"


def short(text: str, n: int = 30) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[: n - 1] + "\u2026"


# ------------------------------------------------------------------------- what to show
def visibility(item: dict, ctx: dict) -> str:
    """``"show"``, ``"disabled"`` or ``"hide"`` for ``item`` in the situation ``ctx``:

    ``has_row`` / ``has_field`` (something is under the cursor), ``n_selected``, ``panel`` (``main`` / ``filter`` /
    ``search``), ``in_strip`` (the sticky strip), ``derived`` (tab made by 'to new tab' / merge)."""
    if item["type"] == "separator":
        return "show"
    if item["type"] == "command":
        need = "row" if ("{line}" in item["args"] or "{lineno}" in item["args"] or "{selection}" in item["args"]
                         or "{field}" in item["args"]) else "any"
        if "{field}" in item["args"] and not ctx.get("has_field"):
            return "disabled"
        return "disabled" if need == "row" and not ctx.get("has_row") else "show"
    needs = BUILTINS[item["id"]][1]
    if item["id"] == "goto_main" and ctx.get("panel") == "main" and not ctx.get("in_strip"):
        return "hide"                                          # already in the main view
    if needs == "any":
        return "show"
    if needs == "derived":
        return "show" if ctx.get("derived") and ctx.get("has_row") else "hide"
    if needs == "multi":
        return "show" if ctx.get("n_selected", 0) >= 2 else "disabled"
    if needs == "field":
        return "show" if ctx.get("has_field") else "disabled"
    return "show" if ctx.get("has_row") else "disabled"


def dynamic_label(item: dict, ctx: dict) -> str:
    """Item text for the current situation (the clicked text is shown in the filter / find / highlight items)."""
    if item["type"] == "command":
        return item["label"]
    iid = item["id"]
    label = BUILTINS[iid][0]
    field = short(ctx.get("field", ""))
    if iid in ("find_text", "filter_include", "filter_exclude", "highlight_text") and field:
        quoted = f"\u201c{field}\u201d"
        return {"find_text": f"Find {quoted}",
                "filter_include": f"Filter: only lines containing {quoted}",
                "filter_exclude": f"Filter: hide lines containing {quoted}",
                "highlight_text": f"Highlight {quoted}"}[iid]
    if iid == "bookmark" and ctx.get("all_bookmarked"):
        return "Remove bookmark"
    if iid == "bookmark" and ctx.get("has_row"):
        return "Add bookmark"
    if iid == "pin" and ctx.get("all_pinned"):
        return "Unpin (remove from the top)"
    return label


# ------------------------------------------------------------------------- custom commands
def split_args(text: str, windows: bool | None = None) -> list[str]:
    windows = (sys.platform == "win32") if windows is None else windows
    text = (text or "").strip()
    if not text:
        return []
    try:
        parts = shlex.split(text, posix=not windows)
    except ValueError as exc:
        raise ValueError(f"cannot parse the arguments: {exc}") from None
    if windows:
        parts = [p[1:-1] if len(p) >= 2 and p[0] == p[-1] == '"' else p for p in parts]
    return parts


def fill(token: str, values: dict[str, str]) -> str:
    for key in PLACEHOLDERS:
        if key in token:
            token = token.replace(key, values.get(key[1:-1], ""))
    return token


def build_command_argv(command: str, args: str, values: dict[str, str], windows: bool | None = None) -> list[str]:
    """``values``: line, field, lineno, file, selection."""
    windows = (sys.platform == "win32") if windows is None else windows
    argv = split_command(command, windows)
    exe = argv[0].lower()
    if exe.endswith(".py"):
        argv = [sys.executable] + argv
    batch = windows and exe.endswith((".bat", ".cmd"))
    room = MAX_ARG
    for tok in split_args(args, windows):
        arg = fill(tok, values)
        if batch:
            arg = BATCH_UNSAFE.sub(" ", arg)               # cmd.exe would interpret these characters
        arg = arg[: max(200, room)]
        room -= len(arg) + 1
        argv.append(arg)
    return argv


def launch_command(command: str, args: str, values: dict[str, str], windows: bool | None = None,
                   popen=subprocess.Popen):
    """Start the program without waiting for it.  Raises ValueError / OSError on failure."""
    windows = (sys.platform == "win32") if windows is None else windows
    argv = build_command_argv(command, args, values, windows)
    env = dict(os.environ)
    env["LOGSCOPE_LINE"] = values.get("line", "")[:MAX_ARG]
    env["LOGSCOPE_LINE_NO"] = values.get("lineno", "")
    env["LOGSCOPE_FILE"] = values.get("file", "")
    kw: dict = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    env=env, shell=False)
    if windows:
        kw["creationflags"] = CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True
    proc = popen(argv, **kw)
    if hasattr(proc, "wait"):
        threading.Thread(target=proc.wait, daemon=True).start()
    return proc
