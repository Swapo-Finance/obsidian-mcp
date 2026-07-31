"""Find orphaned notes in the vault."""

import json
import logging
from datetime import datetime, timedelta
from typing import Any

from ..utils.filesystem import get_vault
from .link_management import get_backlinks, get_outgoing_links

logger = logging.getLogger(__name__)


def _coerce_exclude_folders(value: list[str] | str | None) -> list[str] | None:
    """Accept either a real list or a JSON-encoded list string for
    exclude_folders (some MCP clients send a JSON string instead of a native
    array). Moved here from the mcp_links.py wrapper, which must stay a pure
    passthrough per tools/CLAUDE.md's tool contract.

    Deliberately bakes "Failed to find orphaned notes: " into the
    malformed-JSON message instead of leaving it to the wrapper's generic
    Exception handler, to keep this ToolError string byte-identical to
    before the move: the old wrapper code raised ToolError (not ValueError)
    for malformed JSON, which skipped its own `except ValueError` branch and
    fell into its generic `except Exception as e: raise ToolError(f"Failed
    to find orphaned notes: {e!s}")` fallback, double-wrapping the text. A
    plain ValueError here would only get the wrapper's single `except
    ValueError` wrap and change the client-visible message.
    """
    if not isinstance(value, str):
        return value
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        raise ValueError(
            "Failed to find orphaned notes: Invalid JSON format for "
            'exclude_folders. Expected a JSON array like: ["Daily", "Templates"]'
        )
    if not isinstance(parsed, list):
        raise ValueError("exclude_folders must be a list")
    return parsed


def _parse_note_modified(mod_value: object) -> datetime:
    """Normalize a note's modified-time value to a naive datetime.

    NoteMetadata.modified is typed as a datetime, but callers in tests
    construct notes via MagicMock, so this stays defensive at runtime
    about receiving an ISO-8601 string (timezone-aware or naive) instead --
    hence the `object` parameter type rather than `datetime | str`. The
    result always has tzinfo stripped so it can be compared against
    find_orphaned_notes' date_threshold, which is naive (derived from
    datetime.now()) -- comparing a naive and an offset-aware datetime
    raises TypeError.
    """
    if isinstance(mod_value, datetime):
        mod_time = mod_value
    else:
        mod_str = str(mod_value)
        if mod_str.endswith("Z"):
            mod_str = mod_str[:-1] + "+00:00"
        try:
            # Try to parse with timezone
            mod_time = datetime.fromisoformat(mod_str)
        except ValueError:
            # If that fails, parse as naive and assume local timezone
            mod_time = datetime.fromisoformat(mod_str.split("+")[0].split(".")[0])

    # Make date comparison timezone-naive
    if mod_time.tzinfo is not None:
        mod_time = mod_time.replace(tzinfo=None)

    return mod_time


async def _get_link_state(path: str) -> tuple[list, list]:
    """Fetch a note's backlinks and outgoing links as findings lists.

    Shared by the no_links and isolated branches of find_orphaned_notes'
    orphan_type dispatch, which both need incoming + outgoing link state
    (unlike no_backlinks, which only ever needed backlinks and keeps its
    own single-fetch branch to avoid an unnecessary get_outgoing_links
    call for that type).
    """
    backlinks_result = await get_backlinks(path)
    backlinks = backlinks_result.get("findings", [])
    outgoing_result = await get_outgoing_links(path)
    outgoing = outgoing_result.get("findings", [])
    return backlinks, outgoing


