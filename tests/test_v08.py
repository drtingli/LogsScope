"""v0.8: named parsing rules bound to file-name patterns, About information."""

import tempfile
import unittest
from pathlib import Path

from logscope import __version__, about
from logscope.core.columnizers import (
    ColumnizerRegistry, file_pattern_matches, path_matches, regex_columnizer_from_spec, sanitize_regex_spec,
    split_file_patterns,
)
from logscope.core.settings import Settings
from logscope.core.timestamps import TimestampParser

PIPE = ("2026-11-13T17:02:13.447 | Info    | Debug     | 35308 | XHardwareInterface.cpp:120     "
        "# [ flatPanel-RADOne ] [+] Display status changed  Waiting")
BRACKET = ("[2026-11-13T17:02:11.976]sys=I2C|;|room=tcr 51|;|process=adaPTinsight|;|class=XCU|;|"
           "instance=localhost|;|SafetySignature=A8E0E3B1")

PIPE_RULE = {
    "name": "Pipe log",
    "pattern": r"^(?P<Timestamp>\S+)\s*\|\s*(?P<Level>\S+)\s*\|\s*(?P<Type>[^|]*?)\s*\|\s*(?P<Thread>[^|]*?)\s*\|"
               r"\s*(?P<Source>[^#|]*?)\s*#\s?(?P<Message>.*)$",
    "files": ["adaptinsight*.log"],
}
BRACKET_RULE = {"name": "Bracket log", "pattern": r"^\[(?P<Timestamp>[^\]]+)\](?P<Message>.*)$",
                "files": ["*/i2c/*.txt", "re:/sys_\\d+\\.log$"]}


class FilePatterns(unittest.TestCase):
    def test_name_only(self):
        self.assertTrue(file_pattern_matches("*.log", r"C:\logs\App.LOG"))
        self.assertTrue(file_pattern_matches("app_??.txt", "/var/app_01.txt"))
        self.assertFalse(file_pattern_matches("*.log", "/var/app.log.1"))
        self.assertFalse(file_pattern_matches("app.log", "/var/myapp.log"))

    def test_folder_part(self):
        self.assertTrue(file_pattern_matches("logs/*.log", r"D:\data\logs\a.log"))
        self.assertFalse(file_pattern_matches("logs/*.log", r"D:\data\other\a.log"))
        self.assertTrue(file_pattern_matches("*/i2c/*.txt", "/x/i2c/y.txt"))

    def test_regex(self):
        self.assertTrue(file_pattern_matches(r"re:/sys_\d+\.log$", r"C:\a\SYS_12.log"))
        self.assertFalse(file_pattern_matches(r"re:/sys_\d+\.log$", "/a/sys_x.log"))
        self.assertFalse(file_pattern_matches("re:(", "/a/b"))        # a broken pattern never matches

    def test_split(self):
        self.assertEqual(split_file_patterns(" *.log ; a*.txt;;*.log\nb.csv"), ["*.log", "a*.txt", "b.csv"])
        self.assertEqual(split_file_patterns(""), [])
        self.assertTrue(path_matches(["*.x", "*.log"], "a.log"))
        self.assertFalse(path_matches([], "a.log"))


class Rules(unittest.TestCase):
    def test_pipe_format(self):
        c = regex_columnizer_from_spec(PIPE_RULE)
        self.assertEqual(c.column_names(), ["Date", "Time", "Level", "Type", "Thread", "Source", "Message"])
        self.assertEqual(c.split(PIPE), ["2026-11-13", "17:02:13.447", "Info", "Debug", "35308",
                                         "XHardwareInterface.cpp:120",
                                         "[ flatPanel-RADOne ] [+] Display status changed  Waiting"])
        row = c.split("2026-11-13T17:02:14.001 | Error | Hardware | 1 | A.cpp:5 # msg | with pipe # and hash")
        self.assertEqual(row[2], "Error")
        self.assertEqual(row[6], "msg | with pipe # and hash")
        self.assertEqual(c.split("   at stack frame")[-1], "   at stack frame")      # continuation line

    def test_bracket_format(self):
        c = regex_columnizer_from_spec(BRACKET_RULE)
        self.assertEqual(c.column_names(), ["Date", "Time", "Message"])
        d, t, msg = c.split(BRACKET)
        self.assertEqual((d, t), ("2026-11-13", "17:02:11.976"))
        self.assertTrue(msg.startswith("sys=I2C|;|room=tcr 51"))
        self.assertTrue(msg.endswith("SafetySignature=A8E0E3B1"))

    def test_timestamp_parsers_see_both_formats(self):
        for line in (PIPE, BRACKET):
            p = TimestampParser.detect([line, line])
            self.assertTrue(p.usable)
            self.assertIsNotNone(p.parse(line))

    def test_sanitize_files(self):
        s = sanitize_regex_spec({"name": "n", "pattern": "(a)", "files": [" *.log ", 5, "", "x" * 500]})
        self.assertEqual(s["files"][0], "*.log")
        self.assertEqual(len(s["files"]), 2)
        self.assertEqual(len(s["files"][1]), 200)
        self.assertEqual(sanitize_regex_spec({"name": "n", "pattern": "(a)", "files": "oops"})["files"], [])
        self.assertEqual(sanitize_regex_spec({"name": "n", "pattern": "(a)"})["files"], [])

    def test_bad_file_regex_is_rejected(self):
        with self.assertRaises(ValueError):
            regex_columnizer_from_spec({"name": "n", "pattern": "(a)", "files": ["re:("]})


