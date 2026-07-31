"""Organization tools for Obsidian MCP server.

Pure re-export facade: the implementations that used to live here moved to
flat sibling modules (note_move, note_rename, folders, tag_editing,
frontmatter_tags, tag_listing, batch_properties, note_info) to keep this file
from growing further -- see tools/CLAUDE.md. Add new logic to the owning
module, not here. Every name below (including the private `_`-prefixed
helpers) is re-exported so anything that imported it from this module before
the split keeps resolving -- do not trim a name just because no current
caller is known to still use it through this path; callers move to direct
imports over time and this facade outlives any one of them.
"""

from .batch_properties import _update_frontmatter_properties, batch_update_properties
from .folders import create_folder, move_folder
from .frontmatter_tags import _remove_inline_tags, _update_frontmatter_tags
from .note_info import get_note_info
from .note_move import move_note
from .note_rename import rename_note
from .tag_editing import _clean_tags, add_tags, remove_tags, update_tags
from .tag_listing import _build_tag_item, list_tags

__all__ = [
    "_build_tag_item",
    "_clean_tags",
    "_remove_inline_tags",
    "_update_frontmatter_properties",
    "_update_frontmatter_tags",
    "add_tags",
    "batch_update_properties",
    "create_folder",
    "get_note_info",
    "list_tags",
    "move_folder",
    "move_note",
    "remove_tags",
    "rename_note",
    "update_tags",
]
