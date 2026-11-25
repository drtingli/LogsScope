"""v0.13: layout tree of areas (groups of tabs inside nested splits), drop zones, session."""

import unittest
from pathlib import Path

from logscope.core import layout as L
from logscope.core.session import sanitize_session, sanitize_tab

ROOT = Path(__file__).resolve().parent.parent


def tab(n):
    return {"path": f"/x/{n}.log"}


class Arrange(unittest.TestCase):
    def test_three_columns(self):
        t = L.arrange_tree(3, 3, 1)
        self.assertEqual((t["t"], t["o"]), ("s", "h"))
        self.assertEqual([k["tabs"] for k in t["kids"]], [[0], [1], [2]])

    def test_four_rows(self):
        t = L.arrange_tree(4, 1, 4)
        self.assertEqual((t["t"], t["o"], len(t["kids"])), ("s", "v", 4))

    def test_grid_nested(self):
        t = L.arrange_tree(6, 3, 2)
        self.assertEqual(t["o"], "v")
        self.assertEqual([k["o"] for k in t["kids"]], ["h", "h"])
        self.assertEqual(L.tab_numbers(t), [0, 1, 2, 3, 4, 5])
        self.assertEqual((L.count_groups(t), L.depth(t)), (6, 3))

    def test_more_tabs_than_cells_share_areas(self):
        t = L.arrange_tree(5, 3, 1)
        self.assertEqual([k["tabs"] for k in t["kids"]], [[0, 3], [1, 4], [2]])

    def test_fewer_tabs_leave_empty_areas(self):
        t = L.arrange_tree(1, 2, 2)
        self.assertEqual(L.count_groups(t), 4)
        self.assertEqual(L.tab_numbers(t), [0])

    def test_single_cell_and_no_tabs(self):
        self.assertEqual(L.arrange_tree(3, 1, 1), L.group([0, 1, 2]))
        self.assertEqual(L.arrange_tree(0, 1, 1), L.group([]))

    def test_group_limit(self):
        t = L.arrange_tree(10, 64, 64)
        self.assertLessEqual(L.count_groups(t), L.MAX_GROUPS)

    def test_presets(self):
        self.assertEqual(L.preset_shape("columns", 4), (4, 1))
        self.assertEqual(L.preset_shape("rows", 4), (1, 4))
        self.assertEqual(L.preset_shape("grid", 5), (3, 2))
        self.assertEqual(L.preset_shape("grid", 1), (1, 1))
        self.assertEqual(L.preset_tree("tabs", 3), L.group([0, 1, 2]))
        self.assertEqual(L.count_groups(L.preset_tree("columns", 4)), 4)


class Sanitize(unittest.TestCase):
    def test_roundtrip(self):
        saved = {"mode": "custom", "root": L.split("h", [L.group([0, 2], 1), L.split("v", [L.group([1]), L.group([])])],
                                                  [600, 400])}
        self.assertEqual(L.sanitize_layout(saved, 3), saved)

    def test_bad_input(self):
        for bad in (None, "x", [], {}, {"root": 5}, {"root": {"t": "x"}}):
            self.assertIsNone(L.sanitize_layout(bad, 3))

    def test_unknown_and_duplicate_tabs_dropped(self):
        got = L.sanitize_layout({"root": L.group([0, 0, 9, -1, "a", True, 1])}, 2)
        self.assertEqual(got["root"]["tabs"], [0, 1])
        self.assertEqual(got["mode"], "custom")

    def test_single_child_split_collapses(self):
        got = L.sanitize_layout({"root": {"t": "s", "o": "h", "kids": [L.group([0])]}}, 1)
        self.assertEqual(got["root"], L.group([0]))

    def test_sizes_repaired_and_depth_limited(self):
        got = L.sanitize_layout({"root": {"t": "s", "o": "v", "sizes": [5, -3], "kids": [L.group([0]), L.group([1])]}}, 2)
        self.assertEqual(got["root"]["sizes"], [5, 1000])
        deep = L.group([0])
        for _ in range(20):
            deep = {"t": "s", "o": "h", "kids": [deep, L.group([])]}
        out = L.sanitize_layout({"root": deep}, 1)
        self.assertTrue(out is None or L.depth(out["root"]) <= L.MAX_DEPTH + 1)

    def test_prune_and_renumber(self):
        tree = L.split("h", [L.group([0, 1], 1), L.group([2]), L.group([])])
        p = L.prune(tree, lambda n: n != 2)
        self.assertEqual(L.count_groups(p), 2)                       # the group that only held tab 2 is gone, the empty one stays
        self.assertEqual(L.tab_numbers(p), [0, 1])
        self.assertEqual(L.prune(L.group([0]), lambda n: False), None)
        r = L.renumber(L.group([3, 5], 1), {3: 0, 5: 1})
        self.assertEqual(r, L.group([0, 1], 1))
        shown = L.prune(L.group([4, 5], 1), lambda n: n == 5)
        self.assertEqual(shown, L.group([5], 0))


