"""Search result mode resolution and shaping (spec section 10.4).

Mode is one of: content (today's behavior, a text snippet per result), index
(lightweight {path, name, description, score, match_type} sourced from the
VaultCache, no snippet), or auto (index once a search's result count beats
OBSIDIAN_SEARCH_INDEX_THRESHOLD). This machinery is one concept shared by
four public tools — search_notes, search_by_date, search_by_property, and
search_by_regex — so it lives in its own module rather than under any one
of them.
"""

from typing import Any


def _resolve_search_mode(vault, mode: str | None, count: int) -> str:
    """Effective content/index mode for one search response. An explicit
    per-call `mode` wins over OBSIDIAN_SEARCH_RESULT_MODE; `auto` resolves
    to `index` once `count` exceeds OBSIDIAN_SEARCH_INDEX_THRESHOLD, else
    `content`. Falls back to "content" for anything unrecognized (e.g. a
    bare mock vault in a unit test with no real config) — same
    fail-safe-to-today's-behavior spirit as the rest of this config surface.
    """
    configured = getattr(vault, "search_result_mode", "content")
    effective = mode if mode is not None else configured
    if effective not in ("content", "index", "auto"):
        effective = "content"
    if effective == "auto":
        threshold = getattr(vault, "search_index_threshold", 10)
        if not isinstance(threshold, int):
            threshold = 10
        return "index" if count > threshold else "content"
    return effective


async def _to_index_items(
    vault,
    results: list[dict[str, Any]],
    default_match_type: str,
    score_key: str = "score",
    extra_keys: tuple[str, ...] = (),
) -> list[dict[str, Any]]:
    """Convert content-mode result dicts into lightweight index-mode items —
    {path, name, description, score, match_type} — sourced entirely from the
    VaultCache (spec sections 10.2/10.4), no extra per-note disk reads.
    `score_key` lets a caller repurpose a differently-named numeric field
    (e.g. search_by_regex's match_count) as the index item's `score`.
    `extra_keys` passes through additional fields worth keeping even in
    index mode because they're structured data, not prose content (e.g.
    search_by_property's property_value).
    """
    all_meta = await vault.cache.get_all_note_meta()
    items = []
    for r in results:
        meta = all_meta.get(r["path"], {})
        item = {
            "path": r["path"],
            "name": meta.get("name", ""),
            "description": meta.get("description", ""),
            "score": r.get(score_key, 0),
            "match_type": r.get("match_type", default_match_type),
        }
        for key in extra_keys:
            if key in r:
                item[key] = r[key]
        items.append(item)
    return items


async def _enrich_with_note_meta(
    vault, items: list[dict[str, Any]], path_key: str = "path"
) -> None:
    """In-place: add 'name'/'description' (from the VaultCache) to each item
    that has a `path_key` field. Used for the lighter, always-on enrichment
    on list_notes/get_backlinks/find_broken_links/find_orphaned_notes (spec
    section 10.4's closing sentence) — these tools don't carry a content
    snippet to begin with, so there's nothing to strip, just fields to add.
    """
    if not items:
        return
    all_meta = await vault.cache.get_all_note_meta()
    for item in items:
        path = item.get(path_key)
        if not path:
            continue
        meta = all_meta.get(path, {})
        item["name"] = meta.get("name", "")
        item["description"] = meta.get("description", "")
