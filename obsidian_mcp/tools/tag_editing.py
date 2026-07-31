"""Tag add/update/remove operations for Obsidian MCP server."""

from fastmcp import Context

from ..constants import ERROR_MESSAGES
from ..models import Note
from ..utils import sanitize_path, validate_note_path
from ..utils.filesystem import get_vault
from ..utils.validation import validate_tags
from ..utils.vault_config import normalize_tag_kebab
from .frontmatter_tags import _update_frontmatter_tags
from .write_policy import _serialize_note_writes


def _clean_tags(vault, tags: list[str]) -> list[str]:
    """Strip the '#' prefix and surrounding whitespace from each tag; when
    OBSIDIAN_TAG_STYLE=kebab, also kebab-normalize each ('/'-hierarchical)
    tag, raising ValueError for one that has nothing alphanumeric left
    (e.g. pure emoji) per spec section 1.
    """
    cleaned = []
    for tag in tags:
        stripped = tag.lstrip("#").strip()
        if not stripped:
            continue
        if vault.tag_style == "kebab":
            slug = normalize_tag_kebab(stripped)
            if slug is None:
                raise ValueError(
                    f"Tag '{tag}' cannot be normalized to kebab-case (OBSIDIAN_TAG_STYLE=kebab). "
                    "Each '/'-separated segment must contain at least one letter or digit."
                )
            cleaned.append(slug)
        else:
            cleaned.append(stripped)
    return cleaned


async def _load_note_for_tag_write(
    vault,
    path: str,
    tags: list[str],
    ctx: Context | None,
    log_action: str,
    log_suffix: str = "",
) -> tuple[str, list[str], Note]:
    """Shared validate-path/validate-tags/clean-tags/read-note prelude for
    add_tags/update_tags/remove_tags. Takes `vault` instead of calling
    get_vault() itself: tests patch get_vault module-qualified as
    obsidian_mcp.tools.tag_editing.get_vault, so a helper that resolved its
    own vault would silently bypass that patch (see tools/CLAUDE.md).

    log_action/log_suffix reproduce each caller's original ctx.info text
    (e.g. update_tags's trailing " (merge=...)") without hardcoding one
    caller's message shape into the shared helper.

    Returns (sanitized path, cleaned tags, the note read from that path).
    """
    is_valid, error_msg = validate_note_path(path)
    if not is_valid:
        raise ValueError(f"Invalid path: {error_msg}")

    path = sanitize_path(path)

    is_valid, error = validate_tags(tags)
    if not is_valid:
        raise ValueError(error)

    tags = _clean_tags(vault, tags)

    if ctx:
        await ctx.info(f"{log_action} {path}: {tags}{log_suffix}")

    try:
        note = await vault.read_note(path)
    except FileNotFoundError:
        raise FileNotFoundError(ERROR_MESSAGES["note_not_found"].format(path=path))

    return path, tags, note


@_serialize_note_writes
async def add_tags(path: str, tags: list[str], ctx: Context | None = None) -> dict:
    """
    Add tags to a note's frontmatter.

    Use this tool to add organizational tags to notes. Tags are added
    to the YAML frontmatter and do not modify the note's content.

    Args:
        path: Path to the note
        tags: List of tags to add (without # prefix)
        ctx: MCP context for progress reporting

    Returns:
        Dictionary containing updated tag list

    Example:
        >>> await add_tags("Projects/AI.md", ["machine-learning", "research"], ctx=ctx)
        {
            "success": True,
            "path": "Projects/AI.md",
            "operation": "added",
            "tags": {
                "before": ["ai", "project"],
                "after": ["ai", "project", "machine-learning", "research"],
                "changes": {
                    "added": ["machine-learning", "research"],
                    "removed": []
                }
            }
        }
    """
    vault = get_vault()
    path, tags, note = await _load_note_for_tag_write(
        vault, path, tags, ctx, "Adding tags to"
    )

    # Parse frontmatter and update tags
    content = note.content
    updated_content = _update_frontmatter_tags(content, tags, "add")

    # Update the note
    await vault.write_note(path, updated_content, overwrite=True)

    # Get updated note to return current tags
    updated_note = await vault.read_note(path)

    # Return standardized tag operation structure
    return {
        "success": True,
        "path": path,
        "operation": "added",
        "tags": {
            "before": note.metadata.tags if note.metadata.tags else [],
            "after": updated_note.metadata.tags,
            "changes": {"added": tags, "removed": []},
        },
    }


