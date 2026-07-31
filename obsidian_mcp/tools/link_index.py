"""Vault-notes lookup helpers, split out of tools/link_management.py (same
precedent as utils/links.py -- see the comment near the top of
link_management.py).

Safe to split from get_backlinks/get_outgoing_links/find_broken_links: none
of these four functions call get_vault() (each takes `vault` as a plain
parameter instead), and none is patched by module-qualified name anywhere in
tests/ -- only imported directly as a function, which keeps working
regardless of which module defines it.
"""

from typing import Any


async def build_vault_notes_index(vault, force_refresh: bool = False) -> dict[str, str]:
    """
    Build an index of all notes in the vault.
    Maps note names to their full paths.

    Backed by the vault's VaultCache (auto-updated on every MCP mutation,
    and via TTL-gated stat-diff for changes made outside the MCP server —
    see utils/vault_cache.py) instead of a flat, blindly-300s-TTL,
    re-scan-the-whole-vault-from-scratch cache.
    """
    return await vault.cache.get_notes_index(force_refresh=force_refresh)


async def find_notes_by_names(vault, note_names: list[str]) -> dict[str, str | None]:
    """
    Find multiple notes by their names efficiently.

    Returns a dict mapping requested names to their full paths (or None if not found).
    """
    # Build or get cached index
    notes_index = await build_vault_notes_index(vault)

    results = {}
    for name in note_names:
        # Ensure .md extension for lookup
        lookup_name = name if name.endswith(".md") else name + ".md"

        # First check if it's already a full path that exists
        if lookup_name in notes_index.values():
            results[name] = lookup_name
        else:
            # Look up by filename
            results[name] = notes_index.get(lookup_name) or notes_index.get(name)

    return results


async def check_links_validity_batch(
    vault, links: list[dict[str, str]]
) -> list[dict[str, Any]]:
    """
    Check validity of multiple links in batch for performance.
    """
    # Get unique paths to check
    unique_paths = list({link["path"] for link in links})

    # Find all notes in one go
    found_paths = await find_notes_by_names(vault, unique_paths)

    # Update links with validity info
    results = []
    for link in links:
        link_copy: dict[str, Any] = link.copy()
        found_path = found_paths.get(link["path"])
        link_copy["exists"] = found_path is not None
        if found_path and found_path != link["path"]:
            link_copy["actual_path"] = found_path
        results.append(link_copy)

    return results


def get_link_context(content: str, match, context_length: int = 100) -> str:
    """
    Extract context around a link match.

    Args:
        content: The full content
        match: The regex match object
        context_length: Characters to include before and after

    Returns:
        Context string with the link highlighted
    """
    start = max(0, match.start() - context_length)
    end = min(len(content), match.end() + context_length)

    # Extract context
    context = content[start:end]

    # Add ellipsis if truncated
    if start > 0:
        context = "..." + context
    if end < len(content):
        context = context + "..."

    return context.strip()