class Registry(unittest.TestCase):
    def test_for_path_first_match_wins(self):
        reg = ColumnizerRegistry()
        errors = reg.set_user_regex([
            {"name": "A", "pattern": "(a)", "files": ["*.log"]},
            {"name": "B", "pattern": "(b)", "files": ["special*.log"]},
            {"name": "C", "pattern": "(c)"},
        ])
        self.assertEqual(errors, [])
        self.assertEqual(reg.for_path("/x/special1.log").name, "A")       # list order decides
        self.assertEqual(reg.for_path("/x/other.txt"), None)
        reg.set_user_regex([{"name": "B", "pattern": "(b)", "files": ["special*.log"]},
                            {"name": "A", "pattern": "(a)", "files": ["*.log"]}])
        self.assertEqual(reg.for_path("/x/special1.log").name, "B")
        self.assertEqual(reg.for_path("/x/z.log").name, "A")

    def test_unnamed_rules_never_bind(self):
        reg = ColumnizerRegistry()
        reg.set_user_regex([{"name": "C", "pattern": "(c)"}])
        self.assertIsNone(reg.for_path("/anything.log"))

    def test_clash_with_builtin_keeps_files(self):
        reg = ColumnizerRegistry()
        reg.set_user_regex([{"name": "JSON lines", "pattern": "(j)", "files": ["*.jl"]}])
        c = reg.for_path("a.jl")
        self.assertIsNotNone(c)
        self.assertNotEqual(c.name, "JSON lines")

    def test_settings_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            st = Settings(Path(d))
            st["regex_columnizers"] = [PIPE_RULE, BRACKET_RULE]
            st2 = Settings(Path(d))
            reg = ColumnizerRegistry()
            self.assertEqual(reg.set_user_regex(st2["regex_columnizers"]), [])
            self.assertEqual(reg.for_path("C:/x/AdaPTinsight_1.log").name, "Pipe log")
            self.assertEqual(reg.for_path("/data/sys_7.log").name, "Bracket log")
            self.assertEqual(reg.for_path("/data/i2c/q.txt").name, "Bracket log")


class About(unittest.TestCase):
    def test_version_and_author(self):
        self.assertEqual(__version__, "0.1.0")
        html = about.about_html()
        self.assertIn(__version__, html)
        self.assertIn(about.AUTHOR, html)
        text = about.info_text("/tmp/cfg")
        self.assertIn(f"LogScope {__version__}", text)
        self.assertIn("Python:", text)
        self.assertIn("Settings folder: /tmp/cfg", text)

    def test_changelog_mentions_current_version(self):
        self.assertEqual(about.CHANGELOG[0][0], __version__)


if __name__ == "__main__":
    unittest.main()


from logscope.core.columnizers import RegexColumnizer, TimestampColumnizer
from logscope.core.timefields import (
    DATE_TIME_MESSAGE, DATETIME_SPLIT, SNIPPETS, fields_epoch, join_datetime, split_datetime,
)


