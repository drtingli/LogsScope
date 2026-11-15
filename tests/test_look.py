import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

from logscope.core import actions
from logscope.core.bookmarks import Bookmark, bookmarks_from_list, bookmarks_to_list
from logscope.core.filters import (filters_from_json, filters_to_json, has_actions,
                                   read_filters_file, write_filters_file)
from logscope.core.highlight import HighlightEngine, HighlightRule, groups_from_json, groups_to_json
from logscope.core.reader import LogFileReader
from logscope.core.session import sanitize_filter, sanitize_session, session_actions, strip_actions
from logscope.core.settings import Settings
from logscope.core.search import FilterParams
from logscope.core.triggers import scan_and_run
from logscope.core.theme import DEFAULT_MARKER, default_theme, sanitize_theme, theme_from_json
from logscope.core import uitheme


class ThemeTests(unittest.TestCase):
    def test_defaults_and_roundtrip(self):
        t = default_theme()
        self.assertEqual(sanitize_theme(json.loads(json.dumps(t))), t)

    def test_bad_values_fall_back(self):
        t = sanitize_theme({
            "areas": {"main": {"font_family": " Consolas ", "font_size": 12, "fg": "#ABCDEF", "bg": "red"},
                      "search": {"font_size": 500, "fg": 5}, "bookmarks": "x", "bogus": {}},
            "selection": {"bg": "#000", "fg": "#112233"},
            "grid": {"horizontal": "yes", "vertical": False, "color": "#aabbcc"},
            "bookmark_marker": "  \U0001F516  "})
        self.assertEqual(t["areas"]["main"], {"font_family": "Consolas", "font_size": 12, "fg": "#abcdef", "bg": "", "row_spacing": 4})
        self.assertEqual(t["areas"]["search"]["font_size"], 0)
        self.assertEqual(t["areas"]["search"]["fg"], "")
        self.assertEqual(t["selection"], {"fg": "#112233", "bg": default_theme()["selection"]["bg"]})
        self.assertEqual(t["grid"], {"horizontal": True, "vertical": False, "color": "#aabbcc"})
        self.assertEqual(t["bookmark_marker"], "\U0001F516")
        self.assertEqual(sanitize_theme("junk"), default_theme())
        self.assertEqual(sanitize_theme({"bookmark_marker": ""})["bookmark_marker"], DEFAULT_MARKER)
        with self.assertRaises(ValueError):
            theme_from_json({"hello": 1})

    def test_settings_persist_theme(self):
        with tempfile.TemporaryDirectory() as d:
            s = Settings(Path(d))
            t = s.theme()
            t["areas"]["bookmarks"]["font_size"] = 14
            t["bookmark_marker"] = "\U0001F4CC"
            s.set_theme(t)
            back = Settings(Path(d)).theme()
            self.assertEqual(back["areas"]["bookmarks"]["font_size"], 14)
            self.assertEqual(back["bookmark_marker"], "\U0001F4CC")