class DropZones(unittest.TestCase):
    def test_zones(self):
        z = L.zone_at
        self.assertEqual(z(5, 50, 100, 100), "left")
        self.assertEqual(z(95, 50, 100, 100), "right")
        self.assertEqual(z(50, 3, 100, 100), "top")
        self.assertEqual(z(50, 97, 100, 100), "bottom")
        self.assertEqual(z(50, 50, 100, 100), "center")
        self.assertEqual(z(10, 10, 0, 0), "center")

    def test_rects_and_splits(self):
        self.assertEqual(L.zone_rect("left", 100, 80), (0, 0, 50, 80))
        self.assertEqual(L.zone_rect("bottom", 100, 80), (0, 40, 100, 40))
        self.assertEqual(L.zone_rect("center", 100, 80), (0, 0, 100, 80))
        self.assertEqual(L.zone_split("right"), ("h", False))
        self.assertEqual(L.zone_split("top"), ("v", True))
        self.assertIsNone(L.zone_split("center"))

    def test_insert_index(self):
        centers = [20.0, 60.0, 100.0]
        self.assertEqual([L.insert_index(centers, x) for x in (0, 30, 70, 120)], [0, 1, 2, 3])
        self.assertEqual(L.insert_index([], 5), 0)


class SessionLayout(unittest.TestCase):
    def test_layout_is_kept_and_validated(self):
        layout = {"mode": "custom", "root": L.split("h", [L.group([0]), L.group([1, 7])])}
        s = sanitize_session({"tabs": [tab(0), tab(1)], "current": 1, "layout": layout})
        self.assertEqual(L.tab_numbers(s["layout"]["root"]), [0, 1])           # 7 does not exist
        self.assertNotIn("layout", sanitize_session({"tabs": [tab(0)], "layout": "junk"}))
        self.assertNotIn("layout", sanitize_session({"tabs": [tab(0)]}))

    def test_old_session_files_still_load(self):
        s = sanitize_session({"version": 2, "tabs": [tab(0)], "current": 0})
        self.assertEqual(len(s["tabs"]), 1)
        self.assertIsNotNone(sanitize_tab(tab(0)))


class Sources(unittest.TestCase):
    def test_tab_host_has_drag_and_drop(self):
        src = (ROOT / "src/logscope/ui/tabhost.py").read_text(encoding="utf-8")
        for needle in ("QDrag", "dropEvent", "class Group", "class DropOverlay", "def arrange", "def capture_layout",
                       "def apply_layout", "_split_group", "_collapse"):
            self.assertIn(needle, src, needle)

    def test_window_wiring(self):
        src = (ROOT / "src/logscope/ui/main_window.py").read_text(encoding="utf-8")
        for needle in ("arrange_dialog", "_on_layout_changed", "capture_layout", "apply_layout", "_fill_area_menu"):
            self.assertIn(needle, src, needle)

    def test_session_timer_precedes_initial_layout_signal(self):
        src = (ROOT / "src/logscope/ui/main_window.py").read_text(encoding="utf-8")
        self.assertLess(src.index("self._session_timer = QTimer(self)"),
                        src.index("self.tabs.layoutChanged.connect"))
        self.assertLess(src.index("self.tabs.layoutChanged.connect"),
                        src.index('self.tabs.setLayoutMode(settings["layout"])'))


