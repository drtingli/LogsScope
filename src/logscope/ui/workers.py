"""Background threads: tailing/indexing and generic cancellable scans."""

from __future__ import annotations

import threading
import time

from PySide6.QtCore import QThread, Signal

from ..core import winio

POLL_BYTES = 16 * 1024 * 1024       # work per poll so the UI gets progress updates
EMIT_INTERVAL = 0.08                # seconds: coalesce UI updates (batched refresh)
IDLE_WAIT = 0.25                    # wake-up / polling interval while idle


class ReaderWorker(QThread):
    """Indexes the file, then follows it (tail).  Emits coalesced updates."""

    lines_changed = Signal(int, int)    # first_changed_line, total_lines
    reset = Signal()                    # file truncated / rotated
    caught_up = Signal()                # the initial indexing of the file is finished (once)
    failed = Signal(str)

    def __init__(self, reader, parent=None) -> None:
        super().__init__(parent)
        self.reader = reader
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        watcher = winio.ChangeWatcher(self.reader.path)
        pending_first: int | None = None
        last_emit = 0.0
        caught = False
        try:
            while not self._stop.is_set():
                res = self.reader.poll(max_bytes=POLL_BYTES)
                now = time.monotonic()
                if res.kind == "reset":
                    self.reset.emit()
                    pending_first = 0
                elif res.kind == "appended":
                    pending_first = res.first_changed if pending_first is None \
                        else min(pending_first, res.first_changed)
                if pending_first is not None and (not res.more or now - last_emit >= EMIT_INTERVAL):
                    self.lines_changed.emit(pending_first, res.count)
                    pending_first, last_emit = None, now
                if res.more:
                    continue
                if not caught:
                    caught = True
                    self.caught_up.emit()
                watcher.wait(IDLE_WAIT)
                # Cap the refresh rate for very chatty logs.
                spare = EMIT_INTERVAL - (time.monotonic() - now)
                if spare > 0:
                    self._stop.wait(spare)
        except Exception as exc:        # pragma: no cover - surfaced to the UI
            self.failed.emit(str(exc))
        finally:
            watcher.close()


class FuncWorker(QThread):
    """Runs ``fn(cancel_event, emit)`` off the UI thread (filter / search)."""

    batch = Signal(object)
    done = Signal(object)

    def __init__(self, fn, parent=None) -> None:
        super().__init__(parent)
        self._fn = fn
        self.cancel = threading.Event()

    def run(self) -> None:
        try:
            result = self._fn(self.cancel, self.batch.emit)
        except Exception as exc:        # e.g. invalid regular expression
            result = exc
        self.done.emit(result)
