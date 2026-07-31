"""Link management tools for Obsidian MCP server.

get_backlinks and get_outgoing_links stay in this file. Both call
get_vault() internally, and several tests patch
`obsidian_mcp.tools.link_management.get_vault` /
`obsidian_mcp.tools.link_management.get_backlinks` directly (see
test_auto_search.py, test_move_note_enhanced.py, test_rename_note.py). A
function's get_vault() call always resolves against the globals of the
module it is *defined* in, not the module it happens to be imported into
-- moving either function out (even with a facade re-export of the name)
would make those patches silently no-ops, and the tests would start hitting
a real vault while still appearing to pass.

The rest moved to flat sibling modules (same precedent as utils/links.py):
link_index.py (build_vault_notes_index, find_notes_by_names,
check_links_validity_batch, get_link_context -- no get_vault() call, not
patched by module-qualified name); broken_links.py (find_broken_links --
calls get_vault(), but verified via grep that nothing patches this module's
get_vault while exercising it); wikilink_validation.py (the write-time
validator and its regexes -- takes `vault` as a parameter, never patched);
backlink_scan.py (_target_name_variants, _check_note_for_backlinks -- pure
matching/scanning helpers used only by get_backlinks, neither calls
get_vault()).

This module remains the public facade for every name in __all__ below, for
existing external imports -- note_management.py and daily_notes.py import
validate_wikilinks_for_write from here, tools/__init__.py imports
find_broken_links from here.
"""

import asyncio

from ..constants import ERROR_MESSAGES
from ..utils.filesystem import get_vault

# Re-exported from utils/links.py (moved there so utils/vault_cache.py can
# reuse the same extraction without utils/ importing from tools/). The one
# pre-existing external import — `from ..tools.link_management import
# get_backlinks, WIKI_LINK_PATTERN` in organization.py — keeps working
# unchanged.
from ..utils.links import (
    MARKDOWN_LINK_PATTERN,
    WIKI_LINK_PATTERN,
    extract_links_from_content,
)
from ..utils.validation import validate_note_path
from .backlink_scan import _check_note_for_backlinks, _target_name_variants
from .broken_links import find_broken_links
from .link_index import (
    build_vault_notes_index,
    check_links_validity_batch,
    find_notes_by_names,
    get_link_context,
)
from .wikilink_validation import (
    _FENCED_CODE_RE,
    _INLINE_CODE_RE,
    _MARKDOWN_EMBED_RE,
    _VALIDATION_WIKI_LINK_RE,
    _WIKI_EMBED_RE,
    _mask_ineligible_regions,
    _suggest_similar_notes,
    validate_wikilinks_for_write,
)

__all__ = [
    "MARKDOWN_LINK_PATTERN",
    "WIKI_LINK_PATTERN",
    "_FENCED_CODE_RE",
    "_INLINE_CODE_RE",
    "_MARKDOWN_EMBED_RE",
    "_VALIDATION_WIKI_LINK_RE",
    "_WIKI_EMBED_RE",
    "_mask_ineligible_regions",
    "_suggest_similar_notes",
    "build_vault_notes_index",
    "check_links_validity_batch",
    "find_broken_links",
    "find_notes_by_names",
    "get_backlinks",
    "get_link_context",
    "get_outgoing_links",
    "validate_wikilinks_for_write",
]