class DateTimeSplit(unittest.TestCase):
    def test_split_variants(self):
        cases = {
            "2026-11-13T17:02:11.976": ("2026-11-13", "17:02:11.976"),
            "2026-11-13 17:02:11,976Z": ("2026-11-13", "17:02:11,976Z"),
            "2026-11-13T17:02:11+01:00": ("2026-11-13", "17:02:11+01:00"),
            "13.11.2026 17:02:11,123": ("13.11.2026", "17:02:11,123"),
            "11/13/2026 5:02:11 PM": ("11/13/2026", "5:02:11 PM"),
            "Nov 13 17:02:11": ("Nov 13", "17:02:11"),
            "17:02:11.976": ("", "17:02:11.976"),
        }
        for text, want in cases.items():
            self.assertEqual(split_datetime(text), want, text)
        self.assertIsNone(split_datetime("1731510131"))
        self.assertIsNone(split_datetime("2026-11-13"))
        self.assertIsNone(split_datetime(""))

    def test_fields_give_the_same_seconds_as_the_whole_stamp(self):
        for stamp in ("2026-11-13T17:02:11.976", "2026-11-13 17:02:11,976", "13.11.2026 17:02:11,976"):
            d, t = split_datetime(stamp)
            whole = TimestampParser.detect([stamp]).parse(stamp)[0]
            self.assertAlmostEqual(fields_epoch(d, t), whole, places=3, msg=stamp)
        self.assertEqual(join_datetime("", "17:02"), "17:02")

    def test_predefined_rule(self):
        c = RegexColumnizer("dt", DATE_TIME_MESSAGE)
        self.assertEqual(c.column_names(), ["Date", "Time", "Message"])
        self.assertEqual(c.column_roles(), ["date", "time", "text"])
        self.assertEqual(c.split(BRACKET)[:2], ["2026-11-13", "17:02:11.976"])
        self.assertTrue(c.split(BRACKET)[2].startswith("sys=I2C|;|"))
        self.assertEqual(c.split("2026-11-13 17:02:13,5 | Info | x")[:2], ["2026-11-13", "17:02:13,5"])

    def test_snippets_compile_and_work_together(self):
        import re
        for label, snip in SNIPPETS:
            re.compile(snip)
        iso = dict(SNIPPETS)["ISO 8601 date + time as two columns"]
        m = re.match(iso, "2026-11-13T17:02:11.976Z rest")
        self.assertEqual(m.group("Date"), "2026-11-13")
        self.assertEqual(m.group("Time"), "17:02:11.976Z")
        self.assertIsNotNone(DATETIME_SPLIT.match("2026-11-13T17:02:11"))

    def test_regex_roles(self):
        c = RegexColumnizer("r", r"^(?P<DateTime>\S+) (?P<Level>\w+) (?P<Clock>\S+) (.*)$", split_timestamp=False)
        self.assertEqual(c.column_roles(), ["datetime", "text", "time", "text"])
        c2 = RegexColumnizer("r", r"^(?P<DateTime>\S+) (?P<Level>\w+) (?P<Clock>\S+) (.*)$")
        self.assertEqual(c2.column_roles(), ["date", "time", "text", "time", "text"])


class TimestampColumnizerSplit(unittest.TestCase):
    def test_bracket_format_two_time_columns(self):
        p = TimestampParser.detect([BRACKET] * 3)
        c = TimestampColumnizer(p)
        self.assertEqual(c.column_names(), ["Date", "Time", "Message"])
        d, t, msg = c.split(BRACKET)
        self.assertEqual((d, t), ("2026-11-13", "17:02:11.976"))
        self.assertTrue(msg.startswith("sys=I2C"))
        self.assertAlmostEqual(fields_epoch(d, t, p), p.parse(BRACKET)[0], places=3)
        self.assertEqual(c.split("   at stack frame"), ["", "", "   at stack frame"])

    def test_auto_detect_picks_split_for_bracket_format_with_few_stamped_lines(self):
        reg = ColumnizerRegistry()
        head = [BRACKET.replace("11.976", f"1{i}.000") for i in range(4)] + ["  continuation"] * 6
        self.assertIsInstance(reg.auto_detect(head), TimestampColumnizer)

    def test_level_only_counts_when_present(self):
        reg = ColumnizerRegistry()
        no_level = [f"2026-11-13 17:02:{i:02d}.100 Starting the service {i}" for i in range(10)]
        self.assertIsInstance(reg.auto_detect(no_level), TimestampColumnizer)    # "Starting" is no level
        lvl = [f"2026-11-13 17:02:{i:02d}.100 | Info | x {i}" for i in range(10)]
        c = reg.auto_detect(lvl)
        self.assertEqual(c.name, "Date / Time / Level / Message")
        self.assertEqual(c.split(lvl[0])[:3], ["2026-11-13", "17:02:00.100", "Info"])


from logscope.core.search import selection_to_query


class FindPrefill(unittest.TestCase):
    def test_plain(self):
        self.assertEqual(selection_to_query("  Display status changed "), "Display status changed")

    def test_first_nonempty_line_of_multiline(self):
        self.assertEqual(selection_to_query("\n\n  first \nsecond"), "first")
        self.assertEqual(selection_to_query("a\u2029b"), "a")              # Qt paragraph separator

    def test_regex_mode_escapes(self):
        import re
        q = selection_to_query("[ flatPanel-RADOne ] [+] x.y", regex=True)
        self.assertIsNotNone(re.search(q, "..[ flatPanel-RADOne ] [+] x.y.."))
        self.assertIsNone(re.search(q, "[ flatPanel-RADOne ] [+] xzy"))

    def test_empty_and_cap(self):
        self.assertEqual(selection_to_query(""), "")
        self.assertEqual(selection_to_query(" \n "), "")
        self.assertEqual(len(selection_to_query("x" * 2000)), 500)


