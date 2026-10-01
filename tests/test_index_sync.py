#!/usr/bin/env python3
"""SQLite search index freshness (spec:
docs/superpowers/specs/2026-09-30-vault-index-sync-design.md).

Changes made outside this server become searchable after
ObsidianVault.sync_index() or, in automatic mode, once
OBSIDIAN_INDEX_UPDATE_INTERVAL has passed (the pass is awaited before the
query, never fire-and-forget). Outside edits are simulated by writing straight
to disk, the way the Obsidian app, git, or another agent would.

This file covers the reconcile pass: sync, the automatic interval gate, manual
mode, pass edge cases and logging, what the vault walk skips, concurrent first
queries and property-search freshness. The write path (write-through) and its
interplay with a running pass are in test_index_sync_writes.py; the shared
fixtures and helpers are in _index_sync_helpers.py.
"""

import asyncio
import logging
import os
import sqlite3
import time

import pytest

# Bare sibling import: relies on pytest's default "prepend" import mode (no
# tests/__init__.py, no ini config); breaks under --import-mode=importlib.
from _index_sync_helpers import (
    _age_last_pass,
    _dispose,
    _new_vault,
    _paths,
    _write,
    always_vault,
    auto_vault,
    manual_vault,
)

from obsidian_mcp.tools.search_discovery import search_by_property
from obsidian_mcp.utils.persistent_index import PersistentSearchIndex

# pytest resolves the vault fixtures by name, as test parameters. Ruff does not
# count that as a use of the import (F401/F811), so they are listed as exports.
__all__ = ["always_vault", "auto_vault", "manual_vault"]


