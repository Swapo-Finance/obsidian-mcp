# Vault index sync — design

Date: 2026-09-30 · Status: approved; revised after final review · Branch: `feat/vault-index-sync`

## Problem

Users and other agents edit files in the vault folder directly — without this
server's tools (Obsidian app, editors, shell, git, sync clients, other agents'
own file tools). The SQLite search index at `<vault>/.obsidian/mcp-search-index.db`
then drifts from the files on disk, so text, regex, and property searches
return missing, stale, or phantom results.

## Findings (current behavior, verified in code)

1. **No MCP write updates the SQLite index.** `ObsidianVault.write_note` /
   `delete_note` only call `cache.note_mutated` (the in-memory `VaultCache`).
   `create_note` followed by `search_notes` misses the new note today, with no
   outside edit involved.
2. **One reconcile pass exists: `_update_persistent_index`.** Its logic is
   sound (mtime/size diff, re-read changed files only, purge deleted ones via
   `clear_orphaned_entries`). The problem is *when* it runs:
   - `search_notes` schedules it as a fire-and-forget background task at most
     every `OBSIDIAN_INDEX_UPDATE_INTERVAL` s (300) and answers immediately from
     the old DB — empty on first use.
   - `search_by_regex` awaits it (correct), ignoring `OBSIDIAN_AUTO_INDEX_UPDATE`.
   - `search_by_property` reads the index if it exists, never refreshes it.
   - Results are served from stored content, so a note deleted on disk keeps
     showing up until the next pass.
3. **The pass is slower than it needs to be:** one awaited `SELECT` per file,
   `Path.rglob` (measured 3.3x slower than `os.walk` in `vault_cache.py`), and
   `asyncio.sleep(0.1)` per 50 changed files (~10 s of pure sleep on a
   5,000-note first build).
4. **`VaultCache` (tags, links, names) already self-heals** via a TTL-gated
   stat-diff (`OBSIDIAN_CACHE_STAT_TTL_SECONDS`, 30 s). Bug at
   `vault_cache.py:218`: an externally modified note's snapshot entry is reset
   to its *old* stat key, so it is re-read on every later stat-diff until a
   restart or an MCP write. Results stay correct; cost grows.
5. **Lazy index init is not single-flight:** two concurrent first queries can
   both construct and initialize a `PersistentSearchIndex`.
6. Direct reads (`read_note`, `list_notes`, `search_by_date`, outgoing links)
   hit the disk and are always current.
7. Docs are wrong: `OBSIDIAN_SEARCH_INDEX_THRESHOLD` does not gate the SQLite
   index (it only picks the search response shape).

## Goals

- Every SQLite-backed query reflects the disk with a known, bounded delay.
- MCP writes are searchable immediately.
- An on-demand tool forces a sync now, and agents are told when to call it.
- No new dependency, no background task, no new env var.

## Non-goals

- Real-time file watching (`watchfiles`/`watchdog`).
- Client-side hooks (Claude Code `PostToolUse` recipes).
- Excluding `.trash/` / `.obsidian/` notes from indexing (affects every tool;
  separate task).
- Faster cold builds (per-file commit in `index_file`), moving the walk off the
  event loop, or relocating the DB out of `.obsidian/`.

## Design

### Freshness model

| Change made by | Text / regex / property search | Tags / links / names |
| --- | --- | --- |
| This server's write tools | immediate (write-through) | immediate (`note_mutated`) |
| Anything else | ≤ `OBSIDIAN_INDEX_UPDATE_INTERVAL` (300 s), or immediate after `sync_vault_index_tool` | ≤ `OBSIDIAN_CACHE_STAT_TTL_SECONDS` (30 s), or immediate after `sync_vault_index_tool` |

Text, regex, and property search call `ensure_index_fresh()` before reading the SQLite
index; property search does so only when the index already exists, so only text search,
regex search, and `sync_index` ever build it. A re-check is due when the index is marked
dirty or, with auto update on, `OBSIDIAN_INDEX_UPDATE_INTERVAL` has elapsed (`0` = before
every search). A due re-check runs before the query and the query waits for it; a query
that arrives while a pass or a write-through holds `_index_lock` also waits for it. Where
write-through cannot update the index it marks it dirty instead, so a write is visible to
the next query either way.

`OBSIDIAN_AUTO_INDEX_UPDATE=false` is manual mode: the index is built on the first
text or regex search, then changes only through write-through, a dirty mark, and
`sync_vault_index_tool` — no timed re-check. Tags, links, and names re-check within
`OBSIDIAN_CACHE_STAT_TTL_SECONDS` in both modes. Env var defaults are unchanged.

