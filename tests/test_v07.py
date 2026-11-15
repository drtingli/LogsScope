"""v0.7: regex columnizers, time alignment between files, merged view, find in result lists."""

import os
import random
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

import numpy as np

from logscope.core import derive
from logscope.core.columnizers import (
    ColumnizerRegistry, MergedColumnizer, RegexColumnizer, regex_columnizer_from_spec,
    sanitize_regex_spec, translate_pattern,
)
from logscope.core.reader import LogFileReader
from logscope.core.search import LineMatcher, find_in_rows
from logscope.core.session import sanitize_tab
from logscope.core.settings import Settings
from logscope.core.theme import blend, contrast_fg
from logscope.core.timestamps import TimestampParser
from logscope.core.mergeinfo import MergeInfo, MergeSource
from logscope.core.timesync import (
    effective_ms, format_offset, is_time_only, merge_order, nearest_line, parse_offset, scan_times, time_near,
)


def ts(sec, ms=0):
    """'2026-11-10 12:00:SS.mmm' with seconds counted from 12:00:00."""
    m, s = divmod(sec, 60)
    return f"2026-11-10 12:{m:02d}:{s:02d}.{ms:03d}"


class Tmp(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.readers = []

    def tearDown(self):
        for r in self.readers:
            r.release()
        shutil.rmtree(self.dir, ignore_errors=True)

    def reader(self, name, lines):
        p = os.path.join(self.dir, name)
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(lines) + "\n")
        r = LogFileReader(p)
        while r.poll().more:
            pass
        self.readers.append(r)
        return r


class RegexColumnizerTests(unittest.TestCase):
    def test_groups_are_columns(self):
        c = RegexColumnizer("t", r"(\d+)-(\w+) (.*)")
        self.assertEqual(c.column_names(), ["Col 1", "Col 2", "Col 3"])
        self.assertEqual(c.split("12-ab hello world"), ["12", "ab", "hello world"])

    def test_named_groups_net_syntax_and_custom_titles(self):
        c = RegexColumnizer("t", r"(?<id>\d+) (?P<who>\w+) (.*)", names=["", "User"])
        self.assertEqual(c.column_names(), ["id", "User", "Col 3"])
        self.assertEqual(translate_pattern(r"(?<=a)(?<!b)(?<x>c)"), r"(?<=a)(?<!b)(?P<x>c)")

    def test_unmatched_lines(self):
        for mode, want in (("last", ["", "junk"]), ("first", ["junk", ""]), ("empty", ["", ""])):
            c = RegexColumnizer("t", r"(\d+) (\w+)", unmatched=mode)
            self.assertEqual(c.split("junk"), want)

    def test_search_anywhere_and_ignore_case(self):
        c = RegexColumnizer("t", r"user=(\w+)", search=True, ignore_case=True)
        self.assertEqual(c.split("login USER=bob ok"), ["bob"])
        self.assertEqual(RegexColumnizer("t", r"user=(\w+)").split("login user=bob"), ["login user=bob"])  # no match

    def test_errors(self):
        for bad in ("((", "no groups", "(a)" * 65):
            with self.assertRaises(ValueError):
                RegexColumnizer("t", bad)
        with self.assertRaises(ValueError):
            RegexColumnizer("t", "(a)", unmatched="nowhere")

    def test_empty_optional_group_is_empty_text(self):
        self.assertEqual(RegexColumnizer("t", r"(a)(x)?(b)").split("ab"), ["a", "", "b"])

    def test_sanitize_spec(self):
        self.assertIsNone(sanitize_regex_spec({"name": "", "pattern": "(a)"}))
        self.assertIsNone(sanitize_regex_spec({"name": "x"}))
        self.assertIsNone(sanitize_regex_spec("nope"))
        s = sanitize_regex_spec({"name": " x ", "pattern": "(a)", "unmatched": "weird", "names": ["A", 3]})
        self.assertEqual(s["name"], "x")
        self.assertEqual(s["unmatched"], "last")
        self.assertEqual(s["names"], ["A"])
        self.assertFalse(s["auto"])
        with self.assertRaises(ValueError):
            regex_columnizer_from_spec({"name": "x", "pattern": "plain"})

    def test_registry_user_columnizers(self):
        reg = ColumnizerRegistry()
        errors = reg.set_user_regex([
            {"name": "mine", "pattern": r"(\w+)=(\w+)"},
            {"name": "JSON lines", "pattern": "(a)"},          # clashes with a built-in
            {"name": "bad", "pattern": "(("},
            {"name": "mine", "pattern": "(b)"},                # clashes with an earlier user entry
            "junk",
        ])
        self.assertEqual(len(errors), 2)
        self.assertIn("mine", reg.names())
        self.assertIn("JSON lines (regex)", reg.names())
        self.assertEqual(len(reg.user_names()), 3)
        reg.set_user_regex([])                                    # replaced, built-ins untouched
        self.assertEqual(reg.user_names(), [])
        self.assertIn("JSON lines", reg.names())
        self.assertNotIn("mine", reg.names())

    def test_auto_detection_only_when_enabled(self):
        sample = ["k1=v1 x"] * 10
        reg = ColumnizerRegistry()
        reg.set_user_regex([{"name": "kv", "pattern": r"(\w+)=(\w+) (.*)"}])
        self.assertEqual(reg.auto_detect(sample).name, reg.default.name)
        reg.set_user_regex([{"name": "kv", "pattern": r"(\w+)=(\w+) (.*)", "auto": True}])
        self.assertEqual(reg.auto_detect(sample).name, "kv")
        stamped = ["2026-11-10 12:00:00 INFO hello"] * 10          # a user regex wins ties with built-ins
        reg.set_user_regex([{"name": "mine", "pattern": r"(\S+ \S+) (\w+) (.*)", "auto": True}])
        self.assertEqual(reg.auto_detect(stamped).name, "mine")

    def test_merged_columnizer_adds_source(self):
        class M:
            def path_of(self, n):
                return f"/log/{n}.log"
        c = MergedColumnizer(RegexColumnizer("r", r"(\w+) (\w+)"), M())
        self.assertEqual(c.column_names(), ["Source", "Col 1", "Col 2"])
        self.assertEqual(c.split_at(7, "a b"), ["/log/7.log", "a", "b"])
        self.assertEqual(c.split("a b"), ["", "a", "b"])


