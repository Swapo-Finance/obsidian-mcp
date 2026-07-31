"""Tag management tool wrappers: add, update, remove, and list tags."""

from typing import Annotated, Literal

from fastmcp import Context
from fastmcp.exceptions import ToolError
from pydantic import Field

from .app import mcp
from .tools import (
    add_tags,
    list_tags,
    remove_tags,
    update_tags,
)


@mcp.tool()
async def add_tags_tool(
    path: Annotated[
        str,
        Field(
            description="Path to the note",
            pattern=r"^[^/].*\.md$",
            min_length=1,
            max_length=255,
        ),
    ],
    tags: Annotated[
        list[str],
        Field(
            description="List of tags to add to the note. Don't include the # symbol - it will be added automatically. Supports hierarchical tags with forward slashes.",
            min_length=1,
            max_length=50,
            examples=[
                ["project", "urgent"],
                ["project/web", "project/mobile"],
                ["work/meetings/standup", "work/meetings/planning"],
            ],
        ),
    ],
    ctx: Context | None = None,
):
    """
    Add tags to a note's frontmatter.

    When to use:
    - Organizing notes with tags
    - Creating hierarchical tag structures (e.g., project/web, work/meetings/standup)
    - Bulk tagging operations
    - Adding metadata for search

    Tag format:
    - Simple tags: "project", "urgent"
    - Hierarchical tags: "project/web", "work/meetings/standup"
    - Tags are automatically added without duplicates

    When NOT to use:
    - Adding tags in note content (use update_note)
    - Replacing all tags (use update_tags with merge=False)

    Returns:
        {success, path, operation: "added", tags: {before, after, changes}} —
        tags.changes.added lists what was added (tags.changes.removed is
        always empty for this operation).
    """
    try:
        return await add_tags(path, tags, ctx)
    except (ValueError, FileNotFoundError) as e:
        raise ToolError(str(e))
    except Exception as e:
        raise ToolError(f"Failed to add tags: {e!s}")


@mcp.tool()
async def update_tags_tool(
    path: Annotated[
        str,
        Field(
            description="Path to the note",
            pattern=r"^[^/].*\.md$",
            min_length=1,
            max_length=255,
        ),
    ],
    tags: Annotated[
        list[str],
        Field(
            description="New tags for the note. Empty list removes all tags. Don't include # symbols. Supports hierarchical tags with forward slashes.",
            min_length=0,
            max_length=50,
            examples=[
                ["meeting", "important", "q1-2025"],
                ["project/ai", "research/neural-networks", "status/active"],
            ],
        ),
    ],
    merge: Annotated[
        bool,
        Field(
            description="True = add these tags to existing ones, False = replace all tags with this new list",
            default=False,
        ),
    ] = False,
    ctx: Context | None = None,
):
    """
    Update tags on a note - either replace all tags or merge with existing.

    When to use:
    - After analyzing a note's content to suggest relevant tags
    - Reorganizing tags across your vault
    - Setting consistent tags based on note types or projects
    - AI-driven tag suggestions ("What is this note about? Add appropriate tags")

    When NOT to use:
    - Just adding a few tags (use add_tags)
    - Just removing specific tags (use remove_tags)

    Returns:
        {success, path, operation: "updated", tags: {before, after, changes}} —
        operation is always "updated"; tags.changes.operation_type is
        "replaced" or "merged" depending on merge, and tags.changes.added /
        tags.changes.removed reflect the actual diff.
    """
    try:
        return await update_tags(path, tags, merge, ctx)
    except (ValueError, FileNotFoundError) as e:
        raise ToolError(str(e))
    except Exception as e:
        raise ToolError(f"Failed to update tags: {e!s}")


@mcp.tool()
async def remove_tags_tool(
    path: Annotated[
        str,
        Field(
            description="Path to the note",
            pattern=r"^[^/].*\.md$",
            min_length=1,
            max_length=255,
        ),
    ],
    tags: Annotated[
        list[str],
        Field(
            description="Tags to remove from the note (without # prefix). Removes exact matches only.",
            min_length=1,
            max_length=50,
            examples=[["outdated", "draft"], ["project/completed", "priority/high"]],
        ),
    ],
    ctx: Context | None = None,
):
    """
    Remove specific tags from a note's frontmatter.

    When to use:
    - Cleaning up outdated tags
    - Removing temporary tags (like 'draft' or 'review')
    - Tag maintenance and reorganization
    - After completing tagged tasks

    When NOT to use:
    - Removing all tags (use update_tags with empty list)
    - Replacing tags (use update_tags with merge=False)

    Note: Only removes exact matches. To remove all subtags of a hierarchical tag,
    list them explicitly or use update_tags.

    Returns:
        {success, path, operation: "removed", tags: {before, after, changes}} —
        tags.changes.removed lists exactly which tags were removed (there is
        no separate count field).
    """
    try:
        return await remove_tags(path, tags, ctx)
    except (ValueError, FileNotFoundError) as e:
        raise ToolError(str(e))
    except Exception as e:
        raise ToolError(f"Failed to remove tags: {e!s}")


