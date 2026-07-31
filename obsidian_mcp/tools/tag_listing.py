"""Tag listing for Obsidian MCP server."""

from typing import Any

from ..constants import ERROR_MESSAGES
from ..utils.filesystem import get_vault


def _build_tag_item(
    name: str,
    tag_counts: dict[str, int],
    tags_by_name: dict[str, Any],
    include_counts: bool,
    include_files: bool,
    max_files_per_tag: int,
) -> dict[str, Any]:
    """Build one list_tags() result item for an already-ordered tag `name`.

    Ordering is decided by the caller (see ordered_names in list_tags) --
    this only decorates one name with the optional count/files fields, so
    there is a single place defining item shape instead of two branches
    (item-building vs names-only) each re-deriving it.

    Args:
        name: Tag name, already positioned in final sort order
        tag_counts: Authoritative {tag: usage count} map
        tags_by_name: {tag: set-of-paths} map backing the files list
        include_counts: Whether to add "count"
        include_files: Whether to add "files" / "files_total"
        max_files_per_tag: Cap applied to "files" (files_total stays true)

    Returns:
        {"name": ...} plus whichever optional keys are enabled
    """
    item: dict[str, Any] = {"name": name}
    if include_counts:
        item["count"] = tag_counts[name]
    if include_files:
        files = sorted(tags_by_name[name])
        item["files"] = files[:max_files_per_tag]
        # True total independent of include_counts, so callers can detect
        # truncation via len(files) < files_total.
        item["files_total"] = tag_counts[name]
    return item