import ast
import subprocess
import sys
import xml.etree.ElementTree as ET

from logscope.core import iconset
from logscope.core.settings import DEFAULTS
from logscope.winconsole import should_free

ROOT = Path(__file__).resolve().parent.parent


class Icons(unittest.TestCase):
    def test_every_icon_is_valid_svg(self):
        self.assertGreaterEqual(len(iconset.icon_names()), 15)
        for name in iconset.icon_names():
            root = ET.fromstring(iconset.svg_for(name, "#123456"))
            self.assertTrue(root.tag.endswith("svg"))
            self.assertEqual(root.get("stroke"), "#123456")

    def test_icons_used_by_the_toolbar_exist(self):
        src = (ROOT / "src/logscope/ui/main_window.py").read_text(encoding="utf-8") + \
              (ROOT / "src/logscope/ui/detached.py").read_text(encoding="utf-8")
        import re
        used = set(re.findall(r'"(\w+)", "[^"]+"\)', src))                # decorate(..., "name", "Caption")
        used |= set(re.findall(r'\(self\.\w+, "(\w+)", "', src))
        wanted = {"open", "paste", "save", "follow", "autofit", "wrap", "fit", "find", "panel", "bookmark",
                  "delta", "sync", "link", "merge", "detach", "reattach"}
        for name in wanted:
            self.assertIn(name, iconset.ICONS, name)
            self.assertIn(name, used, f"{name} is not assigned to a toolbar action")

    def test_logo(self):
        root = ET.fromstring(iconset.logo_svg(64))
        self.assertEqual(root.get("width"), "64")
        self.assertGreater(len(list(root)), 5)

    def test_toolbar_defaults(self):
        self.assertIn(DEFAULTS["toolbar_style"], ("icon", "text", "icon_text_beside", "icon_text_under"))
        self.assertIn(DEFAULTS["toolbar_icon_size"], (16, 20, 24, 32))


class Packaging(unittest.TestCase):
    def test_files_exist_and_parse(self):
        for name in ("logscope.ico", "logscope.png", "version_info.txt", "logscope.spec", "launcher.py",
                     "build_exe.ps1", "make_assets.py"):
            self.assertTrue((ROOT / "packaging" / name).is_file(), name)
        for py in ("logscope.spec", "launcher.py", "make_assets.py"):
            ast.parse((ROOT / "packaging" / py).read_text(encoding="utf-8"))
        ico = (ROOT / "packaging" / "logscope.ico").read_bytes()
        self.assertEqual(ico[:4], b"\x00\x00\x01\x00")                      # ICO header
        self.assertGreaterEqual(int.from_bytes(ico[4:6], "little"), 5)      # several sizes inside

    def test_spec_has_no_console_and_icon(self):
        spec = (ROOT / "packaging" / "logscope.spec").read_text(encoding="utf-8")
        self.assertIn("console=False", spec)
        self.assertIn("logscope.ico", spec)

    def test_version_resource_matches(self):
        text = (ROOT / "packaging" / "version_info.txt").read_text(encoding="utf-8")
        self.assertIn(f"'FileVersion', '{__version__}'", text)
        self.assertIn(about.AUTHOR, text)

    def test_gui_script_entry(self):
        toml = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('logscope-gui = "logscope.app:gui_main"', toml)
        self.assertIn(f'version = "{__version__}"', toml)

    def test_app_module_imports_without_qt_side_effects(self):
        from logscope import app
        self.assertTrue(callable(app.gui_main))
        self.assertTrue(callable(app.main))


class Console(unittest.TestCase):
    def test_free_only_when_no_shell_shares_the_console(self):
        self.assertTrue(should_free(["logscope-gui.exe", "python.exe"]))
        self.assertTrue(should_free(["LogScope.exe"] if False else ["logscope.exe", "pythonw.exe"]))
        self.assertFalse(should_free(["cmd.exe", "python.exe"]))
        self.assertFalse(should_free(["powershell.exe", "logscope.exe"]))
        self.assertFalse(should_free([]))


from logscope.core.theme import (
    AREAS, DEFAULT_ROW_SPACING, MAX_ROW_SPACING, default_theme, nudge_row_spacing, reset_row_spacing,
    sanitize_theme,
)