def _edit_keeping_stat(vault, relpath: str, content: str) -> None:
    """An outside edit no (mtime, size) comparison can see: same length, with
    the original mtime put back."""
    path = vault.vault_path / relpath
    before = path.stat()
    assert len(content.encode("utf-8")) == before.st_size, "edit must keep the size"
    path.write_text(content, encoding="utf-8")
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))


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
    async def test_interrupted_full_sync_is_finished_by_the_next_plain_sync(
        self, manual_vault, monkeypatch
    ):
        """full=True is for an index that is wrong although every (mtime, size)
        matches the disk. It invalidates every stored stamp first, so a full
        sync that dies midway leaves the notes it did not reach to the plain
        pass that follows, instead of letting them look up to date."""
        names = ["a.md", "b.md", "c.md"]
        for name in names:
            _write(manual_vault, name, f"old text of {name}")
        await manual_vault.sync_index()
        for name in names:
            _edit_keeping_stat(manual_vault, name, f"new text of {name}")
        assert (await manual_vault.sync_index())["updated"] == 0  # nothing to see

        original_index_file = PersistentSearchIndex.index_file
        reindexed = 0

        async def dies_after_the_first(self, *args, **kwargs):
            nonlocal reindexed
            if reindexed == 1:
                raise asyncio.CancelledError
            reindexed += 1
            return await original_index_file(self, *args, **kwargs)

        monkeypatch.setattr(PersistentSearchIndex, "index_file", dies_after_the_first)
        with pytest.raises(asyncio.CancelledError):
            await manual_vault.sync_index(full=True)
        monkeypatch.undo()

        stats = await manual_vault.sync_index()

        assert stats["updated"] == len(names) - 1
        assert await _paths(manual_vault, "new text") == set(names)
        assert await _paths(manual_vault, "old text") == set()

    @pytest.mark.asyncio
    async def test_full_sync_rebuilds_the_notes_cache_too(self, manual_vault):
        """A plain sync trusts (mtime, size) in the cache as well; full=True
        rebuilds the cache from the files, so the tag index follows."""
        _write(manual_vault, "doc.md", "---\ntags: [alpha]\n---\nzebra here\n")
        await manual_vault.sync_index()
        assert set(await manual_vault.cache.get_tags_index()) == {"alpha"}  # builds it
        _edit_keeping_stat(
            manual_vault, "doc.md", "---\ntags: [bravo]\n---\nllama here\n"
        )

        await manual_vault.sync_index()  # plain: no stamp changed, nothing to see
        assert set(await manual_vault.cache.get_tags_index()) == {"alpha"}
        assert await _paths(manual_vault, "llama") == set()

        await manual_vault.sync_index(full=True)

        assert set(await manual_vault.cache.get_tags_index()) == {"bravo"}
        assert await _paths(manual_vault, "llama") == {"doc.md"}
        assert await _paths(manual_vault, "zebra") == set()

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

    @pytest.mark.asyncio
    async def test_wall_clock_stepping_back_does_not_keep_the_index_fresh(
        self, auto_vault, monkeypatch
    ):
        _write(auto_vault, "seed.md", "seed")
        await _paths(auto_vault, "seed")  # builds the index

        _write(auto_vault, "late.md", "late arrival")
        _age_last_pass(auto_vault)
        # The interval has elapsed, but the wall clock was just stepped back
        # (NTP correction, manual change): a freshness rule that reads it
        # would see a negative elapsed time and keep serving the stale index.
        real_time = time.time
        monkeypatch.setattr(time, "time", lambda: real_time() - 10_000)

        assert await _paths(auto_vault, "arrival") == {"late.md"}


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

    @pytest.mark.asyncio
    async def test_due_pass_on_a_missing_folder_raises_and_keeps_every_row(
        self, always_vault, monkeypatch
    ):
        """The guard at the start of the pass itself: with interval 0 every
        query runs one, and a pass that walked a vanished folder would sweep
        every row as an orphan."""
        _write(always_vault, "precious.md", "precious words")
        await _paths(always_vault, "precious")  # builds the index
        index = always_vault._require_persistent_index()
        before = await index.get_file_stats()

        monkeypatch.setattr(
            always_vault, "vault_path", always_vault.vault_path / "unmounted"
        )
        with pytest.raises(RuntimeError, match="not reachable"):
            await always_vault.search_notes("precious")
        monkeypatch.undo()

        assert await index.get_file_stats() == before

    @pytest.mark.asyncio
    async def test_first_query_on_a_missing_folder_raises_before_creating_an_index(
        self, auto_vault, monkeypatch
    ):
        """The guard before index initialization: without it the server would
        build a database (or fail with an opaque mkdir error) under a path
        that is not the vault."""
        monkeypatch.setattr(
            auto_vault, "vault_path", auto_vault.vault_path / "unmounted"
        )
        with pytest.raises(RuntimeError, match="not reachable"):
            await auto_vault.search_notes("anything")
        monkeypatch.undo()

        assert auto_vault.persistent_index is None
        assert not (auto_vault.vault_path / "unmounted").exists()

    @pytest.mark.asyncio
    async def test_folder_vanishing_during_the_walk_raises_and_sweeps_nothing(
        self, manual_vault, monkeypatch
    ):
        """The guard after the walk: the walk of a folder that vanished midway
        comes back empty, and sweeping against it would drop every row."""
        _write(manual_vault, "precious.md", "precious words")
        await manual_vault.sync_index()
        index = manual_vault._require_persistent_index()
        before = await index.get_file_stats()

        def vanishing_walk(vault_path):
            # An unmounted drive: nothing found, and the folder gone by the end.
            monkeypatch.setattr(
                manual_vault, "vault_path", manual_vault.vault_path / "unmounted"
            )
            return iter(())

        monkeypatch.setattr(
            "obsidian_mcp.utils.filesystem.iter_md_files", vanishing_walk
        )
        with pytest.raises(RuntimeError, match="not reachable"):
            await manual_vault.sync_index()
        monkeypatch.undo()

        assert await index.get_file_stats() == before

    @pytest.mark.asyncio
    async def test_failed_pass_leaves_the_index_dirty_even_in_manual_mode(
        self, manual_vault, monkeypatch
    ):
        """A pass that dies after indexing but before the orphan sweep has
        settled nothing: the next query must finish it, not trust a pass that
        never completed."""
        _write(manual_vault, "stays.md", "stays words")
        _write(manual_vault, "gone.md", "ephemeral words")
        await manual_vault.sync_index()
        (manual_vault.vault_path / "gone.md").unlink()  # deleted outside

        async def locked_database(self, existing_files):
            raise sqlite3.OperationalError("database is locked")

        monkeypatch.setattr(
            PersistentSearchIndex, "clear_orphaned_entries", locked_database
        )
        with pytest.raises(sqlite3.OperationalError):
            await manual_vault.sync_index()
        monkeypatch.undo()

        assert await _paths(manual_vault, "ephemeral") == set()  # re-synced

    @pytest.mark.asyncio
    async def test_zero_batch_size_is_clamped_instead_of_breaking_every_pass(
        self, monkeypatch
    ):
        monkeypatch.setenv("OBSIDIAN_INDEX_BATCH_SIZE", "0")
        vault = _new_vault("false")
        try:
            _write(vault, "a.md", "batched words")

            assert await _paths(vault, "batched") == {"a.md"}
            regex_hits = await vault.search_by_regex(r"batch\w+")
            assert {r["path"] for r in regex_hits} == {"a.md"}
        finally:
            await _dispose(vault)


