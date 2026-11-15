import os
import tempfile
import threading
import unittest
from datetime import datetime
from pathlib import Path

import numpy as np

from logscope.core.clipfile import normalize_text, save_text_file, suggest_name
from logscope.core.columnizers import DefaultColumnizer, RegexColumnizer
from logscope.core.reader import LogFileReader
from logscope.core.widths import (display_width, fit_to_width, measure_columns, merge_max,
                                  pick_candidates)


class WidthMathTests(unittest.TestCase):
    def test_display_width_counts_cjk_double(self):
        self.assertEqual(display_width("abc"), 3)
        self.assertEqual(display_width("日志ab"), 6)

    def test_merge_max(self):
        self.assertEqual(merge_max([1, 5], [3]), [3, 5])
        self.assertEqual(merge_max([], [2, 2]), [2, 2])

    def test_fit_to_width(self):
        self.assertEqual(fit_to_width([], 100), [])
        out = fit_to_width([50, 60], 900)                  # fits: spare room to the last field
        self.assertEqual(out, [50, 850])
        out = fit_to_width([200, 5000, 6000], 1000)         # wide fields share what is left
        self.assertEqual(out[0], 200)
        self.assertEqual(sum(out), 1000)
        self.assertEqual(out[1], out[2])
        out = fit_to_width([500, 500, 500], 600)
        self.assertEqual(out, [200, 200, 200])
        self.assertTrue(all(w >= 48 for w in fit_to_width([1000] * 30, 100)))

    def test_pick_candidates_keeps_longest(self):
        lengths = np.ones(100_000, dtype=np.int64)
        lengths[77_777] = 9999
        idx = pick_candidates(lengths, 10, 100)
        self.assertIn(77_777, idx.tolist())
        self.assertLess(idx.size, 200)
        self.assertEqual(pick_candidates(np.ones(5, dtype=np.int64)).tolist(), [0, 1, 2, 3, 4])


class MeasureTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "w.log")

    def tearDown(self):
        self.dir.cleanup()

    def reader(self, text: str) -> LogFileReader:
        Path(self.path).write_text(text, encoding="utf-8", newline="")
        r = LogFileReader(self.path)
        r.poll()
        return r

    def test_line_lengths(self):
        r = self.reader("ab\r\nabcd\nxyz")
        self.assertEqual(r.line_lengths(np.array([0, 1, 2])).tolist(), [4, 5, 3])
        r.close()

    def test_whole_line_width_exact_even_in_big_file(self):
        lines = [f"line {i}" for i in range(50_000)]
        lines[31_337] = "X" * 300 + "日志"
        r = self.reader("\n".join(lines) + "\n")
        w = measure_columns(r, DefaultColumnizer(), np.arange(r.count), threading.Event())
        self.assertEqual(w, [304])
        r.close()

    def test_fields_of_a_columnizer(self):
        col = RegexColumnizer("kv", r"^(\S+) (\S+) (.*)$")
        r = self.reader("a bb ccc\nlonger b c\nx y zzzzzzzz\n")
        self.assertEqual(measure_columns(r, col, np.arange(r.count)), [6, 2, 8])
        self.assertEqual(measure_columns(r, col, np.array([1])), [6, 1, 1])
        self.assertEqual(measure_columns(r, col, np.empty(0, dtype=np.int64)), [0, 0, 0])
        r.close()

    def test_cancel(self):
        r = self.reader("\n".join("x" * 10 for _ in range(1000)) + "\n")
        ev = threading.Event()
        ev.set()
        w = measure_columns(r, DefaultColumnizer(), np.arange(r.count), ev)
        self.assertEqual(w, [0])
        r.close()


class ClipboardFileTests(unittest.TestCase):
    def test_name_and_normalise(self):
        self.assertEqual(suggest_name(datetime(2026, 11, 10, 23, 5, 9)), "clipboard-20261110-230509.log")
        self.assertEqual(normalize_text("a\r\nb\rc"), "a\nb\nc\n")
        self.assertEqual(normalize_text(""), "")
        self.assertEqual(normalize_text("x\n"), "x\n")

    def test_save_is_utf8_atomic_and_returns_line_count(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "c.log")
            self.assertEqual(save_text_file(p, "日志 1\r\nline 2"), 2)
            self.assertEqual(Path(p).read_bytes(), "日志 1\nline 2\n".encode("utf-8"))
            self.assertEqual(save_text_file(p, "only"), 1)           # overwrite
            self.assertEqual(os.listdir(d), ["c.log"])                # no temp file left behind
            with self.assertRaises(OSError):
                save_text_file(os.path.join(d, "missing", "x.log"), "t")
            self.assertEqual(os.listdir(d), ["c.log"])


if __name__ == "__main__":
    unittest.main()
