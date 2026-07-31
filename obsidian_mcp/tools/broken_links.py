"""find_broken_links, split out of tools/link_management.py (same precedent
as utils/links.py -- see the comment near the top of link_management.py).

Kept separate from get_backlinks/get_outgoing_links: verified by grep across
tests/ that nothing patches `obsidian_mcp.tools.link_management.get_vault`
while exercising this function, so moving it (and its own get_vault() call)
to its own module is safe. get_backlinks/get_outgoing_links could not make
the same move -- several tests patch
`obsidian_mcp.tools.link_management.get_vault` /
`...get_backlinks` directly, and a function's get_vault() call always
resolves against the globals of the module it is *defined* in, not the one
it's imported into.
"""

from ..utils.filesystem import get_vault
from ..utils.links import extract_links_from_content
from ..utils.validation import validate_directory_path
from .link_index import build_vault_notes_index, find_notes_by_names


async def _resolve_scope(
    vault, single_note: str | None, directory: str | None
) -> tuple[str, list[str], dict[str, list[dict]]]:
    """Resolve the single_note vs directory vs whole-vault tri-state once.

    Returns (label, notes_to_check, all_links_by_note):
    - label: human-readable scope description for the ctx progress message.
    - notes_to_check: note paths in scope (used downstream only for a count).
    - all_links_by_note: each in-scope note's parsed links, keyed by path.

    single_note reads the note directly (matches the pre-existing behavior
    of working even when the path isn't already vault-cache-known, e.g.
    missing a .md suffix that vault.read_note fixes up). Directory/vault-wide
    scans instead pull each note's already-parsed links out of the cache
    (no disk I/O, no re-running the regex).
    """
    if single_note:
        try:
            note = await vault.read_note(single_note)
        except FileNotFoundError:
            raise FileNotFoundError(f"Note not found: {single_note}")
        links = extract_links_from_content(note.content)
        all_links_by_note = {note.path: links} if links else {}
        return f"note: {single_note}", [single_note], all_links_by_note

    # Build index to get all notes
    notes_index = await build_vault_notes_index(vault)
    all_notes = list(set(notes_index.values()))  # Get unique paths

    if directory:
        # Filter to notes nested under this directory. Matching on
        # "directory" alone (without the trailing "/") is redundant
        # with "directory + '/'" (the former is implied by the
        # latter) and also matches sibling folders sharing the
        # prefix, e.g. "ProjectsArchive/note.md" incorrectly
        # matching directory="Projects". validate_directory_path
        # above already guarantees no trailing slash to worry about.
        notes_to_check = [n for n in all_notes if n.startswith(directory + "/")]
        label = f"directory: {directory}"
    else:
        notes_to_check = all_notes
        label = "entire vault"

    all_forward_links = await vault.cache.get_all_forward_links()
    all_links_by_note = {}
    for note_path in notes_to_check:
        links = all_forward_links.get(note_path)
        if links:
            all_links_by_note[note_path] = links

    return label, notes_to_check, all_links_by_note


async def find_broken_links(
    directory: str | None = None, single_note: str | None = None, ctx=None
) -> dict:
    """
    Find all broken links in the vault, a specific directory, or a single note (optimized version).

    This tool identifies links pointing to non-existent notes, helping maintain
    vault integrity. Broken links often occur after renaming or deleting notes.

    Args:
        directory: Specific directory to check (optional, defaults to entire vault)
        single_note: Check only this specific note (optional)
        ctx: MCP context for progress reporting

    Returns:
        Dictionary containing:
        - broken_link_count: Total number of broken links
        - affected_notes: Number of notes containing broken links
        - broken_links: List of broken link details including:
          - source_path: Note containing the broken link
          - broken_link: The path that doesn't exist
          - link_text: The display text of the link
          - link_type: 'wiki' or 'markdown'

    Example:
        {
            "broken_link_count": 3,
            "affected_notes": 2,
            "broken_links": [
                {
                    "source_path": "Daily/2024-01-15.md",
                    "broken_link": "Projects/Old Project.md",
                    "link_text": "Old Project",
                    "link_type": "wiki"
                }
            ]
        }
    """
    # Validate directory parameter (rejects a leading "/", a trailing "/",
    # and ".." — same rule as list_notes/list_folders in search_discovery.py)
    is_valid, error = validate_directory_path(directory)
    if not is_valid:
        raise ValueError(error)

    vault = get_vault()
    label, notes_to_check, all_links_by_note = await _resolve_scope(
        vault, single_note, directory
    )

    if ctx:
        await ctx.info(f"Checking for broken links in {label}")
        await ctx.info(f"Checking {len(notes_to_check)} notes...")

    # Get all unique link paths
    all_link_paths = set()
    for links in all_links_by_note.values():
        for link in links:
            all_link_paths.add(link["path"])

    if ctx:
        await ctx.info(f"Checking validity of {len(all_link_paths)} unique links...")

    # Check which links exist - in one batch!
    found_paths = await find_notes_by_names(vault, list(all_link_paths))

    # Find broken links
    broken_links = []
    affected_notes_set = set()

    for note_path, links in all_links_by_note.items():
        for link in links:
            if not found_paths.get(link["path"]):
                broken_link_info = {
                    "source_path": note_path,
                    "broken_link": link["path"],
                    "link_text": link["display_text"],
                    "link_type": link["type"],
                }
                broken_links.append(broken_link_info)
                affected_notes_set.add(note_path)

    if ctx:
        await ctx.info(
            f"Found {len(broken_links)} broken links in {len(affected_notes_set)} notes"
        )

    # Sort broken links by source path
    broken_links.sort(key=lambda x: x["source_path"])

    # Light enrichment (spec section 10.4's closing sentence): add the
    # linking note's cached name/description to each finding.
    if broken_links:
        all_meta = await vault.cache.get_all_note_meta()
        for broken_link in broken_links:
            meta = all_meta.get(broken_link["source_path"], {})
            broken_link["name"] = meta.get("name", "")
            broken_link["description"] = meta.get("description", "")

    # Return standardized analysis results structure
    return {
        "findings": broken_links,
        "summary": {
            "broken_link_count": len(broken_links),
            "affected_notes": len(affected_notes_set),
            "notes_checked": len(notes_to_check),
        },
        "target": single_note if single_note else directory or "vault",
        "scope": {
            "type": "single_note"
            if single_note
            else "directory"
            if directory
            else "vault",
            "path": single_note if single_note else directory if directory else "/",
        },
    }
