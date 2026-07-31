"""Batch frontmatter property updates for Obsidian MCP server.

batch_update_properties stays in this file because it calls get_vault()
directly, and get_vault() always resolves against the globals of the module
a function is *defined* in -- moving it elsewhere (even with a facade
re-export) would silently break any future
`patch("obsidian_mcp.tools.batch_properties.get_vault")`.

The per-note helpers moved to batch_properties_helpers.py (none of them call
get_vault()) to bring this file back under the project's 350-line limit --
see that module's docstring. _update_frontmatter_properties is re-imported
here (and listed in __all__) purely so
`from .batch_properties import _update_frontmatter_properties` in
organization.py keeps resolving.
"""

from ..utils.filesystem import get_vault
from .batch_properties_helpers import (
    _coerce_tag_list,
    _process_batch_note,
    _resolve_notes_to_update,
    _update_frontmatter_properties,
    _validate_batch_search_criteria,
)
from .tag_editing import _clean_tags
from .write_policy import _serialize_note_writes

__all__ = [
    "_update_frontmatter_properties",
    "batch_update_properties",
]


@_serialize_note_writes
async def batch_update_properties(
    search_criteria: dict,
    property_updates: dict | None = None,
    properties_to_remove: list[str] | None = None,
    add_tags: list[str] | str | None = None,
    remove_tags: list[str] | str | None = None,
    remove_inline_tags: bool = False,
    ctx=None,
) -> dict:
    """
    Batch update properties across multiple notes.

    Args:
        search_criteria: How to find notes - dict with one of:
            - 'query': Search query string
            - 'folder': Folder path to process
            - 'files': Explicit list of file paths
        property_updates: Dict of properties to add/update
        properties_to_remove: List of property names to remove
        add_tags: List of tags to add (additive). A list or a JSON-encoded
            list string (e.g. '["archived", "2024"]').
        remove_tags: List of tags to remove. A list or a JSON-encoded list
            string (e.g. '["draft", "todo"]').
        remove_inline_tags: Whether to also remove tags from note body
        ctx: MCP context for progress reporting

    Returns:
        Dict with operation results and affected files
    """
    add_tags = _coerce_tag_list(add_tags, "add_tags")
    remove_tags = _coerce_tag_list(remove_tags, "remove_tags")

    vault = get_vault()

    # Normalize the same way add_tags/update_tags/remove_tags do (strip '#'
    # prefix; kebab-normalize under OBSIDIAN_TAG_STYLE=kebab) so this path
    # and the direct tag tools agree on what ends up stored in frontmatter.
    if add_tags:
        add_tags = _clean_tags(vault, add_tags)
    if remove_tags:
        remove_tags = _clean_tags(vault, remove_tags)

    _validate_batch_search_criteria(search_criteria)

    # Validate that at least one operation is specified
    if not any([property_updates, properties_to_remove, add_tags, remove_tags]):
        raise ValueError(
            "No operations specified. Provide at least one of: "
            "property_updates (to add/update properties), "
            "properties_to_remove (to delete properties), "
            "add_tags (to add tags), or "
            "remove_tags (to remove tags)"
        )

    notes_to_update = await _resolve_notes_to_update(vault, search_criteria, ctx)

    if ctx:
        await ctx.info(f"Found {len(notes_to_update)} notes to update")

    results = {
        "total_notes": len(notes_to_update),
        "updated": 0,
        "failed": 0,
        "details": [],
        "errors": [],
    }

    for note_path in notes_to_update:
        try:
            update = await _process_batch_note(
                vault,
                note_path,
                property_updates,
                properties_to_remove,
                add_tags,
                remove_tags,
                remove_inline_tags,
            )
            if update is not None:
                results["updated"] += 1
                results["details"].append(update)

            if ctx and results["updated"] % 10 == 0:
                await ctx.info(f"Updated {results['updated']} notes...")

        except Exception as e:
            results["errors"].append({"path": note_path, "error": str(e)})
            results["failed"] += 1

    if ctx:
        await ctx.info(
            f"Batch update complete: {results['updated']} updated, {results['failed']} failed"
        )

    return results
