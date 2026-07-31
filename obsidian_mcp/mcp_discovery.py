"""Discovery tool wrappers: list notes/folders, note info, and property search.

search_by_property_tool lives here (not mcp_search.py) so that module stays
under the 350-line budget; grouping is by file size, not by feature."""

from typing import Annotated, Literal

from fastmcp import Context
from fastmcp.exceptions import ToolError
from pydantic import Field

from .app import mcp
from .tools import (
    get_note_info,
    list_folders,
    list_notes,
    search_by_property,
)


@mcp.tool()
async def list_notes_tool(
    directory: Annotated[
        str | None,
        Field(
            description="Specific folder to list notes from. Leave empty to list entire vault.",
            default=None,
            examples=[None, "Projects", "Daily", "Archive/2024"],
        ),
    ] = None,
    recursive: Annotated[
        bool,
        Field(
            description="Include notes from all subfolders. Set to false for only immediate children.",
            default=True,
        ),
    ] = True,
    ctx: Context | None = None,
):
    """
    List notes in the vault or a specific directory.

    When to use:
    - Getting an overview of vault structure
    - Finding notes in a specific folder
    - Checking what notes exist before bulk operations
    - Understanding vault organization

    When NOT to use:
    - Searching for specific content (use search_notes)
    - Finding notes by properties (use search_by_property)
    - Just counting notes (this loads full paths)

    Performance notes:
    - Fast for directories with <100 notes
    - May be slower for large vaults (1000+ notes) with recursive=True

    Returns:
        Hierarchical structure of notes with paths and folder organization
    """
    try:
        return await list_notes(directory, recursive, ctx)
    except Exception as e:
        raise ToolError(f"Failed to list notes: {e!s}")


@mcp.tool()
async def list_folders_tool(
    directory: Annotated[
        str | None,
        Field(
            description="Specific directory to list folders from (optional, defaults to root)",
            default=None,
            examples=[None, "Projects", "Daily", "Archive/2024"],
        ),
    ] = None,
    recursive: Annotated[
        bool,
        Field(description="Whether to include all nested subfolders", default=True),
    ] = True,
    ctx: Context | None = None,
):
    """
    List folders in the vault or a specific directory.

    When to use:
    - Exploring vault organization structure
    - Verifying folder names before creating notes
    - Checking if a specific folder exists
    - Understanding the hierarchy of the vault

    When NOT to use:
    - Listing notes (use list_notes instead)
    - Searching for content (use search_notes)

    Returns:
        Folder structure with paths and names
    """
    try:
        return await list_folders(directory, recursive, ctx)
    except ValueError as e:
        raise ToolError(str(e))
    except Exception as e:
        raise ToolError(f"Failed to list folders: {e!s}")


@mcp.tool()
async def get_note_info_tool(
    path: Annotated[
        str,
        Field(
            description="Path to the note to analyze",
            pattern=r"^[^/].*\.md$",
            min_length=1,
            max_length=255,
            examples=["Projects/Overview.md", "Daily/2024-01-15.md"],
        ),
    ],
    ctx: Context | None = None,
):
    """
    Get metadata and statistics about a note without reading its content.

    When to use:
    - Checking note properties quickly (tags, dates, size)
    - Getting frontmatter without loading content
    - Gathering statistics (word count, link count)
    - Verifying note exists and getting basic info
    - Batch processing note metadata

    When NOT to use:
    - Reading note content (use read_note)
    - Searching for notes (use search tools)
    - Modifying metadata (use specific update tools)

    Returns:
        Note metadata including path, existence, dates, size, frontmatter properties,
        and statistics (word count, link count, tag count, image presence)
    """
    try:
        return await get_note_info(path, ctx)
    except ValueError as e:
        raise ToolError(str(e))
    except Exception as e:
        raise ToolError(f"Failed to get note info: {e!s}")


@mcp.tool()
async def search_by_property_tool(
    property_name: Annotated[
        str,
        Field(
            description="The frontmatter property to search for (e.g., 'status', 'priority'). These are metadata fields at the top of notes.",
            min_length=1,
            examples=["status", "priority", "author", "tags", "deadline"],
        ),
    ],
    value: Annotated[
        str | None,
        Field(
            description="The value to match against. Not needed when using 'exists' to just check if property is present.",
            default=None,
            examples=["active", "high", "2024-01-15", "John Doe"],
        ),
    ] = None,
    operator: Annotated[
        Literal["=", "!=", ">", "<", ">=", "<=", "contains", "exists"],
        Field(
            description="How to compare: '=' exact match, '!=' not equal, '>/</>=/<=' for numbers/dates, 'contains' partial match, 'exists' just checks presence",
            default="=",
        ),
    ] = "=",
    context_length: Annotated[
        int,
        Field(
            description="Characters of note content to include",
            default=20,
            ge=0,
            le=500,
        ),
    ] = 20,
    mode: Annotated[
        Literal["content", "index", "auto"] | None,
        Field(
            description="Result shape override. 'index': lightweight {path, name, description, score, match_type, "
            "property_value} instead of a text snippet (property_value is always kept — it's the point "
            "of this search). 'auto' (server default): index once the result count passes "
            "OBSIDIAN_SEARCH_INDEX_THRESHOLD. Leave unset to use the server's configured default.",
            default=None,
        ),
    ] = None,
    ctx: Context | None = None,
):
    """
    Search for notes by their frontmatter property values.

    When to use:
    - Finding notes with specific metadata (status, priority, etc.)
    - Filtering by numeric properties (rating > 4, priority <= 2)
    - Filtering by date properties (deadline < "2024-12-31")
    - Searching within array/list properties (tags, aliases, categories)
    - Checking which notes have certain properties defined
    - Building database-like queries on your notes

    Property types supported:
    - Text/String: Exact match or contains
    - Numbers: Comparison operators work numerically
    - Dates: ISO format (YYYY-MM-DD) with intelligent comparison
    - Arrays/Lists: Searches within list items, comparisons use list length
    - Legacy properties: Automatically handles tag→tags, alias→aliases migrations

    When NOT to use:
    - Content search (use search_notes instead)
    - Tag search (use search_notes with tag: prefix)
    - Path/filename search (use search_notes with path: prefix)

    Examples:
    - Find active projects: property_name="status", value="active"
    - Find high priority: property_name="priority", operator=">", value="2"
    - Find notes with deadlines: property_name="deadline", operator="exists"
    - Find notes by author: property_name="author", operator="contains", value="john"
    - Find notes with tag in list: property_name="tags", value="project"
    - Find past deadlines: property_name="due_date", operator="<", value="2024-01-01"

    Returns:
        Notes matching the property criteria with values displayed
    """
    try:
        return await search_by_property(
            property_name, value, operator, context_length, mode, ctx
        )
    except ValueError as e:
        raise ToolError(str(e))
    except Exception as e:
        raise ToolError(f"Property search failed: {e!s}")
