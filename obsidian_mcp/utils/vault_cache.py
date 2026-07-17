"""In-memory vault index: notes (basename -> path), tags (tag -> paths), and
each note's raw outgoing links — so backlink/tag/broken-link tools stop
re-reading and re-parsing every note in the vault on every call.

Two auto-update paths, no new dependency (no watchdog/threads):

1. MCP mutations (create/update/edit/delete/move/rename/tag ops/...) all
   funnel through ObsidianVault.write_note / delete_note, which call
   note_mutated() with the content already in memory — no disk re-read.
2. External changes (Obsidian app, git checkout, scripts) are caught by a
   TTL-gated stat-diff: once OBSIDIAN_CACHE_STAT_TTL_SECONDS have passed
   since the last check, the next cache access triggers an os.walk stat
   pass (cheap — stats only) and re-parses just the files whose
   (mtime_ns, size) changed.

The cache never re-resolves a link's target from a stored "resolved"
pointer — resolution against the current notes index happens at query time
in the tools that consume this cache (link_management.get_backlinks /
find_broken_links, organization.list_tags, search_discovery._search_by_tag,
find_orphaned_notes). That keeps this class a dumb, cheap data store: no
bookkeeping bug can leave a stale "resolved" edge around after a note is
created/renamed/deleted, because resolution isn't cached, only extraction is.
"""

import asyncio
import os
import time
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from .links import extract_links_from_content


