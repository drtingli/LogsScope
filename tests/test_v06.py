"""v0.6: timestamps / time differences, derived files (to new tab), tab colours."""

import os
import shutil
import tempfile
import threading
import unittest
from datetime import datetime, timezone

import numpy as np

from logscope.core import derive
from logscope.core.columnizers import ColumnizerRegistry, TimestampColumnizer
from logscope.core.reader import LogFileReader
from logscope.core.session import is_color, sanitize_session, sanitize_tab
from logscope.core.settings import Settings
from logscope.core.timestamps import (
    FORMAT_NAMES, MAX_LINES, TimestampParser, compute_delta, format_duration, format_to_regex,
)
from logscope.core.views import ListView


def epoch(y, mo, d, h=0, mi=0, s=0):
    return datetime(y, mo, d, h, mi, s, tzinfo=timezone.utc).timestamp()


class ParserTests(unittest.TestCase):
    def check(self, line, expect, spec=""):
        p = TimestampParser.from_spec(spec, [line])
        self.assertTrue(p.usable, line)
        self.assertAlmostEqual(p.seconds(line), expect, places=3, msg=line)

    def test_iso_variants(self):
        base = epoch(2026, 11, 10, 12, 34, 56)
        self.check("2026-11-10 12:34:56 INFO hi", base)
        self.check("2026-11-10T12:34:56.789 x", base + 0.789)
        self.check("[2026-11-10 12:34:56,250] x", base + 0.25)
        self.check("2026-11-10T12:34:56Z x", base)
        self.check("2026-11-10T13:34:56+01:00 x", base)
        self.check("2026-11-10 12:34:56.123456789 x", base + 0.123456789)

    def test_other_formats(self):
        self.check("10.11.2026 12:34:56,5 text", epoch(2026, 11, 10, 12, 34, 56) + 0.5)
        self.check('1.2.3.4 - - [10/Nov/2026:12:34:56 +0100] "GET /"', epoch(2026, 11, 10, 11, 34, 56))
        self.check("2026/11/10 12:34:56 x", epoch(2026, 11, 10, 12, 34, 56))
        self.check("1762777496 x", 1762777496.0)
        self.check("1762777496123 x", 1762777496.123)
        year = datetime.now().year
        self.check("Nov 10 12:34:56 host app: x", epoch(year, 11, 10, 12, 34, 56))
        self.check("12:34:56.5 x", 12 * 3600 + 34 * 60 + 56.5)

    def test_detect_needs_most_lines(self):
        sample = ["2026-11-10 12:00:00 a"] * 6 + ["no stamp"] * 4
        self.assertTrue(TimestampParser.detect(sample).usable)
        self.assertFalse(TimestampParser.detect(["no stamp here"] * 10).usable)
        self.assertFalse(TimestampParser.detect(["2026-11-10 12:00:00 a"] + ["x"] * 9).usable)
        self.assertFalse(TimestampParser.detect([]).usable)

    def test_invalid_dates_are_not_timestamps(self):
        p = TimestampParser.builtin(FORMAT_NAMES[0])
        self.assertIsNone(p.seconds("2026-13-45 99:99:99 x"))
        self.assertIsNone(p.seconds("x" * 200 + "2026-11-10 12:00:00"))   # beyond the search window

    def test_custom_format(self):
        p = TimestampParser.from_format("%d.%m.%Y %H:%M:%S,%f")
        self.assertAlmostEqual(p.seconds("<<10.11.2026 12:34:56,125>> x"), epoch(2026, 11, 10, 12, 34, 56) + 0.125, 3)
        p = TimestampParser.from_format("%b %d %Y %I:%M:%S %p")
        self.assertAlmostEqual(p.seconds("Nov 10 2026 01:02:03 PM"), epoch(2026, 11, 10, 13, 2, 3), 3)
        p = TimestampParser.from_format("%y%m%d-%H%M%S")
        self.assertAlmostEqual(p.seconds("261110-123456 x"), epoch(2026, 11, 10, 12, 34, 56), 3)
        for bad in ("%Q", "%Y-%m-%d", "%H:%M %H", "%"):
            with self.assertRaises(ValueError):
                format_to_regex(bad)
        self.assertTrue(TimestampParser.from_spec("%Q nonsense", ["2026-11-10 12:00:00 a"]).usable)  # bad spec -> auto

    def test_span_and_columnizer(self):
        p = TimestampParser.detect(["[2026-11-10 12:00:00.5] hello"])
        v, a, b = p.parse("[2026-11-10 12:00:00.5] hello")
        self.assertEqual("[2026-11-10 12:00:00.5] hello"[a:b].strip(), "2026-11-10 12:00:00.5")
        c = TimestampColumnizer(p)
        self.assertEqual(c.split("[2026-11-10 12:00:00.5] hello"), ["2026-11-10", "12:00:00.5", "hello"])
        self.assertEqual(c.split("no stamp"), ["", "", "no stamp"])
        self.assertEqual(c.column_names(), ["Date", "Time", "Message"])

    def test_registry_auto_detect(self):
        reg = ColumnizerRegistry()
        apache = ['1.2.3.4 - - [10/Nov/2026:12:34:%02d +0100] "GET /"' % i for i in range(20)]
        self.assertIsInstance(reg.auto_detect(apache), TimestampColumnizer)
        self.assertIs(reg.auto_detect(["plain text"] * 20), reg.default)