**Dirty flag.** Anything that may leave the index behind the disk marks it dirty, and
the next query reconciles before reading:

- a write whose write-through cannot update the index: `_index_lock` is held (a pass is
  running), the caller's spelling of the path differs from the on-disk one, or it fails;
  `write_note` / `delete_note` also mark it on any failure or cancellation from the disk
  write on;
- a pass that does not finish (error or cancellation).

The flag is cleared when a pass starts, so a mark made while the pass runs survives it.
The freshness clock is monotonic, so a wall-clock change cannot make a stale index look
fresh.

### Components

**`utils/vault_cache.py`**
- `iter_md_files(vault_path)` — module-level generator (moved out of
  `VaultCache._iter_md_files`) yielding `(posix relpath, os.stat_result)` for
  every `*.md` file; shared with the SQLite pass. It skips non-regular entries
  (FIFOs, sockets, devices named `*.md`) and symlinks that resolve outside the
  vault; symlinks that stay inside the vault keep working.
- `VaultCache.sync(full=False)` — forces a stat-diff now, ignoring the TTL;
  no-op before the first build (the first access full-scans anyway). `full=True`
  runs a full scan instead (and builds a cache that never was), which also
  catches edits that keep size and mtime.
- Fix: drop the line that restores the old stat key for a changed note.

**`utils/persistent_index.py`**
- `get_file_stats() -> dict[str, tuple[float, int]]` — `(mtime, size)` per
  indexed file in one `SELECT`.
- `invalidate_all()` — one `UPDATE` setting every stored mtime to -1 (rows and
  content stay): every file then differs from its disk stamp, so an ordinary
  pass re-indexes all of them, and a pass that dies midway leaves the files it
  did not reach marked stale.
- Remove `needs_update` and `get_file_info` (their only production caller, the
  per-file check, goes away).
- Docstring: default DB name is `mcp-search-index.db`.

**`utils/filesystem.py` (`ObsidianVault`)**
- `ensure_index_fresh()` — the single gate before any SQLite read. Without a
  lock when the index is built, not dirty, the re-check is not due, and
  `_index_lock` is free; otherwise waits for `_index_lock` (a pass commits note
  by note, so a query that arrives while a pass or a write-through holds it
  waits rather than read a half-updated index), initializes the index if needed
  (single-flight; the first text or regex search builds it inline), re-checks,
  and runs the reconcile pass *awaited*. Fresh means: built at least once, not
  dirty, and either auto update is off or `0 < elapsed <= interval` on a
  monotonic clock.
- `sync_index(full=False) -> dict` — vault folder guard, `cache.sync(full)`,
  then, under `_index_lock`, the pass, ignoring the interval. `full=True` makes
  the cache do a full scan and calls the index's `invalidate_all()` before the
  pass, so the normal pass re-indexes every note and an interrupted full sync is
  finished by the next pass instead of starting over. Returns the pass's counts
  plus `full`.
- `_update_persistent_index() -> dict` — rewritten: vault folder guard, clear
  the dirty flag, `get_file_stats` + `iter_md_files`, guard again (a folder that
  vanished mid-walk reads as an empty vault, and sweeping against it would drop
  every row), in-memory diff, index new/changed files, `clear_orphaned_entries`,
  refresh the freshness clock, return
  `{scanned, added, updated, removed, failed, duration_ms}`. A pass that does
  not finish (error or cancellation) marks the index dirty again. Keeps the
  `OBSIDIAN_INDEX_BATCH_SIZE` batching for progress logs (raised to at least 1);
  drops the sleep.