class RowSpacing(unittest.TestCase):
    def test_defaults_keep_the_old_row_heights(self):
        t = default_theme()
        self.assertEqual({a: t["areas"][a]["row_spacing"] for a in AREAS},
                         {"main": 4, "filter": 4, "search": 4, "bookmarks": 6})

    def test_old_theme_without_the_key_gets_defaults(self):
        old = {"areas": {"main": {"font_family": "Consolas", "font_size": 11, "fg": "", "bg": ""}}}
        t = sanitize_theme(old)
        self.assertEqual(t["areas"]["main"]["row_spacing"], 4)
        self.assertEqual(t["areas"]["main"]["font_size"], 11)

    def test_validation(self):
        for bad in (-1, MAX_ROW_SPACING + 1, "8", 3.5, True, None):
            t = sanitize_theme({"areas": {"filter": {"row_spacing": bad}}})
            self.assertEqual(t["areas"]["filter"]["row_spacing"], DEFAULT_ROW_SPACING["filter"], bad)
        t = sanitize_theme({"areas": {"filter": {"row_spacing": 0}, "search": {"row_spacing": 12}}})
        self.assertEqual((t["areas"]["filter"]["row_spacing"], t["areas"]["search"]["row_spacing"]), (0, 12))

    def test_nudge_and_reset(self):
        t = default_theme()
        looser = nudge_row_spacing(t, 3)
        self.assertEqual(looser["areas"]["main"]["row_spacing"], 7)
        self.assertEqual(t["areas"]["main"]["row_spacing"], 4)                  # the original is untouched
        self.assertEqual(nudge_row_spacing(t, -99)["areas"]["bookmarks"]["row_spacing"], 0)
        self.assertEqual(nudge_row_spacing(t, 99)["areas"]["search"]["row_spacing"], MAX_ROW_SPACING)
        self.assertEqual(reset_row_spacing(looser), t)

    def test_survives_the_settings_file(self):
        with tempfile.TemporaryDirectory() as d:
            st = Settings(Path(d))
            st.set_theme(nudge_row_spacing(st.theme(), 5))
            self.assertEqual(Settings(Path(d)).theme()["areas"]["filter"]["row_spacing"], 9)


from logscope.core.session import clean_pins, sanitize_tab


class StickyAndColumns(unittest.TestCase):
    def test_pins_are_cleaned(self):
        self.assertEqual(clean_pins([5, 2, 5, -1, "x", True, 9]), [2, 5, 9])
        self.assertEqual(clean_pins("nope"), [])
        self.assertEqual(len(clean_pins(list(range(5000)))), 1000)

    def test_pins_survive_the_session(self):
        tab = sanitize_tab({"path": "/x/a.log", "pinned": [3, 1, 3]})
        self.assertEqual(tab["pinned"], [1, 3])
        self.assertEqual(sanitize_tab({"path": "/x/a.log"})["pinned"], [])

    def test_gutter_constants(self):
        src = (ROOT / "src/logscope/ui/models.py").read_text(encoding="utf-8")
        self.assertIn("COL_MARK, COL_PIN, COL_LINE = 0, 1, 2", src)
        self.assertIn("GUTTER = 3", src)

    def test_no_stale_first_column_assumptions(self):
        """Fields start after the three gutter columns everywhere in the UI."""
        import re
        for name in ("tableview.py", "models.py", "log_tab.py", "sticky.py"):
            text = (ROOT / "src/logscope/ui" / name).read_text(encoding="utf-8")
            self.assertIsNone(re.search(r"range\(1, .*columnCount", text), name)
            self.assertNotIn("column() > 0", text, name)
            self.assertNotIn("source_col = 1", text, name)


def _undefined_names(path):
    import builtins
    tree = ast.parse(path.read_text(encoding="utf-8"))
    defined = set(dir(builtins))
    for n in ast.walk(tree):
        if isinstance(n, (ast.Import, ast.ImportFrom)):
            defined.update((a.asname or a.name).split(".")[0] for a in n.names)
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defined.add(n.name)
        elif isinstance(n, ast.arg):
            defined.add(n.arg)
        elif isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
            defined.add(n.id)
        elif isinstance(n, ast.ExceptHandler) and n.name:
            defined.add(n.name)
    return sorted({n.id for n in ast.walk(tree)
                   if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load) and n.id not in defined})


class UiModulesStatic(unittest.TestCase):
    """The UI cannot be imported without PySide6; at least catch typos and missing imports."""

    def test_no_undefined_names(self):
        for path in sorted((ROOT / "src/logscope").rglob("*.py")):
            self.assertEqual(_undefined_names(path), [], str(path))


from logscope.core import ctxmenu
from logscope.core.columnizers import Columnizer, DefaultColumnizer


