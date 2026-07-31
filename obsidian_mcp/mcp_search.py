"""Search tool wrappers: full-text search, search by date, search by regex."""

from typing import Annotated, Literal

from fastmcp import Context
from fastmcp.exceptions import ToolError
from pydantic import Field

from .app import mcp
from .tools import (
    search_by_date,
    search_by_regex,
    search_notes,
)


@mcp.tool()
async def search_notes_tool(
    query: Annotated[
        str,
        Field(
            description="Search query that matches BOTH filenames and content by default. Just type a note name to find it! Use prefixes for specific search types: 'tag:' for tags, 'path:' for ONLY filenames, 'property:' for metadata.",
            min_length=1,
            max_length=500,
            examples=[
                "Meeting Notes",
                "Obsidian Tag Refactor",
                "machine learning",
                "tag:project",
                "path:Daily/",
                "property:status:active",
            ],
        ),
    ],
    context_length: Annotated[
        int,
        Field(
            description="How much text to show around each match for context. Higher values show more surrounding content.",
            ge=10,
            le=500,
            default=20,
        ),
    ] = 20,
    max_results: Annotated[
        int,
        Field(
            description="Maximum number of results to return. Use smaller values for faster responses and larger values for comprehensive searches.",
            ge=1,
            le=500,
            default=50,
        ),
    ] = 50,
    mode: Annotated[
        Literal["content", "index", "auto"] | None,
        Field(
            description="Result shape override. 'content': a text snippet per result (today's default shape). "
            "'index': lightweight {path, name, description, score, match_type} from the vault's cache, "
            "no snippet — read_note the ones that matter. 'auto' (server default): index once the "
            "result count passes OBSIDIAN_SEARCH_INDEX_THRESHOLD, else content. Leave unset to use the "
            "server's configured default.",
            default=None,
        ),
    ] = None,
    ctx: Context | None = None,
):
    """
    Search for notes by filename or content, with smart ranking.

    DEFAULT BEHAVIOR (NEW): Searches BOTH note filenames AND content automatically.
    Filename matches are ranked higher than content matches for better discovery.

    When to use:
    - Finding a note when you know part of its name (just type the name)
    - Finding notes containing specific content
    - Locating notes with specific tags
    - Searching within specific folders
    - Finding notes by frontmatter properties

    Search modes:
    - Default: searches BOTH filenames and content (filename matches ranked higher)
      Example: "tag refactor" finds "Obsidian Tag Refactor.md" AND notes mentioning "tag refactor"
    - "path:text" - searches ONLY in filenames/paths
    - "tag:tagname" - searches by tag (supports hierarchical tags)
    - "property:name:value" - searches by frontmatter properties

    Examples:
    - Find a note by name: "Project Tracker" (will find "Project Tracker.md" first)
    - Search content only: Use explicit path: prefix to exclude: "path:Project"
    - Find by tag: "tag:important" or "tag:project/web"
    - Find by property: "property:status:active"

    Tag search supports hierarchical tags:
    - "tag:project" finds all project-related tags including project/web, project/mobile
    - "tag:web" finds any tag ending with "web" like project/web, design/web

    When NOT to use:
    - Searching by date (use search_by_date instead)
    - Listing all notes (use list_notes for better performance)
    - Reading a specific note when you know the exact path (use read_note directly)

    Returns:
        Search results with matched notes, relevance scores, and context.
        Filename matches have higher scores than content matches.
        Response includes match_type field: "filename" or "content".
    """
    try:
        return await search_notes(query, context_length, max_results, mode, ctx)
    except ValueError as e:
        raise ToolError(str(e))
    except Exception as e:
        raise ToolError(f"Search failed: {e!s}")


