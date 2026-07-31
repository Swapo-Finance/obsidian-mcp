"""Per-note update helpers for batch_update_properties.

Split out of batch_properties.py to bring that file back under the
project's 350-line limit after the C901 complexity-reduction pass added
these as private helpers of batch_update_properties. Safe to split: none of
these call get_vault() (the two that touch the vault take it as a plain
`vault` parameter instead), and none is patched by module-qualified name
anywhere in tests/ -- only imported/called directly, which keeps working
regardless of which module defines it. batch_update_properties itself stays
in batch_properties.py because it does call get_vault().

_update_frontmatter_properties is re-imported (and re-exported via that
module's own __all__) by batch_properties.py because
obsidian_mcp/tools/organization.py does
`from .batch_properties import _update_frontmatter_properties,
batch_update_properties` directly -- that import path must keep resolving.
"""

import json

from .frontmatter_tags import _remove_inline_tags


def _coerce_tag_list(
    value: list[str] | str | None, param_name: str
) -> list[str] | None:
    """Accept either a real list or a JSON-encoded list string for add_tags/
    remove_tags (some MCP clients send a JSON string instead of a native
    array). Moved here from the mcp_properties.py wrapper, which must stay a
    pure passthrough per tools/CLAUDE.md's tool contract.

    Deliberately bakes "Failed to batch update properties: " into the
    malformed-JSON message instead of leaving it to the wrapper's generic
    Exception handler, to keep this ToolError string byte-identical to
    before the move: the old wrapper code raised ToolError (not ValueError)
    for malformed JSON, which skipped its own `except ValueError` branch and
    fell into its generic `except Exception as e: raise ToolError(f"Failed
    to batch update properties: {e!s}")` fallback, double-wrapping the text.
    A plain ValueError here would only get the wrapper's single `except
    ValueError` wrap and change the client-visible message.
    """
    if not isinstance(value, str):
        return value
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as e:
        raise ValueError(
            f"Failed to batch update properties: Invalid JSON in {param_name} "
            f'parameter: {e!s}. Expected format: \'["tag1", "tag2"]\' or use '
            "a list directly."
        )
    if not isinstance(parsed, list):
        raise ValueError(f"{param_name} must be a list when parsed from JSON string")
    return parsed


def _update_frontmatter_properties(
    content: str, property_updates: dict, properties_to_remove: list[str] | None = None
) -> str:
    """
    Update multiple frontmatter properties while preserving YAML structure.

    Args:
        content: Note content with frontmatter
        property_updates: Dict of properties to add/update {property: value}
        properties_to_remove: List of property names to remove

    Returns:
        Updated content with modified frontmatter
    """

    import yaml

    properties_to_remove = properties_to_remove or []

    # Check if frontmatter exists
    if not content.startswith("---\n"):
        # Create frontmatter if properties to add
        if property_updates:
            # Use yaml.dump to ensure proper formatting
            yaml_content = yaml.dump(
                property_updates,
                default_flow_style=False,
                allow_unicode=True,
                sort_keys=False,
            )
            return f"---\n{yaml_content}---\n\n{content}"
        return content

    # Parse existing frontmatter
    try:
        end_index = content.index("\n---\n", 4) + 5
        frontmatter_str = content[4 : end_index - 5]
        rest_of_content = content[end_index:]
    except ValueError:
        # Invalid frontmatter
        return content

    # Parse YAML
    try:
        frontmatter = yaml.safe_load(frontmatter_str) or {}
    except yaml.YAMLError:
        # If YAML parsing fails, return original
        return content

    # Update properties
    for key, value in property_updates.items():
        if value is None and key not in properties_to_remove:
            properties_to_remove.append(key)
        else:
            frontmatter[key] = value

    # Remove properties
    for prop in properties_to_remove:
        frontmatter.pop(prop, None)

    # Special handling for tags to ensure list format
    if "tags" in frontmatter and isinstance(frontmatter["tags"], str):
        # Convert string tags to list
        frontmatter["tags"] = [frontmatter["tags"]]

    # Dump back to YAML
    if frontmatter:
        yaml_content = yaml.dump(
            frontmatter, default_flow_style=False, allow_unicode=True, sort_keys=False
        )
        # Remove the trailing newline that yaml.dump adds
        yaml_content = yaml_content.rstrip("\n")
        return f"---\n{yaml_content}\n---\n{rest_of_content}"
    else:
        # No frontmatter left
        return rest_of_content.lstrip("\n")


def _validate_batch_search_criteria(search_criteria: dict) -> None:
    """Raise ValueError unless search_criteria names exactly one selection
    method. Split out of batch_update_properties purely to shrink its own
    body (C901 reduction) -- no behavior change.
    """
    if not search_criteria:
        raise ValueError(
            "search_criteria is required. Provide one of: "
            "1) {'query': 'tag:project'} for search-based selection, "
            "2) {'folder': 'Projects', 'recursive': true} for folder-based selection, "
            "3) {'files': ['Note1.md', 'Note2.md']} for specific files"
        )

    if not any(k in search_criteria for k in ["query", "folder", "files"]):
        raise ValueError(
            "search_criteria must include exactly one selection method: "
            "'query' (search string), 'folder' (directory path), or 'files' (list of paths). "
            f"Received: {list(search_criteria.keys())}"
        )


