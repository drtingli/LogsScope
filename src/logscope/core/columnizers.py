"""Columnizers: split a log line into columns .

Write your own by subclassing :class:`Columnizer` and registering it from a
plugin file (see ``plugins/keyvalue_columnizer.py``).
"""

from __future__ import annotations

import fnmatch
import functools
import importlib.util
import json
import re
import sys
import traceback
from pathlib import Path

from .timefields import DATE_TIME_LEVEL_MESSAGE, split_datetime
from .timestamps import TimestampParser


class Columnizer:
    name = "Columnizer"

    def column_names(self) -> list[str]:
        return ["Message"]

    def split(self, line: str) -> list[str]:
        return [line]

    def score(self, sample: list[str]) -> float:
        """0..1: how well this columnizer fits ``sample`` (used by auto-detect)."""
        return 0.0

    def column_roles(self) -> list[str]:
        """One entry per column: ``"date"``, ``"time"``, ``"datetime"`` or ``"text"``."""
        return ["text"] * len(self.column_names())

    def prepare(self, sample: list[str]) -> None:
        """Called with a sample of lines when this columnizer is chosen automatically."""

    def split_at(self, line_no: int, text: str) -> list[str]:
        """Like :meth:`split`, but also told the (0-based) line number -- only needed by
        columnizers whose output depends on where the line is (see :class:`MergedColumnizer`)."""
        return self.split(text)


class DefaultColumnizer(Columnizer):
    name = "Default (single column)"

    def score(self, sample: list[str]) -> float:
        return 0.01


MAX_GROUPS = 64
UNMATCHED = ("last", "first", "empty")
_NET_NAMED = re.compile(r"\(\?<([A-Za-z_]\w*)>")        # .NET style (?<name>...) -> Python (?P<name>...)


def translate_pattern(pattern: str) -> str:
    return _NET_NAMED.sub(r"(?P<\1>", pattern)


MAX_FILE_PATTERNS = 20
_RE_PREFIX = "re:"


@functools.lru_cache(maxsize=256)
def _compile_file_rx(rx: str):
    return re.compile(rx, re.IGNORECASE)


def split_file_patterns(text: str) -> list[str]:
    """``"*.log; app_*.txt"`` (or one pattern per line) -> ['*.log', 'app_*.txt']."""
    out: list[str] = []
    for part in re.split(r"[;\n]", text or ""):
        part = part.strip()
        if part and part not in out:
            out.append(part[:200])
    return out[:MAX_FILE_PATTERNS]


def check_file_pattern(pattern: str) -> None:
    """Raise ValueError when ``pattern`` is not usable."""
    if pattern.lower().startswith(_RE_PREFIX):
        try:
            _compile_file_rx(pattern[len(_RE_PREFIX):])
        except re.error as exc:
            raise ValueError(f"invalid file pattern {pattern!r}: {exc}") from exc


def file_pattern_matches(pattern: str, path: str) -> bool:
    """Does one file-name pattern match ``path``?  Case-insensitive, ``\\`` and ``/`` are the same.

    * ``*.log``, ``app_??.txt``  -- wildcards, tested against the file name only;
    * ``logs/*.log``             -- a pattern with a folder part is tested against the end of the full path;
    * ``re:<regex>``             -- a regular expression searched in the full path.
    """
    full = (path or "").replace("\\", "/")
    if pattern.lower().startswith(_RE_PREFIX):
        try:
            return _compile_file_rx(pattern[len(_RE_PREFIX):]).search(full) is not None
        except re.error:
            return False
    pat = pattern.replace("\\", "/").lower()
    low = full.lower()
    if "/" in pat:
        return fnmatch.fnmatchcase(low, pat) or fnmatch.fnmatchcase(low, "*/" + pat.lstrip("/"))
    return fnmatch.fnmatchcase(low.rsplit("/", 1)[-1], pat)


def path_matches(patterns, path: str) -> bool:
    return any(file_pattern_matches(p, path) for p in patterns or [])