@mcp.tool()
async def search_by_date_tool(
    date_type: Annotated[
        Literal["created", "modified"],
        Field(
            description="Which date to search by: when the note was first created or last modified",
            default="modified",
        ),
    ] = "modified",
    days_ago: Annotated[
        int,
        Field(
            description="How many days back to search from today. 0 = today, 1 = yesterday, 7 = last week",
            ge=0,
            le=365,
            default=7,
            examples=[0, 1, 7, 30],
        ),
    ] = 7,
    operator: Annotated[
        Literal["within", "exactly"],
        Field(
            description="'within' = all notes in the last N days, 'exactly' = only notes from exactly N days ago",
            default="within",
        ),
    ] = "within",
    mode: Annotated[
        Literal["content", "index", "auto"] | None,
        Field(
            description="Result shape override. 'index' adds each result's cached name/description (no snippet to "
            "strip here). 'auto' (server default): index once the result count passes "
            "OBSIDIAN_SEARCH_INDEX_THRESHOLD. Leave unset to use the server's configured default.",
            default=None,
        ),
    ] = None,
    ctx: Context | None = None,
):
    """
    Search for notes by creation or modification date.

    When to use:
    - Finding recently modified notes
    - Locating notes created in a specific time period
    - Reviewing activity from specific dates

    When NOT to use:
    - Content-based search (use search_notes)
    - Finding notes by tags or path (use search_notes)

    Returns:
        Notes matching the date criteria with paths and timestamps
    """
    try:
        return await search_by_date(date_type, days_ago, operator, mode, ctx)
    except ValueError as e:
        raise ToolError(str(e))
    except Exception as e:
        raise ToolError(f"Date search failed: {e!s}")


@mcp.tool()
async def search_by_regex_tool(
    pattern: Annotated[
        str,
        Field(
            description="Regular expression pattern for advanced searches. Use for finding URLs, code patterns, TODO items, etc.",
            min_length=1,
            max_length=500,
            examples=[r"TODO\s*:.*", r"https?://[^\s]+", r"def\s+\w+\("],
        ),
    ],
    flags: Annotated[
        list[Literal["ignorecase", "multiline", "dotall"]] | None,
        Field(
            description="Options for regex matching: 'ignorecase' = case-insensitive, 'multiline' = ^ and $ match line boundaries, 'dotall' = . matches newlines",
            default=None,
        ),
    ] = None,
    context_length: Annotated[
        int,
        Field(
            description="Characters to show around matches", default=20, ge=10, le=500
        ),
    ] = 20,
    max_results: Annotated[
        int,
        Field(
            description="Maximum number of notes to return. Use smaller values for faster responses.",
            default=50,
            ge=1,
            le=200,
        ),
    ] = 50,
    mode: Annotated[
        Literal["content", "index", "auto"] | None,
        Field(
            description="Result shape override. 'index': lightweight {path, name, description, score, match_type} "
            "(match_count doubles as score) instead of per-match text snippets. 'auto' (server default): "
            "index once the result count passes OBSIDIAN_SEARCH_INDEX_THRESHOLD. Leave unset to use the "
            "server's configured default.",
            default=None,
        ),
    ] = None,
    ctx: Context | None = None,
):
    """
    Search for notes using regular expressions for advanced pattern matching.

    When to use:
    - Finding complex patterns (URLs, code syntax, structured data)
    - Searching with wildcards and special characters
    - Case-sensitive or multi-line pattern matching
    - Finding TODO/FIXME comments with context

    When NOT to use:
    - Simple text search (use search_notes instead)
    - Searching by tags or properties (use dedicated tools)

    Common patterns:
    - URLs: r"https?://[^\\s]+"
    - Email: r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\\.[a-zA-Z]{2,}"
    - TODO comments: r"(TODO|FIXME)\\s*:.*"
    - Markdown headers: r"^#{1,6}\\s+.*"
    - Code blocks: r"```\\w*\\n[\\s\\S]*?```"

    Returns:
        Notes containing regex matches with match details and context
    """
    try:
        return await search_by_regex(
            pattern, flags, context_length, max_results, mode, ctx
        )
    except ValueError as e:
        raise ToolError(str(e))
    except Exception as e:
        raise ToolError(f"Regex search failed: {e!s}")
