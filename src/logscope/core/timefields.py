"""Splitting a timestamp string into its date and time parts, plus ready-made regex building blocks.

``split_datetime("2026-11-13T17:02:11.976Z")`` -> ``("2026-11-13", "17:02:11.976Z")``.
The same pieces are offered to the user as predefined sub-patterns for the parsing-rule editor
(:data:`SNIPPETS`) and as complete rules (:data:`DATE_TIME_MESSAGE` ...).

Date and time columns produced from a timestamp are *recognised*: :func:`fields_epoch` turns the
two cell texts back into seconds, with exactly the formats of :mod:`logscope.core.timestamps`, so
they can be used for time differences, sorting and similar computations.
"""

from __future__ import annotations

import re

# ----------------------------------------------------------------- building blocks (Python re syntax)
DATE_ISO = r"\d{4}[-/.]\d{1,2}[-/.]\d{1,2}"                         # 2026-11-13  2026/11/13  2026.11.13
DATE_DMY = r"\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}"                        # 13.11.2026  11/13/2026
DATE_COMPACT = r"\d{8}"                                              # 20261113
DATE_MON_FIRST = r"[A-Za-z]{3,9}\.?\s+\d{1,2}(?:,?\s+\d{4})?"         # Nov 13   Nov 13, 2026
DATE_DAY_FIRST = r"\d{1,2}\s+[A-Za-z]{3,9}\.?(?:\s+\d{4})?"           # 13 Nov 2026
DATE_ANY = "|".join((DATE_ISO, DATE_DMY, DATE_COMPACT, DATE_MON_FIRST, DATE_DAY_FIRST))

TIME_CLOCK = r"\d{1,2}:\d{2}(?::\d{2}(?:[.,]\d{1,9})?)?"             # 17:02   17:02:11   17:02:11.976
TIME_AMPM = r"(?:\s*[AaPp][Mm])?"
TIME_ZONE = r"(?:\s*(?:Z|UTC|GMT|[+-]\d{2}(?::?\d{2})?))?"            # Z  +01:00  +0100  UTC
TIME_ANY = TIME_CLOCK + TIME_AMPM + TIME_ZONE

# one complete timestamp = optional date, separator (T / blank / comma / underscore), time
DATETIME_SPLIT = re.compile(
    rf"^\s*(?:(?P<date>{DATE_ANY})(?:[Tt]|[\s,_]+))?(?P<time>{TIME_ANY})\s*$")

LEVELS = (r"TRACE|DEBUG|INFO(?:RMATION)?|NOTICE|WARN(?:ING)?|ERROR|ERR|SEVERE|CRIT(?:ICAL)?|FATAL|"
          r"ALERT|EMERG(?:ENCY)?|VERBOSE|FINE|FINER|FINEST")

# named-group snippets for the rule editor ("Insert predefined pattern")
SNIPPETS: list[tuple[str, str]] = [
    ("Date (2026-11-13)", rf"(?P<Date>{DATE_ISO})"),
    ("Time (17:02:11.976, optional zone)", rf"(?P<Time>{TIME_CLOCK}{TIME_ZONE})"),
    ("ISO 8601 date + time as two columns",
     rf"(?P<Date>{DATE_ISO})[Tt ,_]+(?P<Time>{TIME_CLOCK}{TIME_ZONE})"),
    ("Timestamp in [brackets], date + time",
     rf"\[(?P<Date>{DATE_ISO})[Tt ,_]+(?P<Time>{TIME_CLOCK}{TIME_ZONE})\]"),
    ("Day-first date + time (13.11.2026 17:02:11)",
     rf"(?P<Date>{DATE_DMY})[Tt ,_]+(?P<Time>{TIME_CLOCK}{TIME_ZONE})"),
    ("Log level word", rf"(?P<Level>(?i:{LEVELS}))"),
    ("Thread id", r"(?P<Thread>\d+)"),
    ("Rest of the line", r"(?P<Message>.*)"),
]

# complete rules: Date | Time | [Level |] Message
DATE_TIME_MESSAGE = (
    rf"^\s*[\[(<]?(?P<Date>{DATE_ISO})[Tt ,_]+(?P<Time>{TIME_CLOCK}{TIME_ZONE})[\])>]?"
    r"[\s|;,:-]*(?P<Message>.*)$")
DATE_TIME_LEVEL_MESSAGE = (
    rf"^\s*[\[(<]?(?P<Date>{DATE_ISO})[Tt ,_]+(?P<Time>{TIME_CLOCK}{TIME_ZONE})[\])>]?"
    rf"[\s|;,:-]*(?:(?P<Level>(?i:{LEVELS}))\b[\s|;,:\]-]*)?(?P<Message>.*)$")


def split_datetime(text: str) -> tuple[str, str] | None:
    """``(date, time)`` of a timestamp text, ``date`` being ``""`` for a time-only stamp.
    ``None`` when ``text`` is not a clock time (e.g. an epoch number)."""
    m = DATETIME_SPLIT.match(text or "")
    if m is None:
        return None
    return (m.group("date") or "").strip(), m.group("time").strip()


def join_datetime(date: str, time: str) -> str:
    """Inverse of :func:`split_datetime` in a form the timestamp parsers read."""
    if not date:
        return time
    sep = "T" if re.fullmatch(DATE_ISO, date) else " "
    return f"{date}{sep}{time}"


def fields_epoch(date: str, time: str, parser=None) -> float | None:
    """Seconds for a Date and a Time cell (same value the timestamp parser gives for the whole line).

    ``parser`` is a :class:`~logscope.core.timestamps.TimestampParser`; without one the formats are
    detected from this single value."""
    from .timestamps import TimestampParser

    text = join_datetime(date.strip(), time.strip())
    p = parser if parser is not None and parser.usable else TimestampParser.detect([text])
    if not p.usable:
        return None
    r = p.parse(text)
    return None if r is None else r[0]