class HeaderText(unittest.TestCase):
    def test_single_column_header_is_message(self):
        self.assertEqual(DefaultColumnizer().column_names(), ["Message"])
        self.assertEqual(Columnizer().column_names(), ["Message"])
        self.assertEqual(ColumnizerRegistry().default.column_names(), ["Message"])


class ContextMenu(unittest.TestCase):
    CTX = {"has_row": True, "has_field": True, "n_selected": 1, "panel": "main", "in_strip": False,
           "derived": False, "field": "Display status changed", "all_bookmarked": False, "all_pinned": False}

    def test_default_menu_is_valid_and_tidy(self):
        m = ctxmenu.default_menu()
        self.assertEqual(m, ctxmenu.sanitize_menu(m))
        ids = [i["id"] for i in m if i["type"] == "builtin"]
        self.assertEqual(len(ids), len(set(ids)))
        for must in ("copy_lines", "copy_field", "find_text", "filter_include", "bookmark", "pin", "customize"):
            self.assertIn(must, ids)
        self.assertNotEqual(m[0]["type"], "separator")
        self.assertNotEqual(m[-1]["type"], "separator")

    def test_sanitize(self):
        raw = [{"type": "separator"}, {"type": "builtin", "id": "copy_lines"}, {"type": "builtin", "id": "copy_lines"},
               {"type": "builtin", "id": "nope"}, {"type": "separator"}, {"type": "separator"},
               {"type": "command", "label": " Edit ", "command": " notepad.exe ", "args": "-n{lineno}"},
               {"type": "command", "label": "", "command": "x"}, {"type": "command", "label": "y", "command": 5},
               "junk", None, {"type": "separator"}]
        out = ctxmenu.sanitize_menu(raw)
        self.assertEqual([i["type"] for i in out], ["builtin", "separator", "command"])
        self.assertEqual(out[2], {"type": "command", "label": "Edit", "command": "notepad.exe", "args": "-n{lineno}"})
        self.assertEqual(ctxmenu.sanitize_menu("garbage"), ctxmenu.default_menu())
        self.assertEqual(ctxmenu.sanitize_menu([]), [])                       # everything removed stays removed

    def test_visibility(self):
        v = ctxmenu.visibility
        item = lambda i: {"type": "builtin", "id": i}                         # noqa: E731
        self.assertEqual(v(item("copy_lines"), self.CTX), "show")
        self.assertEqual(v(item("goto_main"), self.CTX), "hide")             # already in the main view
        self.assertEqual(v(item("goto_main"), {**self.CTX, "panel": "filter"}), "show")
        self.assertEqual(v(item("goto_main"), {**self.CTX, "in_strip": True}), "show")
        self.assertEqual(v(item("time_diff"), self.CTX), "disabled")         # needs two lines
        self.assertEqual(v(item("time_diff"), {**self.CTX, "n_selected": 3}), "show")
        self.assertEqual(v(item("jump_source"), self.CTX), "hide")
        self.assertEqual(v(item("jump_source"), {**self.CTX, "derived": True}), "show")
        self.assertEqual(v(item("filter_include"), {**self.CTX, "has_field": False}), "disabled")
        self.assertEqual(v(item("copy_lines"), {**self.CTX, "has_row": False}), "disabled")
        self.assertEqual(v(item("customize"), {"has_row": False}), "show")

    def test_labels(self):
        d = ctxmenu.dynamic_label
        self.assertEqual(d({"type": "builtin", "id": "filter_include"}, self.CTX),
                         "Filter: only lines containing \u201cDisplay status changed\u201d")
        self.assertIn("\u2026", ctxmenu.short("x" * 100))
        self.assertEqual(d({"type": "builtin", "id": "bookmark"}, self.CTX), "Add bookmark")
        self.assertEqual(d({"type": "builtin", "id": "bookmark"}, {**self.CTX, "all_bookmarked": True}),
                         "Remove bookmark")
        self.assertTrue(d({"type": "builtin", "id": "pin"}, {**self.CTX, "all_pinned": True}).startswith("Unpin"))

    def test_command_arguments_cannot_be_injected(self):
        vals = {"line": 'evil" --flag "x', "field": "a b", "lineno": "7", "file": "C:\\my logs\\a.log", "selection": ""}
        argv = ctxmenu.build_command_argv("viewer.exe", '--line {lineno} "{file}" {line} {field}', vals, windows=True)
        self.assertEqual(argv, ["viewer.exe", "--line", "7", "C:\\my logs\\a.log", 'evil" --flag "x', "a b"])
        posix = ctxmenu.build_command_argv("/bin/viewer", "{field}", vals, windows=False)
        self.assertEqual(posix, ["/bin/viewer", "a b"])

    def test_batch_files_are_sanitised(self):
        argv = ctxmenu.build_command_argv("run.bat", "{line}", {"line": "a & b | c"}, windows=True)
        self.assertEqual(argv[-1], "a   b   c")

    def test_launch_uses_no_shell_and_sets_env(self):
        seen = {}

        class P:
            def wait(self):
                pass

        def popen(argv, **kw):
            seen.update(argv=argv, **kw)
            return P()

        ctxmenu.launch_command("viewer", "{lineno} {file}", {"line": "L", "lineno": "3", "file": "/x/a.log"},
                               windows=False, popen=popen)
        self.assertEqual(seen["argv"], ["viewer", "3", "/x/a.log"])
        self.assertIs(seen["shell"], False)
        self.assertEqual(seen["env"]["LOGSCOPE_LINE"], "L")
        self.assertEqual(seen["env"]["LOGSCOPE_LINE_NO"], "3")

    def test_bad_arguments_raise(self):
        with self.assertRaises(ValueError):
            ctxmenu.build_command_argv("viewer", '"unterminated', {}, windows=False)

    def test_settings_roundtrip_and_old_files(self):
        with tempfile.TemporaryDirectory() as d:
            st = Settings(Path(d))
            self.assertEqual(st.context_menu(), ctxmenu.default_menu())
            custom = [{"type": "builtin", "id": "copy_lines"}, {"type": "separator"},
                      {"type": "command", "label": "Edit", "command": "notepad.exe", "args": "{file}"}]
            st.set_context_menu(custom)
            self.assertEqual(Settings(Path(d)).context_menu(), custom)
            (Path(d) / "settings.json").write_text('{"follow_tail": false}', encoding="utf-8")
            self.assertEqual(Settings(Path(d)).context_menu(), ctxmenu.default_menu())     # key missing
            (Path(d) / "settings.json").write_text('{"context_menu": "broken"}', encoding="utf-8")
            self.assertEqual(Settings(Path(d)).context_menu(), ctxmenu.default_menu())