class OffsetTests(unittest.TestCase):
    def test_parse(self):
        cases = {"": 0, "+01:00": 3600, "-1:30:15.5": -5415.5, "3600": 3600, "-250ms": -0.25,
                 "1.5h": 5400, "90 m": 5400, "2,5": 2.5, "-00:00:02": -2, "1d": 86400}
        for text, want in cases.items():
            self.assertAlmostEqual(parse_offset(text), want, places=6, msg=text)

    def test_parse_rejects_garbage(self):
        for bad in ("abc", "1:75", "1:00:61", "99999999999", "1x", "--1"):
            with self.assertRaises(ValueError, msg=bad):
                parse_offset(bad)

    def test_format(self):
        self.assertEqual(format_offset(0), "+00:00:00")
        self.assertEqual(format_offset(-5415.5), "-01:30:15.500")
        self.assertEqual(parse_offset(format_offset(7384.25)), 7384.25)


class NearestLineTests(Tmp):
    def parser(self, r):
        p = TimestampParser.detect([l for l in (r.get_line(i) for i in range(min(40, r.count)))
                                    if l[:4].isdigit()])
        self.assertTrue(p.usable)
        return p

    def test_exact_and_between(self):
        lines = [f"{ts(s)} line {s}" for s in range(0, 100, 2)]          # 12:00:00 .. 12:01:38 step 2s
        r = self.reader("a.log", lines)
        p = self.parser(r)
        base = p.seconds(lines[0])
        n = lambda sec: nearest_line(r.get_line, r.count, p, base + sec)
        self.assertEqual(n(0)[0], 0)
        self.assertEqual(n(20)[0], 10)
        self.assertEqual(n(21)[0], 10)                 # 1 s after line 10, 1 s before line 11 -> earlier
        self.assertEqual(n(21.1)[0], 11)
        self.assertEqual(n(-50)[0], 0)                 # before the file
        self.assertEqual(n(5000)[0], 49)               # after the file

    def test_continuation_lines_are_skipped(self):
        lines = []
        for s in range(20):
            lines.append(f"{ts(s)} start {s}")
            lines += [f"    at frame {k}" for k in range(5)]
        r = self.reader("a.log", lines)
        p = self.parser(r)
        base = p.seconds(lines[0])
        for want in (0, 7, 13, 19):
            line, t = nearest_line(r.get_line, r.count, p, base + want)
            self.assertEqual(line, want * 6)
            self.assertAlmostEqual(t, base + want)

    def test_matches_brute_force(self):
        rnd = random.Random(7)
        t, lines = 0, []
        for i in range(400):
            t += rnd.choice((0, 0, 1, 3, 10))
            lines.append(f"{ts(t % 3600)} m{i}" if rnd.random() < 0.8 else f"   cont {i}")
        lines.insert(0, f"{ts(0)} first")
        r = self.reader("a.log", lines)
        p = self.parser(r)
        stamps = [(i, p.seconds(l)) for i, l in enumerate(lines) if p.seconds(l) is not None]
        base = stamps[0][1]
        for _ in range(60):
            target = base + rnd.uniform(-5, stamps[-1][1] - base + 5)
            got = nearest_line(r.get_line, r.count, p, target)
            best = min(abs(tt - target) for _, tt in stamps)
            self.assertAlmostEqual(abs(got[1] - target), best, places=6)

    def test_no_timestamps(self):
        r = self.reader("a.log", ["plain"] * 50)
        self.assertIsNone(nearest_line(r.get_line, r.count, TimestampParser.builtin("ISO 8601  2026-11-10 12:34:56.789"), 0))
        self.assertIsNone(nearest_line(r.get_line, 0, TimestampParser.none(), 0))


