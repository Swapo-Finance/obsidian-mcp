"""Note info/metadata lookup for Obsidian MCP server."""

import re

from fastmcp import Context

from ..utils import sanitize_path, validate_note_path
from ..utils.filesystem import get_vault


async def get_note_info(path: str, ctx: Context | None = None) -> dict:
    """
    Get metadata and information about a note without retrieving its full content.

    Use this tool when you need to check a note's metadata, tags, or other
    properties without loading the entire content.

    Args:
        path: Path to the note
        ctx: MCP context for progress reporting

    Returns:
        Dictionary containing note metadata and statistics

    Example:
        >>> await get_note_info("Projects/AI Research.md", ctx=ctx)
        {
            "path": "Projects/AI Research.md",
            "exists": true,
            "metadata": {
                "tags": ["ai", "research", "active"],
                "created": "2024-01-10T10:00:00Z",
                "modified": "2024-01-15T14:30:00Z",
                "aliases": ["AI Study", "ML Research"]
            },
            "stats": {
                "size_bytes": 4523,
                "word_count": 823,
                "link_count": 12
            }
        }
    """
    # Validate path
    is_valid, error_msg = validate_note_path(path)
    if not is_valid:
        raise ValueError(f"Invalid path: {error_msg}")

    path = sanitize_path(path)

    if ctx:
        await ctx.info(f"Getting info for: {path}")

    vault = get_vault()

    try:
        note = await vault.read_note(path)
    except FileNotFoundError:
        # Return standardized CRUD structure for non-existent note
        return {
            "success": False,
            "path": path,
            "operation": "read",
            "details": {"exists": False, "error": "Note not found"},
        }

    # Calculate statistics
    content = note.content
    word_count = len(content.split())

    # Count links (both [[wikilinks]] and [markdown](links))
    wikilink_count = len(re.findall(r"\[\[([^\]]+)\]\]", content))
    markdown_link_count = len(re.findall(r"\[([^\]]+)\]\(([^)]+)\)", content))
    link_count = wikilink_count + markdown_link_count

    # Return standardized CRUD success structure
    return {
        "success": True,
        "path": path,
        "operation": "read",
        "details": {
            "exists": True,
            "metadata": note.metadata.model_dump(exclude_none=True),
            "stats": {
                "size_bytes": len(content.encode("utf-8")),
                "word_count": word_count,
                "link_count": link_count,
            },
        },
    }