class DetachedLayout(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication(["logscope-test", "-platform", "offscreen"])

    def test_filter_panel_shortcuts(self):
        import tempfile
        from unittest.mock import patch

        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest

        from logscope.core.columnizers import ColumnizerRegistry
        from logscope.core.settings import Settings
        from logscope.ui.main_window import MainWindow

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "sample.log"
            path.write_text("one\n", encoding="utf-8")
            window = MainWindow(Settings(Path(folder)), ColumnizerRegistry())
            try:
                shortcuts = {shortcut.toString() for shortcut in window.panel_act.shortcuts()}
                self.assertEqual(shortcuts, {"F4", "Ctrl+Shift+F"})
                with patch("logscope.ui.log_tab.ReaderWorker.start"):
                    tab = window.open_file(str(path), remember=False)
                window.show()
                self.app.processEvents()
                self.assertFalse(tab.bottom.isVisible())
                QTest.keyClick(window, Qt.Key.Key_F4)
                self.assertTrue(tab.bottom.isVisible())
                QTest.keyClick(window, Qt.Key.Key_F4)
                self.assertFalse(tab.bottom.isVisible())
            finally:
                window.close()
                window.deleteLater()
                self.app.processEvents()

    def test_detach_shows_log_and_reattach_preserves_it(self):
        import tempfile
        from unittest.mock import patch

        from logscope.core.columnizers import ColumnizerRegistry
        from logscope.core.settings import Settings
        from logscope.ui.main_window import MainWindow

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "sample.log"
            path.write_text("first log line\nsecond log line\n", encoding="utf-8")
            settings = Settings(Path(folder) / "settings")
            settings["follow_tail"] = False
            window = MainWindow(settings, ColumnizerRegistry())
            try:
                window.show()
                with patch("logscope.ui.log_tab.ReaderWorker.start"):
                    tab = window.open_file(str(path), remember=False)
                self.assertIsNotNone(tab)
                tab.reader.poll()
                tab._on_lines_changed(0, tab.reader.count)
                self.app.processEvents()
                model = tab.table.model()
                self.assertEqual(model.rowCount(), 2)
                self.assertTrue(tab.table.isVisible())
                self.assertFalse(window.bookmark_panel.isVisible(), "Bookmarks panel opens by default")
                window.bookmark_panel.toggleViewAction().trigger()
                self.assertTrue(window.bookmark_panel.isVisible())
                window.bookmark_panel.toggleViewAction().trigger()

                for color in ("#f39c12", ""):
                    with self.subTest(color=color):
                        tab.tab_color = color
                        window.detach_act.trigger()
                        self.app.processEvents()
                        detached = window.detached[0]
                        try:
                            self.assertTrue(tab.isVisible(), "Detached log tab is hidden")
                            self.assertTrue(tab.table.isVisible())
                            self.assertGreater(tab.table.viewport().height(),
                                               detached.centralWidget().height() // 2)
                            self.assertIs(tab.table.model(), model)
                            self.assertEqual(model.rowCount(), 2)
                        finally:
                            detached.reattach_act.trigger()
                            self.app.processEvents()

                        self.assertFalse(window.detached)
                        self.assertIs(window.tabs.currentWidget(), tab)
                        self.assertTrue(tab.table.isVisible())
                        self.assertIs(tab.table.model(), model)
            finally:
                window.close()
                window.deleteLater()
                self.app.processEvents()

    def test_window_theme_switch_restyles_app_and_logs(self):
        import tempfile
        from unittest.mock import patch

        from PySide6.QtGui import QColor, QPalette

        from logscope.core import uitheme as core_theme
        from logscope.core.columnizers import ColumnizerRegistry
        from logscope.core.settings import Settings
        from logscope.ui.main_window import MainWindow

        role = QPalette.ColorRole
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "sample.log"
            path.write_text("one\ntwo\n", encoding="utf-8")
            settings = Settings(Path(folder) / "settings")
            settings["ui_theme"] = "light"
            window = MainWindow(settings, ColumnizerRegistry())
            try:
                with patch("logscope.ui.log_tab.ReaderWorker.start"):
                    tab = window.open_file(str(path), remember=False)
                for mode in ("dark", "light"):
                    with self.subTest(mode=mode):
                        window.set_ui_theme(mode)
                        normal = core_theme.PALETTES[mode]["normal"]
                        app_pal = self.app.palette()
                        self.assertEqual(app_pal.color(role.Window), QColor(normal["Window"]))
                        self.assertEqual(tab.table.palette().color(role.Base), QColor(normal["Base"]))
                        self.assertTrue(window._ui_theme_actions[mode].isChecked())
                        self.assertEqual(Settings(Path(folder) / "settings")["ui_theme"], mode)
                        grid = core_theme.DARK_GRID if mode == "dark" else core_theme.LIGHT_GRID
                        self.assertEqual(tab.table.itemDelegate().grid_color, QColor(grid))
            finally:
                window.set_ui_theme("system")
                window.close()
                window.deleteLater()
                self.app.processEvents()


if __name__ == "__main__":
    unittest.main()


from logscope.core.ledstate import DECAY, OFF_COLOR, RETRIGGER_BELOW, LedState


class ActivityLed(unittest.TestCase):
    def test_only_lit_while_following(self):
        s = LedState(following=False)
        self.assertFalse(s.flash())
        self.assertEqual((s.level, s.color(), s.glow()), (0.0, OFF_COLOR, 0.0))
        s.set_following(True)
        self.assertTrue(s.flash())
        self.assertEqual(s.level, 1.0)
        self.assertEqual(s.color(), "#4ade80")
        s.set_following(False)                                  # follow switched off: LED goes dark at once
        self.assertEqual((s.level, s.color()), (0.0, OFF_COLOR))

    def test_fades_out(self):
        s = LedState(True)
        s.flash()
        steps = 0
        while s.tick():
            steps += 1
            self.assertLess(steps, 50)
        self.assertEqual(s.level, 0.0)
        self.assertFalse(s.animating)
        self.assertFalse(s.tick())
        self.assertEqual(s.color(), "#1d5a34")                  # following, idle: dark green

    def test_steady_stream_blinks(self):
        s = LedState(True)
        accepted, brightest, dimmest = 0, 0.0, 1.0
        for _ in range(200):                                    # a flash request in every tick
            accepted += s.flash()
            s.tick()
            brightest, dimmest = max(brightest, s.level), min(dimmest, s.level)
        self.assertGreater(accepted, 10)                        # it keeps blinking ...
        self.assertLess(accepted, 100)                          # ... but is not on all the time
        self.assertLess(dimmest, RETRIGGER_BELOW)
        self.assertGreater(brightest, 0.8)
        self.assertGreater(DECAY, 0.0)

    def test_wiring_in_sources(self):
        tab = (ROOT / "src/logscope/ui/log_tab.py").read_text(encoding="utf-8")
        self.assertIn("activity = Signal()", tab)
        self.assertIn("self._caught_up and total > had_rows", tab)          # not while the file is first loaded
        host = (ROOT / "src/logscope/ui/tabhost.py").read_text(encoding="utf-8")
        for needle in ("LedIndicator", "ButtonPosition.LeftSide", "follow_changed", "set_leds_visible"):
            self.assertIn(needle, host, needle)
        win = (ROOT / "src/logscope/ui/main_window.py").read_text(encoding="utf-8")
        self.assertIn("_led_toggled", win)
