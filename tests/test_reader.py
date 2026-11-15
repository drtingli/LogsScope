import os
import tempfile
import threading
import unittest

from logscope.core.reader import LogFileReader
from logscope.core.search import FilterParams, LineMatcher, find_next, scan_matches
import numpy as np


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "a.log")

    def tearDown(self):
        self.dir.cleanup()

    def write(self, data: bytes, mode="wb"):
        with open(self.path, mode) as f:
            f.write(data)

    def lines(self, r):
        return [r.get_line(i) for i in range(r.count)]


class ReaderTests(Base):
    def test_basic_and_crlf(self):
        self.write(b"one\r\ntwo\nthree\n")
        r = LogFileReader(self.path)
        res = r.poll()
        self.assertEqual(res.kind, "appended")
        self.assertEqual(self.lines(r), ["one", "two", "three"])
        self.assertEqual(r.poll().kind, "none")
        r.close()

    def test_no_trailing_newline_and_provisional(self):
        self.write(b"a\nb\npart")
        r = LogFileReader(self.path)
        r.poll()
        self.assertEqual(self.lines(r), ["a", "b", "part"])
        self.write(b"ial\nnext\n", "ab")
        res = r.poll()
        self.assertEqual(res.first_changed, 2)          # provisional line was rewritten
        self.assertEqual(self.lines(r), ["a", "b", "partial", "next"])
        r.close()

    def test_empty_file_then_data(self):
        self.write(b"")
        r = LogFileReader(self.path)
        self.assertEqual(r.poll().kind, "none")
        self.write("你好\n世界\n".encode("gbk"), "ab")
        r.poll()
        self.assertEqual(r.encoding, "gbk")
        self.assertEqual(self.lines(r), ["你好", "世界"])
        r.close()

    def test_matches_splitlines_with_tiny_chunks(self):
        body = "".join(f"line {i} " + "x" * (i % 17) + "\n" for i in range(3000))
        self.write(body.encode())
        for chunk in (2, 7, 64, 4096):
            r = LogFileReader(self.path, chunk_bytes=chunk)
            while True:
                res = r.poll(max_bytes=chunk * 5)
                if not res.more:
                    break
            self.assertEqual(self.lines(r), body.split("\n")[:-1], f"chunk={chunk}")
            r.close()

    def test_incremental_append_across_blocks(self):
        r = LogFileReader(self.path.replace("a.log", "b.log")) if False else None
        self.write(b"")
        r = LogFileReader(self.path, chunk_bytes=16)
        expected = []
        for batch in range(20):
            chunk = "".join(f"b{batch}-{i}\n" for i in range(37))
            expected += chunk.splitlines()
            self.write(chunk.encode(), "ab")
            r.poll()
        self.assertEqual(self.lines(r), expected)
        r.close()

    def test_utf16_with_bom(self):
        self.write("héllo\nwörld 你好\nlast".encode("utf-16"))   # BOM + LE
        r = LogFileReader(self.path, chunk_bytes=6)
        while r.poll(max_bytes=12).more:
            pass
        self.assertEqual(r.encoding, "utf-16-le")
        self.assertEqual(self.lines(r), ["héllo", "wörld 你好", "last"])
        r.close()

    def test_utf8_bom(self):
        self.write(b"\xef\xbb\xbfhello\nworld\n")
        r = LogFileReader(self.path)
        r.poll()
        self.assertEqual(self.lines(r), ["hello", "world"])
        r.close()

    def test_truncation_resets(self):
        self.write(b"1\n2\n3\n4\n")
        r = LogFileReader(self.path)
        r.poll()
        self.write(b"new\n")
        res = r.poll()
        self.assertEqual(res.kind, "reset")
        self.assertEqual(self.lines(r), ["new"])
        r.close()

    def test_rotation_detected(self):
        self.write(b"old1\nold2\n")
        r = LogFileReader(self.path)
        r.poll()
        os.rename(self.path, self.path + ".1")     # rename while we hold it open
        self.write(b"fresh1\nfresh2\nfresh3\n")
        res = r.poll()
        self.assertEqual(res.kind, "reset")
        self.assertEqual(self.lines(r), ["fresh1", "fresh2", "fresh3"])
        r.close()


class SearchTests(Base):
    def make(self, n=5000):
        self.write("".join(f"{i} {'ERROR' if i % 97 == 0 else 'info'} msg\n" for i in range(n)).encode())
        r = LogFileReader(self.path)
        r.poll()
        return r

    def run_filter(self, r, **kw):
        p = FilterParams(**kw)
        out = []
        scan_matches(r, LineMatcher.from_params(p), 0, r.count, threading.Event(),
                     out.append, context=p.context, block=300)
        return np.concatenate(out).tolist() if out else []

    def test_plain_regex_case_invert(self):
        r = self.make()
        lines = self.lines(r)
        self.assertEqual(self.run_filter(r, text="ERROR", case_sensitive=True),
                         [i for i, l in enumerate(lines) if "ERROR" in l])
        self.assertEqual(self.run_filter(r, text="error"),
                         [i for i, l in enumerate(lines) if "error" in l.lower()])
        self.assertEqual(self.run_filter(r, text=r"^\d+ ERR", regex=True),
                         [i for i, l in enumerate(lines) if "ERR" in l])
        self.assertEqual(self.run_filter(r, text="ERROR", invert=True),
                         [i for i, l in enumerate(lines) if "ERROR" not in l])
        r.close()

    def test_gbk_and_utf16_plain_search(self):
        text = "".join(f"第{i}行 日志 {'错误' if i % 7 == 0 else '正常'} ok\n" for i in range(500))
        for codec, data in (("gbk", text.encode("gbk")), ("utf-16", text.encode("utf-16"))):
            self.write(data)
            r = LogFileReader(self.path)
            r.poll()
            lines = self.lines(r)
            for kw in (dict(text="错误", case_sensitive=True), dict(text="OK"), dict(text="ok", case_sensitive=True)):
                want = [i for i, l in enumerate(lines)
                        if (kw["text"] in l if kw.get("case_sensitive") else kw["text"].lower() in l.lower())]
                self.assertEqual(self.run_filter(r, **kw), want, (codec, kw))
            r.close()

    def test_context(self):
        r = self.make(1000)
        got = self.run_filter(r, text="ERROR", case_sensitive=True, context=2)
        want = sorted({j for i in range(0, 1000, 97) for j in range(i - 2, i + 3) if 0 <= j < 1000})
        self.assertEqual(got, want)
        r.close()

    def test_find_next_prev_wrap(self):
        r = self.make(1000)
        m = LineMatcher("ERROR", case_sensitive=True)
        ev = threading.Event()
        self.assertEqual(find_next(r, m, 0, True, ev, block=50), 97)
        self.assertEqual(find_next(r, m, 97, True, ev, block=50), 194)
        self.assertEqual(find_next(r, m, 97, False, ev, block=50), 0)
        self.assertEqual(find_next(r, m, 0, False, ev, block=50), 970)    # wraps
        self.assertEqual(find_next(r, m, 970, True, ev, block=50), 0)     # wraps
        self.assertEqual(find_next(r, LineMatcher("zzz"), 5, True, ev), -1)
        r.close()


if __name__ == "__main__":
    unittest.main()
