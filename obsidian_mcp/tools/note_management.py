"""Note management tools for Obsidian MCP server.

Owns create_note, update_note, and delete_note. read_note moved to
note_read.py, edit_note_section to section_editing.py, and the write-policy
chain (template/frontmatter/slug checks + the write-serialization lock) to
write_policy.py -- all six re-exported below so existing imports keep
resolving unchanged (e.g. daily_notes.py's own
`from .note_management import _serialize_note_writes`). See tools/CLAUDE.md.
"""

from typing import Any

from fastmcp import Context

from ..constants import ERROR_MESSAGES
from ..utils import sanitize_path, validate_note_path
from ..utils.filesystem import get_vault
from ..utils.validation import validate_content
from .link_management import validate_wikilinks_for_write
from .note_read import read_note
from .section_editing import edit_note_section
from .write_policy import (
    _apply_write_checks,
    _serialize_note_writes,
    _size_policy_warning,
    apply_slug_style_to_frontmatter_name,
    apply_slug_style_to_path,
    normalize_frontmatter_tags_for_kebab,
)

__all__ = [
    "_apply_write_checks",
    "_serialize_note_writes",
    "_size_policy_warning",
    "apply_slug_style_to_frontmatter_name",
    "apply_slug_style_to_path",
    "create_note",
    "delete_note",
    "edit_note_section",
    "normalize_frontmatter_tags_for_kebab",
    "read_note",
    "update_note",
]


@_serialize_note_writes
async def create_note(
    path: str, content: str, overwrite: bool = False, ctx: Context | None = None
) -> dict:
    """
    Create a new note or update an existing one.

    Use this tool to create new notes in the Obsidian vault. By default,
    it will fail if a note already exists at the specified path unless
    overwrite is set to true.

    Args:
        path: Path where the note should be created (e.g., "Ideas/New Idea.md")
        content: Markdown content for the note
        overwrite: Whether to overwrite if the note already exists (default: false)
        ctx: MCP context for progress reporting

    Returns:
        Dictionary containing the created note information

    Example:
        >>> await create_note(
        ...     "Ideas/AI Integration.md",
        ...     "# AI Integration Ideas\n\n- Use LLMs for note summarization\n- Auto-tagging",
        ...     ctx=ctx
        ... )
        {
            "path": "Ideas/AI Integration.md",
            "created": true,
            "metadata": {"tags": [], "created": "2024-01-15T15:00:00Z"}
        }
    """
    # Validate path
    is_valid, error_msg = validate_note_path(path)
    if not is_valid:
        raise ValueError(f"Invalid path: {error_msg}")

    # Validate content
    is_valid, error_msg = validate_content(content)
    if not is_valid:
        raise ValueError(error_msg)

    # Sanitize path
    path = sanitize_path(path)

    vault = get_vault()
    path = apply_slug_style_to_path(vault, path)  # OBSIDIAN_SLUG_STYLE=kebab

    if ctx:
        await ctx.info(f"Creating note: {path}")

    # Template conformance (folder rule, if any) + wikilink validation +
    # kebab tag/name normalization. Raises ValueError before anything is
    # written if a hard check fails (strict template/wikilink violation,
    # non-normalizable tag/name).
    content, wikilink_warnings = await _apply_write_checks(
        vault, path, content, enforce_template=True
    )
    warnings = wikilink_warnings + _size_policy_warning(
        vault, path, content, is_incremental=False
    )

    # Create the note
    try:
        note = await vault.write_note(path, content, overwrite=overwrite)
        created = True
    except FileExistsError:
        if not overwrite:
            raise FileExistsError(
                ERROR_MESSAGES["overwrite_protection"].format(path=path)
            )
        # If we get here, overwrite is True but file exists - this shouldn't happen
        # with our write_note implementation, but handle it just in case
        note = await vault.write_note(path, content, overwrite=True)
        created = False

    # Return standardized CRUD success structure
    result: dict[str, Any] = {
        "success": True,
        "path": note.path,
        "operation": "created" if created else "overwritten",
        "details": {
            "created": created,
            "overwritten": not created,
            "metadata": note.metadata.model_dump(exclude_none=True),
        },
    }
    if warnings:
        result["warnings"] = warnings
    return result


