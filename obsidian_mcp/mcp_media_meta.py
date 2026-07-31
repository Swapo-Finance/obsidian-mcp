"""Image, template, help, and daily-note tool wrappers."""

from typing import Annotated

from fastmcp import Context
from fastmcp.exceptions import ToolError
from pydantic import Field

from .app import mcp
from .tools import (
    add_daily_note,
    get_help,
    get_note_template,
    read_image,
    view_note_images,
)


@mcp.tool()
async def read_image_tool(
    path: Annotated[
        str,
        Field(
            description="Path to the image file relative to vault root",
            pattern=r"^[^/].*\.(png|jpg|jpeg|gif|webp|svg|bmp|ico)$",
            min_length=1,
            max_length=255,
            examples=[
                "attachments/screenshot.png",
                "images/diagram.jpg",
                "media/logo.svg",
            ],
        ),
    ],
    include_metadata: Annotated[
        bool,
        Field(
            description="Include file size and other metadata about the image",
            default=False,
        ),
    ] = False,
    ctx: Context | None = None,
):
    """
    Read an image file from the Obsidian vault for analysis.

    When to use:
    - Analyzing specific image files from the vault
    - Examining standalone images (not embedded in notes)
    - Processing images for detailed analysis

    When NOT to use:
    - Getting images embedded in notes (use view_note_images instead)
    - Searching for images (use list_notes with appropriate filters)

    Returns:
        Image object that Claude can analyze and describe
    """
    try:
        return await read_image(path, include_metadata=include_metadata, ctx=ctx)
    except (ValueError, FileNotFoundError) as e:
        raise ToolError(str(e))
    except Exception as e:
        raise ToolError(f"Failed to read image: {e!s}")


@mcp.tool()
async def view_note_images_tool(
    path: Annotated[
        str,
        Field(
            description="Path to the note containing images",
            pattern=r"^[^/].*\.md$",
            min_length=1,
            max_length=255,
            examples=["Projects/Design.md", "Daily/2024-01-15.md", "Ideas/Mockups.md"],
        ),
    ],
    image_index: Annotated[
        int | None,
        Field(
            description="Get only the Nth image from the note (0 = first image). Leave empty to get all images.",
            default=None,
            ge=0,
        ),
    ] = None,
    max_width: Annotated[
        int,
        Field(
            description="Resize images wider than this to save memory. Images smaller than this are unchanged.",
            default=800,
            gt=0,
            le=4096,
        ),
    ] = 800,
    ctx: Context | None = None,
):
    """
    Extract and analyze images embedded in a note.

    When to use:
    - Analyzing images referenced in a note's markdown content
    - Examining visual content within notes (screenshots, diagrams, etc.)
    - Extracting specific images from notes for analysis

    When NOT to use:
    - Reading standalone image files (use read_image instead)
    - Getting note content without images (use read_note instead)

    Returns:
        List of Image objects that Claude can analyze and describe
    """
    try:
        return await view_note_images(path, image_index, max_width, ctx)
    except (ValueError, FileNotFoundError) as e:
        raise ToolError(str(e))
    except Exception as e:
        raise ToolError(f"Failed to view note images: {e!s}")


@mcp.tool()
async def get_note_template_tool(
    path: Annotated[
        str,
        Field(
            description="A note path (e.g. '01-projects/Foo.md') or a folder path (e.g. '01-projects') "
            "to look up the OBSIDIAN_FOLDER_TEMPLATES rule for. Only the folder matters.",
            min_length=0,
            max_length=255,
            examples=["01-projects", "01-projects/Foo.md", ""],
        ),
    ] = "",
    ctx: Context | None = None,
):
    """
    Show the template rule (if any) that applies to a note or folder.

    When to use:
    - Before create_note/update_note in a folder you suspect is enforced
    - Right after a template-conformance ToolError, to get the exact
      skeleton/required headings for a retry
    - Exploring which folders have a template configured

    When NOT to use:
    - Reading an existing note's content (use read_note instead)

    Returns:
        {enforced, folder_rule, template_path, required_headings,
         required_frontmatter_keys, skeleton, instructions}.
        enforced=false means the folder is free-form.
    """
    try:
        return await get_note_template(path, ctx)
    except ValueError as e:
        raise ToolError(str(e))
    except Exception as e:
        raise ToolError(f"Failed to get note template: {e!s}")


@mcp.tool()
async def help_tool(ctx: Context | None = None):
    """
    Catalog of every env var (with its current effective value), the 3
    accepted forms for path-shaped config, and a one-line index of all
    tools — without the token cost of the full tools/list schema.

    When to use:
    - Unsure which OBSIDIAN_* env var controls a behavior, or its current
      effective value
    - Unsure how a folder/template/daily-dir path you're about to configure
      will be resolved
    - Looking for which tool covers something you haven't used yet

    When NOT to use:
    - Detailed parameter schemas for a specific tool (already in tools/list)

    Returns:
        {env_vars: [...], path_anchoring: str, tools: [{name, purpose}, ...]}
    """
    try:
        return await get_help(ctx)
    except Exception as e:
        raise ToolError(f"Failed to build help catalog: {e!s}")


@mcp.tool()
async def add_daily_note_tool(
    content: Annotated[
        str,
        Field(
            description="Markdown to append to the end of today's (or the given date's) daily note.",
            min_length=1,
            max_length=1000000,
            examples=[
                "## 14:30\n\nShipped the auth refactor.",
                "- Talked to [[Jane Doe]] about Q3 planning",
            ],
        ),
    ],
    date: Annotated[
        str | None,
        Field(
            description="Optional ISO date (YYYY-MM-DD) to target a specific day instead of today. "
            "Does not create/backfill other days in between.",
            default=None,
            pattern=r"^\d{4}-\d{2}-\d{2}$",
            examples=["2025-01-15"],
        ),
    ] = None,
    ctx: Context | None = None,
):
    """
    Append to today's daily note, creating it (from the daily-dir's template,
    if one is configured) if it doesn't exist yet.

    When to use:
    - Journaling / logging a quick update without looking up or reading the
      daily note's path first
    - Any time you'd otherwise do read_note + create_note/update_note on a
      note under OBSIDIAN_DAILY_DIR

    When NOT to use:
    - Editing a specific section of the daily note (use edit_note_section
      after reading it — this tool only appends at the end)
    - Non-daily notes (use create_note/update_note instead)

    Returns:
        {path, created, appended: true}. Daily notes are always exempt from
        the note-size policy, and old daily notes are never deleted by this
        tool.
    """
    try:
        return await add_daily_note(content, date, ctx)
    except (ValueError, FileNotFoundError) as e:
        raise ToolError(str(e))
    except Exception as e:
        raise ToolError(f"Failed to add daily note: {e!s}")
