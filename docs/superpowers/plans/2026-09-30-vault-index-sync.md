# Vault Index Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep the SQLite search index in step with the vault files on disk — awaited reconcile before queries, write-through for MCP writes, and an on-demand `sync_vault_index_tool` that agents are told to call after outside edits.

**Architecture:** `ObsidianVault.ensure_index_fresh()` becomes the single gate before every SQLite read (text, regex, property search); it awaits a cheaper reconcile pass (one bulk `SELECT` + `os.walk` diff) when `OBSIDIAN_INDEX_UPDATE_INTERVAL` has passed. `write_note`/`delete_note` write through to SQLite like they already do to `VaultCache`. `ObsidianVault.sync_index()` forces both stores now and backs the new tool; FastMCP `instructions` tell agents when to call it.

**Tech Stack:** Python ≥3.10, FastMCP 2.12, aiosqlite (WAL), aiofiles, pytest + pytest-asyncio (strict mode: `@pytest.mark.asyncio` + `pytest_asyncio.fixture`), ruff, pyright (standard), uv.

**Spec:** `docs/superpowers/specs/2026-09-30-vault-index-sync-design.md`

## Global Constraints

- No new dependency, no new env var, no background task. Env defaults unchanged: `OBSIDIAN_INDEX_UPDATE_INTERVAL=300`, `OBSIDIAN_AUTO_INDEX_UPDATE=true`, `OBSIDIAN_INDEX_BATCH_SIZE=50`, `OBSIDIAN_CACHE_STAT_TTL_SECONDS=30`.
- Code, comments, docstrings, tool descriptions, and docs in English (repo convention).
- `obsidian_mcp/utils/` stays free of MCP concepts (no `Context`, no `ToolError`, no tool names in logic).
- Every `@mcp.tool()` wrapper holds only the `Annotated[..., Field(...)]` schema, a "When to use / When NOT to use" docstring, and a try/except re-raising `ToolError`; logic lives in `tools/`.
- Error text lives in `constants.ERROR_MESSAGES` in the actionable "To fix: 1) … 2) …" form.
- Gates: `uv run ruff check obsidian_mcp tests` → zero findings; `uv run pyright` → `0 errors`; full suite `OBSIDIAN_VAULT_PATH=$(mktemp -d) uv run pytest -q` → all pass.
- Implementers never run `git commit` (`.claude/rules/08-parallel-subagent-driven-development.md`); the controller commits per task.
- Tests that open a SQLite index close it in teardown (`await v.persistent_index.close()` when set) — an unclosed aiosqlite connection can hang interpreter exit.

## Review Focus

1. A note that is not valid UTF-8 → the pass counts it in `failed`, indexes every other note, never raises (Task 3 test `test_unreadable_note_is_counted_not_fatal`).
2. Non-note files (`.png`, `.markdown`) next to notes → ignored by the pass; `scanned` counts only `*.md`, including nested folders (Task 3 test `test_only_md_files_are_scanned`).
3. Accented filenames (`notas/café.md`) → an outside create is found under that exact path, and an MCP delete of it removes the entry (write-through key == walk key) (Task 3 test `test_accented_paths_match_between_walk_and_write_through`).
4. Empty vault → first search returns `[]`, sync returns all-zero counts (Task 3 test `test_empty_vault`).
5. `sync_vault_index_tool` called before any search on a fresh server → builds the index (`added == scanned`) (Task 3 test `test_sync_on_a_fresh_server_builds_the_index`).

## Execution waves

| Wave | Tasks | Why grouped |
| --- | --- | --- |
| 1 | Task 1, Task 2 | disjoint files, no dependencies |
| 2 | Task 3 | consumes Tasks 1–2 |
| 3 | Task 4, Task 5 | both consume Task 3; disjoint files |
| 4 | Task 6 | documents everything above |

---

### Task 1: VaultCache — shared walker, explicit `sync()`, stale-snapshot fix

**Files:**
- Modify: `obsidian_mcp/utils/vault_cache.py` (module docstring; new module function `iter_md_files`; delete method `_iter_md_files` at ~151-174; new method `sync`; `_stat_diff_locked` ~200-221)
- Test: `tests/test_vault_cache_freshness.py` (append two classes before `if __name__ == "__main__":`)

**Depends-on:** none

**Interfaces:**
- Consumes: nothing new.
- Produces:
  - `iter_md_files(vault_path: str | os.PathLike[str]) -> Iterator[tuple[str, os.stat_result]]` — module-level in `obsidian_mcp/utils/vault_cache.py`; yields `("folder/note.md", os.stat_result)` with `/` separators for every `*.md` under `vault_path` (not following symlinked dirs).
  - `async VaultCache.sync() -> None` — stat-diff now, ignoring the TTL; no-op before the first build.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_vault_cache_freshness.py`, just above `if __name__ == "__main__":`:

```python
class TestSyncForcesStatDiff:
    @pytest.mark.asyncio
    async def test_sync_picks_up_external_change_without_waiting_for_ttl(
        self, vault, monkeypatch
    ):
        counts = _count_scans(monkeypatch)
        await build_vault_notes_index(vault)  # lazy full scan
        (vault.vault_path / "External.md").write_text("# External\n")

        await vault.cache.sync()

        index = await build_vault_notes_index(vault)  # still inside the TTL
        assert "External.md" in index
        assert counts["stat_diff"] == 1

    @pytest.mark.asyncio
    async def test_sync_before_first_build_does_not_scan(self, vault, monkeypatch):
        counts = _count_scans(monkeypatch)

        await vault.cache.sync()

        assert counts == {"full_scan": 0, "stat_diff": 0}


