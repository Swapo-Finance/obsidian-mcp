"""Link tool wrappers: backlinks, outgoing links, broken links, orphaned notes."""

from typing import Annotated, Literal

from fastmcp import Context
from fastmcp.exceptions import ToolError
from pydantic import Field

from .app import mcp
from .tools import (
    find_broken_links,
    find_orphaned_notes,
    get_backlinks,
    get_outgoing_links,
)


@mcp.tool()
async def get_backlinks_tool(
    path: Annotated[
        str,
        Field(
            description="Path to the note to find backlinks for",
            pattern=r"^[^/].*\.md$",
            min_length=1,
            max_length=255,
            examples=["Daily/2024-01-15.md", "Projects/AI Research.md"],
        ),
    ],
    include_context: Annotated[
        bool,
        Field(
            description="Include the text surrounding each link to understand why the link was made",
            default=True,
        ),
    ] = True,
    context_length: Annotated[
        int,
        Field(
            description="How much surrounding text to show for each link (in characters)",
            ge=50,
            le=500,
            default=100,
        ),
    ] = 100,
    ctx: Context | None = None,
):
    """
    Find all notes that link to a specific note (backlinks).

    When to use:
    - Understanding which notes reference a concept or topic
    - Discovering relationships between notes
    - Finding notes that depend on the current note
    - Building a mental map of note connections

    When NOT to use:
    - Finding links FROM a note (use get_outgoing_links)
    - Searching for broken links (use find_broken_links)

    Performance note:
    - Fast for small vaults (<100 notes)
    - May take several seconds for large vaults (1000+ notes)
    - Consider using search_notes for specific link queries

    Returns:
        All notes linking to the target with optional context
    """
    try:
        return await get_backlinks(path, include_context, context_length, ctx)
    except (ValueError, FileNotFoundError) as e:
        raise ToolError(str(e))
    except Exception as e:
        raise ToolError(f"Failed to get backlinks: {e!s}")


@mcp.tool()
async def get_outgoing_links_tool(
    path: Annotated[
        str,
        Field(
            description="Path to the note to extract links from",
            pattern=r"^[^/].*\.md$",
            min_length=1,
            max_length=255,
            examples=["Projects/Overview.md", "Index.md"],
        ),
    ],
    check_validity: Annotated[
        bool,
        Field(
            description="Also check if each linked note actually exists in your vault",
            default=False,
        ),
    ] = False,
    ctx: Context | None = None,
):
    """
    List all links from a specific note (outgoing links).

    When to use:
    - Understanding what a note references
    - Checking note dependencies before moving/deleting
    - Exploring the structure of index or hub notes
    - Validating links after changes

    When NOT to use:
    - Finding notes that link TO this note (use get_backlinks)
    - Searching across multiple notes (use find_broken_links)

    Returns:
        All outgoing links with their types and optional validity status
    """
    try:
        return await get_outgoing_links(path, check_validity, ctx)
    except (ValueError, FileNotFoundError) as e:
        raise ToolError(str(e))
    except Exception as e:
        raise ToolError(f"Failed to get outgoing links: {e!s}")


@mcp.tool()
async def find_broken_links_tool(
    directory: Annotated[
        str | None,
        Field(
            description="Check only this folder and its subfolders. Leave empty to check entire vault.",
            default=None,
            examples=[None, "Projects", "Archive/2023"],
        ),
    ] = None,
    single_note: Annotated[
        str | None,
        Field(
            description="Check links in just this one note instead of the whole vault or directory",
            default=None,
            examples=["Daily/2025-01-09.md", "Projects/Overview.md"],
        ),
    ] = None,
    ctx: Context | None = None,
):
    """
    Find all broken links in the vault, a specific directory, or a single note.

    When to use:
    - After renaming or deleting notes
    - Regular vault maintenance
    - Before reorganizing folder structure
    - Cleaning up after imports
    - Checking links in a specific note

    When NOT to use:
    - Just getting outgoing links without needing broken status (use get_outgoing_links)
    - Finding backlinks (use get_backlinks)

    Returns:
        All broken links found in the specified scope
    """
    try:
        return await find_broken_links(directory, single_note, ctx)
    except ValueError as e:
        raise ToolError(str(e))
    except Exception as e:
        raise ToolError(f"Failed to find broken links: {e!s}")


@mcp.tool()
async def find_orphaned_notes_tool(
    orphan_type: Annotated[
        Literal["no_backlinks", "no_links", "no_tags", "no_metadata", "isolated"],
        Field(
            description="What makes a note 'orphaned'. Choose the criteria that best fits your organization needs.",
            default="no_backlinks",
        ),
    ] = "no_backlinks",
    exclude_folders: Annotated[
        list[str] | str | None,
        Field(
            description="Folders to exclude from search. Useful for skipping templates, archives, or daily notes. Can be a list or JSON string.",
            default=None,
            examples=[["Templates", "Archive"], '["Daily", "Templates", "Inbox"]'],
        ),
    ] = None,
    min_age_days: Annotated[
        int | None,
        Field(
            description="Only include notes older than this many days. Helps exclude recent work-in-progress notes.",
            default=None,
            ge=0,
            le=365,
            examples=[7, 30, 90],
        ),
    ] = None,
    ctx: Context | None = None,
):
    """
    Find orphaned notes that may need organization or cleanup.

    When to use:
    - Regular vault maintenance and cleanup
    - Finding forgotten or disconnected notes
    - Identifying notes that need better organization
    - Preparing for vault reorganization
    - Finding candidates for archival or deletion

    Orphan types explained:
    - **no_backlinks**: Notes with no incoming links (most common definition)
    - **no_links**: Notes with no incoming OR outgoing links (completely isolated)
    - **no_tags**: Notes without any tags (untagged content)
    - **no_metadata**: Notes with minimal/no frontmatter properties
    - **isolated**: Notes with no links AND no tags (truly disconnected)

    Default exclusions:
    - Templates folder (usually contains reference notes)
    - Archive folder (already organized)
    - Daily folder (daily notes often standalone)

    When NOT to use:
    - Finding specific notes (use search_notes)
    - Getting all notes in a folder (use list_notes)
    - Finding notes by content (use search tools)

    Performance note:
    - Scans entire vault and checks links/metadata for each note
    - For vaults >1000 notes, this may take 10-30 seconds

    Returns:
        List of orphaned notes with paths, reasons, and metadata.
        Results are sorted by modification date (oldest first).

    Example response:
    {
        "count": 23,
        "orphaned_notes": [
            {
                "path": "Random Thoughts/Old Idea.md",
                "reason": "No incoming links",
                "modified": "2023-06-15T10:30:00Z",
                "size": 245,
                "word_count": 42
            }
        ],
        "stats": {
            "total_notes_scanned": 500,
            "excluded_folders": ["Templates", "Archive", "Daily"],
            "orphan_type": "no_backlinks"
        }
    }
    """
    try:
        return await find_orphaned_notes(
            orphan_type, exclude_folders, min_age_days, ctx
        )
    except ValueError as e:
        raise ToolError(str(e))
    except Exception as e:
        raise ToolError(f"Failed to find orphaned notes: {e!s}")
