"""Write-time policy chain for note writes.

Template conformance, wikilink validation, kebab slug/tag normalization,
frontmatter requirements, note-size policy warnings, and the
write-serialization lock -- the checks tools/CLAUDE.md documents as running
on every note write. Consumed by note_management.py (create_note/
update_note/delete_note), section_editing.py (edit_note_section), and
daily_notes.py (add_daily_note, via _serialize_note_writes re-exported from
note_management).
"""

import functools
import re

from ..utils.filesystem import get_vault
from ..utils.vault_config import (
    apply_frontmatter_requirements,
    check_note_size_policy,
    check_template_conformance,
    count_lines,
    normalize_tag_kebab,
    slugify_kebab,
)
from .frontmatter_tags import _update_frontmatter_tags
from .link_management import validate_wikilinks_for_write


def apply_slug_style_to_path(vault, path: str) -> str:
    """If OBSIDIAN_SLUG_STYLE=kebab, kebab-slugify the note's filename (not
    its folder path — folder names are left as the user/template chose)
    so filenames/links stay portable ASCII (spec section 1, item a).
    Raises ValueError if the filename has nothing alphanumeric to slugify.
    """
    if vault.slug_style != "kebab":
        return path
    directory, _, filename = path.rpartition("/")
    stem = filename.removesuffix(".md")
    slug = slugify_kebab(stem)
    if slug is None:
        raise ValueError(
            f"Filename '{stem}' cannot be normalized to kebab-case (OBSIDIAN_SLUG_STYLE=kebab). "
            "It must contain at least one letter or digit."
        )
    new_filename = f"{slug}.md"
    return f"{directory}/{new_filename}" if directory else new_filename


def apply_slug_style_to_frontmatter_name(vault, content: str) -> str:
    """If OBSIDIAN_SLUG_STYLE=kebab and content has a frontmatter `name:`
    field, kebab-slugify its value in place (spec section 1, item b).
    No-op if slug_style is off, there's no frontmatter, or no `name` key.
    """
    if vault.slug_style != "kebab" or not content.startswith("---\n"):
        return content
    frontmatter, _ = vault._parse_frontmatter(content)
    name = frontmatter.get("name")
    if not name or not isinstance(name, str):
        return content
    slug = slugify_kebab(name)
    if slug is None:
        raise ValueError(
            f"Frontmatter 'name: {name}' cannot be normalized to kebab-case "
            "(OBSIDIAN_SLUG_STYLE=kebab)."
        )
    if slug == name:
        return content

    end_index = content.find("\n---\n", 4)
    if end_index == -1:
        return content
    fm_text = content[4:end_index]
    new_fm_lines = [
        f"name: {slug}" if re.match(r"^name\s*:", line) else line
        for line in fm_text.split("\n")
    ]
    return f"---\n{chr(10).join(new_fm_lines)}\n---\n{content[end_index + 5 :]}"


def normalize_frontmatter_tags_for_kebab(vault, content: str) -> str:
    """If OBSIDIAN_TAG_STYLE=kebab and content has frontmatter tags,
    kebab-normalize them in place (spec section 1: "nas tags de frontmatter
    em create quando enforcement ativo"). No-op otherwise — this never
    injects a tags block where none existed.
    """
    if vault.tag_style != "kebab" or not content.startswith("---\n"):
        return content
    frontmatter, _ = vault._parse_frontmatter(content)
    raw_tags = frontmatter.get("tags", frontmatter.get("tag"))
    if not raw_tags:
        return content
    if isinstance(raw_tags, str):
        raw_tags = [raw_tags]

    normalized = []
    for tag in raw_tags:
        slug = normalize_tag_kebab(str(tag).lstrip("#").strip())
        if slug is None:
            raise ValueError(
                f"Frontmatter tag '{tag}' cannot be normalized to kebab-case "
                "(OBSIDIAN_TAG_STYLE=kebab). Each '/'-separated segment must contain "
                "at least one letter or digit."
            )
        normalized.append(slug)
    return _update_frontmatter_tags(content, normalized, "replace")


async def _apply_write_checks(
    vault, path: str, content: str, enforce_template: bool
) -> tuple[str, list[str]]:
    """Shared write-time checks for create_note and update_note(replace):
    template conformance (only when enforce_template — create/replace, never
    edit_note_section/append), wikilink validation, kebab tag/name
    normalization, and (OBSIDIAN_REQUIRE_FRONTMATTER) the minimal
    name/description frontmatter contract. Returns (possibly-rewritten
    content, warnings). Raises ValueError for any hard violation (strict
    policy, malformed wikilink, non-normalizable tag/name, missing
    description) — caller writes nothing in that case.
    """
    if enforce_template:
        check_template_conformance(vault, path, content)

    content, warnings = await validate_wikilinks_for_write(vault, content)
    content = normalize_frontmatter_tags_for_kebab(vault, content)
    if vault.require_frontmatter:
        # apply_frontmatter_requirements forces `name` from the (already
        # slug-styled) filename unconditionally, superseding the plain
        # slug-style name pass below — skip it so we don't validate a
        # frontmatter `name` value that's about to be overwritten anyway.
        content = apply_frontmatter_requirements(vault, path, content)
    else:
        content = apply_slug_style_to_frontmatter_name(vault, content)
    return content, warnings


def _size_policy_warning(
    vault, path: str, content: str, is_incremental: bool
) -> list[str]:
    """Run check_note_size_policy and wrap a warn-level result as a
    single-item list (empty if ok/off/daily-exempt/strict-already-raised)."""
    warning = check_note_size_policy(vault, path, count_lines(content), is_incremental)
    return [warning] if warning else []


def _serialize_note_writes(func):
    """Hold the vault-wide write lock for the whole call so concurrent
    read-modify-write operations on the same note cannot lose an update
    (e.g. two edit_note_section calls dispatched together against one note).

    functools.wraps preserves func's __name__/__doc__/__module__ (and sets
    __wrapped__) so tracebacks, logging, and debugging show the real
    decorated function instead of the generic `wrapper` -- not for FastMCP's
    tool schema, which is never derived from this function. FastMCP
    registers the separate `*_tool` wrappers in mcp_*.py, and both
    `inspect.signature` call sites in the repo (test_server_tool_wrappers.py,
    test_image_tool_wrapper_bugs.py) introspect `tool.fn` (that wrapper),
    never a `_serialize_note_writes`-decorated function directly."""

    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        vault = get_vault()
        async with vault.write_lock:
            return await func(*args, **kwargs)

    return wrapper
