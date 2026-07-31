"""Tool modules for Obsidian MCP server."""

from .daily_notes import (
    add_daily_note,
)
from .find_orphaned_notes import (
    find_orphaned_notes,
)
from .image_management import (
    read_image,
)
from .link_management import (
    find_broken_links,
    get_backlinks,
    get_outgoing_links,
)
from .note_management import (
    create_note,
    delete_note,
    edit_note_section,
    read_note,
    update_note,
)
from .organization import (
    add_tags,
    batch_update_properties,
    create_folder,
    get_note_info,
    list_tags,
    move_folder,
    move_note,
    remove_tags,
    rename_note,
    update_tags,
)
from .search_discovery import (
    list_folders,
    list_notes,
    search_by_date,
    search_by_property,
    search_by_regex,
    search_notes,
)
from .vault_meta import (
    get_help,
    get_note_template,
)
from .view_note_images import (
    view_note_images,
)

# Grouped by feature area (mirrors the imports above) and ordered within each
# group by workflow/risk, not alphabetically — e.g. note management runs
# read -> create -> update -> edit_note_section -> delete, and tag ops run
# add -> update -> remove. isort-style sorting would destroy that structure.
__all__ = [  # noqa: RUF022
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
    # Vault metadata
    "get_note_template",
    "get_help",
    # Daily notes
    "add_daily_note",
]
