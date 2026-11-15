import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from logscope.core import winio
from logscope.core.columnizers import ColumnizerRegistry
from logscope.core.highlight import HighlightEngine, HighlightRule
from logscope.core.settings import Settings
from logscope.core.views import ListView


class MiscTests(unittest.TestCase):
    def test_columnizer_autodetect(self):
        reg = ColumnizerRegistry()
        ts = ["2024-05-01 12:00:01.123 INFO started", "2024-05-01 12:00:02 WARN slow request"]
        c = reg.auto_detect(ts)
        self.assertEqual(c.name, "Date / Time / Level / Message")
        self.assertEqual(c.split(ts[0]), ["2024-05-01", "12:00:01.123", "INFO", "started"])
        self.assertEqual(c.split(ts[0]), ["2024-05-01", "12:00:01.123", "INFO", "started"])
        js = ['{"a": 1, "b": "x"}', '{"a": 2, "c": true}']
        c = reg.auto_detect(js)
        self.assertEqual(c.name, "JSON lines")
        self.assertEqual(c.column_names(), ["a", "b", "c"])
        self.assertEqual(c.split(js[1]), ["2", "", "true"])
        self.assertIs(reg.auto_detect(["plain text", "more"]), reg.default)

    def test_plugin_loading(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, "ok.py").write_text(
                "from logscope.core.columnizers import Columnizer\n"
                "class K(Columnizer):\n    name='K'\n"
                "def register(reg):\n    reg.register(K())\n")
            Path(d, "bad.py").write_text("raise RuntimeError('boom')\n")
            reg = ColumnizerRegistry()
            errors = reg.load_plugins(d)
            self.assertIn("K", reg.names())
            self.assertEqual(len(errors), 1)

    def test_highlight(self):
        h = HighlightEngine([HighlightRule("error", False, False, "#fff", "#f00"),
                             HighlightRule(r"w(a)rn", True, True, "#000", "#ff0")])
        self.assertEqual(h.match("an ERROR here").bg, "#f00")
        self.assertEqual(h.match("warn").bg, "#ff0")
        self.assertIsNone(h.match("WARN"))
        self.assertIsNone(h.match("fine"))

    def test_settings_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            s = Settings(Path(d))
            s.add_recent("a"); s.add_recent("b"); s.add_recent("a")
            s.save()
            s2 = Settings(Path(d))
            self.assertEqual(s2["recent_files"], ["a", "b"])
            self.assertTrue(len(s2.highlight_rules()) >= 1)

    def test_listview(self):
        v = ListView(np.array([3, 5, 9, 12]))
        self.assertEqual(v.row_of_line(9), 2)
        self.assertEqual(v.row_of_line(10), 3)
        self.assertEqual(v.drop_from(9), 2)
        self.assertEqual(v.n, 2)

    def test_open_shared_allows_rename_and_delete(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d, "x.log"); p.write_bytes(b"abc\n")
            fh = winio.open_shared(str(p), winio.RANDOM)
            p.rename(Path(d, "y.log"))           # would fail on Windows without SHARE_DELETE
            self.assertEqual(fh.read(), b"abc\n")
            fh.close()
            w = winio.ChangeWatcher(str(Path(d, "y.log")))
            self.assertFalse(w.wait(0.01) and not w.native)
            w.close()


if __name__ == "__main__":
    unittest.main()
