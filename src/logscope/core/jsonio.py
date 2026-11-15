"""Small JSON file helpers shared by settings, sessions and bookmark files."""

from __future__ import annotations

import json
import os
from pathlib import Path


def atomic_write_json(path: str | Path, obj) -> None:
    """Write JSON so that a crash never leaves a half-written file behind."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def read_json(path: str | Path):
    """Read JSON; raises OSError (cannot read) or ValueError (not valid JSON)."""
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))
