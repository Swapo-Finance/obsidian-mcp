"""Note move operations for Obsidian MCP server."""

from fastmcp import Context

from ..constants import ERROR_MESSAGES
from ..utils import sanitize_path, validate_note_path
from ..utils.filesystem import get_vault
from .backlink_rewrite import rewrite_backlinks_to_note
from .write_policy import _serialize_note_writes


@_serialize_note_writes
async def move_note(
    source_path: str,
    destination_path: str,
    update_links: bool = True,
    ctx: Context | None = None,
) -> dict:
    """
    Move a note to a new location, optionally with a new name.

    Use this tool to reorganize your vault by moving notes to different
    folders. If the filename changes during the move, all wiki-style links
    will be automatically updated throughout your vault.

    Args:
        source_path: Current path of the note
        destination_path: New path for the note (can include a new filename)
        update_links: Whether to update links when filename changes (default: true)
        ctx: MCP context for progress reporting

    Returns:
        Dictionary containing move status and link update information

    Examples:
        >>> # Move without renaming (no link updates needed)
        >>> await move_note("Inbox/Note.md", "Projects/Note.md")
        {
            "source": "Inbox/Note.md",
            "destination": "Projects/Note.md",
            "moved": true,
            "renamed": false,
            "links_updated": 0
        }

        >>> # Move with renaming (links will be updated)
        >>> await move_note("Inbox/Quick Note.md", "Projects/Project Plan.md")
        {
            "source": "Inbox/Quick Note.md",
            "destination": "Projects/Project Plan.md",
            "moved": true,
            "renamed": true,
            "links_updated": 5,
            "notes_updated": 3
        }
    """
    # Validate paths
    for path, name in [(source_path, "source"), (destination_path, "destination")]:
        is_valid, error_msg = validate_note_path(path)
        if not is_valid:
            raise ValueError(f"Invalid {name} path: {error_msg}")

    # Import link management functions
    from ..tools.link_management import get_backlinks

    # Sanitize paths
    source_path = sanitize_path(source_path)
    destination_path = sanitize_path(destination_path)

    if source_path == destination_path:
        raise ValueError("Source and destination paths are the same")

    vault = get_vault()

    # Check if source exists
    try:
        source_note = await vault.read_note(source_path)
    except FileNotFoundError:
        # If exact path not found, try to find the note by filename
        if ctx:
            await ctx.info(
                f"Note not found at {source_path}, searching for filename..."
            )

        # Import search function
        from ..tools.search_discovery import search_notes

        # Search for notes with this filename
        source_filename = source_path.split("/")[-1]
        search_result = await search_notes(
            f"path:{source_filename}", max_results=10, ctx=None
        )

        if search_result["count"] == 0:
            raise FileNotFoundError(
                ERROR_MESSAGES["note_not_found"].format(path=source_path)
            )
        elif search_result["count"] == 1:
            # Exactly one match - use it
            found_path = search_result["results"][0]["path"]
            if ctx:
                await ctx.info(f"Found unique match at: {found_path}")

            # Update source_path to the found path
            source_path = found_path

            # Now try to read the note again
            source_note = await vault.read_note(source_path)
        else:
            # Multiple matches - show them to the user
            matches = [result["path"] for result in search_result["results"]]
            matches_str = "\n  - ".join(matches)
            raise ValueError(
                f"Multiple notes found with name '{source_filename}'. Please specify the full path:\n  - {matches_str}"
            )

    # Extract filenames to check if name is changing
    source_filename = source_path.split("/")[-1]
    dest_filename = destination_path.split("/")[-1]
    source_name = source_filename.removesuffix(".md")
    dest_name = dest_filename.removesuffix(".md")

    name_changed = source_name != dest_name

    if ctx:
        await ctx.info(f"Moving note from {source_path} to {destination_path}")
        if name_changed:
            await ctx.info(
                f"Filename is changing from '{source_filename}' to '{dest_filename}'"
            )

    # Check if destination already exists
    try:
        await vault.read_note(destination_path)
        raise FileExistsError(f"Note already exists at destination: {destination_path}")
    except FileNotFoundError:
        # Good, destination doesn't exist
        pass

    # If name is changing and update_links is True, update all backlinks before moving
    links_updated = 0
    notes_updated = 0
    link_update_details = []

    if name_changed and update_links:
        if ctx:
            await ctx.info(
                f"Filename changed - updating all links from '{source_name}' to '{dest_name}'"
            )

        # Get all backlinks to the old note
        backlinks_result = await get_backlinks(
            source_path, include_context=False, ctx=None
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
            source_name,
            dest_name,
            source_filename,
            dest_filename,
            ctx=ctx,
        )

        # Re-read source_note: the loop above may have just rewritten
        # source_path in place (a self-referencing wikilink is never
        # excluded from the backlink set), and writing the stale pre-loop
        # snapshot here would silently discard that fix. Only needed when
        # the loop actually ran -- otherwise source_path was never touched.
        source_note = await vault.read_note(source_path)

    # Create note at new location
    await vault.write_note(destination_path, source_note.content, overwrite=False)

    # Delete original note
    await vault.delete_note(source_path)

    if ctx:
        if name_changed and update_links:
            await ctx.info(
                f"Successfully moved and renamed note, updated {links_updated} links"
            )
        else:
            await ctx.info("Successfully moved note")

    # Return standardized move operation structure
    return {
        "success": True,
        "source": source_path,
        "destination": destination_path,
        "type": "note",
        "renamed": name_changed,
        "details": {
            "items_moved": 1,
            "links_updated": links_updated,
            "notes_updated": notes_updated,
            "link_update_details": link_update_details[:10] if name_changed else [],
        },
    }
