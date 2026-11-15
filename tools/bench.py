"""Measure indexing, filtering and tail latency on a generated or given file.

    python tools/bench.py --mb 500            # generate 500 MB temp file
    python tools/bench.py path/to/big.log
Run with PYTHONPATH=src (or after ``pip install -e .``).
"""

import argparse
import os
import tempfile
import threading
import time

from logscope.core.reader import LogFileReader
from logscope.core.search import LineMatcher, scan_matches


def generate(path: str, mb: int) -> None:
    line = "2024-05-01 12:00:01.123 INFO  worker-3 request {} took {} ms and some more padding text\n"
    with open(path, "wb") as f:
        written, i = 0, 0
        while written < mb * 1048576:
            chunk = "".join(line.format(i + k, (i + k) % 900) if (i + k) % 5000 else
                            line.replace("INFO ", "ERROR").format(i + k, 1) for k in range(20000))
            f.write(chunk.encode())
            written += len(chunk)
            i += 20000


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("path", nargs="?")
    ap.add_argument("--mb", type=int, default=200)
    a = ap.parse_args()
    path, tmp = a.path, None
    if not path:
        tmp = tempfile.mkdtemp()
        path = os.path.join(tmp, "bench.log")
        t = time.perf_counter()
        generate(path, a.mb)
        print(f"generated {os.path.getsize(path) / 1048576:.0f} MB in {time.perf_counter() - t:.1f}s")

    r = LogFileReader(path)
    t = time.perf_counter()
    r.poll()
    dt = time.perf_counter() - t
    print(f"index   : {r.count:,} lines in {dt:.2f}s ({r.size / 1048576 / dt:.0f} MB/s)")

    t = time.perf_counter()
    for i in range(0, r.count, max(1, r.count // 2000)):
        r.get_line(i)
    print(f"random  : 2000 line reads in {(time.perf_counter() - t) * 1000:.0f} ms")

    for label, m in (("plain (case)", LineMatcher("ERROR", case_sensitive=True)),
                     ("plain (nocase)", LineMatcher("error")),
                     ("regex", LineMatcher(r"took 1\d\d ms", regex=True))):
        out = []
        t = time.perf_counter()
        scan_matches(r, m, 0, r.count, threading.Event(), out.append)
        n = sum(x.size for x in out)
        print(f"filter {label:15}: {n:>9,} hits in {time.perf_counter() - t:.2f}s")

    with open(path, "ab") as f:
        t = time.perf_counter()
        f.write(b"tail test line\n")
    while r.poll().kind == "none":
        pass
    print(f"tail    : append noticed in {(time.perf_counter() - t) * 1000:.1f} ms (poll loop)")
    r.close()
    if tmp:
        os.remove(path)
        os.rmdir(tmp)


if __name__ == "__main__":
    main()
