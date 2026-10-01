# Vault index sync — design

Date: 2026-09-30 · Status: approved · Branch: `feat/vault-index-sync`

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

`OBSIDIAN_AUTO_INDEX_UPDATE=false` is manual mode: the index is built once on
first use, then changes only through write-through and `sync_vault_index_tool`.
Env var defaults are unchanged.

### Components

**`utils/vault_cache.py`**
- `iter_md_files(vault_path)` — module-level generator (moved out of
  `VaultCache._iter_md_files`) yielding `(posix relpath, os.stat_result)` for
  every `*.md` file; shared with the SQLite pass.
- `VaultCache.sync()` — forces a stat-diff now, ignoring the TTL; no-op before
  the first build (the first access full-scans anyway).
- Fix: drop the line that restores the old stat key for a changed note.

**`utils/persistent_index.py`**
- `get_file_stats() -> dict[str, tuple[float, int]]` — `(mtime, size)` per
  indexed file in one `SELECT`.
- Remove `needs_update` and `get_file_info` (their only production caller, the
  per-file check, goes away).
- Docstring: default DB name is `mcp-search-index.db`.

**`utils/filesystem.py` (`ObsidianVault`)**
- `ensure_index_fresh()` — the single gate before any SQLite read. Fast path
  without a lock when initialized and fresh; otherwise takes `_index_lock`,
  initializes the index if needed (single-flight), re-checks, and runs the
  reconcile pass *awaited*. Fresh means: built at least once, and either auto
  update is off or `0 < elapsed <= interval`.
- `sync_index(full=False) -> dict` — `cache.sync()`, then the pass under
  `_index_lock`, ignoring the interval.
- `_update_persistent_index(full=False) -> dict` — rewritten: vault folder
  guard, `iter_md_files` + `get_file_stats`, in-memory diff, index new/changed
  files (all files when `full`), `clear_orphaned_entries`, set
  `_index_timestamp`, return
  `{scanned, added, updated, removed, failed, full, duration_ms}`. Keeps the
  `OBSIDIAN_INDEX_BATCH_SIZE` batching for progress logs; drops the sleep.
- Write-through `_index_note_mutated(full_path, content)` — called right after
  `cache.note_mutated` in `write_note` / `delete_note`; under `_index_lock`;
  no-op until the index is initialized; relpath derived from the resolved
  `full_path` (same form as the walk). Move/rename/folder moves are covered
  because they are `write_note` + `delete_note`.
- `search_notes` and `search_by_regex` call `ensure_index_fresh()` instead of
  their own gates. Removed: `_start_background_index_update`,
  `_update_search_index_async`, `_update_search_index`,
  `_index_update_in_progress`, `_index_update_task`.

**`tools/search_property_engine.py`** — when the persistent index exists, call
`vault.ensure_index_fresh()` before querying it.

**`sync_vault_index_tool(full=False)`** — standard tool pair:
`tools/index_sync.py` (`sync_vault_index`), export in `tools/__init__.py`,
wrapper in `mcp_search.py`, re-export in `server.py`. Returns
`{success, scanned, added, updated, removed, failed, full, duration_ms}`
(`RESPONSE_STRUCTURES["index_sync"]`). Docstring "When to use": after vault
files changed outside this server; when a result contradicts the disk. "When
NOT to use": after this server's own write tools; routinely before every search.

**Agent guidance** — `app.py` builds the FastMCP `instructions` from the live
config: which changes are tracked automatically, the real interval, and "after
changing vault files outside this server, call `sync_vault_index_tool` before
the next search, tag, or link query". Manual mode says the call is required.
The `help_tool` catalog, README, and CLAUDE.md files document the model.

### Error handling

- Write-through failure never fails the write (the file is already on disk):
  log a warning and reset `_index_timestamp` so the next query reconciles.
- Vault folder missing (unmounted drive/share): the pass raises `RuntimeError`
  with `ERROR_MESSAGES["vault_unavailable"]` **before** touching the index —
  today an empty walk would orphan and delete every entry.
- Per-file read/index failure: logged, counted in `failed`, pass continues.
- Index init failure: existing `RuntimeError` messages; wrappers turn them
  into `ToolError`.

### Concurrency

Lock order is `write_lock` → `_index_lock` (write tools), and `cache._lock` is
never held together with `_index_lock`. The pass never takes `write_lock`, so
no cycle. Write-through waiting on `_index_lock` prevents a concurrent pass
from deleting a just-written note as an orphan.

## Testing

TDD. New `tests/test_index_sync.py` covers, at the vault level: outside
create / modify / delete / rename visible after `sync_index`; interval expiry
reconciles before a query; interval 0 reconciles every query; manual mode only
syncs on demand; MCP create / update / delete / move searchable without sync;
write-through failure does not fail the write and self-heals; missing vault
folder raises and keeps the index; `full=True` re-indexes everything;
concurrent first queries initialize once. Plus: property search sees an
outside frontmatter change; `VaultCache.sync()` and the line-218 regression;
`get_file_stats`; the tool wrapper (success + `ToolError`); instructions name
the tool. Existing tests move from `_update_search_index` to `sync_index`;
tool-count assertions go from 30 to 31.

## Compatibility

- No env var added or removed; defaults unchanged.
- `search_by_regex` now honors `OBSIDIAN_AUTO_INDEX_UPDATE=false` (manual
  mode) like the other searches.
- On Windows, index keys switch from `\` to `/` separators (same form the cache
  uses); the first pass re-indexes once.