class ShiftWheel(unittest.TestCase):
    def test_wheel_handler_present(self):
        src = (ROOT / "src/logscope/ui/tableview.py").read_text(encoding="utf-8")
        self.assertIn("def wheelEvent", src)
        self.assertIn("ShiftModifier", src)
        sticky = (ROOT / "src/logscope/ui/sticky.py").read_text(encoding="utf-8")
        self.assertIn("hscroll_target", sticky)


from logscope.core.timestamps import DeltaReport, compute_delta


class TimestampGroupSplit(unittest.TestCase):
    SPEC = {"name": "AI main log", "pattern": PIPE_RULE["pattern"]}

    def test_timestamp_group_becomes_date_and_time(self):
        c = regex_columnizer_from_spec(self.SPEC)
        self.assertEqual(c.column_names()[:3], ["Date", "Time", "Level"])
        self.assertEqual(c.column_roles()[:2], ["date", "time"])
        self.assertEqual(c.split(PIPE)[:2], ["2026-11-13", "17:02:13.447"])

    def test_split_can_be_switched_off(self):
        c = regex_columnizer_from_spec({**self.SPEC, "split_timestamp": False})
        self.assertEqual(c.column_names()[0], "Timestamp")
        self.assertEqual(c.split(PIPE)[0], "2026-11-13T17:02:13.447")

    def test_old_specs_without_the_key_split(self):
        self.assertTrue(sanitize_regex_spec(self.SPEC)["split_timestamp"])
        self.assertFalse(sanitize_regex_spec({**self.SPEC, "split_timestamp": False})["split_timestamp"])

    def test_unmatched_and_odd_cells(self):
        c = regex_columnizer_from_spec(self.SPEC)
        row = c.split("   at stack frame")
        self.assertEqual(len(row), len(c.column_names()))
        self.assertEqual(row[-1], "   at stack frame")
        c2 = regex_columnizer_from_spec({"name": "e", "pattern": r"^(?P<Timestamp>\d+) (.*)$"})
        self.assertEqual(c2.split("1731510131 hello"), ["", "1731510131", "hello"])      # epoch: no date part


