"""Filesystem operations for Obsidian vault access."""

import asyncio
import logging
import os
import time
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any

import aiofiles

from ..constants import ERROR_MESSAGES
from ..models import Note, NoteMetadata
from .env import read_bool_env, read_choice_env, read_int_env
from .frontmatter import extract_tags, normalize_frontmatter, parse_frontmatter
from .image_io import read_image as read_image_file
from .index_metadata import extract_file_metadata, serialize_metadata
from .persistent_index import PersistentSearchIndex
from .vault_cache import VaultCache, iter_md_files
from .vault_config import normalize_vault_relative_path, parse_folder_templates

logger = logging.getLogger(__name__)


class ObsidianVault:
    """Direct filesystem access to Obsidian vault."""

    def __init__(self, vault_path: str | None = None):
        """
        Initialize vault access.

        Args:
            vault_path: Path to vault. If not provided, uses OBSIDIAN_VAULT_PATH env var.
        """
        raw_vault_path = vault_path or os.getenv("OBSIDIAN_VAULT_PATH", "")

        if not raw_vault_path:
            raise ValueError(
                "Vault path not provided. Set OBSIDIAN_VAULT_PATH environment variable "
                "or pass vault_path parameter."
            )

        # Resolve to an absolute path so all downstream glob / search-index /
        # relative_to operations are independent of the process CWD. A relative
        # OBSIDIAN_VAULT_PATH (e.g. "./brain-swapo") otherwise makes list_notes
        # and the persistent index depend on where the server was launched,
        # silently returning empty results when the CWD differs from the vault's
        # parent. Config stays portable (relative); the server resolves it once.
        self.vault_path = Path(raw_vault_path).expanduser().resolve()

        if not self.vault_path.exists():
            raise ValueError(f"Vault path does not exist: {self.vault_path}")

        if not self.vault_path.is_dir():
            raise ValueError(f"Vault path is not a directory: {self.vault_path}")

        # Initialize SQLite search index
        self.persistent_index: PersistentSearchIndex | None = None
        self._index_timestamp: float | None = None
        self._index_lock = asyncio.Lock()

        # Track if persistent index has been initialized
        self._persistent_index_initialized = False

        # Store last search metadata for access by tools
        self._last_search_metadata: dict[str, Any] | None = None

        # Configuration for index updates
        self._index_update_interval = int(
            os.getenv("OBSIDIAN_INDEX_UPDATE_INTERVAL", "300")
        )  # 5 minutes default
        self._index_batch_size = int(os.getenv("OBSIDIAN_INDEX_BATCH_SIZE", "50"))
        self._auto_index_update = os.getenv(
            "OBSIDIAN_AUTO_INDEX_UPDATE", "true"
        ).lower() in ("true", "1", "yes", "on")

        # Serialize note mutations to prevent lost updates when concurrent
        # read-modify-write operations target the same note (e.g. two
        # edit_note_section calls dispatched in one batch). asyncio.Lock — the
        # server is a single-process async app, so this is the right primitive
        # (not fcntl, which is for cross-process locking).
        # ponytail: global lock; make it per-path if write throughput matters.
        self._write_lock = asyncio.Lock()

        # --- Optional vault-wide write policies. Every knob defaults to
        # today's behavior; nothing here changes anything unless the
        # corresponding OBSIDIAN_* env var is explicitly set. ---
        self.wikilink_policy = self._read_choice_env(
            "OBSIDIAN_WIKILINK_POLICY", ("strict", "warn", "off"), "warn"
        )
        self.note_size_policy = self._read_choice_env(
            "OBSIDIAN_NOTE_SIZE_POLICY", ("strict", "warn", "off"), "warn"
        )
        self.tag_style = self._read_choice_env(
            "OBSIDIAN_TAG_STYLE", ("kebab", "as-is"), "as-is"
        )
        self.slug_style = self._read_choice_env(
            "OBSIDIAN_SLUG_STYLE", ("kebab", "as-is"), "as-is"
        )

        self.max_note_lines = self._read_int_env("OBSIDIAN_MAX_NOTE_LINES", 500)
        self.append_headroom_lines = self._read_int_env(
            "OBSIDIAN_APPEND_HEADROOM_LINES", 100
        )
        self.cache_stat_ttl_seconds = self._read_int_env(
            "OBSIDIAN_CACHE_STAT_TTL_SECONDS", 30
        )

        # Opinionated by default (spec section 10.3): create_note and
        # update_note(replace/create_if_not_exists) require a `description`
        # in frontmatter and force `name` to match the filename, unless this
        # is explicitly turned off.
        self.require_frontmatter = self._read_bool_env(
            "OBSIDIAN_REQUIRE_FRONTMATTER", True
        )

        # Search index mode (spec section 10.4): content (today's behavior),
        # index (lightweight {path, name, description, score, match_type}
        # from the cache), or auto (index once a search's result count beats
        # the threshold below).
        self.search_result_mode = self._read_choice_env(
            "OBSIDIAN_SEARCH_RESULT_MODE", ("content", "index", "auto"), "auto"
        )
        self.search_index_threshold = self._read_int_env(
            "OBSIDIAN_SEARCH_INDEX_THRESHOLD", 10
        )

        daily_dir_raw = os.getenv("OBSIDIAN_DAILY_DIR", "daily")
        normalized_daily = normalize_vault_relative_path(daily_dir_raw, self.vault_path)
        if normalized_daily is None:
            logger.warning(
                "OBSIDIAN_DAILY_DIR=%r resolves outside the vault (%s); falling back to "
                "the default 'daily'. Accepted forms: vault-relative ('daily'), "
                "vault-name-prefixed, or an absolute/'~' path under the vault.",
                daily_dir_raw,
                self.vault_path,
            )
            normalized_daily = "daily"
        self.daily_dir = normalized_daily

        self.folder_templates = parse_folder_templates(
            os.getenv("OBSIDIAN_FOLDER_TEMPLATES"), self.vault_path
        )

        # Lazily-built, incrementally-updated index of notes/tags/links
        # (see vault_cache.py) — avoids re-scanning the whole vault on every
        # backlink/tag/broken-link query.
        self.cache = VaultCache(self)

    @staticmethod
    def _read_choice_env(name: str, choices: tuple, default: str) -> str:
        """Delegates to env.read_choice_env (kept for existing callers)."""
        return read_choice_env(name, choices, default)

    @staticmethod
    def _read_bool_env(name: str, default: bool) -> bool:
        """Delegates to env.read_bool_env (kept for existing callers)."""
        return read_bool_env(name, default)

    @staticmethod
    def _read_int_env(name: str, default: int) -> int:
        """Delegates to env.read_int_env (kept for existing callers, incl.
        tests/test_onda2_sanity.py calling it directly on the class)."""
        return read_int_env(name, default)

    def is_daily_note_path(self, relpath: str) -> bool:
        """True if relpath lives inside OBSIDIAN_DAILY_DIR — daily notes are
        always exempt from OBSIDIAN_MAX_NOTE_LINES / _APPEND_HEADROOM_LINES."""
        if not self.daily_dir:
            return False
        return relpath == self.daily_dir or relpath.startswith(self.daily_dir + "/")

    @property
    def write_lock(self) -> asyncio.Lock:
        """Global note-mutation lock. Acquire around any read-modify-write."""
        return self._write_lock

    def _ensure_safe_path(self, path: str) -> Path:
        """
        Ensure the path is safe and within the vault.

        Args:
            path: Relative path within vault

        Returns:
            Full path object

        Raises:
            ValueError: If path is unsafe
        """
        # Normalize path separators for cross-platform compatibility
        path = path.replace("\\", "/")

        # Remove any leading/trailing slashes
        path = path.strip("/")

        # Validate path components
        parts = path.split("/")
        for part in parts:
            if part in ("..", ".", "") or part.startswith("."):
                raise ValueError(f"Invalid path component: {part}")
            # Check for invalid characters
            if any(char in part for char in '<>:"|?*'):
                raise ValueError(f"Invalid characters in path: {part}")

        # Convert to Path object
        full_path = self.vault_path / path

        # Resolve to absolute path and check it's within vault
        try:
            resolved = full_path.resolve()
            resolved.relative_to(self.vault_path.resolve())
        except (ValueError, RuntimeError):
            raise ValueError(f"Path escapes vault: {path}")

        return resolved

    def _get_absolute_path(self, path: str) -> Path:
        """
        Get absolute path for reading existing files (more lenient validation).
        Only checks for directory traversal, not character restrictions.

        Args:
            path: Relative path within vault

        Returns:
            Absolute Path object

        Raises:
            ValueError: If path escapes vault
        """
        # Normalize path separators
        normalized = path.replace("\\", "/")

        # Basic security check - no directory traversal
        parts = normalized.split("/")
        for part in parts:
            if part in ("..", ".", ""):
                raise ValueError(f"Invalid path component: {part}")

        # Convert to Path object
        full_path = self.vault_path / path

        # Resolve to absolute path and check it's within vault
        try:
            resolved = full_path.resolve()
            resolved.relative_to(self.vault_path.resolve())
        except (ValueError, RuntimeError):
            raise ValueError(f"Path escapes vault: {path}")

        return resolved

    @staticmethod
    def _resolve_normalization_alias(full_path: Path) -> Path:
        """If full_path doesn't exist but the same filename in the other
        Unicode normalization form (NFC vs NFD) does, return that existing
        path instead.

        Filesystems that store raw bytes (Linux ext4, unlike macOS's APFS,
        which reconciles NFC/NFD transparently) treat a precomposed and a
        decomposed encoding of the same visible name as two different
        files. Without this, a note written under one form becomes
        unreadable/undeletable/silently-duplicable under the other -- the
        same class of bug already fixed for wikilink resolution (see
        tools/wikilink_validation.py's _resolve_normalization_fallback_target:
        "this isn't a style choice, the two strings are the same text").

        Additive lookup only: tries the other normalization form, never
        rewrites what's on disk or what the caller asked for. Only the
        final path component is checked -- covers note filenames and a
        list_notes `directory` argument's own leaf folder, not an accented
        *intermediate* folder segment nested deeper in the path.
        # ponytail: leaf-only; extend to per-segment resolution if a
        # mismatched intermediate folder segment turns out to matter too.

        No-op (returns full_path unchanged) if it already exists, or if
        neither alternate form exists either -- a genuinely new or
        genuinely missing path behaves exactly as before.
        """
        if full_path.exists():
            return full_path
        for form in ("NFC", "NFD"):
            alias_name = unicodedata.normalize(form, full_path.name)
            if alias_name != full_path.name:
                alias_path = full_path.with_name(alias_name)
                if alias_path.exists():
                    return alias_path
        return full_path

    def _parse_frontmatter(self, content: str) -> tuple[dict[str, Any], str]:
        """Delegates to frontmatter.parse_frontmatter (kept for existing
        callers, incl. vault_config.py, vault_cache.py, tools/note_management.py,
        and tests/test_frontmatter_yaml_fallback.py)."""
        return parse_frontmatter(content)

    def _normalize_frontmatter(self, frontmatter: dict[str, Any]) -> dict[str, Any]:
        """Delegates to frontmatter.normalize_frontmatter (kept for existing
        callers, incl. vault_cache.py)."""
        return normalize_frontmatter(frontmatter)

    def _extract_tags(self, content: str, frontmatter: dict[str, Any]) -> list[str]:
        """Delegates to frontmatter.extract_tags (kept for existing callers,
        incl. vault_cache.py)."""
        return extract_tags(content, frontmatter)

    async def read_note(self, path: str) -> Note:
        """
        Read a note from the vault.

        Args:
            path: Path to note relative to vault root

        Returns:
            Note object with content and metadata
        """
        # Ensure .md extension
        if not path.endswith(".md"):
            path += ".md"

        # Use lenient path validation for reading existing files
        full_path = self._get_absolute_path(path)
        full_path = self._resolve_normalization_alias(full_path)

        if not full_path.exists():
            raise FileNotFoundError(f"Note not found: {path}")

        # Check file size to prevent memory issues
        stat = full_path.stat()
        max_size = 10 * 1024 * 1024  # 10MB limit
        if stat.st_size > max_size:
            raise ValueError(
                f"File too large: {stat.st_size} bytes (max: {max_size} bytes)"
            )

        # Read file content asynchronously
        async with aiofiles.open(full_path, "r", encoding="utf-8") as f:
            content = await f.read()

        # Parse frontmatter
        frontmatter, clean_content = self._parse_frontmatter(content)

        # Normalize frontmatter for legacy property names
        normalized_frontmatter = self._normalize_frontmatter(frontmatter)

        # Extract tags
        tags = self._extract_tags(clean_content, normalized_frontmatter)

        # Get file stats
        stat = full_path.stat()

        # Create metadata. fromtimestamp() without tz is intentional: these
        # are local file-mtime timestamps, and tools/search_discovery.py's
        # date search builds and compares its own naive datetimes the same
        # way — making just these two aware would raise TypeError the first
        # time a naive/aware comparison meets, for no behavior benefit.
        metadata = NoteMetadata(
            tags=tags,
            aliases=normalized_frontmatter.get("aliases", []),
            created=datetime.fromtimestamp(stat.st_ctime),
            modified=datetime.fromtimestamp(stat.st_mtime),
            frontmatter=normalized_frontmatter,
        )

        return Note(path=path, content=content, metadata=metadata)

    async def write_note(
        self, path: str, content: str, overwrite: bool = False
    ) -> Note:
        """
        Write a note to the vault.

        Args:
            path: Path to note relative to vault root
            content: Markdown content
            overwrite: Whether to overwrite existing file

        Returns:
            Created/updated Note object
        """
        # Ensure .md extension
        if not path.endswith(".md"):
            path += ".md"

        full_path = self._ensure_safe_path(path)
        full_path = self._resolve_normalization_alias(full_path)

        # Check if exists
        if full_path.exists() and not overwrite:
            raise FileExistsError(f"Note already exists: {path}")

        # Create parent directories if needed
        full_path.parent.mkdir(parents=True, exist_ok=True)

        # Write content asynchronously
        async with aiofiles.open(full_path, "w", encoding="utf-8") as f:
            await f.write(content)

        # Return the newly created note
        note = await self.read_note(path)

        # Keep the vault-wide cache (notes/tags/links index) in sync. Every
        # tool that mutates a note (create/update/edit/move/rename/tag ops/
        # daily notes) funnels through this method, so hooking it here is
        # the single point that covers all of them. The SQLite search index
        # gets the same write-through right after.
        await self.cache.note_mutated(path, content)
        await self._index_note_mutated(full_path, content)

        return note

    async def delete_note(self, path: str) -> bool:
        """
        Delete a note from the vault.

        Args:
            path: Path to note relative to vault root

        Returns:
            True if deleted successfully
        """
        # Ensure .md extension
        if not path.endswith(".md"):
            path += ".md"

        full_path = self._ensure_safe_path(path)
        full_path = self._resolve_normalization_alias(full_path)

        if not full_path.exists():
            raise FileNotFoundError(f"Note not found: {path}")

        # Delete the file
        full_path.unlink()
        await self.cache.note_mutated(path, None)
        await self._index_note_mutated(full_path, None)
        return True

    def _require_vault_dir(self) -> None:
        """Raise before any index work when the vault folder is gone (an
        unmounted drive or network share). A pass over a missing folder
        would walk zero notes and delete every index entry as an orphan, and
        initializing would fail with an opaque mkdir error."""
        if not self.vault_path.is_dir():
            raise RuntimeError(
                ERROR_MESSAGES["vault_unavailable"].format(path=self.vault_path)
            )

    async def _initialize_persistent_index(self) -> None:
        """Initialize the persistent search index if not already done."""
        if not self._persistent_index_initialized:
            self._require_vault_dir()
            try:
                self.persistent_index = PersistentSearchIndex(self.vault_path)
                await self.persistent_index.initialize()
                self._persistent_index_initialized = True
                logger.info("Persistent search index initialized")
            except PermissionError as e:
                raise RuntimeError(
                    f"Permission denied when accessing search index. "
                    f"To fix: Ensure '{self.vault_path}/.obsidian' is writable: "
                    f"chmod +w '{self.vault_path}/.obsidian'. "
                    f"Original error: {e}"
                )
            except OSError as e:
                if "read-only" in str(e).lower():
                    raise RuntimeError(
                        f"Cannot create search index: vault appears to be read-only. "
                        f"Please ensure '{self.vault_path}' is writable."
                    )
                else:
                    raise RuntimeError(f"File system error: {e}")
            except Exception as e:
                raise RuntimeError(
                    f"Failed to initialize search index: {type(e).__name__}: {e}. "
                    f"This may be due to: 1) Missing aiosqlite package, "
                    f"2) Corrupted database file, 3) Incompatible Python version"
                )

    def _require_persistent_index(self) -> PersistentSearchIndex:
        """Return the initialized persistent index, narrowed to non-Optional.

        Every caller below runs after _initialize_persistent_index() by
        contract, so self.persistent_index is never actually None here — but
        pyright's narrowing doesn't survive the awaits in between, so each
        caller re-narrows once via this helper into a local variable instead
        of annotating the Optional away.
        """
        if self.persistent_index is None:
            raise RuntimeError("Persistent search index not initialized")
        return self.persistent_index

    def _index_is_fresh(self) -> bool:
        """True when no reconcile pass is due before the next SQLite query.

        Never fresh before the first pass. With OBSIDIAN_AUTO_INDEX_UPDATE
        off (manual mode) it stays fresh after that: only write-through and
        sync_index() change the index. Otherwise fresh until
        OBSIDIAN_INDEX_UPDATE_INTERVAL seconds have passed; 0 means re-check
        before every query (same convention as
        OBSIDIAN_CACHE_STAT_TTL_SECONDS).
        """
        if self._index_timestamp is None:
            return False
        if not self._auto_index_update:
            return True
        elapsed = time.time() - self._index_timestamp
        return (
            self._index_update_interval > 0 and elapsed <= self._index_update_interval
        )

    async def ensure_index_fresh(self) -> None:
        """Make the SQLite index match the vault before a query reads it.

        Every SQLite-backed search calls this first. A due pass is awaited,
        so a query never reads a stale or half-built index (the old refresh
        was fire-and-forget and answered from the previous state). No lock on
        the fast path; otherwise initialization and the pass run under
        _index_lock and are re-checked inside it, so concurrent first queries
        build the index once.
        """
        if self._persistent_index_initialized and self._index_is_fresh():
            return
        async with self._index_lock:
            await self._initialize_persistent_index()
            if not self._index_is_fresh():
                await self._update_persistent_index()

    async def sync_index(self, full: bool = False) -> dict[str, Any]:
        """Reconcile the notes cache and the SQLite index with the vault now,
        ignoring OBSIDIAN_INDEX_UPDATE_INTERVAL -- the explicit resync behind
        sync_vault_index_tool, for edits made outside this server.

        Args:
            full: Re-index every note instead of only those whose mtime/size
                changed (for an index suspected to be wrong).

        Returns:
            This pass's counts -- see _update_persistent_index.

        Raises:
            RuntimeError: The vault folder is not reachable. Checked before
                the cache or the index is touched: syncing the cache against
                a folder it cannot walk would deindex every note.
        """
        self._require_vault_dir()
        await self.cache.sync()
        async with self._index_lock:
            await self._initialize_persistent_index()
            return await self._update_persistent_index(full)

    async def _update_persistent_index(self, full: bool = False) -> dict[str, Any]:
        """Reconcile the persistent index with the vault on disk.

        Diffs every *.md file's (mtime, size) against what the index stored,
        re-indexes new and changed files (all of them when full=True), and
        drops entries whose file is gone. Caller must hold _index_lock.

        Returns:
            {"scanned", "added", "updated", "removed", "failed", "full",
            "duration_ms"} for this pass.

        Raises:
            RuntimeError: The vault folder is not reachable (see
                _require_vault_dir).
        """
        self._require_vault_dir()
        index = self._require_persistent_index()
        started = time.monotonic()
        stored = await index.get_file_stats()
        on_disk = {
            relpath: (stat.st_mtime, stat.st_size)
            for relpath, stat in iter_md_files(self.vault_path)
        }
        changed = [
            relpath
            for relpath, disk_stat in on_disk.items()
            if full or stored.get(relpath) != disk_stat
        ]
        logger.info(
            f"Index pass: {len(on_disk)} notes on disk, {len(changed)} to index"
        )

        added = updated = failed = 0
        for i in range(0, len(changed), self._index_batch_size):
            batch = changed[i : i + self._index_batch_size]
            logger.info(
                f"Indexing batch {i + 1}-{i + len(batch)} of {len(changed)} files"
            )
            for relpath in batch:
                mtime, size = on_disk[relpath]
                try:
                    async with aiofiles.open(
                        self.vault_path / relpath, "r", encoding="utf-8"
                    ) as f:
                        content = await f.read()
                    await index.index_file(
                        relpath,
                        content,
                        mtime,
                        size,
                        self._extract_file_metadata(content),
                    )
                except Exception as e:
                    failed += 1
                    logger.error(f"Failed to index {relpath}: {e}")
                    continue
                if relpath in stored:
                    updated += 1
                else:
                    added += 1

        removed = len(stored.keys() - on_disk.keys())
        await index.clear_orphaned_entries(set(on_disk))
        self._index_timestamp = time.time()

        return {
            "scanned": len(on_disk),
            "added": added,
            "updated": updated,
            "removed": removed,
            "failed": failed,
            "full": full,
            "duration_ms": round((time.monotonic() - started) * 1000),
        }

    async def _index_note_mutated(self, full_path: Path, content: str | None) -> None:
        """Write-through counterpart of cache.note_mutated for the SQLite
        index: re-index (or drop, when content is None) the one note an MCP
        write just touched, so it is searchable without waiting for a pass.

        No-op until the index exists -- the first query's pass indexes the
        note anyway. Runs under _index_lock so a pass that walked the disk
        before this write cannot then drop the fresh entry as an orphan. The
        relpath comes from the resolved full_path, the same form the walk
        produces. A failure here must not fail a write that already hit the
        disk: log it and make the next query reconcile instead.
        """
        if not self._persistent_index_initialized:
            return
        relpath = full_path.relative_to(self.vault_path).as_posix()
        async with self._index_lock:
            index = self._require_persistent_index()
            try:
                if content is None:
                    await index.remove_file(relpath)
                else:
                    stat = full_path.stat()
                    await index.index_file(
                        relpath,
                        content,
                        stat.st_mtime,
                        stat.st_size,
                        self._extract_file_metadata(content),
                    )
            except Exception as e:
                logger.warning(
                    f"Search index write-through failed for {relpath}: {e}; "
                    "the next search re-syncs the index"
                )
                self._index_timestamp = None

    def _extract_file_metadata(self, content: str) -> dict[str, Any]:
        """Delegates to index_metadata.extract_file_metadata (kept as a
        vault method since the index pass and write-through call it via
        self)."""
        return extract_file_metadata(content)

    def _serialize_metadata(self, obj: Any) -> Any:
        """Delegates to index_metadata.serialize_metadata (kept for
        existing callers)."""
        return serialize_metadata(obj)

    async def search_notes(
        self, query: str, context_length: int = 20, max_results: int = 50
    ) -> list[dict[str, Any]]:
        """
        Search for notes containing query text using indexed search.

        Args:
            query: Search query
            context_length: Characters to show around match
            max_results: Maximum number of results to return

        Returns:
            List of search results

        Note: Search metadata (total_count, truncated) is stored in self._last_search_metadata
        """
        await self.ensure_index_fresh()

        # Use persistent index
        return await self._search_with_persistent_index(
            query, context_length, max_results
        )

    def get_last_search_metadata(self) -> dict[str, Any] | None:
        """
        Get metadata from the last search operation.

        Returns:
            Dictionary with total_count, truncated, and limit, or None if no search has been performed
        """
        return self._last_search_metadata

    async def _search_with_persistent_index(
        self, query: str, context_length: int, max_results: int
    ) -> list[dict[str, Any]]:
        """Search using the persistent SQLite index."""
        index = self._require_persistent_index()
        # Use simple search for now (FTS5 search can be added later)
        search_data = await index.search_simple(query, max_results)
        search_results = search_data["results"]
        total_count = search_data["total_count"]
        truncated = search_data["truncated"]

        results = []
        query_lower = query.lower()

        for file_info in search_results:
            content = file_info["content"]
            content_lower = content.lower()

            # Find all matches
            matches = []
            start_pos = 0
            while True:
                match_pos = content_lower.find(query_lower, start_pos)
                if match_pos == -1:
                    break
                matches.append(match_pos)
                start_pos = match_pos + 1

            # Extract context for first match
            if matches:
                first_match = matches[0]

                # Calculate context bounds
                start = max(0, first_match - context_length // 2)
                end = min(len(content), first_match + len(query) + context_length // 2)
                context = content[start:end].strip()

                # Add ellipsis if truncated
                if start > 0:
                    context = "..." + context
                if end < len(content):
                    context = context + "..."

                # Calculate simple relevance score based on match count
                score = min(len(matches) / 10.0 + 1.0, 5.0)  # Score between 1 and 5

                results.append(
                    {
                        "path": file_info["filepath"],
                        "score": score,
                        "matches": [query],
                        "match_count": len(matches),
                        "context": context,
                    }
                )

        # Sort by score (descending)
        results.sort(key=lambda x: x["score"], reverse=True)

        # Store search metadata
        self._last_search_metadata = {
            "total_count": total_count,
            "truncated": truncated,
            "limit": max_results,
        }

        return results

    async def search_by_regex(
        self,
        pattern: str,
        flags: int = 0,
        context_length: int = 20,
        max_results: int = 50,
    ) -> list[dict[str, Any]]:
        """
        Search for notes matching a regular expression pattern.

        Args:
            pattern: Regular expression pattern
            flags: Regex flags (e.g., re.IGNORECASE)
            context_length: Characters to show around match
            max_results: Maximum number of results to return

        Returns:
            List of search results with matches and context
        """
        await self.ensure_index_fresh()

        # Use persistent index for efficient regex search
        index = self._require_persistent_index()
        results = await index.search_regex(pattern, flags, max_results, context_length)
        # Convert filepath to path for consistency
        for result in results:
            result["path"] = result.pop("filepath")
        return results

    async def list_notes(
        self, directory: str | None = None, recursive: bool = True
    ) -> list[dict[str, str]]:
        """
        List all notes in vault or specific directory.

        Args:
            directory: Specific directory to list (optional)
            recursive: Whether to include subdirectories

        Returns:
            List of note paths and names
        """
        notes = []

        # Determine search path
        if directory:
            # Use lenient validation for reading existing directories
            search_path = self._get_absolute_path(directory)
            search_path = self._resolve_normalization_alias(search_path)
            if not search_path.exists() or not search_path.is_dir():
                return []
        else:
            search_path = self.vault_path

        # Find markdown files
        pattern = "**/*.md" if recursive else "*.md"
        for md_file in search_path.glob(pattern):
            rel_path = md_file.relative_to(self.vault_path)
            notes.append({"path": str(rel_path), "name": md_file.name})

        # Sort by path
        notes.sort(key=lambda x: x["path"])

        return notes

    async def find_image(self, filename: str) -> str | None:
        """
        Find an image file anywhere in the vault.

        Args:
            filename: Image filename to search for

        Returns:
            Relative path to image if found, None otherwise
        """
        # Common image extensions
        image_extensions = {
            ".png",
            ".jpg",
            ".jpeg",
            ".gif",
            ".webp",
            ".svg",
            ".bmp",
            ".ico",
        }

        # Check if filename has valid extension
        file_ext = Path(filename).suffix.lower()
        if file_ext not in image_extensions:
            return None

        # Search for the image file
        for image_file in self.vault_path.rglob(filename):
            if image_file.is_file():
                return str(image_file.relative_to(self.vault_path))

        return None

    async def read_image(self, path: str, max_width: int = 1600) -> dict[str, Any]:
        """
        Read an image file from the vault with automatic resizing.

        Args:
            path: Path to image relative to vault root
            max_width: Maximum width for resizing (default: 1600px)

        Returns:
            Dictionary with image data and metadata
        """
        # Use lenient validation for reading existing image files, then
        # delegate the vault-free read/resize logic to image_io.read_image.
        full_path = self._get_absolute_path(path)
        return await read_image_file(full_path, path, max_width)


# Global vault instance (will be initialized in server.py)
vault: ObsidianVault | None = None


def get_vault() -> ObsidianVault:
    """Get the global vault instance."""
    if vault is None:
        raise RuntimeError("Vault not initialized. Call init_vault() first.")
    return vault


def init_vault(vault_path: str | None = None) -> ObsidianVault:
    """
    Initialize the global vault instance.

    Args:
        vault_path: Path to vault (uses OBSIDIAN_VAULT_PATH env var if not provided)
    """
    global vault

    vault = ObsidianVault(vault_path)
    return vault
