"""Main entry point for Obsidian MCP server.

The FastMCP instance and boot sequence live in `app.py`. The ~30 `@mcp.tool()`
wrappers are registered as a side effect of importing the `mcp_*` modules
below, grouped to mirror `tools/`. Re-exporting everything here keeps
`obsidian_mcp.server:main` (the packaged console-script entry point) and
`python -m obsidian_mcp.server` working unchanged.
"""

from .app import main, mcp
from .mcp_discovery import (
    get_note_info_tool,
    list_folders_tool,
    list_notes_tool,
    search_by_property_tool,
)
from .mcp_links import (
    find_broken_links_tool,
    find_orphaned_notes_tool,
    get_backlinks_tool,
    get_outgoing_links_tool,
)
from .mcp_media_meta import (
    add_daily_note_tool,
    get_note_template_tool,
    help_tool,
    read_image_tool,
    view_note_images_tool,
)
from .mcp_notes import (
    create_note_tool,
    delete_note_tool,
    edit_note_section_tool,
    read_note_tool,
    update_note_tool,
)
from .mcp_organization import (
    create_folder_tool,
    move_folder_tool,
    move_note_tool,
    rename_note_tool,
)
from .mcp_properties import batch_update_properties_tool
from .mcp_search import (
    search_by_date_tool,
    search_by_regex_tool,
    search_notes_tool,
)
from .mcp_tags import (
    add_tags_tool,
    list_tags_tool,
    remove_tags_tool,
    update_tags_tool,
)

__all__ = [
    "add_daily_note_tool",
    "add_tags_tool",
    "batch_update_properties_tool",
    "create_folder_tool",
    "create_note_tool",
    "delete_note_tool",
    "edit_note_section_tool",
    "find_broken_links_tool",
    "find_orphaned_notes_tool",
    "get_backlinks_tool",
    "get_note_info_tool",
    "get_note_template_tool",
    "get_outgoing_links_tool",
    "help_tool",
    "list_folders_tool",
    "list_notes_tool",
    "list_tags_tool",
    "main",
    "mcp",
    "move_folder_tool",
    "move_note_tool",
    "read_image_tool",
    "read_note_tool",
    "remove_tags_tool",
    "rename_note_tool",
    "search_by_date_tool",
    "search_by_property_tool",
    "search_by_regex_tool",
    "search_notes_tool",
    "update_note_tool",
    "update_tags_tool",
    "view_note_images_tool",
]


if __name__ == "__main__":
    main()
