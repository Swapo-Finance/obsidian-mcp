"""Persistent search index using SQLite for Obsidian vault."""

import asyncio
import json
import logging
import multiprocessing
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any

import aiosqlite

from .index_text import (
    calculate_line_offsets,
    compute_hash,
    determine_property_type,
    extract_literal_prefix,
    search_file_content,
    search_large_file_content,
)

logger = logging.getLogger(__name__)

# Per-file wall-clock cap on regex matching, enforced by running the actual
# match in a ProcessPoolExecutor worker (see _process_file_regex), not a
# thread: CPython's re engine holds the GIL for the whole match, so a
# ThreadPoolExecutor timeout would free nothing -- verified empirically, a
# catastrophic-backtracking match froze an asyncio event loop for its full
# duration even when dispatched to a thread via loop.run_in_executor. A
# separate process has its own GIL, so the parent stays responsive; the
# timed-out worker keeps running in the background (it can't be force-killed
# without depending on ProcessPoolExecutor's private internals -- see
# close()) until it naturally finishes or the server process exits.
REGEX_MATCH_TIMEOUT_SECONDS = 5.0
# ponytail: fixed size, not configurable. Add an OBSIDIAN_* knob in
# vault_config.py if a deployment ever needs to tune it.
_REGEX_POOL_MAX_WORKERS = 4
# Recycle the process pool after this many consecutive per-file timeouts.
# ProcessPoolExecutor only replaces a worker on crash, never on
# "permanently busy" (see REGEX_MATCH_TIMEOUT_SECONDS above), so enough
# pathological patterns in a row can wedge every worker forever -- every
# later regex search would then silently degrade to an empty result for
# the rest of the process's life. This many consecutive timeouts is a
# crude but cheap proxy for "the whole pool is probably stuck": a false
# positive (legitimately slow, non-malicious files) just costs a wasted
# pool respawn; a false negative costs a few more timeout-empty-result
# searches before the next check trips it.
_REGEX_POOL_RECYCLE_THRESHOLD = _REGEX_POOL_MAX_WORKERS


