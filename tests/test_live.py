"""Live (growing-file) filter / search results must equal a fresh full scan."""

import os
import random
import tempfile
import threading
import unittest

import numpy as np

from logscope.core.reader import LogFileReader
from logscope.core.search import FilterParams, LineMatcher, plan_incremental, scan_matches
from logscope.core.views import ListView


class LiveScanTests(unittest.TestCase):
    """Replays what LogTab._filter_pump does after every append."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "g.log")

    def tearDown(self):
        self.dir.cleanup()

    def full(self, r, params):
        out = []
        scan_matches(r, LineMatcher.from_params(params), 0, r.count, threading.Event(),
                     out.append, params.context, block=7)
        return np.concatenate(out).tolist() if out else []

    def live_run(self, params, seed):
        rnd = random.Random(seed)
        open(self.path, "wb").close()
        r = LogFileReader(self.path)
        view, scanned, dirty = ListView(), 0, None
        matcher = LineMatcher.from_params(params)
        n = 0
        for _ in range(25):
            chunk = ""
            for _ in range(rnd.randint(1, 12)):
                n += 1
                chunk += ("ERROR boom %d" if rnd.random() < 0.25 else "info line %d") % n
                chunk += "\n"
            if rnd.random() < 0.3:                      # an unfinished last line
                chunk += "ERROR part" if rnd.random() < 0.5 else "info part"
            with open(self.path, "ab") as f:
                f.write(chunk.encode())
            res = r.poll()
            if res.kind == "appended":
                dirty = res.first_changed if dirty is None else min(dirty, res.first_changed)
            total = r.count
            if total <= scanned and dirty is None:
                continue
            drop, scan_from, emit_from = plan_incremental(scanned, dirty, params.context)
            dirty = None
            view.drop_from(drop)
            scanned = total
            out = []
            scan_matches(r, matcher, scan_from, total, threading.Event(), out.append,
                         params.context, block=5, emit_from=emit_from)
            for a in out:
                view.append(a)
            expect = self.full(r, params)
            self.assertEqual(view.snapshot().tolist(), expect, f"seed={seed} ctx={params.context}")
        r.close()

    def test_incremental_equals_full_scan(self):
        for ctx in (0, 1, 2, 5):
            for invert in (False, True):
                for seed in range(4):
                    with self.subTest(ctx=ctx, invert=invert, seed=seed):
                        if os.path.exists(self.path):
                            os.remove(self.path)
                        self.live_run(FilterParams("ERROR", invert=invert, context=ctx), seed)

    def test_plan(self):
        self.assertEqual(plan_incremental(100, None, 0), (100, 100, 100))
        self.assertEqual(plan_incremental(100, None, 3), (97, 94, 97))
        self.assertEqual(plan_incremental(100, 90, 0), (90, 90, 90))
        self.assertEqual(plan_incremental(100, 90, 2), (88, 86, 88))
        self.assertEqual(plan_incremental(2, None, 5), (0, 0, 0))


if __name__ == "__main__":
    unittest.main()
