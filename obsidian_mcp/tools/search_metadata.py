"""Date-based note search."""

from datetime import datetime, timedelta

from ..utils.filesystem import get_vault
from ..utils.validation import validate_date_search_params
from .search_result_modes import _enrich_with_note_meta, _resolve_search_mode


def _note_date_in_window(
    file_date: datetime, operator: str, start_date: datetime, end_date: datetime
) -> bool:
    """Whether a note's file_date falls in the requested window: "within"
    is an open lower bound (>= start_date), "exactly" is that single day.

    Split out of search_by_date's per-note loop: the two operator branches
    used to end in a byte-identical days_ago/append block, differing only
    in this guard condition.
    """
    if operator == "within":
        return file_date >= start_date
    return start_date <= file_date < end_date


async def search_by_date(
    date_type: str = "modified",
    days_ago: int = 7,
    operator: str = "within",
    mode: str | None = None,
    ctx=None,
) -> dict:
    """
    Search for notes by creation or modification date.

    Use this tool to find notes created or modified within a specific time period.
    This is useful for finding recent work, tracking activity, or reviewing old notes.

    Args:
        date_type: Either "created" or "modified" (default: "modified")
        days_ago: Number of days to look back (default: 7)
        operator: Either "within" (last N days) or "exactly" (exactly N days ago) (default: "within")
        ctx: MCP context for progress reporting

    Returns:
        Dictionary containing search results with matched notes

    Example:
        >>> await search_by_date("modified", 7, "within", ctx=ctx)
        {
            "query": "Notes modified within last 7 days",
            "count": 15,
            "results": [
                {
                    "path": "Daily/2024-01-15.md",
                    "date": "2024-01-15T10:30:00Z",
                    "days_ago": 1
                }
            ]
        }
    """
    # Validate parameters
    is_valid, error = validate_date_search_params(date_type, days_ago, operator)
    if not is_valid:
        raise ValueError(error)

    # Calculate the date threshold
    now = datetime.now()

    # Both operators need the same window: the start of (now - days_ago) to
    # one day later. "within" treats that as a lower bound (see the
    # operator branch below); "exactly" treats it as that single day's
    # bounds. Only the description differs, so compute the dates once.
    target_date = now - timedelta(days=days_ago)
    start_date = target_date.replace(hour=0, minute=0, second=0, microsecond=0)
    end_date = start_date + timedelta(days=1)
    if operator == "within":
        query_description = f"Notes {date_type} within last {days_ago} days"
    else:
        query_description = f"Notes {date_type} exactly {days_ago} days ago"

    if ctx:
        await ctx.info(f"Searching for {query_description}")

    vault = get_vault()

    try:
        # Get all notes in the vault
        all_notes = await vault.list_notes(recursive=True)

        # Filter by date
        formatted_results = []
        for note_info in all_notes:
            note_path = note_info["path"]

            # Get file stats (use lenient path validation for existing files).
            # Deliberately kept as the private vault method rather than the
            # public normalize_vault_relative_path (utils/vault_paths.py):
            # that helper also strips a leading "<vault-basename>/" segment
            # and accepts absolute/"~" input -- convenience meant for
            # human-typed directory arguments (see list_folders below). Here
            # note_path is already a trusted, vault-relative path straight
            # from vault.list_notes(), so that convenience would risk
            # mis-resolving any note whose first path segment happens to
            # match the vault directory's own name, and it returns a bare
            # string + None-on-escape rather than a Path + raise, needing
            # extra glue for no behavior change. _get_absolute_path already
            # does the resolve()+relative_to() containment check this needs.
            full_path = vault._get_absolute_path(note_path)
            stat = full_path.stat()

            # Get the appropriate timestamp
            if date_type == "created":
                timestamp = stat.st_ctime
            else:
                timestamp = stat.st_mtime

            file_date = datetime.fromtimestamp(timestamp)

            # Check if it matches our criteria
            if not _note_date_in_window(file_date, operator, start_date, end_date):
                continue

            formatted_results.append(
                {
                    "path": note_path,
                    "date": file_date.isoformat(),
                    "days_ago": (now - file_date).days,
                }
            )

        # Sort by date (most recent first)
        formatted_results.sort(key=lambda x: x["date"], reverse=True)

        # Search result mode (spec section 10.4): search_by_date never
        # carried a content snippet to begin with, so "index" mode here
        # just adds name/description per result rather than trimming
        # anything away.
        effective_mode = _resolve_search_mode(vault, mode, len(formatted_results))
        if effective_mode == "index":
            await _enrich_with_note_meta(vault, formatted_results)

        # Return standardized search results structure
        return {
            "results": formatted_results,
            "count": len(formatted_results),
            "query": {
                "date_type": date_type,
                "days_ago": days_ago,
                "operator": operator,
                "description": query_description,
                "mode": effective_mode,
            },
            "truncated": False,
        }

    except Exception as e:
        if ctx:
            await ctx.info(f"Date search failed: {e!s}")
        # Return standardized error structure
        return {
            "results": [],
            "count": 0,
            "query": {
                "date_type": date_type,
                "days_ago": days_ago,
                "operator": operator,
                "description": query_description,
            },
            "truncated": False,
            "error": f"Date-based search failed: {e!s}",
        }
