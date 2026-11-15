"""Simulate an application writing (and optionally rotating) a log file.

    python tools/logwriter.py demo.log --rate 20000 --seconds 30 --rotate-mb 5

Open demo.log in LogScope and watch tail mode keep up.
"""

import argparse
import os
import random
import time

LEVELS = ["INFO"] * 8 + ["WARN", "ERROR"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--rate", type=int, default=5000, help="lines per second")
    ap.add_argument("--seconds", type=float, default=30)
    ap.add_argument("--rotate-mb", type=float, default=0, help="rename to .1 and start over at this size")
    a = ap.parse_args()

    f = open(a.path, "ab", buffering=0)
    n, t0 = 0, time.time()
    tick = 0.05
    per_tick = max(1, int(a.rate * tick))
    while time.time() - t0 < a.seconds:
        stamp = time.strftime("%Y-%m-%d %H:%M:%S") + f".{int(time.time() * 1000) % 1000:03d}"
        buf = []
        for _ in range(per_tick):
            n += 1
            buf.append(f"{stamp} {random.choice(LEVELS):5} worker-{n % 8} request {n} took {random.randint(1, 900)} ms\n")
        f.write("".join(buf).encode())
        if a.rotate_mb and f.tell() > a.rotate_mb * 1048576:
            f.close()
            if os.path.exists(a.path + ".1"):
                os.remove(a.path + ".1")
            os.rename(a.path, a.path + ".1")
            f = open(a.path, "ab", buffering=0)
        time.sleep(tick)
    print(f"wrote {n} lines")


if __name__ == "__main__":
    main()
