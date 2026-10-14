"""Markdown heading grammar: heading lines and the bounds of their sections.

Pure functions over a note split into lines, shared by edit_note_section and
read_note. The grammar does not know code fences or frontmatter: a "# comment"
line inside either one counts as a heading.
"""

import re
import unicodedata
from typing import NamedTuple

_HEADING = re.compile(r"^(#{1,6})\s+(.+)$")
# Looser than _HEADING: a bare "## " line closes sections but is not a heading.
_CLOSER = re.compile(r"^(#{1,6})\s+")


class Heading(NamedTuple):
    """A heading line and its section, lines[start:end]."""

    start: int
    end: int
    level: int
    title: str


def parse_heading(line: str) -> tuple[int, str] | None:
    """(level, stripped text) of a markdown heading line, or None when the line
    is not a heading. section_end uses a looser closer pattern: a bare "## "
    line ends a section but is not a heading."""
    match = _HEADING.match(line)
    if not match:
        return None
    return len(match.group(1)), match.group(2).strip()


def section_end(lines: list[str], start: int, level: int) -> int:
    """Index of the first line after `start` that is a heading of `level` or a
    higher level (fewer #), or len(lines) when the section runs to the end."""
    for j in range(start + 1, len(lines)):
        next_match = _CLOSER.match(lines[j])
        if next_match and len(next_match.group(1)) <= level:
            return j
    return len(lines)


def find_section_bounds(
    lines: list[str], heading_level: int, heading_text: str
) -> tuple[int, int] | None:
    """Locate the first heading (case-insensitive, NFC-normalized) matching
    heading_level/heading_text -- first match wins when a heading repeats.

    Returns (section_start, section_end): section_end is the index of the
    next heading of the same or higher level, or len(lines) if the section
    runs to end of file. Returns None if no heading matches.
    """
    normalized_target = unicodedata.normalize("NFC", heading_text.lower())
    for i, line in enumerate(lines):
        heading = parse_heading(line)
        if heading is None or heading[0] != heading_level:
            continue
        if unicodedata.normalize("NFC", heading[1].lower()) != normalized_target:
            continue
        return i, section_end(lines, i, heading_level)

    return None


def scan_headings(lines: list[str]) -> list[Heading]:
    """Every heading of lines, in order, each with the end that section_end
    gives it, in one pass. A stack holds the open sections, levels rising;
    each closer line ends the open sections of its level or deeper. One
    section_end scan per heading would cost O(lines x open headings)."""
    found: list[tuple[int, int, str]] = []  # (start, level, title)
    ends: list[int] = []
    open_sections: list[int] = []  # indexes into found
    for index, line in enumerate(lines):
        closer = _CLOSER.match(line)
        if closer is None:
            continue
        level = len(closer.group(1))
        while open_sections and found[open_sections[-1]][1] >= level:
            ends[open_sections.pop()] = index
        heading = parse_heading(line)
        if heading is not None:
            open_sections.append(len(found))
            found.append((index, *heading))
            ends.append(len(lines))
    return [
        Heading(start, end, level, title)
        for (start, level, title), end in zip(found, ends, strict=True)
    ]