class TimeNearTests(Tmp):
    def test_unstamped_lines_borrow_the_neighbour(self):
        lines = [f"{ts(1)} a", "  cont", "  cont", f"{ts(9)} b", "  tail"]
        r = self.reader("a.log", lines)
        p = TimestampParser.detect([lines[0], lines[3]])
        near = lambda n: time_near(r.get_line, r.count, p, n)
        self.assertEqual(near(0), p.seconds(lines[0]))
        self.assertEqual(near(2), p.seconds(lines[0]))      # a stamped line before it wins
        self.assertEqual(near(4), p.seconds(lines[3]))
        self.assertIsNone(near(99))
        self.assertIsNone(time_near(r.get_line, r.count, TimestampParser.none(), 0))
        r2 = self.reader("b.log", ["  cont", f"{ts(5)} x"])
        self.assertEqual(time_near(r2.get_line, 2, p, 0), p.seconds(f"{ts(5)} x"))   # only a later one exists

    def test_kinds(self):
        self.assertTrue(is_time_only(12 * 3600.0))
        self.assertFalse(is_time_only(1.7e9))


class MergeInfoTests(unittest.TestCase):
    def test_sources_styles_and_counts(self):
        srcs = [MergeSource("/a.log", "a", "#ff0000"), MergeSource("/b.log", "b", "")]
        info = MergeInfo(srcs, np.array([0, 1, 1, 0, 0], dtype=np.int16))
        self.assertEqual(info.path_of(2), "/b.log")
        self.assertEqual(info.path_of(9), "")
        self.assertIsNone(info.source_of(-1))
        self.assertEqual(info.counts(), [3, 2])
        info.update_styles("#ffffff", "#000000")
        fg, soft, solid = info.style(0)
        self.assertEqual(solid, "#ff0000")
        self.assertEqual(soft, blend("#ff0000", "#ffffff", 0.28))
        self.assertEqual(fg, "#000000")
        self.assertIsNone(info.style(1))                       # a file without a colour keeps the panel look
        self.assertIsNone(info.style(77))

    def test_colour_follows_the_live_tab(self):
        class Tab:
            tab_color, title = "#00ff00", "renamed"
        t = Tab()
        src = MergeSource("/a.log", "a", "#ff0000", t)
        info = MergeInfo([src], np.zeros(2, dtype=np.int16))
        info.update_styles("#ffffff", "#000000")
        self.assertEqual(info.style(0)[2], "#00ff00")
        self.assertEqual(src.title, "renamed")
        del t                                                  # tab closed: the last colour stays
        info.update_styles("#ffffff", "#000000")
        self.assertEqual(info.style(0)[2], "#00ff00")


