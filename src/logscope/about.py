"""Application identity shown in Help > About (no Qt here, so it can be tested)."""

from __future__ import annotations

import platform
import sys

from . import __version__

APP_NAME = "LogScope"
TAGLINE = "A lightweight local tool for analyzing log files, with flexible highlighting and filtering plus live monitoring."
AUTHOR = "Ting Li"                     # edit these lines to change what the About box shows
COPYRIGHT = "Copyright (c) 2026 Ting Li"
BUILD_DATE = "2026-11-14"
CREDITS = "Built with Python and PySide6."

CHANGELOG = [
    ("0.1.0", "Initial public release."),
]


def _module_version(name: str) -> str:
    try:
        mod = __import__(name)
        return str(getattr(mod, "__version__", "installed"))
    except Exception:
        return "not installed"


def runtime_info(settings_dir: str = "") -> list[tuple[str, str]]:
    """(label, value) pairs describing this installation."""
    qt = "not installed"
    try:
        from PySide6 import QtCore
        qt = QtCore.qVersion()
    except Exception:
        pass
    rows = [
        ("Version", __version__),
        ("Build date", BUILD_DATE),
        ("Python", f"{platform.python_version()} ({platform.python_implementation()})"),
        ("PySide6", _module_version("PySide6")),
        ("Qt", qt),
        ("NumPy", _module_version("numpy")),
        ("Platform", platform.platform()),
        ("Executable", sys.executable),
    ]
    if settings_dir:
        rows.append(("Settings folder", settings_dir))
    return rows


def info_text(settings_dir: str = "") -> str:
    """Plain text for 'Copy' (bug reports)."""
    lines = [f"{APP_NAME} {__version__}", f"Author: {AUTHOR}", COPYRIGHT, ""]
    lines += [f"{k}: {v}" for k, v in runtime_info(settings_dir)]
    return "\n".join(lines)


def about_html() -> str:
    rows = "".join(f"<li><b>{v}</b> - {t}</li>" for v, t in CHANGELOG)
    return (f"<h2>{APP_NAME} {__version__}</h2><p>{TAGLINE}.</p>"
            f"<p><b>Author:</b> {AUTHOR}<br>{COPYRIGHT}</p>"
            f"<p>{CREDITS}</p><p><b>Recent changes</b></p><ul>{rows}</ul>")