@mcp.tool()
async def list_tags_tool(
    include_counts: Annotated[
        bool,
        Field(
            description="Show how many times each tag is used across your vault",
            default=True,
        ),
    ] = True,
    sort_by: Annotated[
        Literal["name", "count"],
        Field(
            description="Sort tags alphabetically by 'name' or by popularity with 'count'",
            default="name",
        ),
    ] = "name",
    include_files: Annotated[
        bool,
        Field(
            description="Include the list of file paths that contain each tag",
            default=False,
        ),
    ] = False,
    offset: Annotated[
        int,
        Field(
            description="Number of tags to skip, for paging past the first page of results.",
            ge=0,
            default=0,
        ),
    ] = 0,
    limit: Annotated[
        int,
        Field(
            description="Maximum number of tags to return in this page. Page through the rest with offset. When include_files is true, combines multiplicatively with max_files_per_tag under a 300-path cap (see that field).",
            ge=1,
            le=1000,
            default=100,
        ),
    ] = 100,
    max_files_per_tag: Annotated[
        int,
        Field(
            description="Maximum number of file paths to include per tag when include_files is true. Extra files are truncated rather than blowing up the payload (see files_total in the response). Combines multiplicatively with limit: limit * max_files_per_tag must not exceed 300, or the call raises an error -- lower one of the two, or use search_notes_tool with a 'tag:' query for one tag's complete file list.",
            ge=1,
            le=300,
            default=3,
        ),
    ] = 3,
    ctx: Context | None = None,
):
    """
    List all unique tags used across the vault with usage statistics.

    When to use:
    - Before adding tags to maintain consistency
    - Getting an overview of your tagging taxonomy
    - Finding underused or overused tags
    - Discovering tag variations (e.g., 'project' vs 'projects')
    - Understanding hierarchical tag structures in your vault
    - Finding all files that use a specific tag (with include_files=true)

    Hierarchical tags:
    - Lists the literal tags present in the vault's tag index, not synthesized
      parent paths — "project" appears only if some note is tagged "project"
      directly, independent of whether "project/web" also exists elsewhere.
    - Shows how nested tags are organized in your vault
    - Helps identify opportunities for better tag organization

    File paths (with include_files=true):
    - Returns a list of all file paths that contain each tag
    - Useful for bulk operations on files with specific tags
    - Paths are relative to vault root
    - Capped at `max_files_per_tag` per tag (default 3, max 300); each
      item's `files_total` is the true per-tag count, so truncation is
      visible via len(files) < files_total. Need the complete file list
      for one specific tag? Use search_notes_tool with a `tag:` query
      instead — it has its own pagination and isn't capped by this limit.
    - Combined cost cap: `limit * max_files_per_tag` must not exceed 300
      when include_files=true, or the call raises an error before doing
      any work (measured true worst case across every valid split of that
      product, 255-char paths: ≈95KB — safely under the ~177KB that
      originally overflowed the MCP client). Lower `limit` or
      `max_files_per_tag` to fit, or drop `include_files` and fetch one
      tag's files via search_notes_tool instead. This — not `limit`
      alone — is what bounds response size.

    When NOT to use:
    - Getting tags for a specific note (use get_note_info)
    - Searching notes by tag (use search_notes with tag: prefix)

    Performance note:
    - For vaults with <1000 notes: Fast (1-3 seconds)
    - For vaults with 1000-5000 notes: Moderate (3-10 seconds)
    - For vaults with >5000 notes: May be slow (10+ seconds)
    - Uses batched concurrent requests to optimize performance
    - include_files=true adds minimal overhead

    Pagination (offset/limit):
    - Results are capped at `limit` tags per call (default 100, max 1000) so
      a large vault (especially with include_files=true) can't overflow the
      client in one response.
    - `total` is the vault-wide tag count; `returned` is how many tags are
      in this page (len(items)). Page through the rest by increasing
      `offset` (e.g. offset=0, then offset=limit, ...) until `returned` is
      less than `limit` or `offset >= total`.
    - With include_files=true, also respect the combined cost cap above —
      `limit` alone can still go up to 1000 when include_files=false.

    Returns:
        {items, total, returned, offset, limit, scope}. `items` holds up to
        `limit` tags starting at `offset`; `total` is the full vault-wide
        tag count regardless of paging. When include_files=true, each
        item's `files` list is capped at `max_files_per_tag` and carries
        `files_total` (the true per-tag count) alongside it. Raises (as
        ToolError) if offset/limit/max_files_per_tag are out of range, or
        if include_files=true and limit * max_files_per_tag exceeds 300.
    """
    try:
        return await list_tags(
            include_counts,
            sort_by,
            include_files,
            offset,
            limit,
            max_files_per_tag,
            ctx,
        )
    except ValueError as e:
        raise ToolError(str(e))
    except Exception as e:
        raise ToolError(f"Failed to list tags: {e!s}")