class RegexColumnizer(Columnizer):
    """Columns = the capture groups of a regular expression (named groups give the column names).

    * ``search``: the pattern may match anywhere in the line (default: from the start);
    * ``unmatched``: where a line that does not match goes -- into the ``last`` column (default,
      right for continuation lines such as stack traces), the ``first`` one, or nowhere (``empty``);
    * ``names``: optional column titles that replace the group names / ``Col n``;
    * ``auto``: take part in automatic columnizer detection (``bonus`` lets user columnizers win ties
      against the built-in ones);
    * ``files``: file-name patterns (see :func:`file_pattern_matches`); a file that matches one of them
      gets this columnizer automatically, before any content-based detection.
    """

    def __init__(self, name: str, pattern: str, *, ignore_case: bool = False, search: bool = False,
                 unmatched: str = "last", names: list[str] | None = None, auto: bool = True, bonus: float = 0.0,
                 files: list[str] | None = None, score_group: str | None = None,
                 split_timestamp: bool = True) -> None:
        self.name = name
        self.split_timestamp = bool(split_timestamp)
        self.score_group = score_group          # auto-detect counts only lines where this group is non-empty
        self.files = list(files or [])
        self.pattern = pattern
        try:
            self._rx = re.compile(translate_pattern(pattern), re.IGNORECASE if ignore_case else 0)
        except re.error as exc:
            raise ValueError(f"invalid regular expression: {exc}") from exc
        if self._rx.groups < 1:
            raise ValueError("pattern needs at least one capture group")
        if self._rx.groups > MAX_GROUPS:
            raise ValueError(f"at most {MAX_GROUPS} capture groups")
        if unmatched not in UNMATCHED:
            raise ValueError(f"unmatched must be one of {UNMATCHED}")
        self.search, self.unmatched, self.auto = bool(search), unmatched, bool(auto)
        self.bonus = float(bonus)
        by_index = {idx: nm for nm, idx in self._rx.groupindex.items()}
        self._names = [by_index.get(i, f"Col {i}") for i in range(1, self._rx.groups + 1)]
        for i, custom in enumerate(names or []):
            if i < len(self._names) and isinstance(custom, str) and custom.strip():
                self._names[i] = custom.strip()
        self._find = self._rx.search if self.search else self._rx.match
        self._group_roles = self._roles_of_groups()
        # a column that holds a whole timestamp is shown as two columns: Date | Time
        self._split_at = (self._group_roles.index("datetime")
                          if self.split_timestamp and "datetime" in self._group_roles else -1)
        self._out_names = list(self._names)
        self._out_roles = list(self._group_roles)
        if self._split_at >= 0:
            self._out_names[self._split_at:self._split_at + 1] = ["Date", "Time"]
            self._out_roles[self._split_at:self._split_at + 1] = ["date", "time"]

    def _roles_of_groups(self) -> list[str]:
        """Groups named Date / Time / Timestamp / DateTime are recognised as time columns."""
        by_index = {idx: nm for nm, idx in self._rx.groupindex.items()}
        roles = []
        for i in range(1, self._rx.groups + 1):
            key = (by_index.get(i) or self._names[i - 1]).strip().lower().replace(" ", "").replace("_", "")
            roles.append("date" if key in ("date", "day") else "time" if key in ("time", "clock") else
                         "datetime" if key in ("timestamp", "datetime", "ts", "stamp") else "text")
        return roles

    def column_names(self) -> list[str]:
        return list(self._out_names)

    def column_roles(self) -> list[str]:
        return list(self._out_roles)

    def split(self, line: str) -> list[str]:
        m = self._find(line)
        n = len(self._out_names)
        if m is None:
            row = [""] * n
            if self.unmatched == "last":
                row[-1] = line
            elif self.unmatched == "first":
                row[0] = line
            return row
        row = [g or "" for g in m.groups()]
        if self._split_at >= 0:
            stamp = row[self._split_at].strip()
            parts = split_datetime(stamp)
            row[self._split_at:self._split_at + 1] = list(parts) if parts is not None else ["", stamp]
        return row

    def score(self, sample: list[str]) -> float:
        if not self.auto:
            return 0.0
        sample = [s for s in sample if s.strip()]
        if not sample:
            return 0.0
        if self.score_group and self.score_group in self._rx.groupindex:
            gi = self._rx.groupindex[self.score_group]
            hits = 0
            for s in sample:
                m = self._find(s)
                if m is not None and m.group(gi):
                    hits += 1
            ratio = hits / len(sample)
        else:
            ratio = sum(1 for s in sample if self._find(s)) / len(sample)
        return ratio + self.bonus if ratio >= 0.5 else ratio


TIMESTAMPED_NAME = "Date / Time / Level / Message"


