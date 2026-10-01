"""sync_vault_index: force the notes cache and the SQLite search index to
match the vault files on disk now (for edits made outside this server)."""

from typing import Any

from fastmcp import Context

from ..utils.filesystem import get_vault


async def sync_vault_index(
    full: bool = False, ctx: Context | None = None
) -> dict[str, Any]:
    """Reconcile the notes cache and the SQLite search index with the vault.

    Args:
        full: Re-index every note instead of only those whose modification
            time or size changed.
        ctx: MCP context for progress reporting.

    Returns:
        RESPONSE_STRUCTURES["index_sync"]: {"success", "scanned", "added",
        "updated", "removed", "failed", "full", "duration_ms"}.
    """
    if ctx:
        await ctx.info(
            "Re-indexing every note in the vault"
            if full
            else "Syncing the search index with the vault files"
        )
    stats = await get_vault().sync_index(full=full)
    return {"success": True, **stats}