class MergeTests(Tmp):
    def parser_for(self, r):
        return TimestampParser.detect([r.get_line(i) for i in range(min(20, r.count))])

    def test_scan_and_effective_ms(self):
        r = self.reader("a.log", ["cont0", f"{ts(1)} a", "cont", f"{ts(3)} b", f"{ts(2)} out of order", "cont"])
        p = self.parser_for(r)
        t = scan_times(r, p)
        self.assertTrue(np.isnan(t[0]) and np.isnan(t[2]))
        ms = effective_ms(t)
        base = int(round(p.seconds(f"{ts(0)} x") * 1000))
        self.assertEqual((ms - base).tolist(), [1000, 1000, 1000, 3000, 3000, 3000])   # fill + monotone
        self.assertEqual((effective_ms(t, 2.5) - base).tolist()[0], 3500)
        with self.assertRaises(ValueError):
            effective_ms(np.array([np.nan, np.nan]))
        self.assertEqual(effective_ms(np.empty(0)).size, 0)

    def test_millisecond_resolution(self):
        t = np.array([1000.0004, 1000.0006, 1000.0014])
        self.assertEqual(effective_ms(t).tolist(), [1000000, 1000001, 1000001])

    def merged(self, *lists):
        fi, li = merge_order([np.array(m, dtype=np.int64) for m in lists])
        return list(zip(fi.tolist(), li.tolist()))

    def test_interleaved_by_time(self):
        self.assertEqual(self.merged([1, 5, 9], [2, 3, 10]),
                         [(0, 0), (1, 0), (1, 1), (0, 1), (0, 2), (1, 2)])

    def test_equal_times_form_one_block_per_file(self):
        out = self.merged([5, 5, 5], [5, 5])
        self.assertEqual(out, [(0, 0), (0, 1), (0, 2), (1, 0), (1, 1)])

    def test_previous_file_continues_first(self):
        # file 1 writes at 1, 2 and 3; file 0 at 2 and 3.  At ms 2 file 1 goes first (it wrote the
        # previous line); at ms 3 file 0 goes first (it wrote the line before) -- no block is split.
        out = self.merged([2, 3], [1, 2, 3])
        self.assertEqual(out, [(1, 0), (1, 1), (0, 0), (0, 1), (1, 2)])
        # Without a previous line the lower file index starts.
        self.assertEqual(self.merged([4], [4]), [(0, 0), (1, 0)])

    def test_three_files_and_empty(self):
        out = self.merged([1, 1], [1], [1, 2])
        self.assertEqual(out, [(0, 0), (0, 1), (1, 0), (2, 0), (2, 1)])
        self.assertEqual(self.merged([], [3]), [(1, 0)])
        fi, li = merge_order([])
        self.assertEqual(fi.size, 0)

    def test_order_properties_random(self):
        rnd = random.Random(3)
        files = []
        for _ in range(4):
            t, arr = 0, []
            for _ in range(rnd.randint(0, 300)):
                t += rnd.choice((0, 0, 0, 1, 2))
                arr.append(t)
            files.append(np.array(arr, dtype=np.int64))
        fi, li = merge_order(files)
        self.assertEqual(fi.size, sum(f.size for f in files))
        seen = {(a, b) for a, b in zip(fi.tolist(), li.tolist())}
        self.assertEqual(len(seen), fi.size)                                   # every line exactly once
        times = [files[a][b] for a, b in zip(fi.tolist(), li.tolist())]
        self.assertEqual(times, sorted(times))                                  # globally in time order
        for f in range(4):                                                      # each file keeps its order
            mine = li[fi == f]
            self.assertEqual(mine.tolist(), sorted(mine.tolist()))
        # same time, same file: one contiguous run
        runs = {}
        prev = None
        for a, b, t in zip(fi.tolist(), li.tolist(), times):
            key = (t, a)
            if prev != key:
                self.assertNotIn(key, runs, "a file's lines of one millisecond were split")
                runs[key] = True
            prev = key

    def test_write_merged_end_to_end(self):
        a = self.reader("a.log", [f"{ts(1)} a1", f"{ts(1)} a2", "  a-cont", f"{ts(4)} a3"])
        b = self.reader("b.log", [f"{ts(1)} b1", f"{ts(2)} b2", f"{ts(4)} b3", "  b-cont"])
        ms = [effective_ms(scan_times(r, self.parser_for(r))) for r in (a, b)]
        fi, li = merge_order(ms)
        dest = os.path.join(self.dir, "merged.log")
        derive.write_merged([a, b], fi, li, dest)
        got = Path(dest).read_text(encoding="utf-8").splitlines()
        self.assertEqual([x.strip() for x in got],
                         [f"{ts(1)} a1", f"{ts(1)} a2", "a-cont", f"{ts(1)} b1", f"{ts(2)} b2",
                          f"{ts(4)} b3", "b-cont", f"{ts(4)} a3"])      # at 12:00:04 b continues, a follows

    def test_write_merged_with_offset_and_cancel(self):
        a = self.reader("a.log", [f"{ts(s)} a{s}" for s in range(5)])
        b = self.reader("b.log", [f"{ts(s)} b{s}" for s in range(5)])
        pa, pb = self.parser_for(a), self.parser_for(b)
        ms = [effective_ms(scan_times(a, pa)), effective_ms(scan_times(b, pb), offset=2.0)]   # b is 2 s behind
        fi, li = merge_order(ms)
        dest = os.path.join(self.dir, "m2.log")
        derive.write_merged([a, b], fi, li, dest)
        texts = [l.split()[-1] for l in Path(dest).read_text(encoding="utf-8").splitlines()]
        self.assertEqual(texts, ["a0", "a1", "a2", "b0", "b1", "a3", "a4", "b2", "b3", "b4"])
        ev = threading.Event()
        ev.set()
        dest2 = os.path.join(self.dir, "m3.log")
        with self.assertRaises(InterruptedError):
            derive.write_merged([a, b], fi, li, dest2, ev)
        self.assertFalse(os.path.exists(dest2))

    def test_big_merge_is_consistent(self):
        n = 30_000
        a = self.reader("a.log", [f"2026-11-10 12:{(i // 600) % 60:02d}:{(i // 10) % 60:02d}.{(i % 10) * 100:03d} a{i}" for i in range(n)])
        b = self.reader("b.log", [f"2026-11-10 12:{(i // 300) % 60:02d}:{(i // 5) % 60:02d}.{(i % 5) * 200:03d} b{i}" for i in range(n // 2)])
        ms = [effective_ms(scan_times(r, self.parser_for(r))) for r in (a, b)]
        fi, li = merge_order(ms)
        dest = os.path.join(self.dir, "big.log")
        derive.write_merged([a, b], fi, li, dest)
        out = Path(dest).read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(out), n + n // 2)
        for pos in (0, 1, 777, len(out) - 1):
            src = (a, b)[int(fi[pos])]
            self.assertEqual(out[pos], src.get_line(int(li[pos])))


class FindInRowsTests(Tmp):
    def setUp(self):
        super().setUp()
        self.r = self.reader("a.log", [f"line {i} {'x' if i % 10 == 0 else 'y'}" for i in range(100)])
        self.rows = np.arange(0, 100, 5, dtype=np.int64)          # rows 0..19 show lines 0,5,10,...

    def find(self, text, start, forward):
        return find_in_rows(self.r, LineMatcher(text), self.rows, start, forward, threading.Event())

    def test_forward_backward_and_wrap(self):
        self.assertEqual(self.find(" x", -1, True), (0, False))
        self.assertEqual(self.find(" x", 0, True), (2, False))
        self.assertEqual(self.find(" x", 18, True), (0, True))              # wraps to the top
        self.assertEqual(self.find(" x", 2, False), (0, False))
        self.assertEqual(self.find(" x", 0, False), (18, True))             # wraps to the bottom
        self.assertEqual(self.find(" x", -1, False), (18, False))

    def test_single_match_wraps_onto_itself(self):
        self.assertEqual(self.find("line 35 ", 7, True), (7, True))
        self.assertEqual(self.find("line 35 ", 7, False), (7, True))

    def test_nothing_and_cancel(self):
        self.assertEqual(self.find("zzz", 3, True), (-1, False))
        ev = threading.Event()
        ev.set()
        self.assertEqual(find_in_rows(self.r, LineMatcher("x"), self.rows, 0, True, ev), (-1, False))
        self.assertEqual(find_in_rows(self.r, LineMatcher("x"), np.empty(0, dtype=np.int64), 0, True,
                                      threading.Event()), (-1, False))

    def test_unsorted_rows_and_regex(self):
        rows = np.array([50, 10, 30, 20], dtype=np.int64)
        res = find_in_rows(self.r, LineMatcher(r"line (10|20) ", True), rows, -1, True, threading.Event())
        self.assertEqual(res, (1, False))


class SessionAndColourTests(unittest.TestCase):
    def test_tab_link_and_offset_are_kept(self):
        t = sanitize_tab({"path": "a.log", "linked": True, "ts_offset": 3600.5})
        self.assertTrue(t["linked"])
        self.assertEqual(t["ts_offset"], 3600.5)
        for junk in ("x", None, float("nan"), float("inf"), 1e12, True):
            t = sanitize_tab({"path": "a.log", "linked": "yes", "ts_offset": junk})
            self.assertFalse(t["linked"])
            self.assertEqual(t["ts_offset"], 0.0)

    def test_blend_and_contrast(self):
        self.assertEqual(blend("#ff0000", "#ffffff", 0.0), "#ffffff")
        self.assertEqual(blend("#ff0000", "#ffffff", 1.0), "#ff0000")
        self.assertEqual(blend("#000000", "#ffffff", 0.5), "#808080")
        self.assertEqual(contrast_fg("#ffffff"), "#000000")
        self.assertEqual(contrast_fg("#101010"), "#ffffff")
        self.assertEqual(contrast_fg("#ffffff", "#222222"), "#222222")       # preferred colour is readable
        self.assertEqual(contrast_fg("#202020", "#303030"), "#ffffff")       # preferred colour is not

    def test_settings_defaults(self):
        d = tempfile.mkdtemp()
        try:
            s = Settings(d)
            self.assertTrue(s["time_sync"])
            self.assertEqual(s["regex_columnizers"], [])
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