async def find_orphaned_notes(
    orphan_type: str = "no_backlinks",
    exclude_folders: list[str] | str | None = None,
    min_age_days: int | None = None,
    ctx=None,
) -> dict[str, Any]:
    """
    Find orphaned notes based on specified criteria.

    Args:
        orphan_type: Type of orphaned notes to find
        exclude_folders: Folders to exclude from search. A list or a
            JSON-encoded list string (e.g. '["Daily", "Templates"]').
        min_age_days: Only include notes older than X days
        ctx: MCP context for progress reporting

    Returns:
        Dictionary containing orphaned notes and statistics
    """
    exclude_folders = _coerce_exclude_folders(exclude_folders)

    vault = get_vault()

    # Default exclusions if not specified
    if exclude_folders is None:
        exclude_folders = ["Templates", "Archive", "Daily"]

    if ctx:
        await ctx.info(f"Finding orphaned notes of type: {orphan_type}")

    # Get all notes
    all_notes = await vault.list_notes(recursive=True)
    orphaned_notes = []

    # Calculate date threshold if min_age_days is specified
    date_threshold = None
    if min_age_days is not None:
        date_threshold = datetime.now() - timedelta(days=min_age_days)

    # Process each note
    total_notes = len(all_notes)
    for i, note_info in enumerate(all_notes):
        path = note_info["path"]

        # Skip excluded folders
        if any(
            path.startswith((folder + "/", folder + "\\")) for folder in exclude_folders
        ):
            continue

        try:
            # Read note to get full information
            note = await vault.read_note(path)

            # Check age if threshold is set
            if date_threshold and note.metadata.modified:
                mod_time = _parse_note_modified(note.metadata.modified)
                if mod_time > date_threshold:
                    continue  # Skip recent notes

            # Check orphan criteria
            is_orphaned = False
            orphan_reason = ""

            if orphan_type == "no_backlinks":
                # Get backlinks for this note
                backlinks_result = await get_backlinks(path)
                backlinks = backlinks_result.get("findings", [])
                if not backlinks or len(backlinks) == 0:
                    is_orphaned = True
                    orphan_reason = "No incoming links"

            elif orphan_type == "no_links":
                # Check both incoming and outgoing links
                backlinks, outgoing = await _get_link_state(path)
                if (not backlinks or len(backlinks) == 0) and (
                    not outgoing or len(outgoing) == 0
                ):
                    is_orphaned = True
                    orphan_reason = "No incoming or outgoing links"

            elif orphan_type == "no_tags":
                # Check if note has any tags
                if not note.metadata.tags or len(note.metadata.tags) == 0:
                    is_orphaned = True
                    orphan_reason = "No tags"

            elif orphan_type == "no_metadata":
                # Check if note has any frontmatter properties (beyond basic ones)
                frontmatter = note.metadata.frontmatter
                # Remove system properties
                user_properties = {
                    k: v
                    for k, v in frontmatter.items()
                    if k not in ["tags", "aliases", "cssclass"]
                }
                if not user_properties:
                    is_orphaned = True
                    orphan_reason = "No metadata properties"

            elif orphan_type == "isolated":
                # Multiple criteria: no links AND no tags
                backlinks, outgoing = await _get_link_state(path)
                has_no_links = (not backlinks or len(backlinks) == 0) and (
                    not outgoing or len(outgoing) == 0
                )
                has_no_tags = not note.metadata.tags or len(note.metadata.tags) == 0

                if has_no_links and has_no_tags:
                    is_orphaned = True
                    orphan_reason = "No links and no tags"

            if is_orphaned:
                # Calculate size and word count
                content = note.content
                size_bytes = len(content.encode("utf-8"))
                word_count = len(content.split())

                orphaned_notes.append(
                    {
                        "path": path,
                        "reason": orphan_reason,
                        "modified": note.metadata.modified.isoformat()
                        if note.metadata.modified
                        else None,
                        "size": size_bytes,
                        "word_count": word_count,
                    }
                )

        except Exception as e:
            logger.warning(f"Error processing note {path}: {e}")
            continue

        # Progress reporting
        if ctx and (i + 1) % 50 == 0:
            await ctx.info(f"Processed {i + 1}/{total_notes} notes...")

    # Light enrichment (spec section 10.4's closing sentence): add each
    # orphaned note's cached name/description. Best-effort — a vault
    # without a real VaultCache (e.g. a bare mock in a unit test) just
    # means this loop is skipped, orphan detection itself is unaffected.
    if orphaned_notes:
        try:
            all_meta = await vault.cache.get_all_note_meta()
            for item in orphaned_notes:
                meta = all_meta.get(item["path"], {})
                item["name"] = meta.get("name", "")
                item["description"] = meta.get("description", "")
        except Exception as e:
            logger.warning(f"Skipping name/description enrichment: {e}")

    # Sort by modified date (oldest first)
    orphaned_notes.sort(key=lambda x: x.get("modified", ""), reverse=False)

    # Prepare summary statistics
    stats = {
        "total_notes_scanned": total_notes,
        "excluded_folders": exclude_folders,
        "orphan_type": orphan_type,
        "min_age_days": min_age_days,
    }

    if date_threshold is not None:
        stats["date_threshold"] = date_threshold.isoformat()

    return {
        "orphaned_notes": orphaned_notes,
        "count": len(orphaned_notes),
        "stats": stats,
    }
