import json
import tempfile
import unittest
from pathlib import Path

from logscope.core.bookmarks import (Bookmark, all_tags, bookmarks_from_list, bookmarks_to_list,
                                     normalize_tags, read_bookmark_file, write_bookmark_file)
from logscope.core.highlight import (HighlightRule, groups_from_json, groups_to_json,
                                     validate_rule)
from logscope.core.session import load_session_file, sanitize_session, save_session_file
from logscope.core.settings import Settings


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_assignment_saves_immediately_and_only_on_change(self):
        s = Settings(self.dir)
        self.assertFalse(s.path.exists())
        s["follow_tail"] = False
        self.assertEqual(json.loads(s.path.read_text())["follow_tail"], False)
        mtime = s.path.stat().st_mtime_ns
        s["follow_tail"] = False            # unchanged -> no write
        self.assertEqual(s.path.stat().st_mtime_ns, mtime)

    def test_history_dedupe_order_limit(self):
        s = Settings(self.dir)
        for t in ["a", "b", "c", "a"]:
            s.push_history("find_history", t, limit=3)
        self.assertEqual(s["find_history"], ["a", "c", "b"])
        s.push_history("find_history", "d", limit=3)
        self.assertEqual(s["find_history"], ["d", "a", "c"])
        s.push_history("find_history", "")
        self.assertEqual(Settings(self.dir)["find_history"], ["d", "a", "c"])

    def test_saved_filters_roundtrip_replace_delete(self):
        s = Settings(self.dir)
        s.save_filter({"name": "errors", "text": "ERROR", "regex": False, "case": True,
                       "invert": False, "context": 2})
        s.save_filter({"name": "Auth", "text": "login|logout", "regex": True, "case": False,
                       "invert": False, "context": 0})
        s.save_filter({"name": "errors", "text": "FATAL", "regex": False, "case": True,
                       "invert": False, "context": 0})
        s2 = Settings(self.dir)
        self.assertEqual([f["name"] for f in s2.saved_filters()], ["Auth", "errors"])
        self.assertEqual(s2.saved_filters()[1]["text"], "FATAL")
        s2.delete_filter("Auth")
        self.assertEqual([f["name"] for f in Settings(self.dir).saved_filters()], ["errors"])

    def test_highlight_groups_persist_and_active(self):
        s = Settings(self.dir)
        groups = s.highlight_groups()
        self.assertIn("Default", groups)
        groups["Network"] = [HighlightRule("timeout", False, False, "#ffffff", "#0000ff")]
        s.set_highlight_groups(groups, "Network")
        s2 = Settings(self.dir)
        self.assertEqual(s2.active_group, "Network")
        self.assertEqual(s2.highlight_rules()[0].bg, "#0000ff")
        s2.set_active_group("Default")
        self.assertEqual(Settings(self.dir).active_group, "Default")
        s2.set_active_group("does-not-exist")
        self.assertEqual(s2.active_group, "Default")

    def test_migrates_old_highlight_rules(self):
        (self.dir / "settings.json").write_text(json.dumps(
            {"highlight_rules": [{"pattern": "boom", "regex": False, "case_sensitive": False,
                                  "fg": "#000000", "bg": "#ff00ff"}]}))
        s = Settings(self.dir)
        self.assertEqual(s.active_group, "Default")
        self.assertEqual(s.highlight_rules()[0].pattern, "boom")

    def test_corrupt_file_is_kept_and_defaults_used(self):
        (self.dir / "settings.json").write_text("{ not json")
        s = Settings(self.dir)
        self.assertTrue((self.dir / "settings.json.bad").exists())
        self.assertTrue(s["follow_tail"])

    def test_wrong_types_fall_back(self):
        (self.dir / "settings.json").write_text(json.dumps(
            {"recent_files": "oops", "follow_tail": 5, "find_history": ["x"]}))
        s = Settings(self.dir)
        self.assertEqual(s["recent_files"], [])
        self.assertTrue(s["follow_tail"])
        self.assertEqual(s["find_history"], ["x"])

    def test_session_stored_and_sanitised(self):
        s = Settings(self.dir)
        s["session"] = {"tabs": [{"path": "/x/a.log", "bookmarks": [{"line": 3, "comment": "hi"}],
                                  "follow": False, "line": 7}, {"nonsense": 1}], "current": 5}
        ses = Settings(self.dir).last_session()
        self.assertEqual(len(ses["tabs"]), 1)
        self.assertEqual(ses["current"], 0)          # out of range -> 0
        self.assertEqual(ses["tabs"][0]["bookmarks"], [{"line": 3, "comment": "hi", "tags": []}])
        self.assertFalse(ses["tabs"][0]["follow"])


class HighlightIoTests(unittest.TestCase):
    def test_export_import_roundtrip(self):
        groups = {"A": [HighlightRule("x", True, True, "#112233", "#445566")], "B": []}
        back = groups_from_json(json.loads(json.dumps(groups_to_json(groups))))
        self.assertEqual(back["A"][0], groups["A"][0])
        self.assertEqual(back["B"], [])

    def test_import_single_group_and_bad_data(self):
        g = groups_from_json({"name": "Mine", "rules": [{"pattern": "p", "bg": "red"}, 5, {"pattern": ""}]})
        self.assertEqual(list(g), ["Mine"])
        self.assertEqual(len(g["Mine"]), 1)
        self.assertEqual(g["Mine"][0].bg, "#ffff99")   # invalid colour -> default
        for bad in ([], {}, {"groups": {}}, "x"):
            with self.assertRaises(ValueError):
                groups_from_json(bad)

    def test_validate_rule(self):
        self.assertIsNone(validate_rule(HighlightRule("a(b", False)))
        self.assertIsNotNone(validate_rule(HighlightRule("a(b", True)))


