"""Note CRUD tool wrappers: read, create, update, edit section, delete."""

from typing import Annotated, Literal

from fastmcp import Context
from fastmcp.exceptions import ToolError
from pydantic import Field

from .app import mcp
from .tools import (
    create_note,
    delete_note,
    edit_note_section,
    read_note,
    update_note,
)


@mcp.tool()
async def read_note_tool(
    path: Annotated[
        str,
        Field(
            description="Note location within your vault (e.g., 'Projects/AI Research.md'). Use forward slashes for folders.",
            pattern=r"^[^/].*\.md$",
            min_length=1,
            max_length=255,
            examples=[
                "Daily/2024-01-15.md",
                "Projects/AI Research.md",
                "Ideas/Quick Note.md",
            ],
        ),
    ],
    ctx: Context | None = None,
):
    """
    Read the content and metadata of a specific note.

    When to use:
    - Displaying note contents to the user
    - Analyzing or processing existing note data
    - ALWAYS before updating a note to preserve existing content
    - Verifying a note exists before making changes

    When NOT to use:
    - Searching multiple notes (use search_notes instead)
    - Getting only metadata (use get_note_info for efficiency)
    - Viewing images in a note (use view_note_images instead)

    Returns:
        Note content and metadata including tags, aliases, and frontmatter.
        Image references (![alt](path)) are preserved in the content but images are not loaded.

    IMPORTANT: If the note contains image references, proactively offer to analyze them:
    "I can see this note contains [N] images. Would you like me to analyze/examine them for you?"
    Then use view_note_images to load and analyze the images if requested.
    """
    try:
        return await read_note(path, ctx)
    except (ValueError, FileNotFoundError) as e:
        raise ToolError(str(e))
    except Exception as e:
        raise ToolError(f"Failed to read note: {e!s}")


@mcp.tool()
async def create_note_tool(
    path: Annotated[
        str,
        Field(
            description="Where to create the new note in your vault. Folders will be created automatically if needed.",
            pattern=r"^[^/].*\.md$",
            min_length=1,
            max_length=255,
            examples=[
                "Ideas/New Idea.md",
                "Daily/2024-01-15.md",
                "Projects/Project Plan.md",
            ],
        ),
    ],
    content: Annotated[
        str,
        Field(
            description="The markdown content for your note. Can include headings (#), tags (#tag), links ([[other note]]), and frontmatter.",
            min_length=0,
            max_length=1000000,
            examples=[
                "# Meeting Notes\n#meeting #project-alpha\n\nDiscussed timeline and deliverables...",
                "---\ntags: [daily, planning]\n---\n\n# Daily Note\n\nToday's tasks...",
            ],
        ),
    ],
    overwrite: Annotated[
        bool,
        Field(
            description="Set to true to replace an existing note at this location. Use carefully as this deletes the original content.",
            default=False,
        ),
    ] = False,
    ctx: Context | None = None,
):
    """
    Create a new note or overwrite an existing one.

    When to use:
    - Creating new notes with specific content
    - Setting up templates or structured notes
    - Programmatically generating documentation

    When NOT to use:
    - Updating existing notes (use update_note unless you want to replace entirely)
    - Appending content (use update_note with merge_strategy="append")

    Returns:
        Created note information with path and metadata
    """
    try:
        return await create_note(path, content, overwrite, ctx)
    except (ValueError, FileExistsError) as e:
        raise ToolError(str(e))
    except Exception as e:
        raise ToolError(f"Failed to create note: {e!s}")


@mcp.tool()
async def update_note_tool(
    path: Annotated[
        str,
        Field(
            description="Which note to update in your vault",
            pattern=r"^[^/].*\.md$",
            min_length=1,
            max_length=255,
            examples=["Daily/2024-01-15.md", "Projects/Project.md"],
        ),
    ],
    content: Annotated[
        str,
        Field(
            description="New content for the note. By default this REPLACES all existing content. Use merge_strategy='append' to add to the end instead.",
            min_length=0,
            max_length=1000000,
        ),
    ],
    create_if_not_exists: Annotated[
        bool,
        Field(
            description="Automatically create the note if it doesn't exist yet",
            default=False,
        ),
    ] = False,
    merge_strategy: Annotated[
        Literal["replace", "append"],
        Field(
            description="How to handle existing content. 'replace' = overwrite everything (default), 'append' = add new content to the end",
            default="replace",
        ),
    ] = "replace",
    ctx: Context | None = None,
):
    """
    Update the content of an existing note.

    ⚠️ IMPORTANT: By default, this REPLACES the entire note content.
    Always read the note first if you need to preserve existing content.

    When to use:
    - Updating a note with completely new content (replace)
    - Adding content to the end of a note (append)
    - Programmatically modifying notes

    When NOT to use:
    - Making small edits (read first, then update with full content)
    - Creating new notes (use create_note instead)

    Returns:
        Update status with path, metadata, and operation performed
    """
    try:
        return await update_note(
            path, content, create_if_not_exists, merge_strategy, ctx
        )
    except (ValueError, FileNotFoundError) as e:
        raise ToolError(str(e))
    except Exception as e:
        raise ToolError(f"Failed to update note: {e!s}")