class TestPassLogging:
    @pytest.mark.asyncio
    async def test_pass_summary_is_info_only_when_the_pass_did_something(
        self, manual_vault, caplog
    ):
        _write(manual_vault, "a.md", "first")

        with caplog.at_level(logging.DEBUG, logger="obsidian_mcp.utils.filesystem"):
            await manual_vault.sync_index()  # indexes a.md
            busy = [
                r for r in caplog.records if r.getMessage().startswith("Index pass")
            ]
            caplog.clear()
            await manual_vault.sync_index()  # nothing changed
            idle = [
                r for r in caplog.records if r.getMessage().startswith("Index pass")
            ]

        assert [r.levelno for r in busy] == [logging.INFO]
        assert [r.levelno for r in idle] == [logging.DEBUG]


def _symlink_or_skip(target, link) -> None:
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not available here")


def _release_fifo_readers(fifo) -> None:
    """open() of a FIFO for reading blocks until a writer shows up. A walker
    that hands one to the indexer strands a thread there, and a stranded
    thread hangs interpreter exit: open a writer so it can finish."""
    try:
        os.close(os.open(fifo, os.O_WRONLY | os.O_NONBLOCK))
    except OSError:
        pass  # nobody is blocked on it, the expected outcome


class TestWalkSkipsWhatIsNotAVaultNote:
    @pytest.mark.asyncio
    async def test_symlink_to_a_file_outside_the_vault_is_not_indexed(
        self, manual_vault, tmp_path
    ):
        outside = tmp_path / "creds.md"
        outside.write_text("aws_secret = TOPSECRET-OUTSIDE-VAULT", encoding="utf-8")
        _write(manual_vault, "note.md", "ordinary words")
        _symlink_or_skip(outside, manual_vault.vault_path / "leak.md")

        stats = await manual_vault.sync_index()

        assert stats["scanned"] == 1
        assert await _paths(manual_vault, "TOPSECRET") == set()
        regex_hits = await manual_vault.search_by_regex("TOPSECRET")
        assert {hit["path"] for hit in regex_hits} == set()
        assert await manual_vault.cache.get_all_relpaths() == {"note.md"}

    @pytest.mark.asyncio
    async def test_fifo_named_like_a_note_does_not_hang_the_pass(self, manual_vault):
        if not hasattr(os, "mkfifo"):
            pytest.skip("no FIFOs on this platform")
        _write(manual_vault, "note.md", "ordinary words")
        fifo = manual_vault.vault_path / "evil.md"
        os.mkfifo(fifo)
        try:
            stats = await asyncio.wait_for(manual_vault.sync_index(), timeout=10)
            assert stats["scanned"] == 1
            assert await _paths(manual_vault, "ordinary") == {"note.md"}
        finally:
            _release_fifo_readers(fifo)

    @pytest.mark.asyncio
    async def test_symlink_to_a_note_inside_the_vault_is_still_indexed(
        self, manual_vault
    ):
        _write(manual_vault, "real/original.md", "shared words")
        _symlink_or_skip(
            manual_vault.vault_path / "real" / "original.md",
            manual_vault.vault_path / "alias.md",
        )

        await manual_vault.sync_index()

        assert await _paths(manual_vault, "shared") == {"real/original.md", "alias.md"}


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

    @pytest.mark.asyncio
    async def test_property_search_reports_an_unreachable_vault(
        self, auto_vault, monkeypatch
    ):
        """A gate failure must surface like it does for text and regex search,
        not turn into a silent empty answer from the fallback scan."""
        _write(auto_vault, "task.md", "---\nstatus: done\n---\n# Task\n")
        await auto_vault.sync_index()  # the index exists, so property search uses it
        _age_last_pass(auto_vault)  # a pass is due, so the gate runs it
        monkeypatch.setattr(
            auto_vault, "vault_path", auto_vault.vault_path / "unmounted"
        )

        result = await search_by_property("status", "done")

        assert "not reachable" in result["error"]
        assert result["results"] == []
