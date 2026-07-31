"""Section-level note editing: edit_note_section."""

import re
import unicodedata
from typing import Any

from fastmcp import Context

from ..constants import ERROR_MESSAGES
from ..utils import sanitize_path, validate_note_path
from ..utils.filesystem import get_vault
from .link_management import validate_wikilinks_for_write
from .write_policy import _serialize_note_writes, _size_policy_warning


def _find_section_bounds(
    lines: list[str], heading_level: int, heading_text: str
) -> tuple[int, int] | None:
    """Locate the first heading (case-insensitive, NFC-normalized) matching
    heading_level/heading_text -- first match wins when a heading repeats.

    Returns (section_start, section_end): section_end is the index of the
    next heading of the same or higher level, or len(lines) if the section
    runs to end of file. Returns None if no heading matches.
    """
    normalized_target = unicodedata.normalize("NFC", heading_text.lower())
    for i, line in enumerate(lines):
        line_match = re.match(r"^(#{1,6})\s+(.+)$", line)
        if not line_match:
            continue
        line_level = len(line_match.group(1))
        if line_level != heading_level:
            continue
        line_text = line_match.group(2).strip()
        if unicodedata.normalize("NFC", line_text.lower()) != normalized_target:
            continue

        section_end = len(lines)
        for j in range(i + 1, len(lines)):
            next_match = re.match(r"^(#{1,6})\s+", lines[j])
            if next_match and len(next_match.group(1)) <= heading_level:
                section_end = j
                break
        return i, section_end

    return None


def _apply_insert_after(lines: list[str], section_start: int, content: str) -> None:
    """Insert content right after the heading, always separated from it by
    exactly one blank line."""
    insert_pos = section_start + 1
    if insert_pos >= len(lines) or lines[insert_pos].strip():
        lines.insert(insert_pos, "")
    lines.insert(insert_pos + 1, content)


def _apply_insert_before(lines: list[str], section_start: int, content: str) -> None:
    """Insert content right before the heading, padding blank lines on both
    sides so the heading stays visually separated."""
    insert_pos = section_start
    if insert_pos > 0 and lines[insert_pos - 1].strip():
        lines.insert(insert_pos, "")
        insert_pos += 1
    lines.insert(insert_pos, content)
    lines.insert(insert_pos + 1, "")


def _apply_replace(
    lines: list[str], section_start: int, section_end: int, content: str
) -> None:
    """Replace the entire section (heading included) with content."""
    del lines[section_start:section_end]
    lines.insert(section_start, content)


def _apply_append_to_section(
    lines: list[str], section_start: int, section_end: int, content: str
) -> None:
    """Append content at the end of the section, before the next heading (or
    EOF), skipping back over trailing blank lines first."""
    insert_pos = section_end
    while insert_pos > section_start + 1 and not lines[insert_pos - 1].strip():
        insert_pos -= 1

    if insert_pos > 0 and lines[insert_pos - 1].strip():
        lines.insert(insert_pos, "")
        insert_pos += 1
    lines.insert(insert_pos, content)


@_serialize_note_writes
async def edit_note_section(
    path: str,
    section_identifier: str,
    content: str,
    operation: str = "insert_after",
    create_if_missing: bool = False,
    ctx: Context | None = None,
) -> dict:
    """
    Edit a specific section of a note.

    Use this tool to insert, replace, or append content at specific sections
    identified by markdown headings.

    Args:
        path: Path to the note to edit
        section_identifier: Markdown heading to identify the section (e.g., "## Tasks", "### Status")
        content: Content to insert/replace/append
        operation: One of "insert_after", "insert_before", "replace", "append_to_section"
        create_if_missing: Create the section at the end if it doesn't exist
        ctx: MCP context for progress reporting

    Returns:
        Dictionary containing edit status and details

    Example:
        >>> await edit_note_section(
        ...     "Projects/Project.md",
        ...     "## Status Updates",
        ...     "- 2024-01-15: Completed phase 1",
        ...     operation="append_to_section"
        ... )
        {
            "success": true,
            "path": "Projects/Project.md",
            "operation": "section_edit",
            "section": "## Status Updates",
            "edit_type": "append_to_section",
            "section_found": true,
            "section_created": false
        }
    """
    # Validate path
    is_valid, error_msg = validate_note_path(path)
    if not is_valid:
        raise ValueError(f"Invalid path: {error_msg}")

    # Validate operation
    valid_operations = ["insert_after", "insert_before", "replace", "append_to_section"]
    if operation not in valid_operations:
        raise ValueError(
            f"Invalid operation: {operation}. Must be one of {valid_operations}"
        )

    # Sanitize path
    path = sanitize_path(path)

    if ctx:
        await ctx.info(f"Editing section '{section_identifier}' in: {path}")

    vault = get_vault()

    # Read existing note
    try:
        existing_note = await vault.read_note(path)
        note_content = existing_note.content
    except FileNotFoundError:
        raise FileNotFoundError(ERROR_MESSAGES["note_not_found"].format(path=path))

    # Incremental edit — exempt from template conformance (spec section 3).
    # Wikilink validation runs against just the inserted fragment.
    content, warnings = await validate_wikilinks_for_write(vault, content)

    # Parse the section identifier to extract heading level and text
    heading_match = re.match(r"^(#{1,6})\s+(.+)$", section_identifier)
    if not heading_match:
        raise ValueError(
            f"Invalid section identifier: {section_identifier}. Must be a markdown heading (e.g., '## Section Name')"
        )

    heading_level = len(heading_match.group(1))
    heading_text = heading_match.group(2).strip()

    # Find the section in the content
    lines = note_content.split("\n")
    bounds = _find_section_bounds(lines, heading_level, heading_text)

    # Handle missing section
    if bounds is None:
        if not create_if_missing:
            raise ValueError(f"Section '{section_identifier}' not found in {path}")

        # Add section at the end
        if not note_content.endswith("\n"):
            note_content += "\n"
        note_content += f"\n{section_identifier}\n\n{content}"

        warnings = warnings + _size_policy_warning(
            vault, path, note_content, is_incremental=True
        )

        # Save the updated note
        await vault.write_note(path, note_content, overwrite=True)

        result: dict[str, Any] = {
            "success": True,
            "path": path,
            "operation": "section_edit",
            "section": section_identifier,
            "edit_type": operation,
            "section_found": False,
            "section_created": True,
        }
        if warnings:
            result["warnings"] = warnings
        return result

    section_start, section_end = bounds

    # Perform the requested operation
    if operation == "insert_after":
        _apply_insert_after(lines, section_start, content)
    elif operation == "insert_before":
        _apply_insert_before(lines, section_start, content)
    elif operation == "replace":
        _apply_replace(lines, section_start, section_end, content)
    elif operation == "append_to_section":
        _apply_append_to_section(lines, section_start, section_end, content)

    # Reconstruct the content
    new_content = "\n".join(lines)

    warnings = warnings + _size_policy_warning(
        vault, path, new_content, is_incremental=True
    )

    # Save the updated note
    await vault.write_note(path, new_content, overwrite=True)

    result: dict[str, Any] = {
        "success": True,
        "path": path,
        "operation": "section_edit",
        "section": section_identifier,
        "edit_type": operation,
        "section_found": True,
        "section_created": False,
    }
    if warnings:
        result["warnings"] = warnings
    return result