class BookmarkFileTests(unittest.TestCase):
    def test_roundtrip_with_text_snippet(self):
        bm = {0: Bookmark(), 41: Bookmark("check this", ["bug", "Net"]), 99: Bookmark("later")}
        with tempfile.TemporaryDirectory() as d:
            p = str(Path(d, "b.json"))
            write_bookmark_file(p, "/logs/a.log", bm, lambda ln: f"text of {ln}")
            raw = json.loads(Path(p).read_text())
            self.assertEqual(raw["bookmarks"][1], {"line": 42, "comment": "check this", "tags": ["bug", "Net"],
                                                  "text": "text of 41"})
            file, back = read_bookmark_file(p)
            self.assertEqual(file, "/logs/a.log")
            self.assertEqual(back, bm)

    def test_tolerant_and_strict(self):
        self.assertEqual(bookmarks_from_list([{"line": 0}, {"line": "3"}, {"line": True}, 7,
                                              {"line": 5, "comment": 9}]), {4: Bookmark()})
        self.assertEqual(bookmarks_from_list("nope"), {})
        with tempfile.TemporaryDirectory() as d:
            p = Path(d, "x.json")
            p.write_text('{"hello": 1}')
            with self.assertRaises(ValueError):
                read_bookmark_file(str(p))
            p.write_text("garbage")
            with self.assertRaises(ValueError):
                read_bookmark_file(str(p))
        self.assertEqual(bookmarks_to_list({}), [])

    def test_tags_normalised_and_v02_files_still_load(self):
        self.assertEqual(normalize_tags(" bug , Bug;net,, 网络，慢 "), ["bug", "net", "网络", "慢"])
        self.assertEqual(normalize_tags(["a", 5, "A", "  b  c "]), ["a", "b c"])
        self.assertEqual(normalize_tags(None), [])
        self.assertEqual(len(normalize_tags([str(i) for i in range(50)])), 20)
        old = bookmarks_from_list([{"line": 2, "comment": "c"}, {"line": 3, "tags": "x,y"}])
        self.assertEqual(old, {1: Bookmark("c"), 2: Bookmark("", ["x", "y"])})
        self.assertEqual(all_tags({1: Bookmark("", ["b", "A"]), 2: Bookmark("", ["a", "c"])}), ["A", "b", "c"])

    def test_tags_survive_session_and_settings(self):
        ses = sanitize_session({"tabs": [{"path": "p", "bookmarks": [{"line": 4, "comment": "x", "tags": ["t1", "t2"]}],
                                          "find": {"text": "boom", "regex": True}}]})
        tab = ses["tabs"][0]
        self.assertEqual(tab["bookmarks"][0]["tags"], ["t1", "t2"])
        self.assertEqual(tab["find"], {"text": "boom", "regex": True, "case": False})
        self.assertIsNone(sanitize_session({"tabs": [{"path": "p", "find": {"text": ""}}]})["tabs"][0]["find"])
        with tempfile.TemporaryDirectory() as d:
            st = Settings(Path(d))
            self.assertTrue(st["search_live"])
            self.assertTrue(st["filter_tail"])
            st["search_live"] = False
            st.remember_tags(["Zed", "alpha"])
            st.remember_tags(["ALPHA", "mid"])
            st2 = Settings(Path(d))
            self.assertFalse(st2["search_live"])
            self.assertEqual(st2["bookmark_tags"], ["alpha", "mid", "Zed"])


class SessionFileTests(unittest.TestCase):
    def test_save_and_load(self):
        ses = {"tabs": [{"path": "C:/l/a.log", "bookmarks": [{"line": 2, "comment": "c"}],
                         "follow": False, "line": 10, "columnizer": "JSON lines",
                         "filter": {"text": "ERR", "regex": True, "case": False, "invert": False, "context": 3},
                         "filter_visible": True},
                        {"path": "C:/l/b.log"}], "current": 1}
        with tempfile.TemporaryDirectory() as d:
            p = str(Path(d, "s.json"))
            save_session_file(p, ses)
            back = load_session_file(p)
        self.assertEqual(back["current"], 1)
        self.assertEqual(back["tabs"][0]["filter"]["context"], 3)
        self.assertEqual(back["tabs"][0]["columnizer"], "JSON lines")
        self.assertTrue(back["tabs"][1]["follow"])           # default
        self.assertIsNone(back["tabs"][1]["filter"])

    def test_bad_filter_values_are_cleaned(self):
        s = sanitize_session({"tabs": [{"path": "p", "filter": {"text": "x", "context": 999, "regex": "yes"}}]})
        f = s["tabs"][0]["filter"]
        self.assertEqual((f["context"], f["regex"]), (50, False))
        self.assertIsNone(sanitize_session({"tabs": [{"path": "p", "filter": {"text": ""}}]})["tabs"][0]["filter"])
        with tempfile.TemporaryDirectory() as d:
            p = Path(d, "s.json")
            p.write_text("[]")
            with self.assertRaises(ValueError):
                load_session_file(str(p))


if __name__ == "__main__":
    unittest.main()
