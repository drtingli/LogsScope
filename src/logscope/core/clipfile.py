"""Save clipboard text as a new log file (Qt-free)."""

from __future__ import annotations

import os
import tempfile
from datetime import datetime


def suggest_name(now: datetime | None = None) -> str:
    return "clipboard-" + (now or datetime.now()).strftime("%Y%m%d-%H%M%S") + ".log"


def normalize_text(text: str) -> str:
    """Unix newlines, and a final newline so that the last line is complete."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if text and not text.endswith("\n"):
        text += "\n"
    return text


def save_text_file(path: str, text: str) -> int:
    """Write ``text`` as UTF-8 (atomic).  Returns the number of lines written."""
    text = normalize_text(text)
    directory = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(prefix=".paste-", dir=directory)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(text.encode("utf-8"))
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return text.count("\n")