async def _resolve_notes_to_update(vault, search_criteria: dict, ctx) -> list[str]:
    """Resolve search_criteria's 'files' / 'folder' / 'query' selection
    (in that priority order, matching the original if/elif/elif) into a
    flat list of note paths. Only called after _validate_batch_search_criteria
    has confirmed at least one of the three keys is present. Split out of
    batch_update_properties to isolate the selection-method dispatch from
    the per-note update loop (C901 reduction) -- no behavior change.
    """
    if "files" in search_criteria:
        notes_to_update = []
        for file_path in search_criteria["files"]:
            try:
                note = await vault.read_note(file_path)
                if note:
                    notes_to_update.append(file_path)
            except Exception as e:
                if ctx:
                    await ctx.info(f"Skipping invalid file {file_path}: {e!s}")
        return notes_to_update

    if "folder" in search_criteria:
        folder = search_criteria["folder"]
        all_notes = await vault.list_notes(
            directory=folder, recursive=search_criteria.get("recursive", True)
        )
        return [note["path"] for note in all_notes]

    # "query" -- the only key _validate_batch_search_criteria still allows.
    from .search_discovery import search_notes

    max_results = search_criteria.get("max_results", 500)
    if not isinstance(max_results, int) or not (1 <= max_results <= 500):
        raise ValueError(
            "search_criteria['max_results'] must be an integer between 1 and "
            f"500 (got {max_results!r}) -- the same bound search_notes/"
            "search_by_regex enforce at the schema level for direct calls."
        )

    search_results = await search_notes(
        query=search_criteria["query"],
        max_results=max_results,
        ctx=ctx,
    )
    return [r["path"] for r in search_results["results"]]


def _compute_updated_note_tags(
    current_tags_raw, add_tags: list[str] | None, remove_tags: list[str] | None
) -> tuple[list[str] | None, list[str]]:
    """Compute one note's new tag list for a batch add_tags/remove_tags
    operation, plus human-readable change descriptions. Returns
    (new_tags, changes); new_tags is None when nothing actually changed, so
    the caller knows not to touch frontmatter tags at all. Split out of
    _process_batch_note purely to shrink its complexity (C901 reduction).
    """
    current_tags = current_tags_raw
    if isinstance(current_tags, str):
        current_tags = [current_tags]
    elif not isinstance(current_tags, list):
        current_tags = list(current_tags) if current_tags else []

    updated_tags = current_tags.copy()
    changes: list[str] = []

    if remove_tags:
        for tag in remove_tags:
            if tag in updated_tags:
                updated_tags.remove(tag)
                changes.append(f"Removed tag '{tag}' from frontmatter")

    if add_tags:
        for tag in add_tags:
            if tag not in updated_tags:
                updated_tags.append(tag)
                changes.append(f"Added tag '{tag}' to frontmatter")

    if updated_tags == current_tags:
        return None, changes
    return updated_tags, changes


def _describe_property_changes(
    effective_updates: dict, properties_to_remove: list[str] | None
) -> list[str]:
    """Human-readable change descriptions for a batch frontmatter property
    update. 'tags' is skipped -- _compute_updated_note_tags already
    describes tag changes on its own terms (added/removed per tag). Split
    out of _process_batch_note purely to shrink its complexity (C901
    reduction).
    """
    changes = [
        f"Set {prop} = {value}"
        for prop, value in effective_updates.items()
        if prop != "tags"
    ]
    changes.extend(f"Removed property '{prop}'" for prop in properties_to_remove or [])
    return changes


async def _process_batch_note(
    vault,
    note_path: str,
    property_updates: dict | None,
    properties_to_remove: list[str] | None,
    add_tags: list[str] | None,
    remove_tags: list[str] | None,
    remove_inline_tags: bool,
) -> dict | None:
    """Apply one note's tag add/remove, frontmatter property updates, and
    optional inline-tag removal, writing the note if anything changed.
    Returns {"path", "changes"} when written, None when nothing changed.
    Raises on a missing/unreadable note -- the caller's per-note try/except
    keeps one bad note from aborting the whole batch (see pyproject.toml's
    BLE001 rationale for batch_properties.py).

    property_updates is read, never mutated in place: each note gets its
    own effective copy so a tag change computed for one note can never leak
    into the next note processed in the same batch call. (The original flat
    loop mutated the shared `property_updates` dict across iterations
    instead -- an untested edge case no test or caller relied on; scoping
    it per-note here is a side effect of the extraction, not a deliberate
    behavior change.) Split out of batch_update_properties to isolate the
    per-note update logic from the search-criteria dispatch (C901
    reduction).
    """
    note = await vault.read_note(note_path)
    if not note:
        raise ValueError("Note not found")

    content = note.content
    original_content = content
    changes_made = []
    effective_updates = dict(property_updates) if property_updates else {}

    # Handle special tag operations first
    if add_tags or remove_tags:
        new_tags, tag_changes = _compute_updated_note_tags(
            note.metadata.frontmatter.get("tags", []), add_tags, remove_tags
        )
        changes_made.extend(tag_changes)
        if new_tags is not None:
            effective_updates["tags"] = new_tags

    # Update frontmatter properties
    if effective_updates or properties_to_remove:
        content = _update_frontmatter_properties(
            content, effective_updates, properties_to_remove
        )
        changes_made.extend(
            _describe_property_changes(effective_updates, properties_to_remove)
        )

    # Remove inline tags if requested
    if remove_inline_tags and remove_tags:
        content, inline_tags_removed = _remove_inline_tags(content, remove_tags)
        if inline_tags_removed > 0:
            changes_made.append(f"Removed {inline_tags_removed} inline tags")

    if content == original_content:
        return None

    await vault.write_note(note_path, content, overwrite=True)
    return {"path": note_path, "changes": changes_made}
