"""Vector icons and the program logo, defined as data (no Qt, no image files).

* Toolbar icons are 24x24 line icons.  :func:`svg_for` returns a complete SVG document in any
  colour, so the UI renders them crisply at every size and in light or dark themes
  (``ui/icons.py`` does the painting).
* The logo is a list of simple shapes.  :func:`logo_svg` turns it into SVG for the running
  program; ``packaging/make_assets.py`` draws the very same shapes with Pillow to create the
  ``.ico`` of the executable, so both always look the same.
"""

from __future__ import annotations

# name -> SVG elements inside <svg viewBox="0 0 24 24">, drawn with stroke="currentColor"-style attributes
ICONS: dict[str, str] = {
    "open": '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
    "paste": ('<rect x="8" y="2" width="8" height="4" rx="1"/>'
              '<path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/>'),
    "save": ('<path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/>'
             '<polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/>'),
    "follow": ('<line x1="12" y1="4" x2="12" y2="16"/><polyline points="6 10 12 16 18 10"/>'
               '<line x1="5" y1="20" x2="19" y2="20"/>'),
    "autofit": ('<line x1="4" y1="5" x2="4" y2="19"/><line x1="20" y1="5" x2="20" y2="19"/>'
                '<line x1="7" y1="12" x2="17" y2="12"/><polyline points="10 9 7 12 10 15"/>'
                '<polyline points="14 9 17 12 14 15"/>'),
    "wrap": ('<line x1="4" y1="6" x2="20" y2="6"/><path d="M4 12h13a3 3 0 0 1 0 6h-5"/>'
             '<polyline points="14 15 11 18 14 21"/><line x1="4" y1="18" x2="7" y2="18"/>'),
    "fit": ('<polyline points="15 3 21 3 21 9"/><polyline points="9 21 3 21 3 15"/>'
            '<line x1="21" y1="3" x2="14" y2="10"/><line x1="3" y1="21" x2="10" y2="14"/>'),
    "find": '<circle cx="11" cy="11" r="7"/><line x1="21" y1="21" x2="16.1" y2="16.1"/>',
    "panel": '<rect x="3" y="4" width="18" height="16" rx="2"/><line x1="3" y1="14" x2="21" y2="14"/>',
    "bookmark": '<path d="M6 3h12a1 1 0 0 1 1 1v17l-7-4-7 4V4a1 1 0 0 1 1-1z"/>',
    "delta": '<path d="M12 4 21 19H3z"/>',
    "sync": ('<polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/>'
             '<path d="M3.5 9a9 9 0 0 1 14.9-3.4L23 10M1 14l4.6 4.4A9 9 0 0 0 20.5 15"/>'),
    "link": ('<path d="M10 13a5 5 0 0 0 7.5.5l3-3a5 5 0 0 0-7-7l-1.7 1.7"/>'
             '<path d="M14 11a5 5 0 0 0-7.5-.5l-3 3a5 5 0 0 0 7 7l1.7-1.7"/>'),
    "merge": '<circle cx="18" cy="18" r="3"/><circle cx="6" cy="6" r="3"/><path d="M6 21V9a9 9 0 0 0 9 9"/>',
    "detach": ('<path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>'
               '<polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/>'),
    "reattach": ('<path d="M5 12v7a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-7"/><polyline points="8 8 12 4 16 8"/>'
                 '<line x1="12" y1="4" x2="12" y2="15"/>'),
    "layout": ('<rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/>'
               '<rect x="14" y="14" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/>'),
    "columns": ('<rect x="3" y="4" width="18" height="16" rx="2"/><line x1="9" y1="4" x2="9" y2="20"/>'
                '<line x1="15" y1="4" x2="15" y2="20"/>'),
    "about": ('<circle cx="12" cy="12" r="9"/><line x1="12" y1="11" x2="12" y2="17"/>'
              '<line x1="12" y1="7.5" x2="12" y2="7.6"/>'),
}


def icon_names() -> list[str]:
    return list(ICONS)


def svg_for(name: str, color: str = "#444444", width: float = 1.9) -> str:
    """Complete SVG text of the toolbar icon ``name`` drawn in ``color``."""
    body = ICONS[name]
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
            f'stroke="{color}" stroke-width="{width}" stroke-linecap="round" stroke-linejoin="round">'
            f'{body}</svg>')


# ------------------------------------------------------------------------------------- logo
LOGO_SIZE = 256
BG = "#12263f"
ACCENT = "#38bdf8"

# ("rrect", x, y, w, h, radius, fill) | ("line", x1, y1, x2, y2, width, colour)
# | ("circle", cx, cy, r, fill, fill_opacity, stroke, stroke_width)
LOGO: list[tuple] = [
    ("rrect", 8, 8, 240, 240, 52, BG),
    ("rrect", 44, 56, 40, 16, 8, "#4ade80"), ("rrect", 94, 56, 118, 16, 8, "#cbd5e1"),
    ("rrect", 44, 90, 40, 16, 8, "#fbbf24"), ("rrect", 94, 90, 84, 16, 8, "#cbd5e1"),
    ("rrect", 44, 124, 40, 16, 8, "#f87171"), ("rrect", 94, 124, 104, 16, 8, "#cbd5e1"),
    ("rrect", 44, 158, 40, 16, 8, "#4ade80"), ("rrect", 94, 158, 64, 16, 8, "#cbd5e1"),
    ("circle", 150, 156, 46, BG, 0.82, ACCENT, 15),
    ("line", 184, 190, 222, 228, 20, ACCENT),
]


def logo_svg(size: int = LOGO_SIZE) -> str:
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" '
             f'viewBox="0 0 {LOGO_SIZE} {LOGO_SIZE}">']
    for sh in LOGO:
        kind = sh[0]
        if kind == "rrect":
            _, x, y, w, h, r, fill = sh
            parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}" fill="{fill}"/>')
        elif kind == "circle":
            _, cx, cy, r, fill, op, stroke, sw = sh
            parts.append(f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="{fill}" fill-opacity="{op}" '
                         f'stroke="{stroke}" stroke-width="{sw}"/>')
        elif kind == "line":
            _, x1, y1, x2, y2, w, color = sh
            parts.append(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{color}" '
                         f'stroke-width="{w}" stroke-linecap="round"/>')
    parts.append("</svg>")
    return "".join(parts)
