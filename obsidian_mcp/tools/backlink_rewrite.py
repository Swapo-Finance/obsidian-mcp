"""Shared backlink-rewrite helper for move_note and rename_note.

Extracted from tools/note_move.py and tools/note_rename.py, which each
carried a ~90-130 line block that grouped backlinks by source note, built 6
wikilink regex substitution pairs (plain / `.md`-suffixed / filename, each
with and without an alias), applied them, and wrote back any note that
changed -- identical between the two files except for variable names
(source_name/dest_name vs old_name/new_name).

Takes `vault` as an explicit parameter and never calls get_vault() itself.
move_note/rename_note stay physically defined in their own modules -- a
function's get_vault() call resolves against the globals of the module it is
*defined* in, and tests patch `obsidian_mcp.tools.note_move.get_vault` /
`obsidian_mcp.tools.note_rename.get_vault` directly -- so this module must
not introduce a second, unpatched get_vault() binding; callers pass their
own vault instance in instead.
"""

import re

from fastmcp import Context


def _wikilink_patterns(
    old_name: str, new_name: str, old_filename: str, new_filename: str
) -> list[tuple[re.Pattern, str]]:
    """Build the 6 regex substitution pairs for rewriting wiki-links to a
    renamed/moved note: plain name, `.md`-suffixed, and bare filename, each
    with and without a `|alias` (alias display text is preserved via \\1)."""
    return [
        (re.compile(rf"\[\[{re.escape(old_name)}\]\]"), f"[[{new_name}]]"),
        (
            re.compile(rf"\[\[{re.escape(old_name)}\.md\]\]"),
            f"[[{new_name}.md]]",
        ),
        (
            re.compile(rf"\[\[{re.escape(old_filename)}\]\]"),
            f"[[{new_filename}]]",
        ),
        # With aliases
        (
            re.compile(rf"\[\[{re.escape(old_name)}\|([^\]]+)\]\]"),
            rf"[[{new_name}|\1]]",
        ),
        (
            re.compile(rf"\[\[{re.escape(old_name)}\.md\|([^\]]+)\]\]"),
            rf"[[{new_name}.md|\1]]",
        ),
        (
            re.compile(rf"\[\[{re.escape(old_filename)}\|([^\]]+)\]\]"),
            rf"[[{new_filename}|\1]]",
        ),
    ]


async def rewrite_backlinks_to_note(
    vault,
    backlinks: list[dict],
    old_name: str,
    new_name: str,
    old_filename: str,
    new_filename: str,
    ctx: Context | None = None,
) -> tuple[int, int, list[dict]]:
    """Rewrite every wiki-link to a renamed/moved note across the notes that
    reference it.

    `backlinks` is the `findings` list from get_backlinks() -- grouped here
    by source note (a note can appear multiple times, once per link) so each
    affected note is read and written at most once. For each source note,
    all 6 wikilink patterns are applied and the note is written back only if
    something actually changed.

    Note: this does not exclude the target note itself from `backlinks`. If
    the renamed/moved note links to itself, that self-link is rewritten here
    too, in place on disk -- callers whose subsequent logic reads the note's
    content again (e.g. to move it to a new path) must re-read after calling
    this, not reuse a pre-call snapshot.

    Returns (links_updated, notes_updated, link_update_details) -- the same
    triple move_note/rename_note report back to their own callers.
    """
    updates_by_note: dict[str, list[dict]] = {}
    for backlink in backlinks:
        updates_by_note.setdefault(backlink["source_path"], []).append(backlink)

    patterns_to_replace = _wikilink_patterns(
        old_name, new_name, old_filename, new_filename
    )

    links_updated = 0
    notes_updated = 0
    link_update_details: list[dict] = []

    for note_path in updates_by_note:
        try:
            note = await vault.read_note(note_path)
            content = note.content
            original_content = content
            updates_in_note = 0

            for pattern, replacement in patterns_to_replace:
                content, count = pattern.subn(replacement, content)
                updates_in_note += count

            if content != original_content:
                await vault.write_note(note_path, content, overwrite=True)
                links_updated += updates_in_note
                notes_updated += 1
                link_update_details.append(
                    {"note": note_path, "updates": updates_in_note}
                )

                if ctx:
                    await ctx.info(f"Updated {updates_in_note} links in {note_path}")

        except Exception as e:
            if ctx:
                await ctx.info(f"Error updating links in {note_path}: {e!s}")

    return links_updated, notes_updated, link_update_details
