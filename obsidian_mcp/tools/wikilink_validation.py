"""Write-time wikilink validation (spec section 4), split out of
tools/link_management.py (same precedent as utils/links.py -- see the
comment near the top of link_management.py) -- used by note_management's
create_note/update_note/edit_note_section and by daily_notes.add_daily_note,
both of which import validate_wikilinks_for_write through the
link_management facade re-export.

This is intentionally a *separate* extractor from extract_links_from_content
in utils/links.py: that one feeds get_backlinks/get_outgoing_links/
find_broken_links and must keep matching everything it always has (including
embeds and links inside code, for full backward compatibility). This one
only looks at genuine, prose [[wikilinks]] the user is about to write.

Safe to split out: validate_wikilinks_for_write takes `vault` as a plain
parameter (no get_vault() call), and is only ever imported directly by name
in tests/ and elsewhere -- never patched by module-qualified name.
"""

import re
from pathlib import Path

from ..utils.vault_config import slugify_kebab
from .link_index import build_vault_notes_index

_FENCED_CODE_RE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`[^`\n]+`")
_WIKI_EMBED_RE = re.compile(r"!\[\[[^\]]*\]\]")
_MARKDOWN_EMBED_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_VALIDATION_WIKI_LINK_RE = re.compile(r"(?<!!)\[\[([^\]]*)\]\]")


def _mask_ineligible_regions(content: str) -> str:
    """Blank out (same length, so match spans stay aligned with the
    original string) fenced code, inline code, and embeds, so the wikilink
    validator never matches a link that only appears inside one of those.
    """

    def _blank(match: "re.Match[str]") -> str:
        return " " * len(match.group(0))

    masked = _FENCED_CODE_RE.sub(_blank, content)
    masked = _INLINE_CODE_RE.sub(_blank, masked)
    masked = _WIKI_EMBED_RE.sub(_blank, masked)
    masked = _MARKDOWN_EMBED_RE.sub(_blank, masked)
    return masked


def _resolve_direct_target(note_ref: str, notes_index: dict[str, str]) -> str | None:
    """Resolve note_ref against the vault's basename/stem index: try the
    ".md"-qualified name, then the bare ref, then a direct value match
    (note_ref already looks like a real relpath)."""
    lookup_name = note_ref if note_ref.endswith(".md") else note_ref + ".md"
    resolved_path = notes_index.get(lookup_name) or notes_index.get(note_ref)
    if not resolved_path and lookup_name in notes_index.values():
        resolved_path = lookup_name
    return resolved_path


def _resolve_kebab_fallback_target(
    note_ref: str, notes_index: dict[str, str]
) -> str | None:
    """OBSIDIAN_SLUG_STYLE=kebab fallback: note_ref didn't resolve directly,
    but its kebab-slug may still match an existing note's stem (e.g. an
    ASCII "Cafe Especial" typed against an accented "Café Especial.md" file
    already on disk)."""
    target_slug = slugify_kebab(note_ref)
    if not target_slug:
        return None
    for name, real_path in notes_index.items():
        stem = name.removesuffix(".md")
        if slugify_kebab(stem) == target_slug:
            return real_path
    return None


def _suggest_similar_notes(
    notes_index: dict[str, str], targets: list[str], limit: int = 3
) -> str:
    """Up to `limit` fuzzy (case-insensitive substring/prefix) suggestions
    per broken target, for a strict-policy error message."""
    candidates = sorted({name.removesuffix(".md") for name in notes_index})
    parts = []
    for target in targets:
        target_lower = target.lower()
        matches = [c for c in candidates if target_lower in c.lower()]
        if matches:
            parts.append(f"'{target}' -> maybe: {', '.join(matches[:limit])}")
    return f" Suggestions: {'; '.join(parts)}." if parts else ""


async def validate_wikilinks_for_write(vault, content: str) -> tuple[str, list[str]]:
    """
    Validate (and, for OBSIDIAN_SLUG_STYLE=kebab, transparently fix up)
    [[wikilinks]] in content that is about to be written.

    Format errors — an empty target ([[]]) or nested/unbalanced brackets —
    always raise ValueError, regardless of OBSIDIAN_WIKILINK_POLICY: the
    format is malformed independent of whether broken targets are tolerated.
    [[#Heading]] (no note name — a same-note heading reference) is valid and
    skipped, it isn't a link to another note.

    Broken *targets* (the note doesn't exist) are handled per
    OBSIDIAN_WIKILINK_POLICY: strict raises ValueError with fuzzy
    suggestions, warn returns the message in the warnings list (content is
    still written), off is silent.

    Returns (possibly-rewritten content, warnings). The content is rewritten
    only when OBSIDIAN_SLUG_STYLE=kebab and a link target doesn't resolve
    directly but its kebab-slug matches an existing note — the link is
    rewritten to point at the real filename (keeping the user's original
    text as the alias) so Obsidian can still resolve it.
    """
    masked = _mask_ineligible_regions(content)
    matches = list(_VALIDATION_WIKI_LINK_RE.finditer(masked))
    if not matches:
        return content, []

    notes_index = await build_vault_notes_index(vault)
    warnings: list[str] = []
    broken_targets: list[str] = []
    # (start, end, replacement) spans into `content` — NOT a raw-text ->
    # replacement dict. `masked` is length/position-aligned with `content`
    # (see _mask_ineligible_regions), so match.start()/end() from the masked
    # text are valid offsets into content too. Rewriting by position (below)
    # instead of str.replace(old, new) is required: a str.replace would
    # rewrite every literal occurrence of "[[Nota]]" in content, including
    # ones inside a fenced/inline code block that only happen to contain the
    # same text as a real link elsewhere in the note.
    replacements: list[tuple[int, int, str]] = []

    for match in matches:
        raw_inner = match.group(1)
        if "[[" in raw_inner or "]]" in raw_inner:
            raise ValueError(
                f"Malformed wikilink {match.group(0)!r}: nested or unbalanced brackets. "
                "Fix or remove it before saving."
            )

        target_part, _, alias = raw_inner.partition("|")
        # Strip an optional "#Heading" suffix — only the note target is
        # validated, per spec (the heading itself isn't checked).
        note_ref = target_part.split("#", 1)[0].strip()

        if not note_ref:
            if target_part.strip().startswith("#"):
                continue  # [[#Heading]] — same-note reference, always valid
            raise ValueError(
                f"Malformed wikilink {match.group(0)!r}: empty target. "
                "Fix or remove it before saving."
            )

        resolved_path = _resolve_direct_target(note_ref, notes_index)

        if not resolved_path and vault.slug_style == "kebab":
            resolved_path = _resolve_kebab_fallback_target(note_ref, notes_index)
            if resolved_path:
                display = alias.strip() if alias else target_part.strip()
                real_stem = Path(resolved_path).stem
                replacements.append(
                    (match.start(), match.end(), f"[[{real_stem}|{display}]]")
                )

        if not resolved_path:
            broken_targets.append(note_ref)

    # Right-to-left so earlier spans stay valid as later (higher-offset)
    # ones are rewritten first.
    new_content = content
    for start, end, new in sorted(replacements, key=lambda item: item[0], reverse=True):
        new_content = new_content[:start] + new + new_content[end:]

    if broken_targets:
        if vault.wikilink_policy == "strict":
            suggestions = _suggest_similar_notes(notes_index, broken_targets)
            broken_list = ", ".join(f"[[{t}]]" for t in broken_targets)
            raise ValueError(
                f"Broken wikilink target(s): {broken_list}.{suggestions} "
                "Create the target note first, fix the link text, or remove the link."
            )
        elif vault.wikilink_policy == "warn":
            for target in broken_targets:
                warnings.append(f"Wikilink target not found: [[{target}]]")
        # "off": no-op — content is written as-is.

    return new_content, warnings
