"""Note reading: read_note, with pages and section reads for long notes."""

from typing import Any

from fastmcp import Context

from ..constants import ERROR_MESSAGES, READ_TRUNCATED_MESSAGE
from ..utils import sanitize_path, validate_note_path
from ..utils.filesystem import get_vault
from ..utils.validation import validate_read_window
from .section_editing import _find_section_bounds, _parse_heading, _section_end

# ponytail: fixed 50-entry outline ceiling. It drops the deepest heading level
# first, then keeps the first 50 entries. Add a parameter if agents need more.
OUTLINE_MAX_ENTRIES = 50


def _line_starts(lines: list[str], text_length: int) -> list[int]:
    """Offset where each line starts, plus text_length as the last entry, so
    starts[len(lines)] is the end of the text."""
    starts = [0]
    for line in lines:
        starts.append(starts[-1] + len(line) + 1)
    starts[-1] = text_length
    return starts


def _select_section(text: str, section: str, path: str) -> str:
    """Text of the first section that matches `section` under the rules of
    edit_note_section: from its heading line to the next heading of the same
    or a higher level, sub-sections included."""
    heading = _parse_heading(section)
    lines = text.split("\n")
    bounds = None if heading is None else _find_section_bounds(lines, *heading)
    if bounds is None:
        raise ValueError(
            ERROR_MESSAGES["section_not_found"].format(section=section, path=path)
        )
    starts = _line_starts(lines, len(text))
    return text[starts[bounds[0]] : starts[bounds[1]]]


def _cap_outline(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop the deepest heading level until at most OUTLINE_MAX_ENTRIES
    remain. When one level is left, keep its first OUTLINE_MAX_ENTRIES."""
    while len(entries) > OUTLINE_MAX_ENTRIES:
        deepest = max(entry["level"] for entry in entries)
        shallower = [entry for entry in entries if entry["level"] < deepest]
        if not shallower:
            return entries[:OUTLINE_MAX_ENTRIES]
        entries = shallower
    return entries


def _build_outline(text: str) -> list[dict[str, Any]]:
    """{heading, level, offset, chars} for each heading in text. offset is the
    start of the heading line; chars runs to the next heading of the same or a
    higher level, or to the end of text."""
    lines = text.split("\n")
    starts = _line_starts(lines, len(text))
    entries: list[dict[str, Any]] = []
    for index, line in enumerate(lines):
        heading = _parse_heading(line)
        if heading is None:
            continue
        level, title = heading
        end = _section_end(lines, index, level)
        entries.append(
            {
                "heading": title,
                "level": level,
                "offset": starts[index],
                "chars": starts[end] - starts[index],
            }
        )
    return _cap_outline(entries)


def _page_end(text: str, offset: int, max_chars: int) -> int:
    """End (exclusive) of the page that starts at offset: the end of text when
    the rest fits; else just after the last newline in the window; else a hard
    cut at offset + max_chars. Always greater than offset."""
    window_end = offset + max_chars
    if window_end >= len(text):
        return len(text)
    newline = text.rfind("\n", offset, window_end)
    return window_end if newline == -1 else newline + 1


async def read_note(
    path: str,
    offset: int = 0,
    max_chars: int | None = None,
    section: str | None = None,
    ctx: Context | None = None,
) -> dict:
    """
    Read the content and metadata of a specific note.

    Use this tool when you need to retrieve the content of a note from the
    Obsidian vault. The path should be relative to the vault root. A note
    longer than the page size comes back in pages.

    To view images embedded in a note, use the view_note_images tool.

    Args:
        path: Path to the note relative to vault root (e.g., "Daily/2024-01-15.md")
        offset: Character offset where the page starts (0 = start of the text)
        max_chars: Page size; None uses OBSIDIAN_READ_MAX_CHARS
        section: Markdown heading (e.g., "## Tasks"); read only that section,
            matched like edit_note_section
        ctx: MCP context for progress reporting

    Returns:
        {success, path, operation, details: {content, metadata}} when the text
        fits in one page from offset 0. Else details also holds truncated,
        next_offset, total_chars, outline, and (when text remains) message --
        see RESPONSE_STRUCTURES["read_page"].

    Example:
        >>> await read_note("Projects/My Project.md", ctx=ctx)
        {
            "success": True,
            "path": "Projects/My Project.md",
            "operation": "read",
            "details": {
                "content": "# My Project\n\n![diagram](attachments/diagram.png)\n\nProject details...",
                "metadata": {
                    "tags": ["project", "active"],
                    "created": "2024-01-15T10:00:00Z",
                    "modified": "2024-01-15T14:30:00Z"
                }
            }
        }
    """
    # Validate path
    is_valid, error_msg = validate_note_path(path)
    if not is_valid:
        raise ValueError(f"Invalid path: {error_msg}")

    is_valid, error_msg = validate_read_window(offset, max_chars)
    if not is_valid:
        raise ValueError(error_msg)

    # Sanitize path
    path = sanitize_path(path)

    if ctx:
        await ctx.info(f"Reading note: {path}")

    vault = get_vault()
    try:
        note = await vault.read_note(path)
    except FileNotFoundError:
        raise FileNotFoundError(ERROR_MESSAGES["note_not_found"].format(path=path))

    text = (
        note.content
        if section is None
        else _select_section(note.content, section, path)
    )
    page_size = vault.read_max_chars if max_chars is None else max_chars
    metadata = note.metadata.model_dump(exclude_none=True)

    # A text that fits from offset 0 keeps today's response, byte for byte.
    if offset == 0 and len(text) <= page_size:
        return {
            "success": True,
            "path": note.path,
            "operation": "read",
            "details": {"content": text, "metadata": metadata},
        }

    if offset >= len(text):
        raise ValueError(
            ERROR_MESSAGES["read_offset_out_of_range"].format(
                offset=offset, total_chars=len(text), path=path
            )
        )

    end = _page_end(text, offset, page_size)
    truncated = end < len(text)
    details: dict[str, Any] = {
        "content": text[offset:end],
        "metadata": metadata,
        "truncated": truncated,
        "next_offset": end if truncated else None,
        "total_chars": len(text),
        "outline": _build_outline(text),
    }
    if truncated:
        details["message"] = READ_TRUNCATED_MESSAGE.format(
            start=offset, next_offset=end, total_chars=len(text)
        )
    return {
        "success": True,
        "path": note.path,
        "operation": "read",
        "details": details,
    }
