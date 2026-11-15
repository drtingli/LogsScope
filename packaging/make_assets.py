"""Create the executable's icon and version resource.

    uv run --with pillow python packaging/make_assets.py

* ``logscope.ico`` / ``logscope.png``: the logo, drawn from the shapes in ``logscope.core.iconset``
  (the program draws the same shapes at run time, so window icon and exe icon always match);
* ``version_info.txt``: the Windows "Details" tab of the .exe (version, author, copyright), taken
  from ``logscope.about``.

The generated files are committed, so this only has to be run after changing the logo or the version.
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))

from logscope import __version__, about          # noqa: E402
from logscope.core import iconset                # noqa: E402

ICO_SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]


def _rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def render(size: int):
    """The logo as a Pillow RGBA image (4x supersampled, then reduced)."""
    from PIL import Image, ImageDraw

    scale = 4
    big = size * scale
    k = big / iconset.LOGO_SIZE
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    for sh in iconset.LOGO:
        kind = sh[0]
        layer = Image.new("RGBA", (big, big), (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        if kind == "rrect":
            _, x, y, w, h, r, fill = sh
            d.rounded_rectangle([x * k, y * k, (x + w) * k - 1, (y + h) * k - 1], radius=r * k, fill=_rgb(fill) + (255,))
        elif kind == "circle":
            _, cx, cy, r, fill, opacity, stroke, sw = sh
            d.ellipse([(cx - r) * k, (cy - r) * k, (cx + r) * k, (cy + r) * k],
                      fill=_rgb(fill) + (int(opacity * 255),))
            img = Image.alpha_composite(img, layer)                     # lens glass over the log lines
            layer = Image.new("RGBA", (big, big), (0, 0, 0, 0))
            d = ImageDraw.Draw(layer)
            ro, ri = (r + sw / 2) * k, (r - sw / 2) * k
            d.ellipse([cx * k - ro, cy * k - ro, cx * k + ro, cy * k + ro], fill=_rgb(stroke) + (255,))
            hole = Image.new("L", (big, big), 0)
            ImageDraw.Draw(hole).ellipse([cx * k - ri, cy * k - ri, cx * k + ri, cy * k + ri], fill=255)
            layer.paste((0, 0, 0, 0), mask=hole)                          # ring only
        elif kind == "line":
            _, x1, y1, x2, y2, w, color = sh
            c = _rgb(color) + (255,)
            d.line([x1 * k, y1 * k, x2 * k, y2 * k], fill=c, width=int(w * k))
            for px, py in ((x1, y1), (x2, y2)):                          # round caps
                d.ellipse([px * k - w * k / 2, py * k - w * k / 2, px * k + w * k / 2, py * k + w * k / 2], fill=c)
        img = Image.alpha_composite(img, layer)
    return img.resize((size, size), Image.LANCZOS)


def version_info() -> str:
    parts = [int(p) for p in __version__.split(".")[:3]] + [0] * 3
    v4 = tuple(parts[:3]) + (0,)
    def s(k, v):
        return f"StringStruct('{k}', '{v}')"
    rows = ", ".join([
        s("CompanyName", about.AUTHOR), s("FileDescription", about.TAGLINE),
        s("FileVersion", __version__), s("InternalName", "LogScope"),
        s("LegalCopyright", about.COPYRIGHT), s("OriginalFilename", "LogScope.exe"),
        s("ProductName", about.APP_NAME), s("ProductVersion", __version__),
    ])
    return (f"VSVersionInfo(\n  ffi=FixedFileInfo(filevers={v4}, prodvers={v4}, mask=0x3f, flags=0x0, "
            "OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),\n  kids=[\n"
            f"    StringFileInfo([StringTable('040904B0', [{rows}])]),\n"
            "    VarFileInfo([VarStruct('Translation', [1033, 1200])])\n  ]\n)\n")


def main() -> None:
    big = render(256)
    big.save(HERE / "logscope.png")
    big.save(HERE / "logscope.ico", format="ICO", sizes=ICO_SIZES)
    (HERE / "version_info.txt").write_text(version_info(), encoding="utf-8")
    print("wrote logscope.ico, logscope.png, version_info.txt in", HERE)


if __name__ == "__main__":
    main()