- Write-through `_index_note_mutated` — called right after `cache.note_mutated`
  in `write_note` / `delete_note`; no-op until the index is initialized. It
  marks the index dirty and returns, touching no row, when the caller's spelling
  of the path differs from the on-disk one (NFC vs NFD, or case on APFS) or when
  `_index_lock` is held: it does not wait for a running pass, whose walk may
  predate the write. Otherwise it takes `_index_lock`; a write stats the file
  first and re-reads it from disk (not the caller's in-memory content), so the
  stored text always matches the stored (mtime, size), and the row is keyed by
  the on-disk spelling, the walk's form. A failure there is logged and marks the
  index dirty; `write_note` / `delete_note` mark it dirty on any failure or
  cancellation from the disk write on. Move/rename/folder moves are covered
  because they are `write_note` + `delete_note`.
- `search_notes` and `search_by_regex` call `ensure_index_fresh()` instead of
  their own gates. Removed: `_start_background_index_update`,
  `_update_search_index_async`, `_update_search_index`,
  `_index_update_in_progress`, `_index_update_task`.

**`tools/search_property_engine.py`** — when the persistent index exists, calls
`vault.ensure_index_fresh()` before reading it, outside the `try` that guards the
index query: a gate failure (an unreachable vault) surfaces like it does for text
and regex search instead of falling through to the manual scan, which would
answer from a folder it cannot see. Property search never builds the index;
until a text or regex search or a sync has, it uses that manual scan.

**`sync_vault_index_tool(full=False)`** — standard tool pair:
`tools/index_sync.py` (`sync_vault_index`), export in `tools/__init__.py`,
wrapper in `mcp_search.py`, re-export in `server.py`. Returns
`{success, scanned, added, updated, removed, failed, full, duration_ms}`
(`RESPONSE_STRUCTURES["index_sync"]`). Docstring "When to use": after vault
files changed outside this server; when a result contradicts the disk. "When
NOT to use": after this server's own write tools; routinely before every search.
`full=True` also rebuilds the notes cache with a full scan and resumes if
interrupted (see `sync_index`).

**Agent guidance** — `app.py` builds the FastMCP `instructions` from the live
config: which changes are tracked automatically, the real interval, and "after
changing vault files outside this server, call `sync_vault_index_tool` and wait
for its result before the next search, tag, or link query". Manual mode says the
call is required for text, regex, and property search results (tag, link, and
name results still re-check on their own).
The `help_tool` catalog, README, and CLAUDE.md files document the model.

### Error handling

- Write-through failure never fails the write (the file is already on disk):
  log a warning and mark the index dirty so the next query reconciles. A
  cancellation after the write reached the disk marks it dirty too.
- Vault folder not reachable (unmounted drive/share): the pass raises
  `RuntimeError` with `ERROR_MESSAGES["vault_unavailable"]` ("Vault folder is not
  reachable") **before** touching the index — today an empty walk would orphan
  and delete every entry — and checks the folder again after the walk.
  `sync_index` checks first too, before it touches the cache. Text and regex
  search and `sync_vault_index_tool` surface this error, and so does property
  search once the index exists; the wrappers turn it into `ToolError`.
- A pass that does not finish (error or cancellation) marks the index dirty
  again, so the next query resumes it. A `full` sync resumes the same way
  because its stored stats were invalidated up front.
- Per-file read/index failure: logged, counted in `failed`, pass continues.
- Index init failure: existing `RuntimeError` messages; wrappers turn them
  into `ToolError`.

### Concurrency

Lock order is `write_lock` → `_index_lock` (write tools), and `cache._lock` is
never held together with `_index_lock`. The pass never takes `write_lock`, so
no cycle. A write that finds `_index_lock` held does not wait for the pass
holding it, so it does not block behind a long one (a first build of a large
vault can take over a minute): the write-through marks the index dirty instead,
and the next query reconciles. `asyncio.Lock` has no try-acquire, so the check
is `locked()`, and that is not airtight: the lock is FIFO, so a write that
arrives right after a pass releases it queues behind every query already
waiting, and the first of those may run one incremental reconcile pass (if the
index is dirty). The wait is bounded by that one incremental pass plus quick
handoffs. Queries do the opposite: a query that
arrives while a pass or a write-through holds `_index_lock` waits for it. The
dirty flag is cleared when a pass starts, so a mark made mid-pass is not lost.

## Testing

TDD. New `tests/test_index_sync.py` and `tests/test_index_sync_writes.py` (the
write path and its concurrency) cover, at the vault level: outside
create / modify / delete / rename visible after `sync_index`; interval expiry
reconciles before a query; interval 0 reconciles every query; manual mode never
re-checks on the interval; MCP create / update / delete / move searchable without sync;
write-through failure does not fail the write and self-heals; missing vault
folder raises and keeps the index; `full=True` re-indexes everything;
concurrent first queries initialize once. Plus: property search sees an
outside frontmatter change; `VaultCache.sync()` and the line-218 regression;
`get_file_stats`; the tool wrapper (success + `ToolError`); instructions name
the tool. Existing tests move from `_update_search_index` to `sync_index`;
tool-count assertions go from 30 to 31.

## Compatibility

- No env var added or removed; defaults unchanged. `OBSIDIAN_INDEX_BATCH_SIZE`
  now has a minimum of 1 (smaller values are raised to 1).
- `search_by_regex` now honors `OBSIDIAN_AUTO_INDEX_UPDATE=false` (manual
  mode) like the other searches.
- On Windows, index keys switch from `\` to `/` separators (same form the cache
  uses); the first pass re-indexes once.
- Symlinked notes that resolve outside the vault and non-regular `*.md` entries
  (FIFOs, sockets, devices) are no longer indexed; symlinks that stay inside the
  vault keep working.
- Property search now reports an unreachable vault folder as an error once the
  index exists, like text and regex search.