class TestExternalModifyReadOnce:
    @pytest.mark.asyncio
    async def test_externally_modified_note_is_reread_once_not_on_every_stat_diff(
        self, vault, monkeypatch
    ):
        (vault.vault_path / "A.md").write_text("# A\n")
        await build_vault_notes_index(vault)  # lazy full scan

        (vault.vault_path / "A.md").write_text("# A, edited outside the server\n")

        reads: list[str] = []
        original_read_text = VaultCache._read_text

        async def counting_read_text(self, relpath):
            reads.append(relpath)
            return await original_read_text(self, relpath)

        monkeypatch.setattr(VaultCache, "_read_text", counting_read_text)

        await vault.cache.sync()  # sees the edit: one re-read
        await vault.cache.sync()  # nothing changed since: no re-read

        assert reads == ["A.md"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `OBSIDIAN_VAULT_PATH=$(mktemp -d) uv run pytest tests/test_vault_cache_freshness.py -v`
Expected: the 3 new tests FAIL with `AttributeError: 'VaultCache' object has no attribute 'sync'`; all pre-existing tests PASS.

- [ ] **Step 3: Add `iter_md_files` and `sync()`, delete `_iter_md_files`**

In `obsidian_mcp/utils/vault_cache.py`:

1. Imports become:

```python
import asyncio
import os
import time
from collections.abc import Iterator

from .links import extract_links_from_content
from .vault_config import derive_note_description, derive_note_name
```

2. Add this module-level function between the imports and `class VaultCache`:

```python
def iter_md_files(
    vault_path: str | os.PathLike[str],
) -> Iterator[tuple[str, os.stat_result]]:
    """(relpath, os.stat_result) for every *.md file under vault_path, with
    "/"-separated relpaths -- matches vault.list_notes()'s "**/*.md" glob
    (only .md, not .markdown, for consistency with the rest of the codebase).
    Shared by VaultCache's stat-diff and ObsidianVault's SQLite index pass,
    so both stores key notes identically.

    Deliberately os.walk/os.stat instead of Path.rglob/.stat(): this runs on
    every freshness check, and measured ~3.3x faster than the pathlib
    equivalent on a synthetic 5,000-file tree (60ms vs 196ms/iter) --
    pathlib's per-entry object overhead is not free on a vault-sized tree.
    Keep this one function os.path-based; don't "fix" it to match the rest
    of the codebase.
    """
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
```

3. Delete the whole `def _iter_md_files(self):` method (its docstring moved to `iter_md_files` above). Replace its two call sites — in `_full_scan_locked` and `_stat_diff_locked` — `for relpath, stat in self._iter_md_files():` with:

```python
        for relpath, stat in iter_md_files(self._vault.vault_path):
```

4. In the `# Freshness` section, add `sync` directly above `async def _ensure_fresh`:

```python
    async def sync(self) -> None:
        """Stat-diff against the disk now, ignoring
        OBSIDIAN_CACHE_STAT_TTL_SECONDS -- the explicit resync behind
        ObsidianVault.sync_index() (sync_vault_index_tool). No-op before the
        first build: the first real access full-scans the current disk state
        anyway.
        """
        async with self._lock:
            if self._built:
                await self._stat_diff_locked()
```

5. In the module docstring, after item "2. External changes …" paragraph, add one paragraph:

```text
sync() runs that same stat-diff immediately, ignoring the TTL — used by
ObsidianVault.sync_index() when an agent calls sync_vault_index_tool.
```

- [ ] **Step 4: Run tests — expect only the read-once regression to fail**

Run: `OBSIDIAN_VAULT_PATH=$(mktemp -d) uv run pytest tests/test_vault_cache_freshness.py -v`
Expected: `test_sync_*` PASS; `test_externally_modified_note_is_reread_once_not_on_every_stat_diff` FAILS with `assert ['A.md', 'A.md'] == ['A.md']` (the bug).

- [ ] **Step 5: Fix the stale-snapshot bug**

In `_stat_diff_locked`, delete this line (inside `for relpath in changed:` → `if content is not None:`):

```python
                current[relpath] = self._stat_snapshot.get(relpath, current[relpath])
```

`current[relpath]` already holds the fresh `(st_mtime_ns, st_size)` from the walk; the deleted line put the *old* key back for every modified note, so each later stat-diff saw it as changed again. The loop becomes:

```python
        for relpath in changed:
            content = await self._read_text(relpath)
            self._deindex_note(relpath)
            if content is not None:
                self._index_note(relpath, content)
```

- [ ] **Step 6: Run the file, then lint and typecheck**

Run: `OBSIDIAN_VAULT_PATH=$(mktemp -d) uv run pytest tests/test_vault_cache_freshness.py -v`
Expected: all PASS.
Run: `uv run ruff check obsidian_mcp tests && uv run pyright`
Expected: no ruff findings; `0 errors, 0 warnings, 0 informations`.

- [ ] **Step 7: Controller commits**

```bash
git add obsidian_mcp/utils/vault_cache.py tests/test_vault_cache_freshness.py
git commit -m "fix(cache): add explicit sync and stop re-reading externally edited notes"
```

---

### Task 2: PersistentSearchIndex — bulk `get_file_stats()`

**Files:**
- Modify: `obsidian_mcp/utils/persistent_index.py` (`__init__` docstring ~151; add `get_file_stats` right after `needs_update` ~384-394)
- Test: `tests/test_persistent_index.py` (add one test method right after `test_incremental_update` ~63-82)

**Depends-on:** none

**Interfaces:**
- Consumes: nothing new.
- Produces: `async PersistentSearchIndex.get_file_stats() -> dict[str, tuple[float, int]]` — `{filepath: (mtime, size)}` for every row of `file_index`, one query. Purely additive: `get_file_info` / `needs_update` stay until Task 3 removes their last caller (keeps this task's commit green).

- [ ] **Step 1: Write the failing test**

In `tests/test_persistent_index.py`, add this method right after `test_incremental_update`:

```python
    @pytest.mark.asyncio
    async def test_get_file_stats_returns_stored_mtime_and_size(self, test_vault_dir):
        """One query returns (mtime, size) for every indexed file -- what
        ObsidianVault's reconcile pass diffs against the disk."""
        index = PersistentSearchIndex(Path(test_vault_dir))
        await index.initialize()
        assert await index.get_file_stats() == {}

        await index.index_file("a.md", "Content A", 1000.0, 10)
        await index.index_file("dir/b.md", "Content B", 2000.5, 20)

        assert await index.get_file_stats() == {
            "a.md": (1000.0, 10),
            "dir/b.md": (2000.5, 20),
        }

        await index.remove_file("a.md")
        assert await index.get_file_stats() == {"dir/b.md": (2000.5, 20)}

        await index.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `OBSIDIAN_VAULT_PATH=$(mktemp -d) uv run pytest tests/test_persistent_index.py -v`
Expected: `test_get_file_stats_returns_stored_mtime_and_size` FAILS with `AttributeError: 'PersistentSearchIndex' object has no attribute 'get_file_stats'`; the rest PASS.

- [ ] **Step 3: Implement**

In `obsidian_mcp/utils/persistent_index.py`, add right after `needs_update`:

```python
    async def get_file_stats(self) -> dict[str, tuple[float, int]]:
        """(mtime, size) of every indexed file in one query -- what
        ObsidianVault's reconcile pass diffs against the vault on disk,
        instead of one SELECT per file."""
        db = self._require_db()
        cursor = await db.execute("SELECT filepath, mtime, size FROM file_index")
        return {row[0]: (row[1], row[2]) for row in await cursor.fetchall()}
```

In the `__init__` docstring, fix the default filename:

```text
            index_path: Path to store the SQLite database (defaults to vault/.obsidian/mcp-search-index.db)
```

- [ ] **Step 4: Run tests, lint, typecheck**

Run: `OBSIDIAN_VAULT_PATH=$(mktemp -d) uv run pytest tests/test_persistent_index.py tests/test_persistent_index_properties.py -v`
Expected: all PASS.
Run: `uv run ruff check obsidian_mcp tests && uv run pyright`
Expected: zero findings.

- [ ] **Step 5: Controller commits**

```bash
git add obsidian_mcp/utils/persistent_index.py tests/test_persistent_index.py
git commit -m "refactor(index): read every file's mtime and size in one query"
```

---

### Task 3: ObsidianVault — awaited freshness gate, reconcile pass, write-through, `sync_index()`

**Files:**
- Modify: `obsidian_mcp/utils/filesystem.py` (imports; `__init__` ~65-70; `write_note` ~370-411; `delete_note` ~413-436; replace `_start_background_index_update`/`_update_search_index_async`/`_update_search_index`/`_update_persistent_index` ~481-583; `search_notes` ~595-641; `search_by_regex` ~736-756)
- Modify: `obsidian_mcp/constants.py` (`ERROR_MESSAGES`: add `vault_unavailable` after `invalid_daily_date`)
- Modify: `obsidian_mcp/utils/persistent_index.py` (delete `get_file_info` + `needs_update`, now without callers)
- Create: `tests/test_index_sync.py`
- Modify: `tests/test_persistent_index.py` (`test_file_indexing` ~42-61; delete `test_incremental_update`)
- Modify: `tests/conftest.py:19-24`, `tests/test_filesystem_integration.py:104-109` and `:281-286`, `tests/test_onda2_sanity.py:209-213`, `tests/test_search_index_mode_all_tools.py:38-42`, `tests/test_regex_json_search.py:121`

**Depends-on:** Task 1, Task 2

**Interfaces:**
- Consumes: `iter_md_files(vault_path)` and `VaultCache.sync()` (Task 1); `PersistentSearchIndex.get_file_stats()` (Task 2); existing `index_file(filepath, content, mtime, size, metadata=None)`, `remove_file(filepath)`, `clear_orphaned_entries(existing_files: set)`.
- Produces:
  - `async ObsidianVault.ensure_index_fresh() -> None`
  - `async ObsidianVault.sync_index(full: bool = False) -> dict[str, Any]` returning exactly `{"scanned": int, "added": int, "updated": int, "removed": int, "failed": int, "full": bool, "duration_ms": int}`
  - `ERROR_MESSAGES["vault_unavailable"]` (format key `{path}`; text contains `"not reachable"`), raised as `RuntimeError` by both index initialization and the pass when the vault folder is missing
  - Removed: `_start_background_index_update`, `_update_search_index_async`, `_update_search_index`, attributes `_index_update_in_progress`, `_index_update_task`; `PersistentSearchIndex.get_file_info`, `PersistentSearchIndex.needs_update`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_index_sync.py`:

```python
#!/usr/bin/env python3
"""SQLite search index freshness (spec:
docs/superpowers/specs/2026-09-30-vault-index-sync-design.md).

Changes made outside this server become searchable after
ObsidianVault.sync_index() or, in automatic mode, once
OBSIDIAN_INDEX_UPDATE_INTERVAL has passed (the pass is awaited before the
query, never fire-and-forget). This server's own writes are searchable
immediately (write-through). Outside edits are simulated by writing straight
to disk, the way the Obsidian app, git, or another agent would.
"""

import asyncio
import os
import shutil
import tempfile

import pytest
import pytest_asyncio

from obsidian_mcp.tools.note_management import create_note, delete_note, update_note
from obsidian_mcp.tools.organization import move_note
from obsidian_mcp.utils.filesystem import init_vault
from obsidian_mcp.utils.persistent_index import PersistentSearchIndex


def _new_vault(auto_update: str, interval: str = "300"):
    temp_dir = tempfile.mkdtemp(prefix="obsidian_index_sync_")
    os.environ["OBSIDIAN_REQUIRE_FRONTMATTER"] = "false"
    os.environ["OBSIDIAN_AUTO_INDEX_UPDATE"] = auto_update
    os.environ["OBSIDIAN_INDEX_UPDATE_INTERVAL"] = interval
    return init_vault(temp_dir)


async def _dispose(v) -> None:
    if v.persistent_index:
        await v.persistent_index.close()
    shutil.rmtree(v.vault_path, ignore_errors=True)


@pytest_asyncio.fixture
async def auto_vault():
    """Automatic mode with the default 300 s interval."""
    v = _new_vault("true")
    yield v
    await _dispose(v)


@pytest_asyncio.fixture
async def always_vault():
    """Interval 0: re-check the disk before every query."""
    v = _new_vault("true", interval="0")
    yield v
    await _dispose(v)


@pytest_asyncio.fixture
async def manual_vault():
    """OBSIDIAN_AUTO_INDEX_UPDATE=false: built on first use, then on demand."""
    v = _new_vault("false")
    yield v
    await _dispose(v)


def _write(vault, relpath: str, content: str) -> None:
    """Edit the vault behind the server's back (no MCP tool involved)."""
    path = vault.vault_path / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _age_last_pass(vault) -> None:
    """Pretend the interval elapsed (white-box, same approach as
    test_vault_cache_freshness.py) instead of sleeping 300 s."""
    vault._index_timestamp -= vault._index_update_interval + 1


async def _paths(vault, query: str) -> set[str]:
    return {r["path"] for r in await vault.search_notes(query)}


class TestSyncIndexPicksUpOutsideEdits:
    @pytest.mark.asyncio
    async def test_outside_create_is_found_after_sync(self, auto_vault):
        _write(auto_vault, "seed.md", "seed note")
        assert await _paths(auto_vault, "zebra") == set()  # first query builds

        _write(auto_vault, "new.md", "a zebra appears")
        assert await _paths(auto_vault, "zebra") == set()  # inside the interval

        stats = await auto_vault.sync_index()

        assert await _paths(auto_vault, "zebra") == {"new.md"}
        assert (stats["scanned"], stats["added"], stats["updated"]) == (2, 1, 0)
        assert stats["removed"] == 0

    @pytest.mark.asyncio
    async def test_outside_modify_replaces_old_content(self, auto_vault):
        _write(auto_vault, "note.md", "alpha")
        assert await _paths(auto_vault, "alpha") == {"note.md"}

        _write(auto_vault, "note.md", "omega, longer than before")
        stats = await auto_vault.sync_index()

        assert await _paths(auto_vault, "omega") == {"note.md"}
        assert await _paths(auto_vault, "alpha") == set()
        assert stats["updated"] == 1

    @pytest.mark.asyncio
    async def test_outside_delete_is_not_returned_after_sync(self, auto_vault):
        _write(auto_vault, "gone.md", "ephemeral words")
        assert await _paths(auto_vault, "ephemeral") == {"gone.md"}

        (auto_vault.vault_path / "gone.md").unlink()
        stats = await auto_vault.sync_index()

        assert await _paths(auto_vault, "ephemeral") == set()
        assert stats["removed"] == 1

    @pytest.mark.asyncio
    async def test_outside_rename_moves_the_hit(self, auto_vault):
        _write(auto_vault, "old.md", "wandering text")
        assert await _paths(auto_vault, "wandering") == {"old.md"}

        (auto_vault.vault_path / "sub").mkdir()
        (auto_vault.vault_path / "old.md").rename(
            auto_vault.vault_path / "sub" / "new.md"
        )
        await auto_vault.sync_index()

        assert await _paths(auto_vault, "wandering") == {"sub/new.md"}

    @pytest.mark.asyncio
    async def test_full_sync_reindexes_every_note(self, auto_vault):
        for name in ("a.md", "b.md", "c.md"):
            _write(auto_vault, name, f"content of {name}")
        await auto_vault.sync_index()

        stats = await auto_vault.sync_index(full=True)

        assert stats["full"] is True
        assert (stats["scanned"], stats["added"], stats["updated"]) == (3, 0, 3)

    @pytest.mark.asyncio
    async def test_unchanged_vault_sync_reports_no_work(self, auto_vault):
        _write(auto_vault, "a.md", "steady")
        await auto_vault.sync_index()

        stats = await auto_vault.sync_index()

        assert (stats["added"], stats["updated"], stats["removed"]) == (0, 0, 0)
        assert stats["failed"] == 0
        assert isinstance(stats["duration_ms"], int)


class TestAutomaticReconcileBeforeQueries:
    @pytest.mark.asyncio
    async def test_expired_interval_reconciles_before_the_query(self, auto_vault):
        _write(auto_vault, "seed.md", "seed")
        await _paths(auto_vault, "seed")  # builds the index

        _write(auto_vault, "late.md", "late arrival")
        _age_last_pass(auto_vault)

        assert await _paths(auto_vault, "arrival") == {"late.md"}

    @pytest.mark.asyncio
    async def test_interval_zero_reconciles_before_every_query(self, always_vault):
        _write(always_vault, "seed.md", "seed")
        await _paths(always_vault, "seed")

        _write(always_vault, "instant.md", "instant visibility")

        assert await _paths(always_vault, "visibility") == {"instant.md"}

    @pytest.mark.asyncio
    async def test_regex_search_uses_the_same_gate(self, auto_vault):
        _write(auto_vault, "seed.md", "seed")
        await auto_vault.search_by_regex("seed")

        _write(auto_vault, "late.md", "pattern-42")
        _age_last_pass(auto_vault)

        results = await auto_vault.search_by_regex(r"pattern-\d+")
        assert {r["path"] for r in results} == {"late.md"}


class TestManualMode:
    @pytest.mark.asyncio
    async def test_only_sync_picks_up_outside_edits(self, manual_vault):
        _write(manual_vault, "seed.md", "seed")
        await _paths(manual_vault, "seed")  # the first query still builds

        _write(manual_vault, "late.md", "manual mode text")
        _age_last_pass(manual_vault)
        assert await _paths(manual_vault, "manual") == set()  # no automatic pass

        await manual_vault.sync_index()
        assert await _paths(manual_vault, "manual") == {"late.md"}


class TestWriteThrough:
    @pytest.mark.asyncio
    async def test_mcp_create_update_delete_are_searchable_without_sync(
        self, manual_vault
    ):
        await _paths(manual_vault, "anything")  # build the (empty) index

        await create_note("made.md", "# Made\n\nquokka sighting")
        assert await _paths(manual_vault, "quokka") == {"made.md"}

        await update_note("made.md", "# Made\n\nwombat sighting")
        assert await _paths(manual_vault, "wombat") == {"made.md"}
        assert await _paths(manual_vault, "quokka") == set()

        await delete_note("made.md")
        assert await _paths(manual_vault, "wombat") == set()

    @pytest.mark.asyncio
    async def test_mcp_move_is_searchable_at_the_new_path(self, manual_vault):
        await _paths(manual_vault, "anything")
        await create_note("from.md", "# From\n\nmigrating bird")

        await move_note("from.md", "dest/to.md")

        assert await _paths(manual_vault, "migrating") == {"dest/to.md"}

    @pytest.mark.asyncio
    async def test_write_through_failure_does_not_fail_the_write(
        self, manual_vault, monkeypatch
    ):
        await _paths(manual_vault, "anything")

        async def broken_index_file(self, *args, **kwargs):
            raise RuntimeError("disk full")

        monkeypatch.setattr(PersistentSearchIndex, "index_file", broken_index_file)
        await create_note("kept.md", "# Kept\n\nresilient note")  # must not raise
        assert (manual_vault.vault_path / "kept.md").exists()

        monkeypatch.undo()
        # The failure forces a reconcile on the next query, even in manual mode.
        assert await _paths(manual_vault, "resilient") == {"kept.md"}

    @pytest.mark.asyncio
    async def test_accented_paths_match_between_walk_and_write_through(
        self, manual_vault
    ):
        _write(manual_vault, "notas/café.md", "cafezinho quente")
        await manual_vault.sync_index()
        assert await _paths(manual_vault, "cafezinho") == {"notas/café.md"}

        await delete_note("notas/café.md")

        assert await _paths(manual_vault, "cafezinho") == set()


class TestPassEdgeCases:
    @pytest.mark.asyncio
    async def test_unreadable_note_is_counted_not_fatal(self, manual_vault):
        _write(manual_vault, "good.md", "readable words")
        (manual_vault.vault_path / "bad.md").write_bytes(b"\xff\xfe\xfa not utf-8")

        stats = await manual_vault.sync_index()

        assert (stats["scanned"], stats["added"], stats["failed"]) == (2, 1, 1)
        assert await _paths(manual_vault, "readable") == {"good.md"}

    @pytest.mark.asyncio
    async def test_only_md_files_are_scanned(self, manual_vault):
        _write(manual_vault, "note.md", "real note")
        (manual_vault.vault_path / "image.png").write_bytes(b"\x89PNG")
        _write(manual_vault, "other.markdown", "not indexed")
        _write(manual_vault, "sub/deeper.md", "nested note")

        stats = await manual_vault.sync_index()

        assert stats["scanned"] == 2
        assert await _paths(manual_vault, "nested") == {"sub/deeper.md"}

    @pytest.mark.asyncio
    async def test_empty_vault(self, manual_vault):
        assert await manual_vault.search_notes("anything") == []

        stats = await manual_vault.sync_index()

        counts = (stats["scanned"], stats["added"], stats["updated"])
        assert counts == (0, 0, 0)
        assert (stats["removed"], stats["failed"]) == (0, 0)

    @pytest.mark.asyncio
    async def test_sync_on_a_fresh_server_builds_the_index(self, manual_vault):
        _write(manual_vault, "a.md", "first")
        _write(manual_vault, "b.md", "second")

        stats = await manual_vault.sync_index()  # before any search

        assert (stats["scanned"], stats["added"]) == (2, 2)
        assert await _paths(manual_vault, "second") == {"b.md"}

    @pytest.mark.asyncio
    async def test_missing_vault_folder_raises_and_keeps_the_index(
        self, auto_vault, monkeypatch
    ):
        _write(auto_vault, "precious.md", "precious words")
        await auto_vault.sync_index()

        # An unmounted drive: the folder the server points at is gone.
        monkeypatch.setattr(
            auto_vault, "vault_path", auto_vault.vault_path / "unmounted"
        )
        with pytest.raises(RuntimeError, match="not reachable"):
            await auto_vault.sync_index()
        monkeypatch.undo()

        assert await _paths(auto_vault, "precious") == {"precious.md"}


class TestConcurrentFirstQueries:
    @pytest.mark.asyncio
    async def test_index_is_initialized_once(self, auto_vault, monkeypatch):
        _write(auto_vault, "a.md", "parallel words")
        inits = 0
        original_initialize = PersistentSearchIndex.initialize

        async def counting_initialize(self):
            nonlocal inits
            inits += 1
            await original_initialize(self)

        monkeypatch.setattr(PersistentSearchIndex, "initialize", counting_initialize)

        results = await asyncio.gather(
            auto_vault.search_notes("parallel"), auto_vault.search_notes("parallel")
        )

        assert inits == 1
        assert [len(r) for r in results] == [1, 1]
```

- [ ] **Step 2: Run the new file to verify it fails**

Run: `OBSIDIAN_VAULT_PATH=$(mktemp -d) uv run pytest tests/test_index_sync.py -v`
Expected: FAIL — most tests with `AttributeError: 'ObsidianVault' object has no attribute 'sync_index'`; the write-through and concurrency tests with assertion errors.

- [ ] **Step 3: Add the error message**

In `obsidian_mcp/constants.py`, add to `ERROR_MESSAGES` right after the `"invalid_daily_date"` entry:

```python
    "vault_unavailable": (
        "Vault folder is not reachable: '{path}'. The search index was left untouched. "
        "To fix: 1) Check that the drive or network share holding the vault is mounted, "
        "2) Check that OBSIDIAN_VAULT_PATH points at the vault folder, "
        "3) Retry once the folder is back"
    ),
```

- [ ] **Step 4: Rewrite the index lifecycle in `filesystem.py`**

1. Imports — add `import time` (module level, after `import os`), `from ..constants import ERROR_MESSAGES` (before `from ..models import …`), and change the cache import to:

```python
from .vault_cache import VaultCache, iter_md_files
```

2. In `__init__`, delete:

```python
        # Track if an index update is in progress
        self._index_update_in_progress = False
        self._index_update_task: asyncio.Task | None = None
```

Leave `_index_update_interval`, `_index_batch_size`, and `_auto_index_update` reads exactly as they are.

3. Add the vault-folder guard right above `_initialize_persistent_index`:

```python
    def _require_vault_dir(self) -> None:
        """Raise before any index work when the vault folder is gone (an
        unmounted drive or network share). A pass over a missing folder
        would walk zero notes and delete every index entry as an orphan, and
        initializing would fail with an opaque mkdir error."""
        if not self.vault_path.is_dir():
            raise RuntimeError(
                ERROR_MESSAGES["vault_unavailable"].format(path=self.vault_path)
            )
```

and call it as the first statement inside `_initialize_persistent_index`'s `if not self._persistent_index_initialized:` block — before its `try:`, so the message is not rewrapped by the broad `except Exception` below:

```python
    async def _initialize_persistent_index(self) -> None:
        """Initialize the persistent search index if not already done."""
        if not self._persistent_index_initialized:
            self._require_vault_dir()
            try:
                ...  # unchanged
```

4. Delete `_start_background_index_update`, `_update_search_index_async`, `_update_search_index`, and the old `_update_persistent_index`. In their place (directly after `_require_persistent_index`) add:

```python
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
            self._index_update_interval > 0
            and elapsed <= self._index_update_interval
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
        """
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
```

5. `_extract_file_metadata`'s docstring says it is "kept as a vault method since _update_persistent_index calls it via self" — update it to:

```python
        """Delegates to index_metadata.extract_file_metadata (kept as a
        vault method since the index pass and write-through call it via
        self)."""
```

6. In `write_note`, directly after `await self.cache.note_mutated(path, content)` add:

```python
        await self._index_note_mutated(full_path, content)
```

and extend the comment above `note_mutated` with one sentence: `The SQLite search index gets the same write-through right after.`

7. In `delete_note`, directly after `await self.cache.note_mutated(path, None)` add:

```python
        await self._index_note_mutated(full_path, None)
```

8. In `search_notes`, replace everything from `import time` through the closing `elif self._index_update_in_progress:` block (the init check, the `should_update` gate, and the background start) with:

```python
        await self.ensure_index_fresh()
```

so the body is that line followed by the existing `return await self._search_with_persistent_index(query, context_length, max_results)`.

9. In `search_by_regex`, replace everything from `import time` through `await self._update_search_index()` (the init check, the comment, and the interval check) with:

```python
        await self.ensure_index_fresh()
```

10. Confirm no `import time` remains inside a method body: `grep -n "^        import time" obsidian_mcp/utils/filesystem.py` → no output.

- [ ] **Step 5: Remove the per-file check API and migrate the existing tests**

In `obsidian_mcp/utils/persistent_index.py`, delete the methods `get_file_info` and `needs_update` entirely — the old pass was `needs_update`'s only caller and `needs_update` was `get_file_info`'s; the new pass uses `get_file_stats` (Task 2).

`tests/test_persistent_index.py`:

- delete the whole `test_incremental_update` method (it tested `needs_update`; `test_get_file_stats_returns_stored_mtime_and_size` covers the replacement, and `tests/test_index_sync.py` covers "only changed files are re-indexed" end to end);
- in `test_file_indexing`, replace the four lines from `# Check that file was indexed` through `assert file_info["size"] == 100` with:

```python
        # Check that file was indexed
        assert await index.get_file_stats() == {"test.md": (1234567890.0, 100)}
```

`tests/conftest.py` — replace the comment block above `os.environ.setdefault("OBSIDIAN_AUTO_INDEX_UPDATE", "false")` with:

```python
# Manual index mode (OBSIDIAN_AUTO_INDEX_UPDATE=false) for the whole test
# session: the SQLite index is built on the first query, then only this
# server's writes and ObsidianVault.sync_index() change it, so no test depends
# on the 300 s re-check interval. Tests that write files straight to disk and
# then search call sync_index() themselves; tests of the automatic re-check
# set the var to "true" explicitly (see test_index_sync.py).
```

`tests/test_filesystem_integration.py`:

- line ~109: `await current_vault._update_search_index()` → `await current_vault.sync_index()`
- lines ~285-286: delete `vault._index_timestamp = None  # Force re-index` and change `await vault._update_search_index()` → `await vault.sync_index()`

`tests/test_onda2_sanity.py` ~209-213 — replace the 4-line comment and the call with:

```python
        # Notes were written straight to disk above, so bring the SQLite index
        # up to date before the content-search assertions (same pattern as
        # test_filesystem_integration.py's fixture).
        await v.sync_index()
```

`tests/test_search_index_mode_all_tools.py` ~38-42 — replace the comment and the call with:

```python
    # The first search would build the index anyway, but syncing here (same
    # pattern as test_onda2_sanity.py's fixture) keeps every test in this file
    # deterministic regardless of call order.
    await v.sync_index()
```

`tests/test_regex_json_search.py` ~121: `await vault._update_search_index()` → `await vault.sync_index()`

Then: `grep -rn "_update_search_index\|needs_update\|get_file_info\|_index_update_in_progress\|_index_update_task" obsidian_mcp tests` → no output.

- [ ] **Step 6: Run the new tests, then the full suite, lint, typecheck**

Run: `OBSIDIAN_VAULT_PATH=$(mktemp -d) uv run pytest tests/test_index_sync.py -v`
Expected: all PASS.
Run: `OBSIDIAN_VAULT_PATH=$(mktemp -d) uv run pytest -q`
Expected: all PASS (baseline was 488; now 488 + new tests − 0).
Run: `uv run ruff check obsidian_mcp tests && uv run pyright`
Expected: zero findings; `0 errors, 0 warnings, 0 informations`.

- [ ] **Step 7: Controller commits**

```bash
git add obsidian_mcp/utils/filesystem.py obsidian_mcp/utils/persistent_index.py obsidian_mcp/constants.py tests/test_index_sync.py tests/test_persistent_index.py tests/conftest.py tests/test_filesystem_integration.py tests/test_onda2_sanity.py tests/test_search_index_mode_all_tools.py tests/test_regex_json_search.py
git commit -m "fix(index): reconcile the search index before queries and write through MCP writes"
```

---

### Task 4: Property search reads a fresh index

**Files:**
- Modify: `obsidian_mcp/tools/search_property_engine.py` (`_search_by_property`, the `if hasattr(vault, "persistent_index") and vault.persistent_index:` block ~222-230)
- Test: `tests/test_index_sync.py` (append one class at the end)

**Depends-on:** Task 3

**Interfaces:**
- Consumes: `ObsidianVault.ensure_index_fresh()` (Task 3); fixtures `auto_vault`, helpers `_write`, `_age_last_pass` in `tests/test_index_sync.py` (Task 3); public `search_by_property(property_name, value=None, operator="=", context_length=20, mode=None, ctx=None) -> dict` with a `"results"` list whose items carry `"path"`.
- Produces: nothing new.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_index_sync.py` (add `from obsidian_mcp.tools.search_discovery import search_by_property` to the imports):

```python
class TestPropertySearchFreshness:
    @pytest.mark.asyncio
    async def test_property_search_sees_outside_frontmatter_change(self, auto_vault):
        _write(auto_vault, "task.md", "---\nstatus: open\n---\n# Task\n")
        await auto_vault.sync_index()  # the index exists, so property search uses it

        before = await search_by_property("status", "done")
        assert [r["path"] for r in before["results"]] == []

        _write(auto_vault, "task.md", "---\nstatus: done\n---\n# Task, finished\n")
        _age_last_pass(auto_vault)

        after = await search_by_property("status", "done")
        assert [r["path"] for r in after["results"]] == ["task.md"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `OBSIDIAN_VAULT_PATH=$(mktemp -d) uv run pytest tests/test_index_sync.py::TestPropertySearchFreshness -v`
Expected: FAIL — `assert [] == ['task.md']` (the index still holds `status: open`).

- [ ] **Step 3: Implement**

In `_search_by_property`, make the first statement inside the `try:` of the persistent-index branch:

```python
            # Same freshness gate as text/regex search: a due reconcile pass
            # runs (awaited) before the index is read.
            await vault.ensure_index_fresh()
```

It goes inside the `try` on purpose: if the gate fails (e.g. vault unreachable), the existing `except` falls back to the live manual scan.

- [ ] **Step 4: Run tests, lint, typecheck**

Run: `OBSIDIAN_VAULT_PATH=$(mktemp -d) uv run pytest tests/test_index_sync.py tests/test_property_search.py tests/test_persistent_index_properties.py -v`
Expected: all PASS.
Run: `uv run ruff check obsidian_mcp tests && uv run pyright`
Expected: zero findings.

- [ ] **Step 5: Controller commits**

```bash
git add obsidian_mcp/tools/search_property_engine.py tests/test_index_sync.py
git commit -m "fix(search): refresh the index before property search reads it"
```

---

### Task 5: Agent-facing surface — `sync_vault_index_tool`, server instructions, help catalog

**Files:**
- Create: `obsidian_mcp/tools/index_sync.py`
- Modify: `obsidian_mcp/tools/__init__.py` (import + `__all__`)
- Modify: `obsidian_mcp/mcp_search.py` (docstring, import, new wrapper at the end)
- Modify: `obsidian_mcp/server.py` (import + `__all__`)
- Modify: `obsidian_mcp/constants.py` (`RESPONSE_STRUCTURES["index_sync"]`)
- Modify: `obsidian_mcp/app.py` (instructions builder)
- Modify: `obsidian_mcp/tools/vault_meta.py` (3 `_env_row` descriptions ~132-157)
- Create: `tests/test_sync_vault_index_tool.py`
- Modify: `tests/test_server_tool_wrappers.py` (~125-137 and ~280-353: 30 → 31)

**Depends-on:** Task 3

**Interfaces:**
- Consumes: `ObsidianVault.sync_index(full: bool = False) -> dict[str, Any]` (Task 3); `ERROR_MESSAGES["vault_unavailable"]` text contains `"not reachable"` (Task 3).
- Produces:
  - `async sync_vault_index(full: bool = False, ctx: Context | None = None) -> dict[str, Any]` in `obsidian_mcp/tools/index_sync.py`, exported from `obsidian_mcp.tools`; returns `{"success": True, **sync_index stats}`.
  - MCP tool `sync_vault_index_tool(full: bool = False)` in `obsidian_mcp/mcp_search.py`, re-exported from `obsidian_mcp.server`.
  - `_build_instructions(vault: ObsidianVault) -> str` in `obsidian_mcp/app.py`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_sync_vault_index_tool.py`:

```python
#!/usr/bin/env python3
"""sync_vault_index_tool through the registered @mcp.tool() wrapper, plus the
server instructions that tell agents when to call it.

server.py raises at import time without OBSIDIAN_VAULT_PATH; the bootstrap
dir below only satisfies that check -- every test repoints the vault with
init_vault(temp_dir), same as test_server_tool_wrappers.py.
"""

import os
import shutil
import tempfile

import pytest
import pytest_asyncio

os.environ["OBSIDIAN_VAULT_PATH"] = tempfile.mkdtemp(
    prefix="obsidian_sync_tool_bootstrap_"
)

from fastmcp.exceptions import ToolError

from obsidian_mcp.app import _build_instructions
from obsidian_mcp.server import mcp, search_notes_tool, sync_vault_index_tool
from obsidian_mcp.utils.filesystem import init_vault


@pytest_asyncio.fixture
async def vault():
    temp_dir = tempfile.mkdtemp(prefix="obsidian_sync_tool_")
    os.environ["OBSIDIAN_REQUIRE_FRONTMATTER"] = "false"
    v = init_vault(temp_dir)
    yield v
    if v.persistent_index:
        await v.persistent_index.close()
    shutil.rmtree(temp_dir, ignore_errors=True)


class TestSyncVaultIndexTool:
    @pytest.mark.asyncio
    async def test_outside_edit_is_searchable_after_the_tool(self, vault):
        (vault.vault_path / "seed.md").write_text("seed")
        await search_notes_tool.fn(query="seed")  # builds the index

        (vault.vault_path / "outside.md").write_text("capybara facts")
        result = await sync_vault_index_tool.fn()

        assert result["success"] is True
        assert (result["added"], result["removed"], result["full"]) == (1, 0, False)
        found = await search_notes_tool.fn(query="capybara")
        assert [r["path"] for r in found["results"]] == ["outside.md"]

    @pytest.mark.asyncio
    async def test_full_reindexes_every_note(self, vault):
        (vault.vault_path / "a.md").write_text("a")
        (vault.vault_path / "b.md").write_text("b")
        await sync_vault_index_tool.fn()

        result = await sync_vault_index_tool.fn(full=True)

        assert (result["scanned"], result["updated"], result["full"]) == (2, 2, True)

    @pytest.mark.asyncio
    async def test_unreachable_vault_becomes_tool_error(self, vault, monkeypatch):
        monkeypatch.setattr(vault, "vault_path", vault.vault_path / "unmounted")

        with pytest.raises(ToolError, match="not reachable"):
            await sync_vault_index_tool.fn()


class TestServerInstructions:
    def test_registered_instructions_name_the_sync_tool(self):
        assert "sync_vault_index_tool" in (mcp.instructions or "")

    @pytest.mark.asyncio
    async def test_automatic_mode_states_the_recheck_interval(
        self, vault, monkeypatch
    ):
        monkeypatch.setattr(vault, "_auto_index_update", True)

        text = _build_instructions(vault)

        assert "at most every 300 seconds" in text
        assert "call sync_vault_index_tool" in text

    @pytest.mark.asyncio
    async def test_manual_mode_makes_the_call_mandatory(self, vault, monkeypatch):
        monkeypatch.setattr(vault, "_auto_index_update", False)

        assert "you MUST call sync_vault_index_tool" in _build_instructions(vault)
```

If `FastMCP` 2.12 exposes no public `instructions` attribute, read it the way the installed package stores it (check `.venv/lib/python3.12/site-packages/fastmcp/server/server.py`, e.g. `mcp._mcp_server.instructions`) and use that in `test_registered_instructions_name_the_sync_tool`.

In `tests/test_server_tool_wrappers.py`:

- rename `test_tools_catalog_has_thirty_entries_including_new_ones` → `test_tools_catalog_has_thirty_one_entries_including_new_ones`; `assert len(result["tools"]) == 30` → `== 31`; add `"sync_vault_index_tool",` to the expected-subset set in that test.
- in the test that builds `all_tools` (~280-353): add `sync_vault_index_tool,` to its `from obsidian_mcp.server import (...)` list (alphabetical, after `search_notes_tool,`), add `sync_vault_index_tool,` to `all_tools` right after `search_by_property_tool,`, and change `assert len(all_tools) == 30, f"Expected 30 tools, found {len(all_tools)}"` → `assert len(all_tools) == 31, f"Expected 31 tools, found {len(all_tools)}"`.

- [ ] **Step 2: Run to verify they fail**

Run: `OBSIDIAN_VAULT_PATH=$(mktemp -d) uv run pytest tests/test_sync_vault_index_tool.py tests/test_server_tool_wrappers.py -v`
Expected: collection ERROR `ImportError: cannot import name 'sync_vault_index_tool'` / `'_build_instructions'`.

- [ ] **Step 3: Implement the tool function**

Create `obsidian_mcp/tools/index_sync.py`:

```python
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
```

In `obsidian_mcp/tools/__init__.py`, add after the `.image_management` import block:

```python
from .index_sync import (
    sync_vault_index,
)
```

and in `__all__`, under `# Search and discovery`, after `"list_folders",` add `"sync_vault_index",`.

- [ ] **Step 4: Implement the wrapper**

In `obsidian_mcp/mcp_search.py`:

- module docstring → `"""Search tool wrappers: full-text search, search by date, search by regex, and index sync."""`
- add `sync_vault_index,` to the `from .tools import (...)` list (after `search_notes,`)
- append at the end of the file:

```python
@mcp.tool()
async def sync_vault_index_tool(
    full: Annotated[
        bool,
        Field(
            description=(
                "Re-index every note, not just those whose modification time or "
                "size changed. Slower; use only if results still look wrong "
                "after a normal sync."
            ),
            default=False,
        ),
    ] = False,
    ctx: Context | None = None,
):
    """
    Force the search index and notes cache to match the vault files on disk now.

    This server's own write tools keep the index current automatically. Files
    changed any other way are picked up by the server's periodic re-check
    (every OBSIDIAN_INDEX_UPDATE_INTERVAL seconds, default 300); until then
    search_notes, search_by_regex, and search_by_property can miss new notes,
    return deleted ones, or show old content.

    When to use:
    - Right after vault files changed WITHOUT this server's tools: your own
      file write/edit tools, shell commands, git (pull, checkout, merge), the
      Obsidian app, sync clients, or other agents
    - When a search, tag, or link result contradicts what is on disk

    When NOT to use:
    - After this server's own write tools (create/update/edit/delete/move/
      rename notes, tags, properties) -- those are already reflected
    - Routinely before every search

    Returns:
        {success, scanned, added, updated, removed, failed, full, duration_ms}:
        notes seen on disk, newly indexed, re-indexed, dropped, and unreadable
        in this pass.
    """
    try:
        return await sync_vault_index(full, ctx)
    except ValueError as e:
        raise ToolError(str(e))
    except Exception as e:
        raise ToolError(f"Index sync failed: {e!s}")
```

In `obsidian_mcp/server.py`: add `sync_vault_index_tool,` to the `from .mcp_search import (...)` list (after `search_notes_tool,`) and `"sync_vault_index_tool",` to `__all__` (after `"search_notes_tool",`).

In `obsidian_mcp/constants.py`, add to `RESPONSE_STRUCTURES` right before `# Error response`:

```python
    # Index sync (sync_vault_index_tool)
    "index_sync": {
        "success": True,
        "scanned": int,  # Notes found on disk
        "added": int,  # Notes indexed for the first time
        "updated": int,  # Notes re-indexed (changed, or all with full=True)
        "removed": int,  # Index entries dropped because the note is gone
        "failed": int,  # Notes that could not be read or indexed
        "full": bool,  # Whether every note was re-indexed
        "duration_ms": int,  # Wall time of the pass
    },
```

- [ ] **Step 5: Server instructions**

In `obsidian_mcp/app.py`, change the import to `from .utils.filesystem import ObsidianVault, get_vault, init_vault`, add this function after `init_vault()`, and build `mcp` with it:

```python
def _build_instructions(vault: ObsidianVault) -> str:
    """FastMCP server instructions, built from the live index config. Clients
    such as Claude Code add them to the agent's system prompt -- the lever
    that gets agents to call sync_vault_index_tool after editing the vault
    outside this server."""
    outside = (
        "your own file edit tools, shell, git, the Obsidian app, sync clients, "
        "other agents"
    )
    if vault._auto_index_update:
        recheck = max(vault._index_update_interval, vault.cache_stat_ttl_seconds)
        freshness = (
            "Index freshness: this server's own write tools keep search, tag, and "
            f"link results current. Files changed any other way ({outside}) are "
            f"picked up at most every {recheck} seconds. After changing vault "
            "files outside this server, call sync_vault_index_tool once before "
            "the next search, tag, or link query."
        )
    else:
        freshness = (
            "Index freshness: automatic re-checks are off "
            "(OBSIDIAN_AUTO_INDEX_UPDATE=false). This server's own write tools "
            "keep results current, but after vault files change any other way "
            f"({outside}) you MUST call sync_vault_index_tool before relying on "
            "search, tag, or link results."
        )
    return (
        f"MCP server for direct filesystem access to Obsidian vaults.\n\n{freshness}"
    )


# Create FastMCP server instance
mcp = FastMCP("obsidian-mcp", instructions=_build_instructions(get_vault()))
```

(`init_vault()` stays exactly where it is, above the function; `main()` is unchanged.)

- [ ] **Step 6: Help catalog descriptions**

In `obsidian_mcp/tools/vault_meta.py`, change only the description argument (5th) of three `_env_row(...)` calls:
- `OBSIDIAN_INDEX_UPDATE_INTERVAL` → `"Seconds between automatic re-checks of the vault for outside changes; a due re-check runs before the next text/regex/property search, not in the background. 0 = re-check before every search. sync_vault_index_tool forces one now."`
- `OBSIDIAN_INDEX_BATCH_SIZE` → `"Files per progress-log batch while the search index re-indexes changed notes."`
- `OBSIDIAN_AUTO_INDEX_UPDATE` → `"true: re-check the vault for outside changes every OBSIDIAN_INDEX_UPDATE_INTERVAL seconds before a search. false (manual): the index is built once, then only this server's writes and sync_vault_index_tool update it."`

- [ ] **Step 7: Run tests, full suite, lint, typecheck**

Run: `OBSIDIAN_VAULT_PATH=$(mktemp -d) uv run pytest tests/test_sync_vault_index_tool.py tests/test_server_tool_wrappers.py -v`
Expected: all PASS.
Run: `OBSIDIAN_VAULT_PATH=$(mktemp -d) uv run pytest -q`
Expected: all PASS.
Run: `uv run ruff check obsidian_mcp tests && uv run pyright`
Expected: zero findings.

- [ ] **Step 8: Controller commits**

```bash
git add obsidian_mcp/tools/index_sync.py obsidian_mcp/tools/__init__.py obsidian_mcp/mcp_search.py obsidian_mcp/server.py obsidian_mcp/constants.py obsidian_mcp/app.py obsidian_mcp/tools/vault_meta.py tests/test_sync_vault_index_tool.py tests/test_server_tool_wrappers.py
git commit -m "feat(tools): add sync_vault_index_tool and tell agents when to call it"
```

---

### Task 6: Documentation

**Files:**
- Modify: `README.md` (Available Tools table near line 194 and the per-tool `<details>` entries near line 451; project tree near line 1167; "Performance and Indexing" section from line 1310, including the env-var rows at ~1337-1348)
- Modify: `CLAUDE.md` (lines ~100, ~101, ~104, ~112)
- Modify: `obsidian_mcp/utils/CLAUDE.md` (Key patterns: the `PersistentSearchIndex` bullet ~42)

**Depends-on:** Task 4, Task 5

**Interfaces:**
- Consumes: final behavior from Tasks 1–5 (read `docs/superpowers/specs/2026-09-30-vault-index-sync-design.md`, the "Freshness model" table is the source of truth).
- Produces: docs only.

- [ ] **Step 1: README — tool reference**

Add a row to the "Available Tools" table right after the `search_by_regex` row, in the table's existing format:

```markdown
| [`sync_vault_index`](#sync_vault_index) | Force the search index to match vault files changed outside the server |
```

Add a `<details>` entry right after the `search_by_regex` one, copying its exact HTML structure (`<a id="sync_vault_index"></a>`, `<details>`, `<summary><b><code>sync_vault_index</code></b></summary>`, …). Content: one-sentence purpose; parameter `full` (bool, default `false`: re-index every note instead of only changed ones); when to use (after files changed without this server's tools — own file tools, shell, git, Obsidian app, sync clients, other agents — or when a result contradicts the disk); when not to use (after this server's own write tools; routinely before every search); an example call `{"full": false}`; an example response `{"success": true, "scanned": 1250, "added": 1, "updated": 3, "removed": 1, "failed": 0, "full": false, "duration_ms": 84}`.

- [ ] **Step 2: README — indexing behavior and env vars**

In "Performance and Indexing", add a short "Index freshness" subsection with the spec's freshness-model table (MCP writes: immediate; outside edits: text/regex/property search within `OBSIDIAN_INDEX_UPDATE_INTERVAL` (300 s), tags/links/names within `OBSIDIAN_CACHE_STAT_TTL_SECONDS` (30 s), or immediately after `sync_vault_index`), plus: a due re-check runs before the search (the search waits for it, it never answers from a stale index); `OBSIDIAN_AUTO_INDEX_UPDATE=false` is manual mode; the server's instructions tell agents to call `sync_vault_index` after outside edits.

Update the three env-var rows to:

```markdown
| `OBSIDIAN_AUTO_INDEX_UPDATE` | `true`: re-check the vault for outside changes before searches every `OBSIDIAN_INDEX_UPDATE_INTERVAL` seconds. `false`: manual mode — index built once, then only server writes and `sync_vault_index` update it | `true` | boolean |
| `OBSIDIAN_INDEX_UPDATE_INTERVAL` | Seconds between automatic re-checks; a due re-check runs before the next search. `0` = before every search | `300` | integer |
| `OBSIDIAN_INDEX_BATCH_SIZE` | Files per progress-log batch while re-indexing changed notes | `50` | integer |
```

Project tree (~1167): `mcp_search.py` comment → `# @mcp.tool() wrappers: search_notes, search_by_date, search_by_regex, sync_vault_index`; if the tree lists `tools/` modules, add `index_sync.py  # sync_vault_index` in alphabetical position.

- [ ] **Step 3: CLAUDE.md files**

Root `CLAUDE.md`:

- lines ~100 and ~101: `30` → `31` (both "the 30 `@mcp.tool()` wrappers" and "re-exports the 30 wrappers").
- line ~104: replace "used above the `OBSIDIAN_SEARCH_INDEX_THRESHOLD` vault size." with "backs text, regex, and property search for every vault size (`OBSIDIAN_SEARCH_INDEX_THRESHOLD` only picks the response shape). `ObsidianVault.ensure_index_fresh()` reconciles it before each query once `OBSIDIAN_INDEX_UPDATE_INTERVAL` has passed (awaited, never background), `write_note`/`delete_note` write through, and `sync_vault_index_tool` forces a pass." Also refresh its line count from `wc -l obsidian_mcp/utils/persistent_index.py`, and the `filesystem.py` line count on its own bullet from `wc -l obsidian_mcp/utils/filesystem.py`.
- line ~112: `413 tests` → the count printed by `OBSIDIAN_VAULT_PATH=$(mktemp -d) uv run pytest -q`.

`obsidian_mcp/utils/CLAUDE.md` — refresh the `persistent_index.py` line count in the `PersistentSearchIndex` bullet, and add this bullet right after it:

```markdown
- Index freshness: every SQLite read goes through `ObsidianVault.ensure_index_fresh()` (an
  awaited reconcile pass when `OBSIDIAN_INDEX_UPDATE_INTERVAL` has passed); `write_note` /
  `delete_note` write through to both stores (`cache.note_mutated`, `_index_note_mutated`);
  `sync_index()` forces both now. A new SQLite read path must call the gate first.
```

- [ ] **Step 4: Verify**

Run: `OBSIDIAN_VAULT_PATH=$(mktemp -d) uv run pytest -q` (docs-only task; confirms the count you wrote).
Check every relative link/anchor you added resolves: `grep -n 'id="sync_vault_index"' README.md` → one hit.

- [ ] **Step 5: Controller commits**

```bash
git add README.md CLAUDE.md obsidian_mcp/utils/CLAUDE.md
git commit -m "docs: document index freshness and sync_vault_index"
```
