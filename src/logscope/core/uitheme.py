"""Window theme (Qt-free): follow Windows, or force a light or dark look.

The window theme colours the program itself (menus, toolbar, dialogs, panels).  The log panels keep
the per-area colours of :mod:`core.theme`; areas whose colours are left empty follow the window theme.
"""

from __future__ import annotations

from .theme import clean_colour

MODES = ("system", "light", "dark")
MODE_LABELS = {"system": "Follow Windows", "light": "Light", "dark": "Dark"}
DEFAULT_MODE = "system"

LIGHT_GRID = "#d9d9d9"                       # the default grid colour of core.theme
DARK_GRID = "#3a3a3a"

# palette role -> colour; "disabled" holds the colours of greyed-out widgets
PALETTES: dict[str, dict[str, dict[str, str]]] = {
    "light": {
        "normal": {
            "Window": "#f3f3f3", "WindowText": "#1f1f1f", "Base": "#ffffff", "AlternateBase": "#f6f6f6",
            "ToolTipBase": "#ffffdc", "ToolTipText": "#1f1f1f", "PlaceholderText": "#767676",
            "Text": "#1f1f1f", "Button": "#f0f0f0", "ButtonText": "#1f1f1f", "BrightText": "#c00000",
            "Light": "#ffffff", "Midlight": "#e3e3e3", "Mid": "#b8b8b8", "Dark": "#a0a0a0",
            "Shadow": "#707070", "Highlight": "#2f6fdb", "HighlightedText": "#ffffff",
            "Link": "#0b63ce", "LinkVisited": "#6b3fa0",
        },
        "disabled": {"WindowText": "#a0a0a0", "Text": "#a0a0a0", "ButtonText": "#a0a0a0",
                     "Highlight": "#c8c8c8", "HighlightedText": "#6e6e6e"},
    },
    "dark": {
        "normal": {
            "Window": "#202020", "WindowText": "#e6e6e6", "Base": "#1b1b1b", "AlternateBase": "#262626",
            "ToolTipBase": "#2b2b2b", "ToolTipText": "#e6e6e6", "PlaceholderText": "#8a8a8a",
            "Text": "#e6e6e6", "Button": "#2d2d2d", "ButtonText": "#e6e6e6", "BrightText": "#ff6b6b",
            "Light": "#3c3c3c", "Midlight": "#333333", "Mid": "#2a2a2a", "Dark": "#151515",
            "Shadow": "#000000", "Highlight": "#2f6fdb", "HighlightedText": "#ffffff",
            "Link": "#5ea8ff", "LinkVisited": "#b48cff",
        },
        "disabled": {"WindowText": "#7a7a7a", "Text": "#7a7a7a", "ButtonText": "#7a7a7a",
                     "Highlight": "#3a3a3a", "HighlightedText": "#9a9a9a"},
    },
}


def sanitize_mode(value) -> str:
    return value if isinstance(value, str) and value in MODES else DEFAULT_MODE


def effective_scheme(mode, system_dark: bool) -> str:
    """``"light"`` or ``"dark"``: what the window shows for ``mode`` (``system_dark``: Windows is dark)."""
    mode = sanitize_mode(mode)
    if mode == "system":
        return "dark" if system_dark else "light"
    return mode


def palette_colours(scheme: str) -> dict[str, dict[str, str]]:
    return PALETTES["dark" if scheme == "dark" else "light"]


def grid_colour(colour, dark: bool) -> str:
    """The default grid colour suits a light background; on a dark one it becomes a dim grey."""
    c = clean_colour(colour, LIGHT_GRID)
    if dark and c == LIGHT_GRID:
        return DARK_GRID
    return c
