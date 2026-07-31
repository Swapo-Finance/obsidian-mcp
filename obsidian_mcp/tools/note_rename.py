"""Note rename operations for Obsidian MCP server."""

from fastmcp import Context

from ..constants import ERROR_MESSAGES
from ..utils import sanitize_path, validate_note_path
from ..utils.filesystem import get_vault
from .backlink_rewrite import rewrite_backlinks_to_note
from .write_policy import _serialize_note_writes


@_serialize_note_writes
async def rename_note(
    old_path: str, new_path: str, update_links: bool = True, ctx: Context | None = None
) -> dict:
    """
    Rename a note and optionally update all references to it.

    Use this tool to change a note's filename while automatically updating
    all wiki-style links ([[note name]]) that reference it throughout your
    vault. This maintains link integrity when reorganizing notes.

    Args:
        old_path: Current path of the note
        new_path: New path for the note (must be in same directory)
        update_links: Whether to update links in other notes (default: true)
        ctx: MCP context for progress reporting

    Returns:
        Dictionary containing rename status and updated links information

    Example:
        >>> await rename_note("Projects/AI Research.md", "Projects/Machine Learning Study.md", ctx=ctx)
        {
            "success": true,
            "old_path": "Projects/AI Research.md",
            "new_path": "Projects/Machine Learning Study.md",
            "operation": "renamed",
            "details": {
                "links_updated": 12,
                "notes_updated": 8,
                "link_update_details": [
                    {
                        "note": "Daily/2024-01-15.md",
                        "updates": 2
                    }
                ]
            }
        }
    """
    # Import link management functions
    from ..tools.link_management import get_backlinks

    # Validate paths
    for path, name in [(old_path, "old"), (new_path, "new")]:
        is_valid, error_msg = validate_note_path(path)
        if not is_valid:
            raise ValueError(f"Invalid {name} path: {error_msg}")

    # Sanitize paths
    old_path = sanitize_path(old_path)
    new_path = sanitize_path(new_path)

    if old_path == new_path:
        raise ValueError("Old and new paths are the same")

    vault = get_vault()

    # Check if source exists
    try:
        source_note = await vault.read_note(old_path)
    except FileNotFoundError:
        # If exact path not found, try to find the note by filename
        if ctx:
            await ctx.info(f"Note not found at {old_path}, searching for filename...")

        # Import search function
        from ..tools.search_discovery import search_notes

        # Search for notes with this filename
        old_filename = old_path.split("/")[-1]
        search_result = await search_notes(
            f"path:{old_filename}", max_results=10, ctx=None
        )

        if search_result["count"] == 0:
            raise FileNotFoundError(
                ERROR_MESSAGES["note_not_found"].format(path=old_path)
            )
        elif search_result["count"] == 1:
            # Exactly one match - use it
            found_path = search_result["results"][0]["path"]
            if ctx:
                await ctx.info(f"Found unique match at: {found_path}")

            # Update old_path to the found path
            old_path = found_path

            # Also update new_path to use the same directory
            found_dir = (
                "/".join(found_path.split("/")[:-1]) if "/" in found_path else ""
            )
            new_filename = new_path.split("/")[-1]
            new_path = f"{found_dir}/{new_filename}" if found_dir else new_filename

            # Now try to read the note again
            source_note = await vault.read_note(old_path)
        else:
            # Multiple matches - show them to the user
            matches = [result["path"] for result in search_result["results"]]
            matches_str = "\n  - ".join(matches)
            raise ValueError(
                f"Multiple notes found with name '{old_filename}'. Please specify the full path:\n  - {matches_str}"
            )

    # Now that we have the actual paths, extract directory and filename
    old_dir = "/".join(old_path.split("/")[:-1]) if "/" in old_path else ""
    new_dir = "/".join(new_path.split("/")[:-1]) if "/" in new_path else ""

    if old_dir != new_dir:
        raise ValueError(
            "Rename can only change the filename, not the directory. Use move_note to change directories."
        )

    # Extract filenames with and without .md extension
    old_filename = old_path.split("/")[-1]
    new_filename = new_path.split("/")[-1]
    old_name = old_filename.removesuffix(".md")
    new_name = new_filename.removesuffix(".md")

    if ctx:
        await ctx.info(f"Renaming note from {old_path} to {new_path}")

    # Check if destination already exists
    try:
        await vault.read_note(new_path)
        raise FileExistsError(f"Note already exists at destination: {new_path}")
    except FileNotFoundError:
        # Good, destination doesn't exist
        pass

    # If update_links is True, find and update all backlinks before renaming
    links_updated = 0
    notes_updated = 0
    link_update_details = []

    if update_links:
        if ctx:
            await ctx.info(f"Finding all notes that link to {old_name}")

        # Get all backlinks to the old note
        backlinks_result = await get_backlinks(
            old_path, include_context=False, ctx=None
        )
        backlinks = backlinks_result["findings"]

        if ctx:
            await ctx.info(f"Found {len(backlinks)} backlinks to update")

        (
            links_updated,
            notes_updated,
            link_update_details,
        ) = await rewrite_backlinks_to_note(
            vault,
            backlinks,
            old_name,
            new_name,
            old_filename,
            new_filename,
            ctx=ctx,
        )

        # Re-read source_note: the loop above may have just rewritten
        # old_path in place (a self-referencing wikilink is never excluded
        # from the backlink set), and writing the stale pre-loop snapshot
        # here would silently discard that fix. Only needed when the loop
        # actually ran -- otherwise old_path was never touched.
        source_note = await vault.read_note(old_path)

    # Now rename the note itself
    await vault.write_note(new_path, source_note.content, overwrite=False)
    await vault.delete_note(old_path)

    if ctx:
        await ctx.info(f"Successfully renamed note and updated {links_updated} links")

    # Return standardized CRUD success structure
    return {
        "success": True,
        "old_path": old_path,
        "new_path": new_path,
        "operation": "renamed",
        "details": {
            "links_updated": links_updated,
            "notes_updated": notes_updated,
            "link_update_details": link_update_details[
                :10
            ],  # Limit details to first 10 notes
        },
    }
