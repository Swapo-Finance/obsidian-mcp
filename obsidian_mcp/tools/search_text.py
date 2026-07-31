"""Tag- and path-pattern search strategies used by search_notes.

search_notes itself stays defined in search_discovery.py rather than moving
here — see the comment above its definition there. These two helpers take
`vault` as an explicit argument instead of calling get_vault() themselves,
so they carry none of the coupling that keeps search_notes in place, and
can live in their own module.
"""

from typing import Any


async def _search_by_tag(vault, tag: str, context_length: int) -> list[dict[str, Any]]:
    """Search for notes containing a specific tag, supporting hierarchical tags.

    Matching tags (and which notes carry them) come from the vault's tags
    index (see utils/vault_cache.py) instead of reading and re-parsing every
    note in the vault; only the notes that actually matched are then read,
    to build the surrounding-text context.
    """
    tags_index = await vault.cache.get_tags_index()  # tag -> {relpaths}

    # For hierarchical tags, we support:
    # - Exact match: "parent/child" matches "parent/child"
    # - Parent match: "parent" matches "parent/child", "parent/grandchild"
    # - Child match: searching for "child" finds "parent/child"
    # - Any level match: searching for "middle" finds "parent/middle/child"
    matches_by_path: dict[str, list[str]] = {}
    for note_tag, paths in tags_index.items():
        matched = (
            note_tag == tag
            or note_tag.startswith(tag + "/")
            or ("/" in note_tag and note_tag.split("/")[-1] == tag)
            or ("/" in note_tag and f"/{tag}/" in f"/{note_tag}/")
        )
        if matched:
            for path in paths:
                matches_by_path.setdefault(path, []).append(note_tag)

    results = []
    for path, matching_tags in matches_by_path.items():
        try:
            note = await vault.read_note(path)
        except Exception:
            # Skip notes we can't read
            continue

        # Get context around the tag occurrences
        content = note.content
        contexts = []

        # Search for all matching tags in content
        for matched_tag in matching_tags:
            tag_pattern = f"#{matched_tag}"
            idx = 0
            while True:
                idx = content.find(tag_pattern, idx)
                if idx == -1:
                    break

                # Extract context
                start = max(0, idx - context_length // 2)
                end = min(len(content), idx + len(tag_pattern) + context_length // 2)
                context = content[start:end].strip()
                contexts.append(context)
                idx += 1

        results.append(
            {
                "path": note.path,
                "score": 1.0,
                "matches": matching_tags,
                "context": " ... ".join(contexts)
                if contexts
                else f"Note contains tags: {', '.join(f'#{t}' for t in matching_tags)}",
            }
        )

    return results


async def _search_by_path(
    vault, path_pattern: str, context_length: int
) -> list[dict[str, Any]]:
    """Search for notes matching a path pattern."""
    results = []

    # Get all notes
    all_notes = await vault.list_notes(recursive=True)

    for note_info in all_notes:
        # Check if path matches pattern
        if path_pattern.lower() in note_info["path"].lower():
            try:
                # Read note to get some content for context
                note = await vault.read_note(note_info["path"])

                # Get first N characters as context
                context = note.content[:context_length].strip()
                if len(note.content) > context_length:
                    context += "..."

                results.append(
                    {
                        "path": note.path,
                        "score": 1.0,
                        "matches": [path_pattern],
                        "context": context,
                    }
                )
            except Exception:
                # If we can't read, still include in results
                results.append(
                    {
                        "path": note_info["path"],
                        "score": 1.0,
                        "matches": [path_pattern],
                        "context": "",
                    }
                )

    return results
