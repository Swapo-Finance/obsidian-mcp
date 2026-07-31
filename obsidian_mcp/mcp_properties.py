"""Batch frontmatter property update tool wrapper."""

from typing import Annotated

from fastmcp import Context
from fastmcp.exceptions import ToolError
from pydantic import Field

from .app import mcp
from .tools import batch_update_properties


@mcp.tool()
async def batch_update_properties_tool(
    search_criteria: Annotated[
        dict,
        Field(
            description="How to find notes to update. Must include one of: 'query' (search string), 'folder' (folder path), or 'files' (list of paths). Use 'query' for complex searches, 'folder' for directory operations, 'files' for specific notes. When using 'query', an optional integer 'max_results' key bounds how many matches are fetched (1-500, default 500).",
            examples=[
                {"query": "tag:project status:active"},
                {"folder": "Projects", "recursive": True},
                {"files": ["Projects/A.md", "Projects/B.md"]},
            ],
        ),
    ],
    property_updates: Annotated[
        dict | None,
        Field(
            description="Properties to add or update in frontmatter. Set value to null to remove.",
            default=None,
            examples=[
                {"status": "completed", "priority": 1},
                {"year": 2024, "archived": True},
            ],
        ),
    ] = None,
    properties_to_remove: Annotated[
        list[str] | None,
        Field(
            description="List of property names to remove from frontmatter",
            default=None,
            examples=[["draft", "temp"], ["old_status", "deprecated_field"]],
        ),
    ] = None,
    add_tags: Annotated[
        list[str] | str | None,
        Field(
            description="Tags to add to notes (additive, won't remove existing tags). Can be a list or JSON string.",
            default=None,
            examples=[
                ["archived", "2024"],
                ["reviewed", "approved"],
                '["archived", "2024"]',
            ],
        ),
    ] = None,
    remove_tags: Annotated[
        list[str] | str | None,
        Field(
            description="Tags to remove from notes. Can be a list or JSON string.",
            default=None,
            examples=[
                ["draft", "todo"],
                ["urgent", "needs-review"],
                '["draft", "todo"]',
            ],
        ),
    ] = None,
    remove_inline_tags: Annotated[
        bool,
        Field(
            description="Also remove tags from note body (inline #tags). Only applies when remove_tags is specified.",
            default=False,
        ),
    ] = False,
    ctx: Context | None = None,
):
    """
    Batch update properties across multiple notes.

    When to use:
    - Updating metadata across many notes (status, priority, etc.)
    - Bulk tag operations (add/remove tags from multiple notes)
    - Archiving projects (set archived=true, add year property)
    - Cleaning up properties (remove outdated fields)
    - Normalizing metadata across your vault

    Search criteria options:
    - query: Use search syntax (tag:project, folder:Archive, property:status:active)
    - folder: Process all notes in a folder (with optional recursive flag)
    - files: Explicit list of file paths

    Property operations:
    - Add/update any frontmatter property
    - Remove properties by name
    - Special handling for tags (add/remove with deduplication)
    - Remove inline #tags from note body (optional)

    Examples:
    - Archive completed projects: query="tag:project status:completed", property_updates={"archived": true, "year": 2024}
    - Clean up draft tags: query="tag:draft", remove_tags=["draft"], remove_inline_tags=true
    - Update all notes in folder: folder="Projects/2023", property_updates={"year": 2023}

    When NOT to use:
    - Single note updates (use update_note, add_tags, etc.)
    - Complex content changes (this only updates frontmatter)

    Returns:
        {
            "total_notes": 10,         # Total notes found matching criteria
            "updated": 8,              # Successfully updated notes
            "failed": 2,               # Failed updates
            "details": [...],          # List of changes per note
            "errors": [...]            # List of errors with paths and reasons
        }
    """
    try:
        return await batch_update_properties(
            search_criteria,
            property_updates,
            properties_to_remove,
            add_tags,
            remove_tags,
            remove_inline_tags,
            ctx,
        )
    except ValueError as e:
        raise ToolError(str(e))
    except Exception as e:
        raise ToolError(f"Failed to batch update properties: {e!s}")
