"""Daily note tool: add_daily_note.

One call, no chaining: resolves today's (or a given date's) daily note,
creates it from the daily-dir's template if missing (conformant by
construction — never re-validated against the template afterwards, per
spec section 3's incremental-edit exemption), and appends the given content.
"""

import unicodedata
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastmcp import Context
from ..utils.filesystem import get_vault
from ..utils.validation import validate_content
from ..utils.vault_config import build_template_info, seed_daily_frontmatter
from ..constants import ERROR_MESSAGES
from .link_management import validate_wikilinks_for_write
from .note_management import _serialize_note_writes


@_serialize_note_writes
async def add_daily_note(
    content: str,
    date: Optional[str] = None,
    ctx: Optional[Context] = None,
) -> Dict[str, Any]:
    """
    Append content to today's daily note, creating it first if needed.

    Use this tool instead of read_note + create_note/update_note when
    journaling — it resolves the daily note's path for you (rotating
    automatically at local midnight) and always appends, so you never need
    to read the note first to avoid clobbering it.

    Args:
        content: Markdown to append to the end of the daily note.
        date: Optional ISO date (YYYY-MM-DD) to target a specific day's
            note instead of today. Does not create/backfill other days.
        ctx: MCP context for progress reporting

    Returns:
        {path, created, appended: true}. Daily notes are always exempt from
        OBSIDIAN_MAX_NOTE_LINES / _APPEND_HEADROOM_LINES, and are never
        deleted by this tool — cleaning up old daily notes is up to the
        caller/client.
    """
    is_valid, error_msg = validate_content(content)
    if not is_valid:
        raise ValueError(error_msg)

    vault = get_vault()

    if date:
        try:
            day = datetime.strptime(date, "%Y-%m-%d").date()
        except ValueError:
            raise ValueError(ERROR_MESSAGES["invalid_daily_date"].format(date=date))
    else:
        # Local clock, not UTC, so the note rotates at local midnight.
        day = datetime.now(timezone.utc).astimezone().date()

    daily_path = f"{vault.daily_dir}/{day.isoformat()}.md" if vault.daily_dir else f"{day.isoformat()}.md"

    if ctx:
        ctx.info(f"Appending to daily note: {daily_path}")

    try:
        existing = await vault.read_note(daily_path)
        created = False
        base_content = existing.content
    except FileNotFoundError:
        created = True
        # Conformant by construction: seeded straight from the daily-dir's
        # template (if OBSIDIAN_FOLDER_TEMPLATES has a rule for it), so this
        # never needs a separate template-conformance check.
        info = build_template_info(vault, vault.daily_dir or "")
        base_content = info["skeleton"] if info["enforced"] else f"# {day.isoformat()}\n"
        # OBSIDIAN_REQUIRE_FRONTMATTER (default on): the server — not the
        # LLM — is creating this file, so name/description are generated
        # automatically here rather than raising (spec section 10.3's
        # add_daily bullet). No-op when the config is off.
        base_content = seed_daily_frontmatter(vault, base_content, day.isoformat())

    fragment = unicodedata.normalize("NFC", content)
    fragment, warnings = await validate_wikilinks_for_write(vault, fragment)

    if base_content.strip():
        final_content = base_content.rstrip("\n") + "\n\n" + fragment
    else:
        final_content = fragment
    final_content = unicodedata.normalize("NFC", final_content)

    # No size-policy check here by design: daily notes are always exempt
    # (spec section 1, hard rule) regardless of OBSIDIAN_NOTE_SIZE_POLICY.
    await vault.write_note(daily_path, final_content, overwrite=True)

    result = {"path": daily_path, "created": created, "appended": True}
    if warnings:
        result["warnings"] = warnings
    return result
