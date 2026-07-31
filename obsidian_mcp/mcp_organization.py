"""Note and folder organization tool wrappers: move, rename, create folder."""

from typing import Annotated

from fastmcp import Context
from fastmcp.exceptions import ToolError
from pydantic import Field

from .app import mcp
from .tools import (
    create_folder,
    move_folder,
    move_note,
    rename_note,
)


@mcp.tool()
async def move_note_tool(
    source_path: Annotated[
        str,
        Field(
            description="Current location of the note to move",
            pattern=r"^[^/].*\.md$",
            min_length=1,
            max_length=255,
            examples=["Inbox/Quick Note.md", "Projects/Old Project.md"],
        ),
    ],
    destination_path: Annotated[
        str,
        Field(
            description="New location for the note. Folders will be created if needed.",
            pattern=r"^[^/].*\.md$",
            min_length=1,
            max_length=255,
            examples=["Projects/Active/Quick Note.md", "Archive/2024/Old Project.md"],
        ),
    ],
    update_links: Annotated[
        bool,
        Field(
            description="Automatically update all [[wiki links]] if the filename changes during move",
            default=True,
        ),
    ] = True,
    ctx: Context | None = None,
):
    """
    Move a note to a new location, optionally with a new name.

    When to use:
    - Reorganizing notes into different folders
    - Moving AND renaming in one operation
    - Archiving completed projects
    - Consolidating scattered notes

    When NOT to use:
    - Just renaming within same folder (use rename_note for clarity)
    - Copying notes (use read_note + create_note instead)
    - Moving entire folders (use move_folder)

    Link updating:
    - Automatically detects if filename changes during move
    - Updates all [[wiki-style links]] only when name changes
    - Preserves link aliases and formatting
    - No updates needed for simple folder moves (links work by name)

    Returns:
        Move confirmation with path changes and link update details
    """
    try:
        return await move_note(source_path, destination_path, update_links, ctx)
    except (ValueError, FileNotFoundError, FileExistsError) as e:
        raise ToolError(str(e))
    except Exception as e:
        raise ToolError(f"Failed to move note: {e!s}")


@mcp.tool()
async def rename_note_tool(
    old_path: Annotated[
        str,
        Field(
            description="Current path of the note to rename",
            pattern=r"^[^/].*\.md$",
            min_length=1,
            max_length=255,
            examples=["Projects/Old Name.md", "Ideas/Temporary Title.md"],
        ),
    ],
    new_path: Annotated[
        str,
        Field(
            description="New path for the note (must be in same directory)",
            pattern=r"^[^/].*\.md$",
            min_length=1,
            max_length=255,
            examples=["Projects/New Name.md", "Ideas/Final Title.md"],
        ),
    ],
    update_links: Annotated[
        bool,
        Field(
            description="Automatically update all [[wiki links]] to this note across the vault",
            default=True,
        ),
    ] = True,
    ctx: Context | None = None,
):
    """
    Rename a note and automatically update all references to it.

    When to use:
    - Changing a note's title to better reflect its content
    - Fixing typos in note names
    - Standardizing naming conventions
    - Updating temporary names to permanent ones

    When NOT to use:
    - Moving notes to different folders (use move_note)
    - Creating a copy with new name (use read_note + create_note)

    Important:
    - Can only rename within the same directory
    - Automatically updates all [[wiki-style links]] throughout vault
    - Preserves link aliases like [[old name|display text]]
    - Shows which notes were updated for transparency

    Returns:
        Rename confirmation with link update details
    """
    try:
        return await rename_note(old_path, new_path, update_links, ctx)
    except (ValueError, FileNotFoundError, FileExistsError) as e:
        raise ToolError(str(e))
    except Exception as e:
        raise ToolError(f"Failed to rename note: {e!s}")


@mcp.tool()
async def create_folder_tool(
    folder_path: Annotated[
        str,
        Field(
            description="Path of the folder to create",
            min_length=1,
            max_length=255,
            examples=["Projects/2025", "Archive/Q1", "Daily/January"],
        ),
    ],
    create_placeholder: Annotated[
        bool,
        Field(
            description="Whether to create a placeholder file (.gitkeep or README.md)",
            default=True,
        ),
    ] = True,
    ctx: Context | None = None,
):
    """
    Create a new folder in the vault, including all parent folders in the path.

    When to use:
    - Setting up project structure in advance
    - Creating deep folder hierarchies (e.g., "Research/Studies/2024")
    - Creating archive folders before moving notes
    - Establishing organizational hierarchy
    - Preparing folders for future content

    When NOT to use:
    - If you're about to create a note in that path (folders are created automatically)
    - For temporary organization (just create notes directly)

    Note: Will create all necessary parent folders. For example, "Research/Studies/2024"
    will create Research, Research/Studies, and Research/Studies/2024 if they don't exist.

    Returns:
        Creation status with list of folders created and placeholder file path
    """
    try:
        return await create_folder(folder_path, create_placeholder, ctx)
    except ValueError as e:
        raise ToolError(str(e))
    except Exception as e:
        raise ToolError(f"Failed to create folder: {e!s}")


@mcp.tool()
async def move_folder_tool(
    source_folder: Annotated[
        str,
        Field(
            description="Current folder path to move",
            min_length=1,
            max_length=255,
            examples=["Projects/Old", "Archive/2023", "Inbox/Unsorted"],
        ),
    ],
    destination_folder: Annotated[
        str,
        Field(
            description="New location for the folder",
            min_length=1,
            max_length=255,
            examples=["Archive/Projects/Old", "Completed/2023", "Projects/Sorted"],
        ),
    ],
    update_links: Annotated[
        bool,
        Field(
            description="Whether to update links in other notes (future enhancement)",
            default=True,
        ),
    ] = True,
    ctx: Context | None = None,
):
    """
    Move an entire folder and all its contents to a new location.

    When to use:
    - Reorganizing vault structure
    - Archiving completed projects
    - Consolidating related notes
    - Seasonal organization (e.g., moving to year-based archives)

    When NOT to use:
    - Moving individual notes (use move_note instead)
    - Moving to a subfolder of the source (creates circular reference)

    Returns:
        Move status with count of notes and folders moved
    """
    try:
        return await move_folder(source_folder, destination_folder, update_links, ctx)
    except (ValueError, FileNotFoundError) as e:
        raise ToolError(str(e))
    except Exception as e:
        raise ToolError(f"Failed to move folder: {e!s}")
