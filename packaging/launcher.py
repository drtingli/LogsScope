"""PyInstaller entry script (``logscope/__main__.py`` uses a relative import, so it cannot be the entry)."""

from logscope.app import gui_main

if __name__ == "__main__":
    raise SystemExit(gui_main())
