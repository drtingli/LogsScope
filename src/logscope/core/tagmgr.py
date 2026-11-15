"""Manage bookmark tags across all open files: list, add, rename (merging), delete (Qt-free).

Tags live in two places: on the bookmarks of every open file (``{line: Bookmark}``) and in the list of
suggestions in the settings (what "Quick add" offers).  The functions here work on plain data and return
what changed, so the dialog can tell the user and refresh the right tabs."""

from __future__ import annotations

from dataclasses import dataclass, field

from .bookmarks import Bookmark, MAX_TAG_LEN, normalize_tags


@dataclass
class TagRow:
    name: str
    here: int = 0           # bookmarks of the current file that carry the tag
    total: int = 0          # ... in all open files
    suggested: bool = False


@dataclass
class TagChange:
    bookmarks: int = 0                                      # bookmarks that were modified
    files: set[int] = field(default_factory=set)            # indexes of the files that had changes
    suggestions: list[str] = field(default_factory=list)    # the new list of suggestions


def clean_tag(text) -> str:
    """One tag from user input; ValueError with a plain explanation when it is not usable."""
    if isinstance(text, str) and any(c in text for c in ",;\uff0c"):
        raise ValueError("A tag cannot contain commas or semicolons.")
    tags = normalize_tags(text if isinstance(text, str) else "")
    if not tags:
        raise ValueError("The tag is empty.")
    if len(" ".join(str(text).split())) > MAX_TAG_LEN:
        raise ValueError(f"A tag has at most {MAX_TAG_LEN} characters.")
    return tags[0]


def _sorted(tags: list[str]) -> list[str]:
    return sorted(normalize_tags(tags), key=str.lower)


def usage(files: list[dict[int, Bookmark]], suggestions: list[str], current: int | None = None) -> list[TagRow]:
    """Every known tag with its counts, sorted case-insensitively."""
    rows: dict[str, TagRow] = {}
    for fi, bms in enumerate(files):
        for bm in bms.values():
            for t in bm.tags:
                r = rows.setdefault(t.lower(), TagRow(t))
                r.total += 1
                if fi == current:
                    r.here += 1
    for t in suggestions:
        rows.setdefault(t.lower(), TagRow(t)).suggested = True
    return sorted(rows.values(), key=lambda r: r.name.lower())


def add(suggestions: list[str], tag: str) -> list[str]:
    """Add a tag to the suggestions (it is then offered by 'Quick add').  ValueError if it exists."""
    tag = clean_tag(tag)
    if tag.lower() in {t.lower() for t in suggestions}:
        raise ValueError(f"The tag \u201c{tag}\u201d exists already.")
    return _sorted(list(suggestions) + [tag])


def rename(files: list[dict[int, Bookmark]], suggestions: list[str], old: str, new: str) -> TagChange:
    """Rename ``old`` to ``new`` everywhere.  If ``new`` exists already the two tags are merged."""
    new = clean_tag(new)
    key = old.lower()
    ch = TagChange()
    for fi, bms in enumerate(files):
        for bm in bms.values():
            if any(t.lower() == key for t in bm.tags):
                bm.tags = normalize_tags([new if t.lower() == key else t for t in bm.tags])
                ch.bookmarks += 1
                ch.files.add(fi)
    ch.suggestions = _sorted([new if t.lower() == key else t for t in suggestions])
    return ch


def delete(files: list[dict[int, Bookmark]], suggestions: list[str], tag: str) -> TagChange:
    """Remove ``tag`` from every bookmark and from the suggestions."""
    key = tag.lower()
    ch = TagChange()
    for fi, bms in enumerate(files):
        for bm in bms.values():
            if any(t.lower() == key for t in bm.tags):
                bm.tags = [t for t in bm.tags if t.lower() != key]
                ch.bookmarks += 1
                ch.files.add(fi)
    ch.suggestions = [t for t in suggestions if t.lower() != key]
    return ch


def forget_unused(files: list[dict[int, Bookmark]], suggestions: list[str]) -> list[str]:
    """Suggestions that no bookmark of an open file uses are dropped."""
    used = {t.lower() for bms in files for bm in bms.values() for t in bm.tags}
    return [t for t in suggestions if t.lower() in used]