class PersistentSearchIndex:
    """SQLite-based persistent search index for efficient vault searching."""

    def __init__(self, vault_path: Path, index_path: Path | None = None):
        """
        Initialize persistent search index.

        Args:
            vault_path: Path to the Obsidian vault
            index_path: Path to store the SQLite database (defaults to vault/.obsidian/search-index.db)
        """
        self.vault_path = vault_path

        # Default index location in .obsidian folder
        if index_path is None:
            obsidian_dir = vault_path / ".obsidian"
            obsidian_dir.mkdir(exist_ok=True)
            self.index_path = obsidian_dir / "mcp-search-index.db"
        else:
            self.index_path = index_path

        self.db: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()
        # Lazily created on first regex search -- see _get_regex_process_pool.
        self._regex_process_pool: ProcessPoolExecutor | None = None
        # Consecutive per-file regex timeouts since the pool was last (re)created.
        # See _REGEX_POOL_RECYCLE_THRESHOLD and _recycle_regex_process_pool.
        self._consecutive_regex_timeouts = 0

    async def initialize(self):
        """Initialize database connection and create tables if needed."""
        self.db = await aiosqlite.connect(str(self.index_path))

        # Enable WAL mode for better concurrent access
        await self.db.execute("PRAGMA journal_mode=WAL")
        # SQLite disables FK enforcement by default, per connection -- the
        # file_properties.filepath -> file_index.filepath CASCADE declared
        # below is otherwise decorative. Belt-and-suspenders alongside the
        # explicit DELETE in _remove_file_locked, which is the actually
        # load-bearing fix since it works even if a future connection
        # forgets this pragma.
        await self.db.execute("PRAGMA foreign_keys = ON")

        # Create tables
        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS file_index (
                filepath TEXT PRIMARY KEY,
                content TEXT NOT NULL,
                content_lower TEXT NOT NULL,
                mtime REAL NOT NULL,
                size INTEGER NOT NULL,
                content_hash TEXT NOT NULL,
                last_indexed REAL NOT NULL,
                metadata TEXT,
                line_offsets TEXT
            )
        """)

        # Check if we need to add line_offsets column (for existing databases)
        cursor = await self.db.execute("PRAGMA table_info(file_index)")
        columns = await cursor.fetchall()
        column_names = [col[1] for col in columns]

        if "line_offsets" not in column_names:
            await self.db.execute("ALTER TABLE file_index ADD COLUMN line_offsets TEXT")
            logger.info("Added line_offsets column to existing database")

        # Create properties table for efficient property searches
        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS file_properties (
                filepath TEXT NOT NULL,
                property_name TEXT NOT NULL,
                property_value TEXT,
                property_type TEXT,
                PRIMARY KEY (filepath, property_name),
                FOREIGN KEY (filepath) REFERENCES file_index(filepath) ON DELETE CASCADE
            )
        """)

        # Create indexes for faster searching
        await self.db.execute("""
            CREATE INDEX IF NOT EXISTS idx_mtime ON file_index(mtime)
        """)
        await self.db.execute("""
            CREATE INDEX IF NOT EXISTS idx_size ON file_index(size)
        """)

        # Create indexes for property searches
        await self.db.execute("""
            CREATE INDEX IF NOT EXISTS idx_property_name ON file_properties(property_name)
        """)
        # idx_property_value intentionally not (re)created: EXPLAIN QUERY PLAN
        # against every search_by_property predicate shape (LIKE '%x%',
        # LOWER(x) = LOWER(?), CAST(x AS REAL) <op> CAST(?), plain exists/!=)
        # confirmed the planner never picks it -- each predicate wraps
        # property_value in a function or a leading wildcard, which defeats a
        # plain B-tree index. It was paid for on every write and never earned
        # back on a read. Explicit DROP (not just omitting the CREATE) so an
        # existing on-disk database from before this fix stops carrying it too.
        await self.db.execute("DROP INDEX IF EXISTS idx_property_value")

        # Create FTS5 virtual table for full-text search
        await self.db.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS file_search
            USING fts5(
                filepath UNINDEXED,
                content,
                content_lower,
                tokenize='porter unicode61'
            )
        """)

        await self.db.commit()

    async def close(self):
        """Close database connection."""
        if self.db:
            await self.db.close()
            self.db = None
        if self._regex_process_pool is not None:
            # wait=False: closing the index must never block on a worker
            # still spinning through a pathological pattern (see
            # REGEX_MATCH_TIMEOUT_SECONDS above). The process is left running
            # in the background rather than force-killed -- same accepted
            # trade-off as the timeout itself.
            self._regex_process_pool.shutdown(wait=False, cancel_futures=True)
            self._regex_process_pool = None

    def _get_regex_process_pool(self) -> ProcessPoolExecutor:
        """Lazily create the process pool that runs regex matching outside
        the event loop's own process (see REGEX_MATCH_TIMEOUT_SECONDS above).

        Safe under concurrent callers: nothing between the None check and
        the assignment awaits, so there's no interleaving window on
        asyncio's single-threaded event loop for two coroutines to each
        create a pool.
        """
        if self._regex_process_pool is None:
            self._regex_process_pool = ProcessPoolExecutor(
                max_workers=_REGEX_POOL_MAX_WORKERS,
                mp_context=multiprocessing.get_context("spawn"),
            )
        return self._regex_process_pool

    def _recycle_regex_process_pool(self) -> None:
        """Discard the current regex process pool so the next call lazily
        creates a fresh one -- see _REGEX_POOL_RECYCLE_THRESHOLD for when
        this fires.

        Same shutdown as close(): wait=False so this never blocks on a
        worker mid-pathological-match, cancel_futures=True to drop anything
        still queued (should be empty in practice now that search_regex
        caps a batch at _REGEX_POOL_MAX_WORKERS). The abandoned workers keep
        running until they naturally finish or the server process exits --
        same accepted trade-off as everywhere else in this module.
        """
        if self._regex_process_pool is not None:
            logger.warning(
                "Recycling regex process pool after "
                f"{self._consecutive_regex_timeouts} consecutive per-file "
                "timeouts -- assuming the worker pool is wedged"
            )
            self._regex_process_pool.shutdown(wait=False, cancel_futures=True)
            self._regex_process_pool = None
        self._consecutive_regex_timeouts = 0

    def _require_db(self) -> aiosqlite.Connection:
        """Return the open database connection, narrowed to non-Optional.

        Every method below runs after initialize() by contract (ObsidianVault
        always awaits it first), so self.db is never actually None here — but
        pyright's narrowing doesn't survive the awaits in between, so each
        method re-narrows once via this helper into a local variable instead
        of annotating the Optional away.
        """
        if self.db is None:
            raise RuntimeError(
                "PersistentSearchIndex used before initialize() was called"
            )
        return self.db

    async def get_file_info(self, filepath: str) -> dict[str, Any] | None:
        """Get cached file information."""
        db = self._require_db()
        async with self._lock:
            cursor = await db.execute(
                "SELECT mtime, size, content_hash, last_indexed FROM file_index WHERE filepath = ?",
                (filepath,),
            )
            row = await cursor.fetchone()

            if row:
                return {
                    "mtime": row[0],
                    "size": row[1],
                    "content_hash": row[2],
                    "last_indexed": row[3],
                }
            return None

    async def needs_update(
        self, filepath: str, current_mtime: float, current_size: int
    ) -> bool:
        """Check if a file needs to be re-indexed."""
        file_info = await self.get_file_info(filepath)

        if not file_info:
            return True

        # Check if file has been modified
        return file_info["mtime"] != current_mtime or file_info["size"] != current_size

    def _determine_property_type(self, value: Any) -> str:
        """Delegates to index_text.determine_property_type (kept since
        test_persistent_index_properties.py calls it via self)."""
        return determine_property_type(value)

    async def index_file(
        self,
        filepath: str,
        content: str,
        mtime: float,
        size: int,
        metadata: dict | None = None,
    ):
        """Index a single file with its content and properties."""
        content_hash = compute_hash(content)
        content_lower = content.lower()
        metadata_json = json.dumps(metadata) if metadata else None

        # Calculate line offsets for efficient line number lookups
        line_offsets = calculate_line_offsets(content)
        line_offsets_json = json.dumps(line_offsets)

        # time.time() rather than datetime.now().timestamp(): this is stored
        # as a raw epoch float (last_indexed) and never round-tripped through
        # a datetime for comparison, so it needs no timezone at all — just
        # "now" as seconds since epoch, which is what time.time() already is.
        now = time.time()

        db = self._require_db()
        async with self._lock:
            try:
                # Update main index
                await db.execute(
                    """
                    INSERT OR REPLACE INTO file_index
                    (filepath, content, content_lower, mtime, size, content_hash, last_indexed, metadata, line_offsets)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        filepath,
                        content,
                        content_lower,
                        mtime,
                        size,
                        content_hash,
                        now,
                        metadata_json,
                        line_offsets_json,
                    ),
                )

                # Update FTS index. filepath is UNINDEXED on file_search (see
                # the CREATE VIRTUAL TABLE above), so this DELETE is a full
                # scan of the shadow table every time -- measured ~0.2ms at
                # 1k indexed files, ~1.2ms at 10k, ~5ms at 50k (linear per
                # call, so effectively O(n^2) across a full vault reindex).
                # A real fix means tracking FTS5's own rowid per filepath (a
                # schema migration, coordinated with file_index's INSERT OR
                # REPLACE) -- judged riskier than the problem right now: a
                # wrong rowid mapping corrupts the search index, whereas this
                # is only slow, not wrong, at realistic vault sizes. Revisit
                # with rowid tracking if bulk reindexing a real vault this
                # large becomes actually painful.
                await db.execute(
                    "DELETE FROM file_search WHERE filepath = ?", (filepath,)
                )
                await db.execute(
                    "INSERT INTO file_search (filepath, content, content_lower) VALUES (?, ?, ?)",
                    (filepath, content, content_lower),
                )

                # Update properties if metadata contains frontmatter
                if metadata and "frontmatter" in metadata:
                    # Clear existing properties for this file
                    await db.execute(
                        "DELETE FROM file_properties WHERE filepath = ?", (filepath,)
                    )

                    # Insert new properties
                    frontmatter = metadata["frontmatter"]
                    for prop_name, prop_value in frontmatter.items():
                        # Determine property type
                        prop_type = self._determine_property_type(prop_value)

                        # Convert value to string for storage
                        if isinstance(prop_value, (list, dict)):
                            prop_value_str = json.dumps(prop_value)
                        else:
                            prop_value_str = str(prop_value)

                        await db.execute(
                            """
                            INSERT INTO file_properties (filepath, property_name, property_value, property_type)
                            VALUES (?, ?, ?, ?)
                        """,
                            (filepath, prop_name, prop_value_str, prop_type),
                        )

                await db.commit()
            except Exception:
                await db.rollback()
                raise

    async def _remove_file_locked(
        self, db: aiosqlite.Connection, filepath: str
    ) -> None:
        """Delete every row for filepath, across all three tables that index it.

        Caller must already hold self._lock and handle commit/rollback.
        Factored out so remove_file() and clear_orphaned_entries() can share
        it without clear_orphaned_entries's outer lock causing remove_file()
        to deadlock re-acquiring the same (non-reentrant) asyncio.Lock --
        confirmed empirically: calling remove_file() from inside
        clear_orphaned_entries()'s `async with self._lock` block used to hang
        indefinitely whenever there was at least one orphaned file.
        """
        await db.execute("DELETE FROM file_index WHERE filepath = ?", (filepath,))
        # See the matching comment in index_file() re: this being a full
        # shadow-table scan rather than a keyed lookup.
        await db.execute("DELETE FROM file_search WHERE filepath = ?", (filepath,))
        # file_properties.filepath declares ON DELETE CASCADE, but SQLite
        # disables FK enforcement per-connection unless a prior connection
        # remembered to run PRAGMA foreign_keys = ON (see initialize()) --
        # without this explicit delete, every removed/renamed-away file
        # leaked its property rows forever, and those orphaned rows then
        # surfaced in get_all_property_names()/get_property_values() for
        # notes that no longer exist.
        await db.execute("DELETE FROM file_properties WHERE filepath = ?", (filepath,))

    async def remove_file(self, filepath: str):
        """Remove a file from the index."""
        db = self._require_db()
        async with self._lock:
            try:
                await self._remove_file_locked(db, filepath)
                await db.commit()
            except Exception:
                await db.rollback()
                raise

    async def search_content(
        self, query: str, limit: int = 50
    ) -> list[tuple[str, str]]:
        """
        Search for content using FTS5.

        Returns list of (filepath, snippet) tuples.
        """
        db = self._require_db()
        # Use FTS5 for efficient full-text search
        cursor = await db.execute(
            """
            SELECT filepath, snippet(file_search, 1, '<b>', '</b>', '...', 32)
            FROM file_search
            WHERE file_search MATCH ?
            ORDER BY rank
            LIMIT ?
        """,
            (query, limit),
        )

        results = await cursor.fetchall()
        # aiosqlite's Row isn't structurally a tuple[str, str] as far as
        # pyright is concerned, even though it behaves like one at runtime —
        # rebuild plain tuples so the return type actually matches.
        return [(row[0], row[1]) for row in results]

    async def search_simple(self, query: str, limit: int = 50) -> dict[str, Any]:
        """
        Simple substring search with total count.

        Returns dictionary with results and metadata.
        """
        db = self._require_db()
        query_lower = query.lower()
        search_pattern = f"%{query_lower}%"

        # First get total count
        count_cursor = await db.execute(
            """
            SELECT COUNT(*)
            FROM file_index
            WHERE content_lower LIKE ?
        """,
            (search_pattern,),
        )
        # A bare COUNT(*) always returns exactly one row, but fetchone()'s
        # type is Optional regardless of the query — guard rather than assert.
        count_row = await count_cursor.fetchone()
        total_count = count_row[0] if count_row is not None else 0

        # Then get limited results
        cursor = await db.execute(
            """
            SELECT filepath, content, mtime, size
            FROM file_index
            WHERE content_lower LIKE ?
            LIMIT ?
        """,
            (search_pattern, limit),
        )

        results = []
        async for row in cursor:
            results.append(
                {"filepath": row[0], "content": row[1], "mtime": row[2], "size": row[3]}
            )

        return {
            "results": results,
            "total_count": total_count,
            "limit": limit,
            "truncated": len(results) < total_count,
        }

    async def search_regex(
        self,
        pattern: str,
        flags: int = 0,
        limit: int = 50,
        context_length: int = 100,
        max_parallel: int = 10,
    ) -> list[dict[str, Any]]:
        """
        Search using regular expressions with efficient streaming and parallel processing.

        Args:
            pattern: Regular expression pattern
            flags: Regex flags (e.g., re.IGNORECASE)
            limit: Maximum number of results
            context_length: Characters to show around match
            max_parallel: Maximum number of files to process in parallel

        Returns:
            List of search results with matches and context
        """
        import re

        # Compile regex pattern
        try:
            regex = re.compile(pattern, flags)
        except re.error as e:
            raise ValueError(f"Invalid regex pattern: {e}")

        # Check if we can use FTS5 pre-filtering
        literal_prefix = extract_literal_prefix(pattern)

        # Build query - order by size for faster initial results
        if literal_prefix and len(literal_prefix) >= 3:
            # Safe to interpolate unescaped: extract_literal_prefix()
            # guarantees literal_prefix never contains a '"', which is the
            # only character that would let it break out of this phrase
            # quoting and be reinterpreted as FTS5 query syntax.
            fts5_query = f'"{literal_prefix}"'

            # Use FTS5 to pre-filter files containing the literal prefix
            query = """
                SELECT f.filepath, f.content, f.mtime, f.size, f.line_offsets
                FROM file_index f
                JOIN file_search s ON f.filepath = s.filepath
                WHERE file_search MATCH ?
                ORDER BY f.size ASC, f.mtime DESC
            """
            params = (fts5_query,)
        else:
            # Full scan, but ordered by size
            query = """
                SELECT filepath, content, mtime, size, line_offsets
                FROM file_index
                ORDER BY size ASC, mtime DESC
            """
            params = ()

        db = self._require_db()
        cursor = await db.execute(query, params)
        # list(...): aiosqlite types fetchall() as Iterable[Row], which
        # supports neither len() nor slicing — both used below to batch rows.
        rows = list(await cursor.fetchall())

        # Process files in batches for parallel execution
        results = []
        total_results = 0
        # Clamp to the pool's actual worker count: dispatching more
        # concurrent tasks than there are workers just queues the rest
        # behind them, and asyncio.wait_for's timer starts at submission,
        # not execution -- so a perfectly ordinary file queued behind a busy
        # worker can time out on queue wait alone and get mislogged in
        # _process_file_regex as "possible catastrophic backtracking".
        # Capping here means every dispatched batch fits the pool exactly.
        batch_size = min(max_parallel, _REGEX_POOL_MAX_WORKERS)

        for i in range(0, len(rows), batch_size):
            if total_results >= limit:
                break

            batch = rows[i : i + batch_size]

            # Process batch in parallel
            batch_tasks = []
            for row in batch:
                if total_results >= limit:
                    break

                filepath, content, _mtime, size, line_offsets_json = row

                # Create task for processing this file
                task = self._process_file_regex(
                    filepath, content, size, line_offsets_json, regex, context_length
                )
                batch_tasks.append(task)

            # Wait for batch to complete
            batch_results = await asyncio.gather(*batch_tasks, return_exceptions=True)

            # Collect results
            for result in batch_results:
                # BaseException, not Exception: return_exceptions=True can
                # hand back a BaseException (e.g. CancelledError) that isn't
                # an Exception subclass, which would otherwise slip past this
                # check and crash the result["match_contexts"] access below.
                if isinstance(result, BaseException):
                    logger.error(f"Error processing file: {result}")
                    continue

                if result and result["match_contexts"]:
                    results.append(
                        {
                            "filepath": result["filepath"],
                            "match_count": len(result["match_contexts"]),
                            "matches": result["match_contexts"],
                            "score": min(
                                len(result["match_contexts"]) / 5.0 + 1.0, 5.0
                            ),
                        }
                    )
                    total_results += 1

                    if total_results >= limit:
                        break

        return results[:limit]

    async def _process_file_regex(
        self,
        filepath: str,
        content: str,
        size: int,
        line_offsets_json: str | None,
        regex,
        context_length: int,
        max_matches: int = 5,
    ) -> dict[str, Any]:
        """Process a single file for regex matches (for parallel execution).

        The actual match runs in a worker process (REGEX_MATCH_TIMEOUT_SECONDS
        above explains why a process rather than a thread), so a pathological
        pattern can never block the caller past the timeout, no matter how
        long the match itself keeps running.
        """
        # Parse line offsets if available
        try:
            line_offsets = json.loads(line_offsets_json) if line_offsets_json else None
        except json.JSONDecodeError:
            # Corrupt/foreign line_offsets JSON: fall back to a linear scan
            # for line numbers (below) instead of failing the whole file over
            # a cosmetic field. Narrowed from bare `except:` during the
            # tools/utils split -- verified deliberate: line_offsets_json is
            # str | None and, once the `if line_offsets_json` guard above
            # rules out None/empty, json.loads() on a str raises nothing but
            # JSONDecodeError for content it can't parse.
            line_offsets = None

        # For large files, stream in chunks; both paths run in the process
        # pool below so neither can stall the event loop.
        worker = (
            search_large_file_content if size > 1024 * 1024 else search_file_content
        )
        loop = asyncio.get_running_loop()
        pool = self._get_regex_process_pool()
        try:
            match_contexts = await asyncio.wait_for(
                loop.run_in_executor(
                    pool,
                    worker,
                    content,
                    regex,
                    line_offsets,
                    context_length,
                    max_matches,
                ),
                timeout=REGEX_MATCH_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            logger.warning(
                f"Regex match on {filepath!r} exceeded "
                f"{REGEX_MATCH_TIMEOUT_SECONDS}s (possible catastrophic "
                "backtracking) -- skipping this file's results"
            )
            match_contexts = []
            # See _REGEX_POOL_RECYCLE_THRESHOLD: enough consecutive timeouts
            # means every worker is probably wedged on a pathological
            # pattern, so recycle the whole pool rather than let every later
            # search silently degrade to this same empty result forever.
            self._consecutive_regex_timeouts += 1
            if self._consecutive_regex_timeouts >= _REGEX_POOL_RECYCLE_THRESHOLD:
                self._recycle_regex_process_pool()
        else:
            self._consecutive_regex_timeouts = 0

        return {"filepath": filepath, "match_contexts": match_contexts}

    async def get_all_files(self) -> list[str]:
        """Get list of all indexed files."""
        db = self._require_db()
        cursor = await db.execute("SELECT filepath FROM file_index")
        files = [row[0] for row in await cursor.fetchall()]
        return files

    async def get_stats(self) -> dict[str, Any]:
        """Get index statistics."""
        db = self._require_db()
        cursor = await db.execute(
            "SELECT COUNT(*), SUM(size), MAX(last_indexed) FROM file_index"
        )
        row = await cursor.fetchone()
        # Same bare-aggregate case as search_simple's count: always exactly
        # one row in practice, but fetchone() is typed Optional regardless.
        if row is None:
            return {"total_files": 0, "total_size": 0, "last_update": None}

        # datetime.fromtimestamp() without tz is intentional here, not an
        # oversight: last_indexed is a local file-mtime-derived epoch float,
        # and the rest of the codebase (filesystem.py's read_note metadata,
        # search_discovery.py's date search) consistently renders these as
        # naive local wall-clock time and compares them against other naive
        # datetimes built the same way. Making just this one tz-aware would
        # both break that comparison convention elsewhere and show a
        # different (UTC) time than every other timestamp in a response.
        return {
            "total_files": row[0] or 0,
            "total_size": row[1] or 0,
            "last_update": datetime.fromtimestamp(row[2]) if row[2] else None,
        }

    async def clear_orphaned_entries(self, existing_files: set):
        """Remove index entries for files that no longer exist."""
        db = self._require_db()
        async with self._lock:
            cursor = await db.execute("SELECT filepath FROM file_index")
            indexed_files = {row[0] for row in await cursor.fetchall()}

            orphaned = indexed_files - existing_files

            # Uses _remove_file_locked directly rather than calling
            # remove_file() in the loop: remove_file() acquires self._lock
            # itself, and asyncio.Lock is not reentrant, so calling it while
            # already holding the lock here used to deadlock indefinitely
            # (confirmed empirically) the moment there was at least one
            # orphaned file. Batched into one commit/rollback too, so a
            # failure partway through can't leave some orphans cleared and
            # others not.
            try:
                for filepath in orphaned:
                    await self._remove_file_locked(db, filepath)
                await db.commit()
            except Exception:
                await db.rollback()
                raise

        for filepath in orphaned:
            logger.info(f"Removed orphaned index entry: {filepath}")

    async def search_by_property(
        self,
        property_name: str,
        operator: str,
        value: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """
        Search for files by property values.

        Args:
            property_name: Name of the property to search
            operator: Comparison operator (=, !=, >, <, >=, <=, contains, exists)
            value: Value to compare against (optional for 'exists')
            limit: Maximum number of results

        Returns:
            List of file info with matching properties
        """
        # Build SQL query based on operator
        if operator == "exists":
            sql = """
                SELECT DISTINCT f.filepath, f.content, p.property_value, p.property_type
                FROM file_index f
                JOIN file_properties p ON f.filepath = p.filepath
                WHERE p.property_name = ?
                ORDER BY f.mtime DESC
                LIMIT ?
            """
            params = (property_name, limit)

        elif operator == "contains":
            sql = """
                SELECT DISTINCT f.filepath, f.content, p.property_value, p.property_type
                FROM file_index f
                JOIN file_properties p ON f.filepath = p.filepath
                WHERE p.property_name = ? AND p.property_value LIKE ?
                ORDER BY f.mtime DESC
                LIMIT ?
            """
            params = (property_name, f"%{value}%", limit)

        elif operator == "!=":
            # For not equal, we need to include files without the property
            sql = """
                SELECT DISTINCT f.filepath, f.content, 
                       COALESCE(p.property_value, '') as property_value,
                       COALESCE(p.property_type, '') as property_type
                FROM file_index f
                LEFT JOIN file_properties p ON f.filepath = p.filepath AND p.property_name = ?
                WHERE p.property_value IS NULL OR p.property_value != ?
                ORDER BY f.mtime DESC
                LIMIT ?
            """
            params = (property_name, value, limit)

        else:  # =, >, <, >=, <=
            # For numeric comparisons, we need to handle type conversion
            if operator in [">", "<", ">=", "<="]:
                sql = f"""
                    SELECT DISTINCT f.filepath, f.content, p.property_value, p.property_type
                    FROM file_index f
                    JOIN file_properties p ON f.filepath = p.filepath
                    WHERE p.property_name = ? AND 
                          ((p.property_type = 'number' AND CAST(p.property_value AS REAL) {operator} CAST(? AS REAL)) OR
                           (p.property_type != 'number' AND p.property_value {operator} ?))
                    ORDER BY f.mtime DESC
                    LIMIT ?
                """
                params = (property_name, value, value, limit)
            else:  # = operator
                sql = """
                    SELECT DISTINCT f.filepath, f.content, p.property_value, p.property_type
                    FROM file_index f
                    JOIN file_properties p ON f.filepath = p.filepath
                    WHERE p.property_name = ? AND LOWER(p.property_value) = LOWER(?)
                    ORDER BY f.mtime DESC
                    LIMIT ?
                """
                params = (property_name, value, limit)

        db = self._require_db()
        cursor = await db.execute(sql, params)

        results = []
        async for row in cursor:
            results.append(
                {
                    "filepath": row[0],
                    "content": row[1],
                    "property_value": row[2],
                    "property_type": row[3] if len(row) > 3 else None,
                }
            )

        return results

    async def get_all_property_names(self) -> list[str]:
        """Get a list of all unique property names in the index."""
        db = self._require_db()
        cursor = await db.execute("""
            SELECT DISTINCT property_name
            FROM file_properties
            ORDER BY property_name
        """)

        return [row[0] for row in await cursor.fetchall()]

    async def get_property_values(self, property_name: str) -> list[tuple[str, int]]:
        """Get all unique values for a property with counts."""
        db = self._require_db()
        cursor = await db.execute(
            """
            SELECT property_value, COUNT(*) as count
            FROM file_properties
            WHERE property_name = ?
            GROUP BY property_value
            ORDER BY count DESC, property_value
        """,
            (property_name,),
        )

        # See search_content: rebuild plain tuples to match the declared
        # return type instead of returning aiosqlite's Row objects directly.
        return [(row[0], row[1]) for row in await cursor.fetchall()]