class DeltaTests(unittest.TestCase):
    LINES = [f"2026-11-10 12:00:{i:02d}.000 line {i}" if i % 5 else f"no stamp {i}" for i in range(60)]

    def test_basic(self):
        p = TimestampParser.detect(self.LINES)
        rep = compute_delta([3, 4, 6, 41], self.LINES.__getitem__, p)
        self.assertEqual((rep.selected, rep.stamped), (4, 4))
        self.assertEqual((rep.first_line, rep.last_line), (3, 41))
        self.assertAlmostEqual(rep.total, 38.0)
        self.assertAlmostEqual(rep.max_gap, 35.0)
        self.assertEqual((rep.max_gap_from + 1, rep.max_gap_to + 1), (7, 42))
        self.assertAlmostEqual(rep.mean_gap, 38 / 3)
        self.assertTrue(rep.ok)
        self.assertIn("0:00:38.000", rep.summary())
        self.assertIn("Largest gap", rep.details())

    def test_order_duplicates_and_unstamped(self):
        p = TimestampParser.detect(self.LINES)
        rep = compute_delta(np.array([41, 3, 3, 5, 10]), self.LINES.__getitem__, p)
        self.assertEqual(rep.selected, 4)            # 3, 5, 10, 41 (duplicates removed)
        self.assertEqual(rep.stamped, 2)             # lines 5 and 10 have no stamp
        self.assertAlmostEqual(rep.total, 38.0)

    def test_too_few_stamps(self):
        p = TimestampParser.detect(self.LINES)
        rep = compute_delta([5, 10, 3], self.LINES.__getitem__, p)
        self.assertFalse(rep.ok)
        self.assertEqual(rep.stamped, 1)
        self.assertIn("at least 2", rep.summary())

    def test_negative_when_time_goes_backwards(self):
        lines = ["2026-11-10 12:00:10 a", "2026-11-10 12:00:04 b"]
        rep = compute_delta([0, 1], lines.__getitem__, TimestampParser.detect(lines))
        self.assertAlmostEqual(rep.total, -6.0)
        self.assertAlmostEqual(rep.span, 6.0)
        self.assertIn("-0:00:06.000", rep.summary())

    def test_sampling_keeps_first_and_last_exact(self):
        n = 5000
        get = lambda i: f"2026-11-10 {i // 3600:02d}:{i // 60 % 60:02d}:{i % 60:02d} x"
        p = TimestampParser.detect([get(i) for i in range(10)])
        rep = compute_delta(np.arange(n), get, p, limit=300)
        self.assertTrue(rep.truncated)
        self.assertEqual((rep.first_line, rep.last_line), (0, n - 1))
        self.assertAlmostEqual(rep.total, n - 1)
        self.assertLessEqual(rep.stamped, 300)
        self.assertEqual(rep.selected, n)
        self.assertGreater(MAX_LINES, 1000)

    def test_across_midnight_and_zones(self):
        lines = ["2026-11-10T23:59:59+01:00 a", "2026-11-11T00:00:01+01:00 b",
                 "2026-11-10T23:00:02Z c"]
        rep = compute_delta([0, 1], lines.__getitem__, TimestampParser.detect(lines))
        self.assertAlmostEqual(rep.total, 2.0)
        rep = compute_delta([0, 2], lines.__getitem__, TimestampParser.detect(lines))
        self.assertAlmostEqual(rep.total, 23 * 3600 + 2 - (22 * 3600 + 59 * 60 + 59), 3)

    def test_format_duration(self):
        self.assertEqual(format_duration(0), "0:00:00.000")
        self.assertEqual(format_duration(3725.5), "1:02:05.500")
        self.assertEqual(format_duration(-0.25), "-0:00:00.250")
        self.assertEqual(format_duration(90061.001), "1d 1:01:01.001")


class DerivedFileTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.src = os.path.join(self.dir, "a.log")
        with open(self.src, "w", encoding="utf-8", newline="") as f:
            for i in range(3000):
                f.write(f"2026-11-10 12:00:00 line {i} \u4e2d\u6587\r\n" if i % 2 else f"plain {i}\n")
        self.reader = LogFileReader(self.src)
        while self.reader.poll().more:
            pass

    def tearDown(self):
        self.reader.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_write_and_origin_map(self):
        dest = derive.new_path("filter of a.log")
        self.assertTrue(derive.is_derived_path(dest))
        origin = derive.write_lines(self.reader, [1500, 3, 3, 5, 2999, 9999, -4], dest)
        self.assertEqual(origin.tolist(), [3, 5, 1500, 2999])        # sorted, unique, in range
        with open(dest, encoding="utf-8", newline="") as f:
            text = f.read()
        self.assertEqual(text, "".join(self.reader.get_line(i) + "\n" for i in origin.tolist()))
        self.assertNotIn("\r", text)
        r2 = LogFileReader(dest, "utf-8")                              # opens as a normal file
        while r2.poll().more:
            pass
        self.assertEqual(r2.count, 4)
        self.assertEqual(r2.get_line(0), self.reader.get_line(3))
        r2.close()
        self.assertTrue(derive.remove_file(dest))
        self.assertFalse(os.path.exists(dest))

    def test_sparse_and_dense_runs(self):
        dest = derive.new_path("x")
        lines = list(range(0, 3000, 7)) + list(range(100, 700))
        origin = derive.write_lines(self.reader, lines, dest)
        with open(dest, encoding="utf-8") as f:
            got = f.read().split("\n")[:-1]
        self.assertEqual(got, [self.reader.get_line(i) for i in origin.tolist()])
        derive.remove_file(dest)

    def test_cancel_removes_the_file(self):
        dest = derive.new_path("c")
        ev = threading.Event()
        ev.set()
        with self.assertRaises(InterruptedError):
            derive.write_lines(self.reader, range(3000), dest, cancel=ev)
        self.assertFalse(os.path.exists(dest))

    def test_never_deletes_foreign_files(self):
        self.assertFalse(derive.remove_file(self.src))
        self.assertTrue(os.path.exists(self.src))
        self.assertFalse(derive.is_derived_path(self.src))

    def test_save_copy_is_atomic_and_exact(self):
        dest = derive.new_path("save-me")
        derive.write_lines(self.reader, range(10, 50), dest)
        out = os.path.join(self.dir, "saved.log")
        derive.save_copy(dest, out)
        with open(out, "rb") as a, open(dest, "rb") as b:
            self.assertEqual(a.read(), b.read())
        self.assertEqual([f for f in os.listdir(self.dir) if f.startswith(".save-")], [])
        derive.remove_file(dest)

    def test_second_level(self):
        d1 = derive.new_path("l1")
        o1 = derive.write_lines(self.reader, range(0, 3000, 2), d1)       # the "plain" lines
        r1 = LogFileReader(d1, "utf-8")
        while r1.poll().more:
            pass
        d2 = derive.new_path("l2")
        o2 = derive.write_lines(r1, [0, 10, 20], d2)
        self.assertEqual(o1[o2].tolist(), [0, 20, 40])                     # maps back to the original
        r1.close()
        derive.remove_file(d1)
        derive.remove_file(d2)


class ViewAndSettingsTests(unittest.TestCase):
    def test_list_view_slice(self):
        v = ListView(np.array([5, 9, 12, 40], dtype=np.int64))
        self.assertEqual(v.slice(1, 3).tolist(), [9, 12])
        self.assertEqual(v.slice(-3, 99).tolist(), [5, 9, 12, 40])
        self.assertEqual(v.slice(3, 1).tolist(), [])

    def test_tab_colors_persist_and_validate(self):
        d = tempfile.mkdtemp()
        try:
            s = Settings(d)
            self.assertEqual(s.tab_color("/x/a.log"), "")
            s.set_tab_color("/x/a.log", "#E74C3C")
            s.set_tab_color("/x/b.log", "not a colour")
            s2 = Settings(d)
            self.assertEqual(s2.tab_color("/x/a.log"), "#e74c3c")
            self.assertEqual(s2.tab_color("/x/b.log"), "")
            s2.set_tab_color("/x/a.log", "")
            self.assertEqual(Settings(d).tab_color("/x/a.log"), "")
            for i in range(400):
                s2.set_tab_color(f"/f/{i}.log", "#112233")
            self.assertLessEqual(len(Settings(d)["tab_colors"]), 300)
            self.assertEqual(Settings(d)["layout"], "tabs")
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_session_keeps_colour_title_and_format(self):
        tab = sanitize_tab({"path": "a.log", "color": "#ABCDEF", "title": "  My tab  ",
                            "ts_format": "%H:%M:%S"})
        self.assertEqual((tab["color"], tab["title"], tab["ts_format"]), ("#abcdef", "My tab", "%H:%M:%S"))
        bad = sanitize_tab({"path": "a.log", "color": "red", "title": 5, "ts_format": None})
        self.assertEqual((bad["color"], bad["title"], bad["ts_format"]), ("", "", ""))
        old = sanitize_tab({"path": "a.log"})                               # sessions from v0.5
        self.assertEqual(old["color"], "")
        self.assertEqual(sanitize_session({"tabs": [old]})["tabs"][0]["path"], "a.log")
        self.assertTrue(is_color("#00ff7f"))
        self.assertFalse(is_color("#00ff7"))


if __name__ == "__main__":
    unittest.main()
