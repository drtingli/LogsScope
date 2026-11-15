"""Entry point: ``logscope [--portable] [--plugins DIR] [--console] [FILE ...]``.

``logscope-gui`` (the GUI launcher that ``pip`` / ``uv sync`` creates from ``[project.gui-scripts]``)
runs :func:`gui_main`: no console window, and errors are written to ``crash.log`` in the settings
folder instead of a terminal nobody can see.
"""

from __future__ import annotations

import argparse
import datetime
import sys
import traceback
from pathlib import Path

from . import __version__
from .core.columnizers import ColumnizerRegistry
from .core.settings import Settings, default_dir

APP_USER_MODEL_ID = "TL.LogScope"


def _log(folder: Path, name: str, text: str) -> None:
    """Append to a log file in the settings folder; never raises."""
    try:
        folder.mkdir(parents=True, exist_ok=True)
        with open(folder / name, "a", encoding="utf-8") as fh:
            fh.write(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {text}\n")
    except OSError:
        pass


def _say(text: str) -> None:
    """Print to stderr when there is one (a windowless launcher has none)."""
    if sys.stderr is not None:
        try:
            print(text, file=sys.stderr)
        except Exception:
            pass


def _install_excepthook(folder: Path) -> None:
    previous = sys.excepthook

    def hook(kind, value, tb) -> None:
        text = "".join(traceback.format_exception(kind, value, tb))
        _log(folder, "crash.log", text)
        try:
            from PySide6.QtWidgets import QApplication, QMessageBox

            if QApplication.instance() is not None:
                box = QMessageBox(QMessageBox.Icon.Warning, "LogScope - unexpected error",
                                  f"{kind.__name__}: {value}\n\nDetails were saved to {folder / 'crash.log'}")
                box.setDetailedText(text)
                box.exec()
        except Exception:
            pass
        if sys.stderr is not None:
            previous(kind, value, tb)

    sys.excepthook = hook


def main(argv: list[str] | None = None, gui: bool = False) -> int:
    ap = argparse.ArgumentParser(prog="logscope", description="A lightweight local tool for analyzing log files, with flexible highlighting and filtering plus live monitoring.")
    ap.add_argument("files", nargs="*", help="log files to open")
    ap.add_argument("--portable", action="store_true", help="keep settings next to the program")
    ap.add_argument("--plugins", action="append", default=[], help="extra columnizer plugin folder")
    ap.add_argument("--console", action="store_true", help="keep the console window (Windows)")
    ap.add_argument("--version", action="version", version=f"LogScope {__version__}")
    args = ap.parse_args(argv)

    from .winconsole import hide_own_console, set_app_user_model_id

    if not args.console:
        hide_own_console()                        # no black window when started by a double-click
    set_app_user_model_id(APP_USER_MODEL_ID)

    from PySide6.QtWidgets import QApplication
    from .ui.icons import app_icon
    from .ui.main_window import MainWindow

    app = QApplication(sys.argv[:1])
    app.setStyle("Fusion")           # honours the selected-row and background colours of the theme
    app.setApplicationName("LogScope")
    app.setApplicationVersion(__version__)
    app.setWindowIcon(app_icon())
    settings = Settings(default_dir(args.portable))
    _install_excepthook(settings.dir)

    registry = ColumnizerRegistry()
    problems: list[str] = []
    for err in registry.set_user_regex(settings["regex_columnizers"]):   # bad user config must not stop start-up
        problems.append(f"parsing rule skipped: {err}")
    for folder in [settings.dir / "plugins", *map(Path, args.plugins)]:
        for err in registry.load_plugins(folder):
            problems.append(f"plugin error: {err}")
    for p in problems:
        _say(p)
        _log(settings.dir, "startup.log", p)

    win = MainWindow(settings, registry)
    win.setWindowIcon(app_icon())
    win.show()
    if problems:
        win.statusBar().showMessage(f"{len(problems)} start-up problem(s) - see {settings.dir / 'startup.log'}", 12000)
    if args.files:
        for f in args.files:
            win.open_file(f)
    elif settings["restore_session"]:
        win.restore_last_session()
    return app.exec()


def gui_main() -> int:
    """Entry point of ``logscope-gui``."""
    return main(gui=True)


if __name__ == "__main__":
    raise SystemExit(main())
