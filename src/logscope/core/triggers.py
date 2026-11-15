"""Scan newly arrived lines with a filter and start the filter's program for the matches (Qt-free)."""

from __future__ import annotations

import threading

import numpy as np

from . import actions
from .search import FilterParams, LineMatcher, scan_matches


def scan_and_run(reader, params: FilterParams, action: dict, log_path: str, start: int, stop: int,
                 limit: int, cancel: threading.Event | None = None, launcher=actions.launch) -> dict:
    """Look at lines ``[start, stop)``; start the program according to ``action['mode']``.

    Result keys: ``launched, skipped, errors, fired, last_line (1-based, 0 = none), matches``."""
    found: list[np.ndarray] = []
    scan_matches(reader, LineMatcher.from_params(params), start, stop, cancel or threading.Event(),
                 found.append, 0)
    lines = np.concatenate(found).tolist() if found else []
    every = action.get("mode") == "every"
    need = limit if every else 1
    picked = [(ln, reader.get_line(ln)) for ln in lines[:need]]
    res = actions.run_matches(action, picked, log_path, limit, launcher)
    if every:
        res["skipped"] += max(0, len(lines) - len(picked))
    res["last_line"] = picked[-1][0] + 1 if picked else 0
    res["matches"] = len(lines)
    return res
