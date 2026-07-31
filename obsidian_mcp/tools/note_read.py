"""Note reading: read_note."""

from fastmcp import Context

from ..constants import ERROR_MESSAGES
from ..utils import sanitize_path, validate_note_path
from ..utils.filesystem import get_vault


async def read_note(path: str, ctx: Context | None = None) -> dict:
    """
    Read the content and metadata of a specific note.

    Use this tool when you need to retrieve the full content of a note
    from the Obsidian vault. The path should be relative to the vault root.

    To view images embedded in a note, use the view_note_images tool.

    Args:
        path: Path to the note relative to vault root (e.g., "Daily/2024-01-15.md")
        ctx: MCP context for progress reporting

    Returns:
        Dictionary containing the note content and metadata

    Example:
        >>> await read_note("Projects/My Project.md", ctx=ctx)
        {
            "path": "Projects/My Project.md",
            "content": "# My Project\n\n![diagram](attachments/diagram.png)\n\nProject details...",
            "metadata": {
                "tags": ["project", "active"],
                "created": "2024-01-15T10:00:00Z",
                "modified": "2024-01-15T14:30:00Z"
            }
        }
    """
    # Validate path
    is_valid, error_msg = validate_note_path(path)
    if not is_valid:
        raise ValueError(f"Invalid path: {error_msg}")

    # Sanitize path
    path = sanitize_path(path)

    if ctx:
        await ctx.info(f"Reading note: {path}")

    vault = get_vault()
    try:
        note = await vault.read_note(path)
    except FileNotFoundError:
        raise FileNotFoundError(ERROR_MESSAGES["note_not_found"].format(path=path))

    # Return standardized CRUD success structure
    return {
        "success": True,
        "path": note.path,
        "operation": "read",
        "details": {
            "content": note.content,
            "metadata": note.metadata.model_dump(exclude_none=True),
        },
    }
