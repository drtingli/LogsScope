"""Timestamp recognition and time differences between log lines.

``TimestampParser.parse(line)`` finds the first timestamp near the start of a
line and returns ``(seconds, start, end)``: seconds since 1970 (UTC when the
text carries a zone, otherwise "as written"), and the character span of the
timestamp.  Differences between two lines of the same log are therefore exact
even for logs without a zone.

Built-in formats are tried in priority order and the one matching most lines
of a sample wins (``detect``).  A custom ``strptime`` format
(e.g. ``%d.%m.%Y %H:%M:%S,%f``) can be given instead (``from_format``).
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Iterable, Sequence

import numpy as np

SEARCH_LIMIT = 160          # a timestamp is looked for in the first N characters
MAX_LINES = 200_000         # most lines used for one time-difference computation
_MONTHS = {m.lower(): i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}

_FRAC = r"(?:[.,](\d{1,9}))?"
_TZ = r"\s?(Z|[+-]\d{2}:?\d{2})?"


def _frac(s: str | None) -> float:
    return float("0." + s) if s else 0.0


def _offset(s: str | None) -> int:
    """'+0100' / '+01:00' / 'Z' -> seconds east of UTC (None -> 0)."""
    if not s or s == "Z":
        return 0
    sign = -1 if s[0] == "-" else 1
    d = s[1:].replace(":", "")
    return sign * (int(d[:2]) * 3600 + int(d[2:4]) * 60)


def _epoch(y: int, mo: int, d: int, h: int, mi: int, s: int) -> int | None:
    try:
        return calendar.timegm((y, mo, d, h, mi, s, 0, 0, 0))
    except (ValueError, OverflowError):
        return None


@dataclass(frozen=True)
class _Format:
    name: str
    rx: re.Pattern
    build: Callable[[re.Match], float | None]


_DAYS: dict[tuple[str, str, str], int | None] = {}


def _day_epoch(y: str, mo: str, d: str) -> int | None:
    """Seconds at 00:00 of a date given as digit strings (cached: log lines repeat their date)."""
    key = (y, mo, d)
    try:
        return _DAYS[key]
    except KeyError:
        pass
    month, day = int(mo), int(d)
    v = _epoch(int(y), month, day, 0, 0, 0) if 1 <= month <= 12 and 1 <= day <= 31 else None
    if len(_DAYS) > 4096:
        _DAYS.clear()
    _DAYS[key] = v
    return v


def _clock(day: int | None, h: str, mi: str, s: str, frac: str | None, tz: str | None) -> float | None:
    if day is None:
        return None
    hh, mm, ss = int(h), int(mi), int(s)
    if hh > 23 or mm > 59 or ss > 60:
        return None
    v = day + hh * 3600 + mm * 60 + ss
    if frac:
        v += float("0." + frac)
    return v - _offset(tz) if tz else v


def _iso(m: re.Match) -> float | None:
    g = m.groups()
    return _clock(_day_epoch(g[0], g[1], g[2]), g[3], g[4], g[5], g[6], g[7])


def _dmy(m: re.Match) -> float | None:
    g = m.groups()
    return _clock(_day_epoch(g[2], g[1], g[0]), g[3], g[4], g[5], g[6], g[7])


def _clf(m: re.Match) -> float | None:
    mo = _MONTHS.get(m.group(2).lower())
    if mo is None:
        return None
    base = _epoch(int(m.group(3)), mo, int(m.group(1)), int(m.group(4)), int(m.group(5)), int(m.group(6)))
    return None if base is None else base - _offset(m.group(7))


def _syslog(m: re.Match) -> float | None:
    mo = _MONTHS.get(m.group(1).lower())
    if mo is None:
        return None
    year = datetime.now().year               # syslog stamps carry no year
    base = _epoch(year, mo, int(m.group(2)), int(m.group(3)), int(m.group(4)), int(m.group(5)))
    return base


def _time_only(m: re.Match) -> float | None:
    h, mi, s = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if h > 23 or mi > 59 or s > 60:
        return None
    return h * 3600 + mi * 60 + s + _frac(m.group(4))


def _epoch_s(m: re.Match) -> float | None:
    v = int(m.group(1)) + _frac(m.group(2))
    return v if 946684800 <= v <= 4102444800 else None       # 2000 .. 2100


def _epoch_ms(m: re.Match) -> float | None:
    v = int(m.group(1)) / 1000.0
    return v if 946684800 <= v <= 4102444800 else None


FORMATS: list[_Format] = [
    _Format("ISO 8601  2026-11-10 12:34:56.789",
            re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})" + _FRAC + _TZ), _iso),
    _Format("Slashes  2026/11/10 12:34:56",
            re.compile(r"(?<!\d)(\d{4})/(\d{2})/(\d{2})[T ](\d{2}):(\d{2}):(\d{2})" + _FRAC + _TZ), _iso),
    _Format("Day first  10.11.2026 12:34:56,789",
            re.compile(r"(?<!\d)(\d{2})[./-](\d{2})[./-](\d{4})[T ](\d{2}):(\d{2}):(\d{2})" + _FRAC + _TZ), _dmy),
    _Format("Apache  10/Nov/2026:12:34:56 +0100",
            re.compile(r"(?<!\d)(\d{2})/([A-Za-z]{3})/(\d{4}):(\d{2}):(\d{2}):(\d{2})\s?([+-]\d{4})?"), _clf),
    _Format("Syslog  Nov 10 12:34:56",
            re.compile(r"(?<![A-Za-z])([A-Z][a-z]{2})\s+(\d{1,2})\s+(\d{2}):(\d{2}):(\d{2})"), _syslog),
    _Format("Epoch milliseconds",
            re.compile(r"^\W{0,3}(\d{13})(?!\d)"), _epoch_ms),
    _Format("Epoch seconds",
            re.compile(r"^\W{0,3}(\d{10})(?:\.(\d{1,9}))?(?!\d)"), _epoch_s),
    _Format("Time only  12:34:56.789",
            re.compile(r"(?<![\d:])(\d{2}):(\d{2}):(\d{2})" + _FRAC + r"(?![\d:])"), _time_only),
]
FORMAT_NAMES = [f.name for f in FORMATS]

# strptime directive -> regex
_DIRECTIVES = {
    "Y": r"(?P<Y>\d{4})", "y": r"(?P<y>\d{2})", "m": r"(?P<m>\d{1,2})", "d": r"(?P<d>\d{1,2})",
    "H": r"(?P<H>\d{1,2})", "M": r"(?P<M>\d{1,2})", "S": r"(?P<S>\d{1,2})",
    "f": r"(?P<f>\d{1,9})", "b": r"(?P<b>[A-Za-z]{3})", "B": r"(?P<B>[A-Za-z]{3,9})",
    "z": r"(?P<z>Z|[+-]\d{2}:?\d{2})", "p": r"(?P<p>[AaPp][Mm])", "I": r"(?P<I>\d{1,2})",
    "j": r"(?P<j>\d{1,3})", "%": "%",
}


def format_to_regex(fmt: str) -> re.Pattern:
    """Translate a strptime format into a regular expression (raises ValueError)."""
    out, i, seen = [], 0, set()
    while i < len(fmt):
        c = fmt[i]
        if c == "%":
            i += 1
            if i >= len(fmt) or fmt[i] not in _DIRECTIVES:
                raise ValueError(f"unsupported directive in {fmt!r}")
            d = fmt[i]
            if d in seen and d != "%":
                raise ValueError(f"%{d} used twice")
            seen.add(d)
            out.append(_DIRECTIVES[d])
        elif c.isspace():
            out.append(r"\s+")
        else:
            out.append(re.escape(c))
        i += 1
    if not ({"H", "I"} & seen):
        raise ValueError("the format needs a time (%H:%M:%S)")
    return re.compile("".join(out))


def _custom_builder(rx: re.Pattern) -> Callable[[re.Match], float | None]:
    names = set(rx.groupindex)

    def build(m: re.Match) -> float | None:
        g = {k: v for k, v in m.groupdict().items() if v is not None}
        try:
            if "Y" in names:
                year = int(g["Y"])
            elif "y" in names:
                year = 2000 + int(g["y"]) if int(g["y"]) < 70 else 1900 + int(g["y"])
            else:
                year = datetime.now().year
            if "m" in g:
                month = int(g["m"])
            elif "b" in g or "B" in g:
                month = _MONTHS[(g.get("b") or g["B"])[:3].lower()]
            else:
                month = 1
            day = int(g.get("d", 1))
            if "j" in g:
                dt = datetime(year, 1, 1) + timedelta(days=int(g["j"]) - 1)
                month, day = dt.month, dt.day
            if "I" in g:
                hour = int(g["I"]) % 12 + (12 if g.get("p", "am").lower() == "pm" else 0)
            else:
                hour = int(g.get("H", 0))
            base = _epoch(year, month, day, hour, int(g.get("M", 0)), int(g.get("S", 0)))
            if base is None:
                return None
            return base + _frac(g.get("f")) - _offset(g.get("z"))
        except (KeyError, ValueError):
            return None
    return build


class TimestampParser:
    def __init__(self, fmt: _Format | None, label: str) -> None:
        self._fmt = fmt
        self.label = label

    @property
    def usable(self) -> bool:
        return self._fmt is not None

    def parse(self, line: str) -> tuple[float, int, int] | None:
        f = self._fmt
        if f is None:
            return None
        m = f.rx.search(line, 0, SEARCH_LIMIT)
        if m is None:
            return None
        v = f.build(m)
        return None if v is None else (v, m.start(), m.end())

    def seconds(self, line: str) -> float | None:
        r = self.parse(line)
        return None if r is None else r[0]

    # ---------------------------------------------------------------- factories
    @classmethod
    def none(cls) -> "TimestampParser":
        return cls(None, "no timestamp format")

    @classmethod
    def from_format(cls, fmt: str) -> "TimestampParser":
        rx = format_to_regex(fmt)
        return cls(_Format(fmt, rx, _custom_builder(rx)), f"custom: {fmt}")

    @classmethod
    def builtin(cls, name: str) -> "TimestampParser":
        for f in FORMATS:
            if f.name == name:
                return cls(f, f.name)
        return cls.none()

    @classmethod
    def detect(cls, sample: Iterable[str], min_ratio: float = 0.3) -> "TimestampParser":
        lines = [s for s in sample if s.strip()]
        if not lines:
            return cls.none()
        best, best_hits = None, 0
        for f in FORMATS:
            hits = 0
            for s in lines:
                m = f.rx.search(s, 0, SEARCH_LIMIT)
                if m is not None and f.build(m) is not None:
                    hits += 1
            if hits > best_hits:                # earlier (more specific) formats win ties
                best, best_hits = f, hits
        if best is None or best_hits < max(1, min_ratio * len(lines)):
            return cls.none()
        return cls(best, best.name)

    @classmethod
    def from_spec(cls, spec: str, sample: Sequence[str] = ()) -> "TimestampParser":
        """'' = auto-detect, 'builtin:<name>' or a strptime format; bad spec -> auto."""
        spec = (spec or "").strip()
        if not spec:
            return cls.detect(sample)
        if spec.startswith("builtin:"):
            return cls.builtin(spec[8:])
        try:
            return cls.from_format(spec)
        except ValueError:
            return cls.detect(sample)


# ---------------------------------------------------------------- differences
def format_duration(seconds: float) -> str:
    """-3725.5 -> '-1:02:05.500'; >= 1 day -> '2d 03:04:05.000'."""
    sign = "-" if seconds < 0 else ""
    ms = int(round(abs(seconds) * 1000))
    days, ms = divmod(ms, 86_400_000)
    hours, ms = divmod(ms, 3_600_000)
    minutes, ms = divmod(ms, 60_000)
    secs, ms = divmod(ms, 1000)
    core = f"{hours}:{minutes:02d}:{secs:02d}.{ms:03d}"
    return f"{sign}{days}d {core}" if days else f"{sign}{core}"


def format_stamp(seconds: float) -> str:
    """Absolute value for display (UTC wall clock) -- small values are 'time of day'."""
    if seconds < 86_400 * 366:               # time-only formats
        return format_duration(seconds)
    try:
        dt = datetime.fromtimestamp(seconds, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return "?"
    return dt.strftime("%Y-%m-%d %H:%M:%S.") + f"{dt.microsecond // 1000:03d}"


TIME_OF_DAY_LIMIT = 86_400 * 366      # smaller values are a time of day (the format has no date)
ROLLOVER_DROP = 12 * 3600.0           # a time-only log jumping back by more than this crossed midnight
PROBE_BUDGET = 20_000                 # lines read between selected lines to find midnight crossings


def unwrap_time_of_day(stamps: list[tuple[int, float]], get_text: Callable[[int], str],
                       parser: "TimestampParser", budget: int = PROBE_BUDGET) -> tuple[list[float], int]:
    """Make the times of a log *without dates* continuous over midnight.

    ``stamps`` = ``[(line, seconds since midnight), ...]`` in line order.  The log is assumed to be
    monotonic over midnight: when the time of day falls back by more than 12 hours, a new day has begun.
    Lines between the stamped ones are sampled (``budget`` lines in total) so that a midnight between two
    far-apart lines is not missed.  Returns ``(seconds with whole days added, midnights crossed)``."""
    per_gap = max(0, budget // max(1, len(stamps) - 1))
    seq: list[tuple[float, int]] = []
    for i, (ln, v) in enumerate(stamps):
        seq.append((v, i))
        if i + 1 < len(stamps) and per_gap:
            nxt = stamps[i + 1][0]
            room = nxt - ln - 1
            if room > 0:
                for pl in np.unique(np.linspace(ln + 1, nxt - 1, min(room, per_gap)).astype(np.int64)).tolist():
                    pv = parser.seconds(get_text(pl))
                    if pv is not None and pv < TIME_OF_DAY_LIMIT:
                        seq.append((pv, -1))
    out = [0.0] * len(stamps)
    day, prev = 0, None
    for raw, idx in seq:
        if prev is not None and prev - raw > ROLLOVER_DROP:
            day += 1
        prev = raw
        if idx >= 0:
            out[idx] = raw + day * 86_400.0
    return out, day


@dataclass
class DeltaReport:
    selected: int = 0            # lines in the selection
    stamped: int = 0             # of which carry a timestamp
    first_line: int = -1         # earliest / latest stamped line (0-based line numbers)
    last_line: int = -1
    first_time: float = 0.0
    last_time: float = 0.0
    total: float = 0.0           # last - first, in selection (line) order
    max_gap: float = 0.0         # largest difference between neighbouring stamped lines
    max_gap_from: int = -1       # line before the gap / line after the gap
    max_gap_to: int = -1
    mean_gap: float = 0.0
    span: float = 0.0            # latest - earliest time (>= 0), ignores line order
    truncated: bool = False      # the selection was sampled (see compute_delta)
    label: str = ""
    days_crossed: int = 0        # midnights between the first and the last line (logs without dates)

    @property
    def ok(self) -> bool:
        return self.stamped >= 2

    def summary(self) -> str:
        if self.stamped < 2:
            return (f"Δt: need at least 2 lines with a timestamp "
                    f"({self.stamped} of {self.selected} selected lines have one)")
        day = f"crosses midnight {self.days_crossed}x; " if self.days_crossed else ""
        s = (f"Δt {format_duration(self.total)}  (line {self.first_line + 1} → {self.last_line + 1}, "
             f"{self.stamped} stamped of {self.selected} lines; {day}"
             f"max gap {format_duration(self.max_gap)}, mean {format_duration(self.mean_gap)})")
        return s + ("  [sampled]" if self.truncated else "")

    def details(self) -> str:
        if self.stamped < 2:
            return self.summary()
        rows = [
            f"Timestamp format: {self.label}",
            f"Selected lines: {self.selected}   with timestamp: {self.stamped}",
            f"First: line {self.first_line + 1}   {format_stamp(self.first_time)}",
            f"Last:  line {self.last_line + 1}   {format_stamp(self.last_time)}"
            + (f"   (+{self.days_crossed} day{'s' if self.days_crossed != 1 else ''}: the log has no date, "
               f"midnight was crossed)" if self.days_crossed else ""),
            f"Difference (last - first): {format_duration(self.total)}   ({self.total:.3f} s)",
            f"Largest gap: {format_duration(self.max_gap)}   "
            f"(between line {self.max_gap_from + 1} and line {self.max_gap_to + 1})",
            f"Average gap: {format_duration(self.mean_gap)}",
        ]
        if abs(self.span - abs(self.total)) > 0.0005:
            rows.append(f"Total time span (earliest to latest, any order): {format_duration(self.span)}")
        if self.truncated:
            rows.append("(a very large selection: sampled lines were used, gap statistics are approximate)")
        return "\n".join(rows)


def compute_delta(lines, get_text: Callable[[int], str], parser: TimestampParser,
                  limit: int = MAX_LINES) -> DeltaReport:
    """Time difference over ``lines`` (any order; sorted here).

    More than ``limit`` lines are sampled: the first and last 1000 plus evenly spaced ones,
    so the first-to-last difference stays exact while gap statistics become approximate."""
    rep = DeltaReport(label=parser.label)
    arr = np.unique(np.asarray(lines, dtype=np.int64))
    rep.selected = int(arr.size)
    if arr.size > limit:
        n, edge = int(arr.size), min(1000, limit // 4)
        idx = np.unique(np.concatenate([np.arange(edge), np.linspace(0, n - 1, limit - 2 * edge).astype(np.int64),
                                        np.arange(n - edge, n)]))
        arr, rep.truncated = arr[idx], True
    ordered = arr.tolist()
    stamps: list[tuple[int, float]] = []
    for ln in ordered:
        v = parser.seconds(get_text(ln))
        if v is not None:
            stamps.append((ln, v))
    rep.stamped = len(stamps)
    if len(stamps) < 2:
        return rep
    rep.first_line, rep.first_time = stamps[0]
    rep.last_line, rep.last_time = stamps[-1]
    rep.total = rep.last_time - rep.first_time
    times = [t for _, t in stamps]
    if all(t < TIME_OF_DAY_LIMIT for t in times):            # no date in the log: follow it over midnight
        times, rep.days_crossed = unwrap_time_of_day(stamps, get_text, parser)
        rep.total = times[-1] - times[0]
    rep.span = max(times) - min(times)
    gaps = [(times[i + 1] - times[i], i) for i in range(len(stamps) - 1)]
    g, i = max(gaps, key=lambda x: abs(x[0]))
    rep.max_gap, rep.max_gap_from, rep.max_gap_to = g, stamps[i][0], stamps[i + 1][0]
    rep.mean_gap = rep.total / (len(stamps) - 1)
    return rep