class DeltaAcrossMidnight(unittest.TestCase):
    def _delta(self, lines, picks):
        p = TimestampParser.detect(lines)
        self.assertTrue(p.usable)
        return compute_delta(picks, lambda i: lines[i], p)

    def test_iso_timestamps_use_date_and_time(self):
        lines = ["2026-11-13T23:59:58.500 a", "2026-11-13T23:59:59.900 b", "2026-11-14T00:00:01.250 c"]
        rep = self._delta(lines, [0, 2])
        self.assertAlmostEqual(rep.total, 2.75, places=3)
        self.assertEqual(rep.days_crossed, 0)                       # dated: nothing to guess

    def test_time_only_log_crossing_midnight(self):
        lines = ["23:59:58.500 a", "23:59:59.900 b", "00:00:01.250 c", "00:00:02.000 d"]
        rep = self._delta(lines, [0, 3])
        self.assertAlmostEqual(rep.total, 3.5, places=3)
        self.assertEqual(rep.days_crossed, 1)
        self.assertIn("midnight", rep.summary())
        self.assertIn("+1 day", rep.details())

    def test_midnight_between_far_apart_selected_lines_is_found(self):
        lines = [f"{h:02d}:00:00.000 x" for h in range(10, 24)] + [f"{h:02d}:00:00.000 y" for h in range(0, 10)]
        rep = self._delta(lines, [0, len(lines) - 1])                # 10:00 -> next day 09:00
        self.assertAlmostEqual(rep.total, 23 * 3600.0, places=3)
        self.assertEqual(rep.days_crossed, 1)

    def test_small_backwards_jitter_is_not_midnight(self):
        lines = ["12:00:00.100 a", "12:00:00.300 b", "12:00:00.200 c", "12:00:01.000 d"]
        rep = self._delta(lines, [0, 3])
        self.assertAlmostEqual(rep.total, 0.9, places=3)
        self.assertEqual(rep.days_crossed, 0)

    def test_two_midnights(self):
        lines = ["20:00:00.000 a", "02:00:00.000 b", "20:00:00.000 c", "02:00:00.000 d"]
        rep = self._delta(lines, [0, 1, 2, 3])
        self.assertAlmostEqual(rep.total, 2 * 86400 - 20 * 3600 + 2 * 3600 - 0, places=3)
        self.assertEqual(rep.days_crossed, 2)


from logscope.core import tagmgr
from logscope.core.bookmarks import Bookmark


class TagManager(unittest.TestCase):
    def _files(self):
        a = {1: Bookmark(tags=["Step 1", "bug"]), 2: Bookmark(tags=["bug"]), 3: Bookmark()}
        b = {5: Bookmark(tags=["BUG", "ui"])}
        return [a, b]

    def test_usage(self):
        rows = {r.name.lower(): r for r in tagmgr.usage(self._files(), ["todo", "bug"], current=0)}
        self.assertEqual((rows["bug"].here, rows["bug"].total, rows["bug"].suggested), (2, 3, True))
        self.assertEqual((rows["ui"].here, rows["ui"].total), (0, 1))
        self.assertEqual((rows["todo"].total, rows["todo"].suggested), (0, True))
        self.assertEqual([r.name.lower() for r in tagmgr.usage(self._files(), [])], ["bug", "step 1", "ui"])

    def test_add(self):
        self.assertEqual(tagmgr.add(["b"], "A"), ["A", "b"])
        with self.assertRaises(ValueError):
            tagmgr.add(["bug"], "BUG")
        for bad in ("", "  ", "a,b", "x" * 41):
            with self.assertRaises(ValueError):
                tagmgr.add([], bad)

    def test_rename_everywhere_and_merge(self):
        files = self._files()
        ch = tagmgr.rename(files, ["bug", "x"], "bug", "Defect")
        self.assertEqual(ch.bookmarks, 3)
        self.assertEqual(ch.files, {0, 1})
        self.assertEqual(files[0][1].tags, ["Step 1", "Defect"])
        self.assertEqual(files[1][5].tags, ["Defect", "ui"])
        self.assertEqual(ch.suggestions, ["Defect", "x"])
        files = self._files()                                          # merge into an existing tag
        tagmgr.rename(files, [], "bug", "Step 1")
        self.assertEqual(files[0][1].tags, ["Step 1"])                 # no duplicate
        self.assertEqual(files[0][2].tags, ["Step 1"])

    def test_delete_and_forget(self):
        files = self._files()
        ch = tagmgr.delete(files, ["bug", "keep"], "BUG")
        self.assertEqual((ch.bookmarks, ch.files, ch.suggestions), (3, {0, 1}, ["keep"]))
        self.assertEqual(files[1][5].tags, ["ui"])
        self.assertEqual(tagmgr.forget_unused(self._files(), ["bug", "nobody", "UI"]), ["bug", "UI"])


class LayoutDetails(unittest.TestCase):
    def test_header_and_bookmark_column_sources(self):
        models = (ROOT / "src/logscope/ui/models.py").read_text(encoding="utf-8")
        self.assertIn("TextAlignmentRole", models)
        self.assertIn("AlignLeft", models)
        table = (ROOT / "src/logscope/ui/tableview.py").read_text(encoding="utf-8")
        self.assertIn("setDefaultAlignment", table)
        self.assertIn("def _mark_px", table)