@_serialize_note_writes
async def update_note(
    path: str,
    content: str,
    create_if_not_exists: bool = False,
    merge_strategy: str = "replace",
    ctx: Context | None = None,
) -> dict:
    """
    Update the content of an existing note.

    Use this tool to modify the content of an existing note while preserving
    its metadata and location. Optionally create the note if it doesn't exist.

    IMPORTANT: This tool REPLACES the entire note content by default. Always
    read the note first with read_note_tool if you want to preserve existing content.

    Args:
        path: Path to the note to update
        content: New markdown content for the note (REPLACES existing content)
        create_if_not_exists: Create the note if it doesn't exist (default: false)
        merge_strategy: How to handle updates - "replace" (default) or "append"
        ctx: MCP context for progress reporting

    Returns:
        Dictionary containing update status

    Example:
        >>> await update_note(
        ...     "Projects/My Project.md",
        ...     "# My Project\\n\\n## Updated Status\\nProject is now complete!",
        ...     ctx=ctx
        ... )
        {
            "path": "Projects/My Project.md",
            "updated": true,
            "created": false,
            "metadata": {"tags": ["project", "completed"], "modified": "2024-01-15T16:00:00Z"}
        }
    """
    # Validate path
    is_valid, error_msg = validate_note_path(path)
    if not is_valid:
        raise ValueError(f"Invalid path: {error_msg}")

    # Sanitize path
    path = sanitize_path(path)

    if ctx:
        await ctx.info(f"Updating note: {path}")

    vault = get_vault()

    # Try to read existing note
    try:
        existing_note = await vault.read_note(path)
    except FileNotFoundError:
        existing_note = None

    if existing_note is None:
        if create_if_not_exists:
            # A first-time write is a full-content write, same as
            # create_note: template conformance + wikilink validation +
            # kebab tag/name normalization all apply.
            content, wikilink_warnings = await _apply_write_checks(
                vault, path, content, enforce_template=True
            )
            warnings = wikilink_warnings + _size_policy_warning(
                vault, path, content, is_incremental=False
            )

            note = await vault.write_note(path, content, overwrite=False)
            # Return standardized CRUD success structure
            result: dict[str, Any] = {
                "success": True,
                "path": note.path,
                "operation": "created",
                "details": {
                    "updated": False,
                    "created": True,
                    "metadata": note.metadata.model_dump(exclude_none=True),
                },
            }
            if warnings:
                result["warnings"] = warnings
            return result
        else:
            raise FileNotFoundError(ERROR_MESSAGES["note_not_found"].format(path=path))

    # Handle merge strategies
    if merge_strategy == "append":
        # Incremental edit — exempt from template conformance (spec section
        # 3). Wikilink validation and the size check run against just the
        # appended fragment / resulting total, matching edit_note_section.
        content, wikilink_warnings = await validate_wikilinks_for_write(vault, content)
        final_content = existing_note.content.rstrip() + "\n\n" + content
        warnings = wikilink_warnings + _size_policy_warning(
            vault, path, final_content, is_incremental=True
        )
    elif merge_strategy == "replace":
        content, wikilink_warnings = await _apply_write_checks(
            vault, path, content, enforce_template=True
        )
        final_content = content
        warnings = wikilink_warnings + _size_policy_warning(
            vault, path, final_content, is_incremental=False
        )
    else:
        raise ValueError(
            f"Invalid merge_strategy: {merge_strategy}. Must be 'replace' or 'append'"
        )

    # Update existing note
    note = await vault.write_note(path, final_content, overwrite=True)

    # Return standardized CRUD success structure
    result: dict[str, Any] = {
        "success": True,
        "path": note.path,
        "operation": "updated",
        "details": {
            "updated": True,
            "created": False,
            "merge_strategy": merge_strategy,
            "metadata": note.metadata.model_dump(exclude_none=True),
        },
    }
    if warnings:
        result["warnings"] = warnings
    return result


@_serialize_note_writes
async def delete_note(path: str, ctx: Context | None = None) -> dict:
    """
    Delete a note from the vault.

    Use this tool to permanently remove a note from the Obsidian vault.
    This action cannot be undone.

    Args:
        path: Path to the note to delete
        ctx: MCP context for progress reporting

    Returns:
        Dictionary containing deletion status

    Example:
        >>> await delete_note("Temporary/Draft.md", ctx)
        {"path": "Temporary/Draft.md", "deleted": true}
    """
    # Validate path
    is_valid, error_msg = validate_note_path(path)
    if not is_valid:
        raise ValueError(f"Invalid path: {error_msg}")

    # Sanitize path
    path = sanitize_path(path)

    if ctx:
        await ctx.info(f"Deleting note: {path}")

    vault = get_vault()

    try:
        await vault.delete_note(path)
    except FileNotFoundError:
        raise FileNotFoundError(ERROR_MESSAGES["note_not_found"].format(path=path))

    # Return standardized CRUD success structure
    return {
        "success": True,
        "path": path,
        "operation": "deleted",
        "details": {"deleted": True},
    }
