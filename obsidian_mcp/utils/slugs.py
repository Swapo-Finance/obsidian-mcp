"""Slug (filename) and tag kebab-normalization (spec section 1:
OBSIDIAN_SLUG_STYLE / OBSIDIAN_TAG_STYLE).

Split out of vault_config.py; re-exported from there for backward
compatibility.
"""

import re
import unicodedata

_NON_SLUG_RE = re.compile(r"[^a-z0-9]+")
_HYPHEN_RUN_RE = re.compile(r"-{2,}")
_TAG_SEGMENT_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def slugify_kebab(text: str) -> str | None:
    """Transliterate arbitrary text to ASCII kebab-case: NFD-decompose,
    strip combining marks (accents — e.g. "a" + U+0301 from decomposed "á"),
    lowercase, collapse any run of non-[a-z0-9] characters to a single '-',
    trim leading/trailing '-'.

    Returns None if nothing alphanumeric survives (e.g. an all-emoji or
    all-CJK string can't be transliterated to ASCII) — callers treat that as
    "non-normalizable" and raise.
    """
    decomposed = unicodedata.normalize("NFD", text)
    # unicodedata.combining() is the correct stdlib tool for "is this
    # codepoint a combining mark" — avoids hardcoding a specific Unicode
    # block via regex (and avoids embedding literal combining characters in
    # source, which are unreadable/fragile in a diff).
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    lowered = stripped.lower()
    slug = _NON_SLUG_RE.sub("-", lowered).strip("-")
    slug = _HYPHEN_RUN_RE.sub("-", slug)
    return slug or None


def normalize_tag_kebab(tag: str) -> str | None:
    """Kebab-normalize a (possibly hierarchical, 'a/b/c') tag, segment by
    segment. Returns None if any segment has nothing alphanumeric left.
    """
    segments = tag.split("/")
    normalized_segments = []
    for segment in segments:
        slug = slugify_kebab(segment)
        if slug is None or not _TAG_SEGMENT_RE.match(slug):
            return None
        normalized_segments.append(slug)
    return "/".join(normalized_segments)