class VaultCache:
    """Lazily-built, incrementally-updated index owned by one ObsidianVault.
    Built on first access (never blocks server boot).
    """

    def __init__(self, vault):
        self._vault = vault
        self._lock = asyncio.Lock()
        self._built = False
        self._stat_snapshot: Dict[str, Tuple[int, int]] = {}  # relpath -> (mtime_ns, size)
        self._snapshot_time: float = 0.0

        self._key_to_relpaths: Dict[str, Set[str]] = {}  # "Foo.md"/"Foo" -> {relpaths}
        self._all_relpaths: Set[str] = set()
        self._tags_index: Dict[str, Set[str]] = {}  # tag -> {relpaths}
        self._forward_links: Dict[str, List[dict]] = {}  # relpath -> extract_links_from_content() result

    # ------------------------------------------------------------------
    # Public accessors — each ensures freshness before reading.
    # ------------------------------------------------------------------

    async def get_notes_index(self, force_refresh: bool = False) -> Dict[str, str]:
        """basename/stem (with & without .md) -> path. Same shape the old
        flat build_vault_notes_index() returned; collisions keep the
        lexicographically-largest relpath, matching the old last-write-wins
        behavior over vault.list_notes()'s sorted iteration order.
        """
        if force_refresh:
            async with self._lock:
                await self._full_scan_locked()
        else:
            await self._ensure_fresh()
        return {key: max(paths) for key, paths in self._key_to_relpaths.items() if paths}

    async def get_all_relpaths(self) -> Set[str]:
        await self._ensure_fresh()
        return set(self._all_relpaths)

    async def get_tags_index(self) -> Dict[str, Set[str]]:
        await self._ensure_fresh()
        return {tag: set(paths) for tag, paths in self._tags_index.items()}

    async def get_forward_links(self, relpath: str) -> List[dict]:
        await self._ensure_fresh()
        return list(self._forward_links.get(relpath, []))

    async def get_all_forward_links(self) -> Dict[str, List[dict]]:
        await self._ensure_fresh()
        return {relpath: list(links) for relpath, links in self._forward_links.items()}

    # ------------------------------------------------------------------
    # Mutation hook — called by ObsidianVault.write_note / delete_note.
    # ------------------------------------------------------------------

    async def note_mutated(self, relpath: str, content: Optional[str]) -> None:
        """content=None means the note was deleted. If the cache hasn't
        been built yet, this is a no-op — the next real access triggers a
        full scan that picks up the current on-disk state anyway, so there
        is nothing useful to do with a partial index yet.
        """
        async with self._lock:
            if not self._built:
                return
            self._deindex_note(relpath)
            if content is not None:
                self._index_note(relpath, content)
                try:
                    stat = (self._vault.vault_path / relpath).stat()
                    self._stat_snapshot[relpath] = (stat.st_mtime_ns, stat.st_size)
                except OSError:
                    pass
            else:
                self._stat_snapshot.pop(relpath, None)

    # ------------------------------------------------------------------
    # Freshness
    # ------------------------------------------------------------------

    async def _ensure_fresh(self) -> None:
        async with self._lock:
            if not self._built:
                await self._full_scan_locked()
                return
            ttl = self._vault.cache_stat_ttl_seconds
            if ttl == 0 or (time.monotonic() - self._snapshot_time) > ttl:
                await self._stat_diff_locked()

    def _iter_md_files(self):
        """(relpath, os.stat_result) for every *.md file in the vault —
        matches vault.list_notes()'s "**/*.md" glob (only .md, not
        .markdown, for consistency with the rest of the codebase).
        """
        vault_path = self._vault.vault_path
        for dirpath, _dirnames, filenames in os.walk(vault_path):
            for filename in filenames:
                if not filename.endswith(".md"):
                    continue
                full = os.path.join(dirpath, filename)
                try:
                    stat = os.stat(full)
                except OSError:
                    continue
                relpath = os.path.relpath(full, vault_path).replace(os.sep, "/")
                yield relpath, stat

    async def _read_text(self, relpath: str) -> Optional[str]:
        try:
            return (self._vault.vault_path / relpath).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None

    async def _full_scan_locked(self) -> None:
        self._key_to_relpaths.clear()
        self._all_relpaths.clear()
        self._tags_index.clear()
        self._forward_links.clear()
        self._stat_snapshot.clear()

        for relpath, stat in self._iter_md_files():
            content = await self._read_text(relpath)
            if content is None:
                continue
            self._stat_snapshot[relpath] = (stat.st_mtime_ns, stat.st_size)
            self._index_note(relpath, content)

        self._built = True
        self._snapshot_time = time.monotonic()

    async def _stat_diff_locked(self) -> None:
        current: Dict[str, Tuple[int, int]] = {}
        changed: List[str] = []
        for relpath, stat in self._iter_md_files():
            key = (stat.st_mtime_ns, stat.st_size)
            current[relpath] = key
            if self._stat_snapshot.get(relpath) != key:
                changed.append(relpath)

        removed = set(self._stat_snapshot) - set(current)
        for relpath in removed:
            self._deindex_note(relpath)

        for relpath in changed:
            content = await self._read_text(relpath)
            self._deindex_note(relpath)
            if content is not None:
                self._index_note(relpath, content)
                current[relpath] = self._stat_snapshot.get(relpath, current[relpath])

        self._stat_snapshot = current
        self._snapshot_time = time.monotonic()

    # ------------------------------------------------------------------
    # Index maintenance
    # ------------------------------------------------------------------

    def _keys_for(self, relpath: str) -> Set[str]:
        filename = relpath.rsplit("/", 1)[-1]
        keys = {filename}
        if filename.endswith(".md"):
            keys.add(filename[:-3])
        return keys

    def _index_note(self, relpath: str, content: str) -> None:
        frontmatter, clean_content = self._vault._parse_frontmatter(content)
        normalized_fm = self._vault._normalize_frontmatter(frontmatter)
        tags = self._vault._extract_tags(clean_content, normalized_fm)
        for tag in tags:
            self._tags_index.setdefault(tag, set()).add(relpath)

        for key in self._keys_for(relpath):
            self._key_to_relpaths.setdefault(key, set()).add(relpath)

        self._all_relpaths.add(relpath)
        self._forward_links[relpath] = extract_links_from_content(content)

    def _deindex_note(self, relpath: str) -> None:
        self._all_relpaths.discard(relpath)
        self._forward_links.pop(relpath, None)

        empty_tags = []
        for tag, paths in self._tags_index.items():
            paths.discard(relpath)
            if not paths:
                empty_tags.append(tag)
        for tag in empty_tags:
            del self._tags_index[tag]

        empty_keys = []
        for key, paths in self._key_to_relpaths.items():
            paths.discard(relpath)
            if not paths:
                empty_keys.append(key)
        for key in empty_keys:
            del self._key_to_relpaths[key]
