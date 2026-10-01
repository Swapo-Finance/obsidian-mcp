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
        # Build the notes cache too: it must survive the failed sync as well.
        assert "precious.md" in await auto_vault.cache.get_all_relpaths()

        # An unmounted drive: the folder the server points at is gone.
        monkeypatch.setattr(
            auto_vault, "vault_path", auto_vault.vault_path / "unmounted"
        )
        with pytest.raises(RuntimeError, match="not reachable"):
            await auto_vault.sync_index()
        monkeypatch.undo()

        assert await _paths(auto_vault, "precious") == {"precious.md"}
        assert "precious.md" in await auto_vault.cache.get_all_relpaths()


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