async def get_backlinks(
    path: str, include_context: bool = True, context_length: int = 100, ctx=None
) -> dict:
    """
    Get all notes that link to the specified note (optimized version).

    This tool finds all backlinks (incoming links) to a specific note,
    helping understand how notes are connected and referenced.

    Args:
        path: Path to the target note
        include_context: Whether to include surrounding text context
        context_length: Characters of context to include (default 100)
        ctx: MCP context for progress reporting

    Returns:
        Dictionary containing:
        - target_note: The note being linked to
        - backlink_count: Number of backlinks found
        - backlinks: List of backlink information including:
          - source_path: Note containing the link
          - link_text: The display text of the link
          - link_type: 'wiki' or 'markdown'
          - context: Surrounding text (if requested)

    Example:
        {
            "target_note": "Projects/My Project.md",
            "backlink_count": 3,
            "backlinks": [
                {
                    "source_path": "Daily/2024-01-15.md",
                    "link_text": "My Project",
                    "link_type": "wiki",
                    "context": "...working on [[My Project]] today..."
                }
            ]
        }
    """
    # Validate the note path
    is_valid, error = validate_note_path(path)
    if not is_valid:
        raise ValueError(error)

    if ctx:
        await ctx.info(f"Finding backlinks to: {path}")

    vault = get_vault()

    # Verify the target note exists
    try:
        note = await vault.read_note(path)
    except FileNotFoundError:
        raise FileNotFoundError(ERROR_MESSAGES["note_not_found"].format(path=path))

    target_names = _target_name_variants(path)

    # Narrow the scan to notes whose extracted (already-parsed, in-memory —
    # no disk I/O) links plausibly resolve to our target, instead of
    # re-reading and regex-scanning every note in the vault. The actual
    # match/context extraction below is untouched: same regex, same note
    # content, same output shape — just run over a smaller candidate set.
    all_forward_links = await vault.cache.get_all_forward_links()
    candidate_note_paths = [
        source_path
        for source_path, links in all_forward_links.items()
        if source_path != note.path
        and any(link["path"] in target_names for link in links)
    ]

    if ctx:
        await ctx.info(f"Will match against variations: {target_names}")
        await ctx.info(
            f"Scanning {len(candidate_note_paths)} candidate notes (of {len(all_forward_links)} total)..."
        )

    # Process notes in parallel batches
    backlinks = []
    batch_size = 10  # Process 10 notes at a time

    for i in range(0, len(candidate_note_paths), batch_size):
        batch = candidate_note_paths[i : i + batch_size]
        batch_results = await asyncio.gather(
            *[
                _check_note_for_backlinks(
                    vault,
                    note_path,
                    path,
                    target_names,
                    include_context,
                    context_length,
                )
                for note_path in batch
            ]
        )

        for note_backlinks in batch_results:
            backlinks.extend(note_backlinks)

    if ctx:
        await ctx.info(f"Found {len(backlinks)} backlinks")

    # Light enrichment (spec section 10.4's closing sentence): add the
    # linking note's cached name/description to each finding, from the
    # VaultCache — no extra disk reads.
    if backlinks:
        all_meta = await vault.cache.get_all_note_meta()
        for backlink in backlinks:
            meta = all_meta.get(backlink["source_path"], {})
            backlink["name"] = meta.get("name", "")
            backlink["description"] = meta.get("description", "")

    # Return standardized analysis results structure
    return {
        "findings": backlinks,
        "summary": {
            "backlink_count": len(backlinks),
            "sources": len(
                {bl["source_path"] for bl in backlinks}
            ),  # Unique source notes
        },
        "target": path,
        "scope": {"include_context": include_context, "context_length": context_length},
    }


async def get_outgoing_links(path: str, check_validity: bool = False, ctx=None) -> dict:
    """
    Get all links from a specific note (optimized version).

    This tool extracts all outgoing links from a note, helping understand
    what other notes and resources it references.

    Args:
        path: Path to the source note
        check_validity: Whether to check if linked notes exist
        ctx: MCP context for progress reporting

    Returns:
        Dictionary containing:
        - source_note: The note containing the links
        - link_count: Number of links found
        - links: List of link information including:
          - path: The linked note path
          - display_text: The display text of the link
          - type: 'wiki' or 'markdown'
          - exists: Whether the linked note exists (if check_validity=True)
          - actual_path: The actual path if different from link path

    Example:
        {
            "source_note": "Daily/2024-01-15.md",
            "link_count": 5,
            "links": [
                {
                    "path": "Projects/My Project.md",
                    "display_text": "My Project",
                    "type": "wiki",
                    "exists": true
                }
            ]
        }
    """
    # Validate the note path
    is_valid, error = validate_note_path(path)
    if not is_valid:
        raise ValueError(error)

    if ctx:
        await ctx.info(f"Extracting links from: {path}")

    vault = get_vault()

    # Read the note content
    try:
        note = await vault.read_note(path)
    except FileNotFoundError:
        raise FileNotFoundError(ERROR_MESSAGES["note_not_found"].format(path=path))

    content = note.content

    # Extract all links
    links = extract_links_from_content(content)

    # Check validity if requested - in batch!
    if check_validity:
        if ctx:
            await ctx.info(f"Checking validity of {len(links)} links...")
        links = await check_links_validity_batch(vault, links)

    if ctx:
        await ctx.info(f"Found {len(links)} outgoing links")

    # Return standardized analysis results structure
    return {
        "findings": links,
        "summary": {
            "link_count": len(links),
            "checked_validity": check_validity,
            "broken_count": len(
                [l for l in links if check_validity and not l.get("exists", True)]
            ),
        },
        "target": path,
        "scope": {"check_validity": check_validity},
    }