async def list_tags(
    include_counts: bool = True,
    sort_by: str = "name",
    include_files: bool = False,
    offset: int = 0,
    limit: int = 100,
    max_files_per_tag: int = 3,
    ctx=None,
) -> dict:
    """
    List all unique tags used across the vault with usage statistics.

    Use this tool to discover existing tags before creating new ones. This helps
    maintain consistency in your tagging system and prevents duplicate tags with
    slight variations (e.g., 'project' vs 'projects').

    Args:
        include_counts: Whether to include usage count for each tag (default: true)
        sort_by: How to sort results - "name" (alphabetical) or "count" (by usage) (default: "name")
        include_files: Whether to include file paths for each tag (default: false)
        offset: Number of tags to skip, for paging past the first page (default: 0)
        limit: Maximum number of tags to return in this page (default: 100)
        max_files_per_tag: Maximum file paths per tag when include_files=true;
            extra files are truncated rather than blowing up the payload (see
            files_total in the response). When include_files=true, limit *
            max_files_per_tag must not exceed 300 or list_tags raises
            ValueError -- lower limit or max_files_per_tag, or fetch one
            tag's complete file list via search_notes_tool with a 'tag:'
            query instead (default: 3)
        ctx: MCP context for progress reporting

    Returns:
        Dictionary with:
        - items: up to `limit` tags starting at `offset` -- either
          {"name": ...} dicts decorated with "count" (include_counts) and
          "files"/"files_total" (include_files), or bare name strings when
          both flags are false
        - total: vault-wide tag count, independent of paging
        - returned: number of tags in this page (== len(items))
        - offset, limit: echoed back from the request
        - scope: {include_counts, sort_by, include_files}

    Example:
        >>> await list_tags(include_counts=True, sort_by="count", limit=3)
        {
            "items": [
                {"name": "project", "count": 42},
                {"name": "meeting", "count": 38},
                {"name": "idea", "count": 15}
            ],
            "total": 25,
            "returned": 3,
            "offset": 0,
            "limit": 3,
            "scope": {"include_counts": true, "sort_by": "count", "include_files": false}
        }

        >>> await list_tags(include_files=True, include_counts=False, max_files_per_tag=2, limit=2)
        {
            "items": [
                {"name": "meeting", "files": ["Meetings/2024-01-15.md", "Meetings/2024-01-22.md"], "files_total": 2},
                {"name": "project", "files": ["Projects/Mobile App.md", "Projects/Web App.md"], "files_total": 5}
            ],
            "total": 2,
            "returned": 2,
            "offset": 0,
            "limit": 2,
            "scope": {"include_counts": false, "sort_by": "name", "include_files": true}
        }
    """
    # Validate sort_by parameter
    if sort_by not in ["name", "count"]:
        raise ValueError(ERROR_MESSAGES["invalid_sort_by"].format(value=sort_by))

    # Guard offset/limit/max_files_per_tag directly: the pydantic Field(ge=...)
    # bounds on list_tags_tool only protect calls made through that MCP
    # wrapper. list_tags() is called directly elsewhere (this module's own
    # test suite calls it that way by design), so without this a negative
    # offset/limit silently mis-slices instead of erroring -- e.g.
    # offset=-5 on a 14-tag list returns tags[-5:...], the LAST 5 tags,
    # with no exception.
    if offset < 0:
        raise ValueError(f"offset must be >= 0, got {offset}")
    if limit < 1:
        raise ValueError(f"limit must be >= 1, got {limit}")
    if max_files_per_tag < 1:
        raise ValueError(f"max_files_per_tag must be >= 1, got {max_files_per_tag}")

    # Combined cost ceiling (only matters when include_files=True): limit and
    # max_files_per_tag are independent and multiply into the response's
    # total file-path count. Measured via json.dumps on this exact response
    # shape, true worst case across every valid split of this product (a
    # lopsided "many tags, 1 file each" split costs more than a balanced
    # split -- more items means more repeated per-item overhead): limit=300
    # x max_files_per_tag=1, 255-char paths (the max validate_note_path
    # allows) -> ~95KB. That's safely under the ~177KB that originally
    # overflowed the MCP client (46% margin); the default itself (limit=100
    # x max_files_per_tag=3 = 300) costs ~82KB in its own worst case. Keep
    # server.py's max_files_per_tag le=300 Field bound in sync with this
    # number.
    if include_files and limit * max_files_per_tag > 300:
        raise ValueError(
            f"limit ({limit}) x max_files_per_tag ({max_files_per_tag}) = "
            f"{limit * max_files_per_tag} file paths requested, over the "
            "300 cap for one list_tags response. Lower limit or "
            "max_files_per_tag, or set include_files=False. For one tag's "
            "complete file list, use search_notes_tool with a 'tag:' query "
            "instead -- it paginates independently of this cap."
        )

    if ctx:
        await ctx.info("Collecting tags from vault...")

    vault = get_vault()

    try:
        # Sourced from the vault's tags index (see utils/vault_cache.py)
        # instead of reading and re-parsing every note on every call.
        tags_by_name = await vault.cache.get_tags_index()
        tag_counts = {tag: len(paths) for tag, paths in tags_by_name.items()}

        if ctx:
            await ctx.info(f"Found {len(tag_counts)} unique tags across the vault...")

        # Single source of truth for ordering: both the item-building and
        # names-only shapes below must produce identical order (see
        # TestListTagsSortOrderFlagMatrix) -- computing it once here, instead
        # of once per branch, makes that a structural guarantee instead of a
        # hand-maintained mirror (this exact duplication caused two rounds
        # of ordering-drift bugs before this refactor).
        if sort_by == "count":
            ordered_names = sorted(
                tag_counts.keys(), key=lambda tag: tag_counts[tag], reverse=True
            )
        else:  # sort by name
            ordered_names = sorted(tag_counts.keys(), key=str.lower)

        # Format results
        if include_counts or include_files:
            tags = [
                _build_tag_item(
                    name,
                    tag_counts,
                    tags_by_name,
                    include_counts,
                    include_files,
                    max_files_per_tag,
                )
                for name in ordered_names
            ]
        else:
            # Just return tag names, already in sorted order above.
            tags = ordered_names

        # Paginate: real offset/limit slicing so truncation is visible via
        # returned/offset/limit instead of silently dropping tags.
        page = tags[offset : offset + limit]

        # Return standardized list results structure
        return {
            "items": page,
            "total": len(tag_counts),
            "returned": len(page),
            "offset": offset,
            "limit": limit,
            "scope": {
                "include_counts": include_counts,
                "sort_by": sort_by,
                "include_files": include_files,
            },
        }

    except Exception as e:
        if ctx:
            await ctx.info(f"Failed to list tags: {e!s}")
        raise ValueError(ERROR_MESSAGES["tag_collection_failed"].format(error=str(e)))