def _luminance(hex_colour: str) -> float:
    def lin(c):
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (int(hex_colour[i:i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


class WindowThemeTests(unittest.TestCase):
    def test_mode_resolution(self):
        self.assertEqual(uitheme.sanitize_mode("dark"), "dark")
        for bad in ("Dark", "", None, 3):
            self.assertEqual(uitheme.sanitize_mode(bad), "system")
        self.assertEqual(uitheme.effective_scheme("system", True), "dark")
        self.assertEqual(uitheme.effective_scheme("system", False), "light")
        self.assertEqual(uitheme.effective_scheme("light", True), "light")
        self.assertEqual(uitheme.effective_scheme("junk", True), "dark")

    def test_default_grid_dims_on_dark_only(self):
        self.assertEqual(uitheme.grid_colour(uitheme.LIGHT_GRID, True), uitheme.DARK_GRID)
        self.assertEqual(uitheme.grid_colour(uitheme.LIGHT_GRID, False), uitheme.LIGHT_GRID)
        self.assertEqual(uitheme.grid_colour("#aabbcc", True), "#aabbcc")   # a chosen colour is kept

    def test_palettes_are_complete_and_readable(self):
        light, dark = uitheme.PALETTES["light"], uitheme.PALETTES["dark"]
        self.assertEqual(light["normal"].keys(), dark["normal"].keys())
        self.assertEqual(light["disabled"].keys(), dark["disabled"].keys())
        for name, pal in uitheme.PALETTES.items():
            n = pal["normal"]
            for fg, bg in (("Text", "Base"), ("WindowText", "Window"), ("ButtonText", "Button")):
                hi, lo = sorted((_luminance(n[fg]), _luminance(n[bg])), reverse=True)
                with self.subTest(theme=name, pair=fg):
                    self.assertGreaterEqual((hi + 0.05) / (lo + 0.05), 4.5)
        self.assertLess(_luminance(dark["normal"]["Base"]), _luminance(light["normal"]["Base"]))

    def test_setting_persists(self):
        with tempfile.TemporaryDirectory() as d:
            s = Settings(Path(d))
            self.assertEqual(s["ui_theme"], "system")
            s["ui_theme"] = "dark"
            self.assertEqual(Settings(Path(d))["ui_theme"], "dark")


class HighlightScopeTests(unittest.TestCase):
    def test_whole_line_vs_words(self):
        h = HighlightEngine([
            HighlightRule("fatal", False, False, "#fff", "#f00", True),
            HighlightRule("user", False, False, "#000", "#0f0", False),
            HighlightRule(r"\d+ms", True, False, "#000", "#00f", False),
        ])
        self.assertTrue(h.has_word_rules)
        self.assertEqual(h.match("a FATAL thing").bg, "#f00")
        self.assertIsNone(h.match("user took 15ms"))                  # word rules never colour the line
        self.assertEqual(h.spans("User took 15ms, user 7MS"),
                         [(0, 4, "#000", "#0f0"), (10, 14, "#000", "#00f"),
                          (16, 20, "#000", "#0f0"), (21, 24, "#000", "#00f")])

    def test_overlap_earlier_rule_wins_and_limit(self):
        h = HighlightEngine([HighlightRule("abc", False, False, "#111111", "#222222", False),
                             HighlightRule("bcd", False, False, "#333333", "#444444", False)])
        self.assertEqual(h.spans("abcd"), [(0, 3, "#111111", "#222222")])
        h2 = HighlightEngine([HighlightRule("a", False, True, "#000", "#fff", False)])
        self.assertEqual(len(h2.spans("a" * 1000)), 300)
        self.assertEqual(HighlightEngine([HighlightRule("x", whole_line=True)]).spans("xxx"), [])

    def test_regex_chars_in_plain_word_rule_are_literal(self):
        h = HighlightEngine([HighlightRule("a.b", False, False, "#000", "#fff", False)])
        self.assertEqual(len(h.spans("a.b axb")), 1)

    def test_io_keeps_scope_and_defaults_to_whole_line(self):
        g = {"G": [HighlightRule("x", False, False, "#000000", "#ffffff", False), HighlightRule("y")]}
        back = groups_from_json(json.loads(json.dumps(groups_to_json(g))))
        self.assertEqual([r.whole_line for r in back["G"]], [False, True])
        old = groups_from_json({"name": "O", "rules": [{"pattern": "p"}]})
        self.assertTrue(old["O"][0].whole_line)


class BookmarkMarkerTests(unittest.TestCase):
    def test_marker_roundtrip_and_old_files(self):
        bm = {0: Bookmark("a", ["t"], "\U0001F41E"), 3: Bookmark("b")}
        items = bookmarks_to_list(bm)
        self.assertEqual(items[0]["marker"], "\U0001F41E")
        self.assertNotIn("marker", items[1])
        self.assertEqual(bookmarks_from_list(json.loads(json.dumps(items))), bm)
        self.assertEqual(bookmarks_from_list([{"line": 2, "marker": 5}])[1].marker, "")


class ActionTests(unittest.TestCase):
    def test_sanitize(self):
        self.assertIsNone(actions.sanitize_action({"command": "  "}))
        self.assertIsNone(actions.sanitize_action("x"))
        a = actions.sanitize_action({"command": " run.exe ", "mode": "weird", "enabled": False})
        self.assertEqual(a, {"command": "run.exe", "mode": "first", "enabled": False})
        self.assertFalse(actions.is_active(a))
        self.assertTrue(actions.is_active({"command": "x", "mode": "every"}))

    def test_split_and_argv(self):
        self.assertEqual(actions.split_command('"C:\\Program Files\\x.exe" -v', windows=True),
                         ["C:\\Program Files\\x.exe", "-v"])
        self.assertEqual(actions.split_command("python -u 'a b.py'", windows=False), ["python", "-u", "a b.py"])
        with tempfile.TemporaryDirectory() as d:
            f = Path(d, "my tool.sh")
            f.write_text("x")
            self.assertEqual(actions.split_command(str(f)), [str(f)])          # existing file, unquoted
        with self.assertRaises(ValueError):
            actions.split_command("   ")
        with self.assertRaises(ValueError):
            actions.split_command('"unterminated', windows=False)
        self.assertEqual(actions.build_argv("tool -x", "the line\r\n", windows=False), ["tool", "-x", "the line"])
        self.assertEqual(actions.build_argv("job.py", "l", windows=False), [sys.executable, "job.py", "l"])

    def test_batch_files_get_sanitised_argument(self):
        argv = actions.build_argv("c:\\x\\notify.bat", 'a & del *.* | "q" %PATH% ^ !', windows=True)
        for ch in '&|<>^%"!':
            self.assertNotIn(ch, argv[-1])
        exe = actions.build_argv("c:\\x\\notify.exe", 'a & b "q"', windows=True)
        self.assertEqual(exe[-1], 'a & b "q"')                                  # real programs get it verbatim

    def test_huge_line_is_truncated(self):
        argv = actions.build_argv("tool", "x" * 100_000, windows=False)
        self.assertLessEqual(len(argv[-1]), actions.MAX_ARG)

    def test_launch_passes_whole_line_as_first_argument_and_env(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d, "out.txt")
            script = Path(d, "tool.py")
            script.write_text("import os, sys\nopen(sys.argv[1], 'w', encoding='utf-8').write("
                              "sys.argv[2] + '|' + os.environ['LOGSCOPE_LINE_NO'] + '|' + os.environ['LOGSCOPE_LINE'])\n")
            line = "2026-11-10 ERROR \"quoted\" 日志 & more | stuff  with   spaces"
            # the output path is argument 1 of the *script*; the log line is the last argument
            cmd = f'"{sys.executable}" "{script}" "{out}"'
            actions.launch(cmd, line, 42, "x.log", windows=(sys.platform == "win32"))
            for _ in range(100):
                if out.exists() and out.stat().st_size:
                    break
                time.sleep(0.05)
            self.assertEqual(out.read_text(encoding="utf-8"), f"{line}|42|{line}")

    def test_run_matches_modes_limit_and_errors(self):
        calls = []
        launcher = lambda cmd, text, no, path: calls.append((cmd, text, no, path))
        matches = [(i, f"line {i}") for i in range(5)]
        first = actions.run_matches({"command": "c", "mode": "first"}, matches, "f.log", launcher=launcher)
        self.assertEqual((first["launched"], first["fired"], calls), (1, True, [("c", "line 0", 1, "f.log")]))
        calls.clear()
        every = actions.run_matches({"command": "c", "mode": "every"}, matches, "f.log", 3, launcher)
        self.assertEqual((every["launched"], every["skipped"], len(calls)), (3, 2, 3))
        off = actions.run_matches({"command": "c", "enabled": False}, matches, "f.log", launcher=launcher)
        self.assertFalse(off["fired"])

        def bad(*a):
            raise OSError("no such program")
        res = actions.run_matches({"command": "c", "mode": "first"}, matches, "f.log", launcher=bad)
        self.assertEqual((res["launched"], res["fired"], res["errors"]), (0, True, ["no such program"]))


class FilterActionPersistenceTests(unittest.TestCase):
    SPEC = {"text": "ERR", "regex": True, "case": False, "invert": False, "context": 1,
            "action": {"command": "notify.bat", "mode": "every", "enabled": True}}

    def test_filter_and_session_keep_action(self):
        f = sanitize_filter(self.SPEC)
        self.assertEqual(f["action"], self.SPEC["action"])
        self.assertNotIn("action", sanitize_filter({"text": "x"}))
        self.assertNotIn("action", sanitize_filter({"text": "x", "action": {"command": ""}}))
        ses = sanitize_session({"tabs": [{"path": "p", "filter": self.SPEC}, {"path": "q"}]})
        self.assertEqual(session_actions(ses), ["notify.bat"])
        self.assertEqual(session_actions(strip_actions(ses)), [])
        self.assertIsNotNone(ses["tabs"][0]["filter"])

    def test_filters_file_roundtrip(self):
        flt = [{"name": "errors", **sanitize_filter(self.SPEC)}, {"name": "plain", **sanitize_filter({"text": "x"})}]
        self.assertTrue(has_actions(flt))
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "f.json")
            write_filters_file(p, flt)
            self.assertEqual(read_filters_file(p), flt)
        self.assertEqual(len(filters_from_json({"filters": [flt[0], {"name": " ", "text": "z"}, 5, {"text": "q"}]})), 1)
        for bad in ([], {}, {"filters": []}, {"filters": [{"name": "n"}]}):
            with self.assertRaises(ValueError):
                filters_from_json(bad)
        self.assertEqual(filters_to_json(flt)["filters"], flt)


class CompleteCountTests(unittest.TestCase):
    def test_last_unterminated_line_is_not_complete(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "a.log")
            Path(p).write_bytes(b"one\ntwo\npart")
            r = LogFileReader(p)
            r.poll()
            self.assertEqual((r.count, r.complete_count), (3, 2))
            with open(p, "ab") as f:
                f.write(b"ial\nthree\n")
            r.poll()
            self.assertEqual((r.count, r.complete_count), (4, 4))
            r.close()


class TriggerScanTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "t.log")
        Path(self.path).write_bytes(b"ERROR old1\nok\nERROR old2\n")
        self.r = LogFileReader(self.path)
        self.r.poll()
        self.calls = []
        self.launcher = lambda cmd, text, no, path: self.calls.append((text, no))

    def tearDown(self):
        self.r.close()
        self.dir.cleanup()

    def append(self, data: bytes):
        with open(self.path, "ab") as f:
            f.write(data)
        self.r.poll()

    def test_only_new_lines_fire_and_first_mode_stops_after_one(self):
        armed_at = self.r.complete_count                       # what LogTab records when arming
        self.append(b"fine\nERROR new1\nERROR new2\n")
        res = scan_and_run(self.r, FilterParams("ERROR"), {"command": "c", "mode": "first"}, self.path,
                           armed_at, self.r.complete_count, 25, launcher=self.launcher)
        self.assertEqual(self.calls, [("ERROR new1", 5)])
        self.assertEqual((res["fired"], res["launched"], res["matches"], res["last_line"]), (True, 1, 2, 5))

    def test_every_mode_passes_each_full_line_and_limits_bursts(self):
        start = self.r.complete_count
        self.append("".join(f"ERROR 日志 {i} & more\n" for i in range(10)).encode())
        res = scan_and_run(self.r, FilterParams("error"), {"command": "c", "mode": "every"}, self.path,
                           start, self.r.complete_count, 4, launcher=self.launcher)
        self.assertEqual([t for t, _ in self.calls], [f"ERROR 日志 {i} & more" for i in range(4)])
        self.assertEqual((res["launched"], res["skipped"], res["matches"]), (4, 6, 10))

    def test_unterminated_last_line_waits_until_complete(self):
        start = self.r.complete_count
        self.append(b"ERROR part")                              # no newline yet
        self.assertEqual(self.r.complete_count, start)
        none = scan_and_run(self.r, FilterParams("ERROR"), {"command": "c", "mode": "every"}, self.path,
                            start, self.r.complete_count, 5, launcher=self.launcher)
        self.assertEqual((none["fired"], self.calls), (False, []))
        self.append(b"ial line\n")
        scan_and_run(self.r, FilterParams("ERROR"), {"command": "c", "mode": "every"}, self.path,
                     start, self.r.complete_count, 5, launcher=self.launcher)
        self.assertEqual(self.calls, [("ERROR partial line", 4)])

    def test_invert_and_disabled(self):
        start = self.r.complete_count
        self.append(b"ERROR x\nquiet\n")
        scan_and_run(self.r, FilterParams("ERROR", invert=True), {"command": "c", "mode": "every"}, self.path,
                     start, self.r.complete_count, 5, launcher=self.launcher)
        self.assertEqual(self.calls, [("quiet", 5)])
        self.calls.clear()
        res = scan_and_run(self.r, FilterParams("ERROR"), {"command": "c", "enabled": False}, self.path,
                           start, self.r.complete_count, 5, launcher=self.launcher)
        self.assertEqual((res["fired"], self.calls), (False, []))


if __name__ == "__main__":
    unittest.main()
