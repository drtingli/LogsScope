"""Line highlighting rules (simplified)."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass

HEX_COLOUR = re.compile(r"^#[0-9a-fA-F]{6}$")


@dataclass
class HighlightRule:
    pattern: str
    regex: bool = False
    case_sensitive: bool = False
    fg: str = "#000000"
    bg: str = "#ffff99"
    whole_line: bool = True        # False: only the matching words are coloured

    def to_dict(self) -> dict:
        return asdict(self)


class HighlightEngine:
    def __init__(self, rules: list[HighlightRule] | None = None) -> None:
        self.set_rules(rules or [])

    def set_rules(self, rules: list[HighlightRule]) -> None:
        self.rules = [r for r in rules if r.pattern]
        self._compiled = []              # whole-line rules
        self._words = []                 # (rule, compiled regex) for matching-words-only rules
        for r in self.rules:
            try:
                if not r.whole_line:
                    flags = 0 if r.case_sensitive else re.IGNORECASE
                    self._words.append((r, re.compile(r.pattern if r.regex else re.escape(r.pattern), flags)))
                elif r.regex:
                    rx = re.compile(r.pattern, 0 if r.case_sensitive else re.IGNORECASE)
                    self._compiled.append((r, rx.search, None))
                else:
                    needle = r.pattern if r.case_sensitive else r.pattern.lower()
                    self._compiled.append((r, None, needle))
            except re.error:
                continue

    @property
    def has_word_rules(self) -> bool:
        return bool(self._words)

    def spans(self, text: str, limit: int = 300) -> list[tuple[int, int, str, str]]:
        """``(start, end, fg, bg)`` of every word matched by a matching-words-only rule.
        Earlier rules win where matches overlap."""
        out: list[tuple[int, int, str, str]] = []
        for rule, rx in self._words:
            for m in rx.finditer(text):
                s, e = m.span()
                if e <= s or any(s < oe and e > os_ for os_, oe, _f, _b in out):
                    continue
                out.append((s, e, rule.fg, rule.bg))
                if len(out) >= limit:
                    return sorted(out)
        return sorted(out)

    def match(self, line: str) -> HighlightRule | None:
        """First matching whole-line rule wins (rule order = priority)."""
        low = None
        for rule, search, needle in self._compiled:
            if search is not None:
                if search(line):
                    return rule
            elif rule.case_sensitive:
                if needle in line:
                    return rule
            else:
                if low is None:
                    low = line.lower()
                if needle in low:
                    return rule
        return None


DEFAULT_RULES = [
    HighlightRule("error|fatal|exception", True, False, "#ffffff", "#c0392b"),
    HighlightRule("warn", False, False, "#000000", "#f5cd79"),
]


def validate_rule(rule: HighlightRule) -> str | None:
    """Error text if the rule cannot be used, else None."""
    if rule.regex:
        try:
            re.compile(rule.pattern)
        except re.error as exc:
            return str(exc)
    return None


def groups_to_json(groups: dict[str, list[HighlightRule]]) -> dict:
    return {"version": 1,
            "groups": {n: [r.to_dict() for r in rs] for n, rs in groups.items()}}


def _rule_from_dict(d) -> HighlightRule | None:
    if not isinstance(d, dict) or not isinstance(d.get("pattern"), str) or not d["pattern"]:
        return None
    base = HighlightRule("")
    fg, bg = d.get("fg"), d.get("bg")
    return HighlightRule(
        d["pattern"],
        d.get("regex") is True,
        d.get("case_sensitive") is True,
        fg if isinstance(fg, str) and HEX_COLOUR.match(fg) else base.fg,
        bg if isinstance(bg, str) and HEX_COLOUR.match(bg) else base.bg,
        d.get("whole_line") is not False,
    )


def groups_from_json(obj) -> dict[str, list[HighlightRule]]:
    """Accepts ``{"groups": {name: [rules]}}`` or a single ``{"name":..., "rules": [...]}``.
    Raises ValueError when nothing usable is found."""
    raw: dict = {}
    if isinstance(obj, dict) and isinstance(obj.get("groups"), dict):
        raw = obj["groups"]
    elif isinstance(obj, dict) and isinstance(obj.get("rules"), list):
        raw = {str(obj.get("name") or "Imported"): obj["rules"]}
    out: dict[str, list[HighlightRule]] = {}
    for name, rules in raw.items():
        if isinstance(rules, list) and str(name).strip():
            out[str(name).strip()] = [r for r in map(_rule_from_dict, rules) if r is not None]
    if not out:
        raise ValueError("no highlight groups found in file")
    return out