def sanitize_regex_spec(d) -> dict | None:
    """One user-defined regex columnizer as stored in the settings, or None if unusable."""
    if not isinstance(d, dict):
        return None
    name, pattern = d.get("name"), d.get("pattern")
    if not isinstance(name, str) or not isinstance(pattern, str) or not name.strip() or not pattern:
        return None
    names = d.get("names")
    names = [n.strip()[:40] for n in names if isinstance(n, str)][:MAX_GROUPS] if isinstance(names, list) else []
    um = d.get("unmatched")
    files = d.get("files")
    files = [f.strip()[:200] for f in files if isinstance(f, str) and f.strip()][:MAX_FILE_PATTERNS] \
        if isinstance(files, list) else []
    return {"name": name.strip()[:60], "pattern": pattern[:2000],
            "ignore_case": d.get("ignore_case") is True, "search": d.get("search") is True,
            "unmatched": um if um in UNMATCHED else "last", "names": names, "auto": d.get("auto") is True,
            "files": files, "split_timestamp": d.get("split_timestamp") is not False}


def regex_columnizer_from_spec(spec: dict) -> RegexColumnizer:
    s = sanitize_regex_spec(spec)
    if s is None:
        raise ValueError("a name and a pattern are required")
    for f in s["files"]:
        check_file_pattern(f)
    return RegexColumnizer(s["name"], s["pattern"], ignore_case=s["ignore_case"], search=s["search"],
                           unmatched=s["unmatched"], names=s["names"], auto=s["auto"],
                           bonus=0.05 if s["auto"] else 0.0, files=s["files"],
                           split_timestamp=s["split_timestamp"])


TIMESTAMPED = RegexColumnizer(TIMESTAMPED_NAME, DATE_TIME_LEVEL_MESSAGE, score_group="Level")


class JsonLinesColumnizer(Columnizer):
    name = "JSON lines"

    def __init__(self) -> None:
        self._keys: list[str] = ["message"]

    def prepare(self, sample: list[str]) -> None:
        keys: dict[str, None] = {}
        for s in sample:
            obj = _loads(s)
            if isinstance(obj, dict):
                for k in obj:
                    keys.setdefault(str(k), None)
        self._keys = list(keys)[:12] or ["message"]

    def column_names(self) -> list[str]:
        return list(self._keys)

    def split(self, line: str) -> list[str]:
        obj = _loads(line)
        if not isinstance(obj, dict):
            return [line] + [""] * (len(self._keys) - 1)
        return [_cell(obj.get(k, "")) for k in self._keys]

    def score(self, sample: list[str]) -> float:
        sample = [s for s in sample if s.strip()]
        if not sample:
            return 0.0
        hits = sum(1 for s in sample if isinstance(_loads(s), dict))
        return 0.95 * hits / len(sample)


class TimestampColumnizer(Columnizer):
    """Splits off the timestamp (any format of ``core.timestamps``): Date | Time | Message.

    The date and the time are cut out of the very span the timestamp parser found, so the two
    columns always agree with what the time computations (differences, sync, merge) read.  A stamp
    without a date gives an empty Date; a stamp that is no clock time (epoch seconds) goes to Time.

    The shared registry instance only scores; each tab binds its own copy to its
    timestamp parser with :meth:`with_parser`."""

    name = "Date | Time | Message"
    ROLES = ("date", "time", "text")
    _TRIM = " \t[({<|-"
    MIN_HITS = 3                      # "several" lines with a timestamp are enough to choose this one

    def __init__(self, parser: TimestampParser | None = None) -> None:
        self.parser = parser or TimestampParser.none()

    def with_parser(self, parser: TimestampParser) -> "TimestampColumnizer":
        return TimestampColumnizer(parser)

    def column_names(self) -> list[str]:
        return ["Date", "Time", "Message"]

    def column_roles(self) -> list[str]:
        return list(self.ROLES)

    def split(self, line: str) -> list[str]:
        r = self.parser.parse(line)
        if r is None:
            return ["", "", line]
        _, a, b = r
        before, after = line[:a].strip(self._TRIM + "])}>"), line[b:].lstrip(" \t])}>|-:")
        msg = f"{before} {after}".strip() if before else after
        stamp = line[a:b].strip()
        parts = split_datetime(stamp)
        date, time = parts if parts is not None else ("", stamp)
        return [date, time, msg]

    def score(self, sample: list[str]) -> float:
        lines = [s for s in sample if s.strip()]
        if not lines:
            return 0.0
        p = TimestampParser.detect(lines)
        if not p.usable:
            return 0.0
        hits = sum(1 for s in lines if p.parse(s) is not None)
        ratio = hits / len(lines)
        if hits >= min(self.MIN_HITS, len(lines)) and ratio >= 0.3:
            return 0.51 + 0.09 * ratio      # above the 0.5 bar, still below any rule that fits the lines
        return 0.6 * ratio


