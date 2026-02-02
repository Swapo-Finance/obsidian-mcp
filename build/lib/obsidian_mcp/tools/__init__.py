"""Tool modules for Obsidian MCP server."""

from .note_management import (
    read_note,
    create_note,
    update_note,
    edit_note_section,
    delete_note,
)
from .search_discovery import (
    search_notes,
    search_by_date,
    search_by_regex,
    search_by_property,
    list_notes,
    list_folders,
)
from .organization import (
    move_note,
    rename_note,
    create_folder,
    move_folder,
    add_tags,
    update_tags,
    remove_tags,
    get_note_info,
    list_tags,
    batch_update_properties,
)
from .link_management import (
    get_backlinks,
    get_outgoing_links,
    find_broken_links,
)
from .find_orphaned_notes import (
    find_orphaned_notes,
)
from .image_management import (
    read_image,
)
from .view_note_images import (
    view_note_images,
)

__all__ = [
    # Note management
    "read_note",
    "create_note", 
    "update_note",
    "edit_note_section",
    "delete_note",
    # Search and discovery
    "search_notes",
    "search_by_date",
    "search_by_regex",
    "search_by_property",
    "list_notes",
    "list_folders",
    # Organization
    "move_note",
    "rename_note",
    "create_folder",
    "move_folder",
    "add_tags",
    "update_tags",
    "remove_tags",
    "get_note_info",
    "list_tags",
    "batch_update_properties",
    # Link management
    "get_backlinks",
    "get_outgoing_links",
    "find_broken_links",
    "find_orphaned_notes",
    # Image management
    "read_image",
    "view_note_images",
]