@mcp.tool()
async def edit_note_section_tool(
    path: Annotated[
        str,
        Field(
            description="Path to the note to edit",
            pattern=r"^[^/].*\.md$",
            min_length=1,
            max_length=255,
            examples=["Daily/2024-01-15.md", "Projects/Project.md"],
        ),
    ],
    section_identifier: Annotated[
        str,
        Field(
            description="Markdown heading that identifies the section (e.g., '## Tasks', '### Status')",
            pattern=r"^#{1,6}\s+.+$",
            min_length=3,
            max_length=200,
            examples=[
                "## Tasks",
                "### Status Updates",
                "# Overview",
                "#### Implementation Details",
            ],
        ),
    ],
    content: Annotated[
        str,
        Field(
            description="Content to insert, replace, or append to the section",
            min_length=1,
            max_length=100000,
        ),
    ],
    operation: Annotated[
        Literal["insert_after", "insert_before", "replace", "append_to_section"],
        Field(
            description="How to edit the section. 'insert_after' = add content after heading, 'insert_before' = add before heading, 'replace' = replace entire section, 'append_to_section' = add to end of section",
            default="insert_after",
        ),
    ] = "insert_after",
    create_if_missing: Annotated[
        bool,
        Field(
            description="Create the section at the end of the note if it doesn't exist",
            default=False,
        ),
    ] = False,
    ctx: Context | None = None,
):
    """
    Edit a specific section of a note identified by a markdown heading.

    When to use:
    - Adding content to a specific section without rewriting the whole note
    - Updating a particular section (like status updates, task lists)
    - Inserting content at precise locations in structured notes
    - Building up notes incrementally by section

    When NOT to use:
    - Simple append to end of note (use update_note with merge_strategy='append')
    - Replacing entire note content (use update_note)
    - Creating a new note (use create_note)

    Section identification:
    - Sections are identified by markdown headings (# ## ### etc.)
    - Match is case-insensitive
    - First matching heading is used if duplicates exist
    - Section includes content until next heading of same/higher level

    Operations:
    - insert_after: Add content immediately after the section heading
    - insert_before: Add content immediately before the section heading
    - replace: Replace entire section including the heading
    - append_to_section: Add content at the end of the section

    Returns:
        Edit status including whether section was found or created
    """
    try:
        return await edit_note_section(
            path, section_identifier, content, operation, create_if_missing, ctx
        )
    except (ValueError, FileNotFoundError) as e:
        raise ToolError(str(e))
    except Exception as e:
        raise ToolError(f"Failed to edit section: {e!s}")


@mcp.tool()
async def delete_note_tool(
    path: Annotated[
        str,
        Field(
            description="Path to the note to delete from your vault",
            pattern=r"^[^/].*\.md$",
            min_length=1,
            max_length=255,
            examples=["Archive/Old Note.md", "Temp/Draft.md"],
        ),
    ],
    ctx: Context | None = None,
):
    """
    Delete a note from the vault permanently.

    When to use:
    - Removing outdated or duplicate notes
    - Cleaning up temporary drafts
    - Part of a move operation (delete after successful copy)

    When NOT to use:
    - Archiving (use move_note to Archive folder instead)
    - Temporary removal (no undo available)

    ⚠️ WARNING: This operation cannot be undone. The note will be permanently deleted.

    Returns:
        Deletion confirmation with the path of the deleted note
    """
    try:
        return await delete_note(path, ctx)
    except (ValueError, FileNotFoundError) as e:
        raise ToolError(str(e))
    except Exception as e:
        raise ToolError(f"Failed to delete note: {e!s}")