@_serialize_note_writes
async def update_tags(
    path: str, tags: list[str], merge: bool = False, ctx: Context | None = None
) -> dict:
    """
    Update tags on a note - either replace all tags or merge with existing.

    Use this tool when you want to set a note's tags based on its content
    or purpose. Perfect for AI-driven tag suggestions after analyzing a note.

    Args:
        path: Path to the note
        tags: New tags to set (without # prefix)
        merge: If True, adds to existing tags. If False, replaces all tags (default: False)
        ctx: MCP context for progress reporting

    Returns:
        Dictionary containing previous and new tag lists

    Example:
        >>> # After analyzing a note about machine learning project
        >>> await update_tags("Projects/ML Research.md", ["ai", "research", "neural-networks"], ctx=ctx)
        {
            "success": True,
            "path": "Projects/ML Research.md",
            "operation": "updated",
            "tags": {
                "before": ["project", "todo"],
                "after": ["ai", "research", "neural-networks"],
                "changes": {
                    "added": ["ai", "research", "neural-networks"],
                    "removed": ["project", "todo"],
                    "merge_mode": False,
                    "operation_type": "replaced"
                }
            }
        }
    """
    vault = get_vault()
    path, tags, note = await _load_note_for_tag_write(
        vault, path, tags, ctx, "Updating tags for", f" (merge={merge})"
    )

    # Store previous tags
    previous_tags = note.metadata.tags.copy() if note.metadata.tags else []

    # Determine final tags based on merge setting
    if merge:
        # Merge with existing tags (like add_tags but more explicit)
        final_tags = list(set(previous_tags + tags))
        operation = "merged"
    else:
        # Replace all tags
        final_tags = tags
        operation = "replaced"

    # Update the note's frontmatter
    content = note.content
    updated_content = _update_frontmatter_tags(content, final_tags, "replace")

    # Update the note
    await vault.write_note(path, updated_content, overwrite=True)

    # Return standardized tag operation structure
    added_tags = list(set(final_tags) - set(previous_tags)) if merge else final_tags
    removed_tags = list(set(previous_tags) - set(final_tags)) if not merge else []

    return {
        "success": True,
        "path": path,
        "operation": "updated",
        "tags": {
            "before": previous_tags,
            "after": final_tags,
            "changes": {
                "added": added_tags,
                "removed": removed_tags,
                "merge_mode": merge,
                "operation_type": operation,
            },
        },
    }


@_serialize_note_writes
async def remove_tags(path: str, tags: list[str], ctx: Context | None = None) -> dict:
    """
    Remove tags from a note's frontmatter.

    Use this tool to remove organizational tags from notes.

    Args:
        path: Path to the note
        tags: List of tags to remove (without # prefix)
        ctx: MCP context for progress reporting

    Returns:
        Dictionary containing updated tag list

    Example:
        >>> await remove_tags("Projects/AI.md", ["outdated"], ctx=ctx)
        {
            "success": True,
            "path": "Projects/AI.md",
            "operation": "removed",
            "tags": {
                "before": ["ai", "project", "machine-learning", "outdated"],
                "after": ["ai", "project", "machine-learning"],
                "changes": {
                    "added": [],
                    "removed": ["outdated"]
                }
            }
        }
    """
    vault = get_vault()
    path, tags, note = await _load_note_for_tag_write(
        vault, path, tags, ctx, "Removing tags from"
    )

    # Parse frontmatter and update tags
    content = note.content
    updated_content = _update_frontmatter_tags(content, tags, "remove")

    # Update the note
    await vault.write_note(path, updated_content, overwrite=True)

    # Get updated note to return current tags
    updated_note = await vault.read_note(path)

    # Return standardized tag operation structure
    return {
        "success": True,
        "path": path,
        "operation": "removed",
        "tags": {
            "before": note.metadata.tags if note.metadata.tags else [],
            "after": updated_note.metadata.tags if updated_note.metadata.tags else [],
            "changes": {"added": [], "removed": tags},
        },
    }