def _loads(s: str):
    s = s.lstrip()
    if not s.startswith("{"):
        return None
    try:
        return json.loads(s)
    except ValueError:
        return None


def _cell(v) -> str:
    return v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)


class MergedColumnizer(Columnizer):
    """Wraps a columnizer of a merged tab and puts the source file of each line in front of it.

    ``merge`` only needs a ``path_of(line_no) -> str`` method."""

    def __init__(self, inner: Columnizer, merge) -> None:
        self.inner, self.merge = inner, merge
        self.name = f"{inner.name} + Source"

    def column_names(self) -> list[str]:
        return ["Source"] + self.inner.column_names()

    def column_roles(self) -> list[str]:
        return ["text"] + self.inner.column_roles()

    def split(self, line: str) -> list[str]:
        return [""] + self.inner.split(line)

    def split_at(self, line_no: int, text: str) -> list[str]:
        try:
            path = self.merge.path_of(line_no)
        except Exception:
            path = ""
        return [path] + self.inner.split(text)


class ColumnizerRegistry:
    def __init__(self) -> None:
        self._items: dict[str, Columnizer] = {}
        self._user: list[str] = []                 # names of the user-defined regex columnizers
        self.default = DefaultColumnizer()
        for c in (self.default, TIMESTAMPED, TimestampColumnizer(), JsonLinesColumnizer()):
            self.register(c)

    def register(self, c: Columnizer) -> None:
        self._items[c.name] = c

    def names(self) -> list[str]:
        return list(self._items)

    def user_names(self) -> list[str]:
        return list(self._user)

    def set_user_regex(self, specs) -> list[str]:
        """Replace the user-defined regex columnizers.  Returns one message per unusable entry."""
        for n in self._user:
            self._items.pop(n, None)
        self._user = []
        errors: list[str] = []
        for raw in specs if isinstance(specs, list) else []:
            spec = sanitize_regex_spec(raw)
            if spec is None:
                errors.append("an entry without name or pattern was skipped")
                continue
            try:
                c = regex_columnizer_from_spec(spec)
            except ValueError as exc:
                errors.append(f"{spec['name']}: {exc}")
                continue
            base, n = c.name, 1
            while c.name in self._items:              # never replace a built-in or an earlier entry
                n += 1
                c.name = f"{base} (regex)" if n == 2 else f"{base} (regex {n - 1})"
            self.register(c)
            self._user.append(c.name)
        return errors

    def for_path(self, path: str) -> Columnizer | None:
        """The first user-defined regex columnizer (in list order) whose file patterns match ``path``."""
        for n in self._user:
            c = self._items.get(n)
            if c is not None and path_matches(getattr(c, "files", None), path):
                return c
        return None

    def get(self, name: str | None) -> Columnizer:
        return self._items.get(name or "", self.default)

    def auto_detect(self, sample: list[str]) -> Columnizer:
        best, best_score = self.default, 0.5   # need >=50 % of sample lines to match
        for c in self._items.values():
            s = c.score(sample)
            if s > best_score:
                best, best_score = c, s
        best.prepare(sample)
        return best

    def load_plugins(self, folder: str | Path) -> list[str]:
        """Import every ``*.py`` in ``folder`` and call its ``register(registry)``."""
        errors: list[str] = []
        folder = Path(folder)
        if not folder.is_dir():
            return errors
        for f in sorted(folder.glob("*.py")):
            try:
                spec = importlib.util.spec_from_file_location(f"logscope_plugin_{f.stem}", f)
                mod = importlib.util.module_from_spec(spec)
                sys.modules[spec.name] = mod
                spec.loader.exec_module(mod)
                reg = getattr(mod, "register", None)
                if callable(reg):
                    reg(self)
            except Exception:                           # a broken plugin must not kill the app
                errors.append(f"{f.name}: {traceback.format_exc(limit=1)}")
        return errors
