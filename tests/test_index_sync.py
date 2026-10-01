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
import logging
import os
import shutil
import sqlite3
import tempfile
import time
import unicodedata

import pytest
import pytest_asyncio

from obsidian_mcp.tools.note_management import create_note, delete_note, update_note
from obsidian_mcp.tools.organization import move_note
from obsidian_mcp.tools.search_discovery import search_by_property
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


def _edit_keeping_stat(vault, relpath: str, content: str) -> None:
    """An outside edit no (mtime, size) comparison can see: same length, with
    the original mtime put back."""
    path = vault.vault_path / relpath
    before = path.stat()
    assert len(content.encode("utf-8")) == before.st_size, "edit must keep the size"
    path.write_text(content, encoding="utf-8")
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))


@pytest.fixture
def park_index_file(monkeypatch):
    """Factory ``park(relpath) -> (parked, release)``: a pass blocks inside
    index_file(relpath), holding _index_lock, until ``release`` is set --
    deterministic choreography for acting while a pass is in flight. Every
    parked pass is released at teardown, so a failing test cannot strand one."""
    releases: list[asyncio.Event] = []

    def park(relpath: str) -> tuple[asyncio.Event, asyncio.Event]:
        parked, release = asyncio.Event(), asyncio.Event()
        original = PersistentSearchIndex.index_file

        async def gated(self, filepath, *args, **kwargs):
            if filepath == relpath:
                parked.set()
                await release.wait()
            return await original(self, filepath, *args, **kwargs)

        monkeypatch.setattr(PersistentSearchIndex, "index_file", gated)
        releases.append(release)
        return parked, release

    yield park
    for release in releases:
        release.set()


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
        nfc, _ = _distinct_spellings("notas/café.md")
        _write(manual_vault, nfc, "cafezinho quente")
        if nfc.rsplit("/", 1)[-1] not in os.listdir(manual_vault.vault_path / "notas"):
            pytest.skip("filesystem rewrites the Unicode normalization of names")
        await manual_vault.sync_index()
        assert await _ascii_paths(manual_vault, "cafezinho") == {ascii(nfc)}

        await delete_note(nfc)

        assert await _ascii_paths(manual_vault, "cafezinho") == set()


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


class TestWriteThroughNeverWaitsForAPass:
    @pytest.mark.asyncio
    async def test_write_during_a_running_pass_finishes_without_waiting_for_it(
        self, manual_vault, park_index_file
    ):
        """A write queued behind a long pass would stall the tool call for the
        pass's whole duration. It flags the index instead: the pass cleared
        the flag when it started, so whatever the walk missed is picked up by
        the next query's pass -- manual mode included."""
        await _paths(manual_vault, "anything")  # build the (empty) index
        _write(manual_vault, "seed.md", "seed words")  # the pass has this to index
        parked, release = park_index_file("seed.md")
        sync = asyncio.create_task(manual_vault.sync_index())
        await asyncio.wait_for(parked.wait(), timeout=5)  # walked, holding the lock

        await asyncio.wait_for(
            create_note("new.md", "# New\n\nplatypus sighting"), timeout=5
        )

        assert not sync.done()  # the write finished while the pass is parked
        assert manual_vault._index_dirty
        release.set()
        await sync
        assert manual_vault._index_dirty  # finishing the pass must not wipe the flag
        assert await _paths(manual_vault, "platypus") == {"new.md"}


class TestWriteThroughIndexesTheDisk:
    @pytest.mark.asyncio
    async def test_outside_edit_landing_before_write_through_is_indexed_as_found(
        self, manual_vault, monkeypatch
    ):
        """Write-through stats the file, then reads it back, as a pass does. An
        outside edit that lands after the MCP write must be indexed with its
        own stamp: the caller's older content filed under that stamp would
        look current to every later pass, and nothing short of a full sync
        would repair it."""
        await _paths(manual_vault, "anything")  # build the (empty) index
        on_disk, release = asyncio.Event(), asyncio.Event()
        original_note_mutated = manual_vault.cache.note_mutated

        async def parked_note_mutated(*args, **kwargs):
            on_disk.set()  # the MCP write is on the disk, write-through not yet run
            await release.wait()
            return await original_note_mutated(*args, **kwargs)

        monkeypatch.setattr(manual_vault.cache, "note_mutated", parked_note_mutated)
        write = asyncio.create_task(create_note("new.md", "# New\n\nplatypus sighting"))
        await asyncio.wait_for(on_disk.wait(), timeout=5)
        _write(manual_vault, "new.md", "# New\n\nechidna sighting, edited outside")
        release.set()
        await write
        monkeypatch.undo()

        await manual_vault.sync_index()  # the plain remedy agents are told about

        assert await _paths(manual_vault, "echidna") == {"new.md"}
        assert await _paths(manual_vault, "platypus") == set()

    @pytest.mark.asyncio
    async def test_failing_spelling_check_does_not_fail_the_write(
        self, manual_vault, monkeypatch
    ):
        await _paths(manual_vault, "anything")

        def broken_check(full_path):
            raise OSError("cannot list the folder")

        monkeypatch.setattr(manual_vault, "_spelled_as_on_disk", broken_check)
        await create_note("kept.md", "# Kept\n\nresilient note")  # must not raise
        monkeypatch.undo()

        assert (manual_vault.vault_path / "kept.md").exists()
        assert await _paths(manual_vault, "resilient") == {"kept.md"}

    @pytest.mark.asyncio
    async def test_delete_checks_the_spelling_only_when_an_index_exists(
        self, manual_vault, monkeypatch
    ):
        """The check lists every folder on the path; with no index there is
        nothing to write through to, so it is not worth the listings."""
        checks = []
        original_check = manual_vault._spelled_as_on_disk

        def counting_check(full_path):
            checks.append(full_path)
            return original_check(full_path)

        monkeypatch.setattr(manual_vault, "_spelled_as_on_disk", counting_check)
        _write(manual_vault, "first.md", "first")
        await delete_note("first.md")  # the index was never built
        assert checks == []

        _write(manual_vault, "second.md", "second")
        await manual_vault.sync_index()  # now there is one
        await delete_note("second.md")
        assert len(checks) == 1


class TestInterruptedWritesMarkTheIndexDirty:
    """From the moment a write hits the disk the index may lag it, however the
    call ends: a client interrupt or an error anywhere after the disk change
    must leave the index flagged, or in manual mode nothing would ever
    reconcile it."""

    @pytest.mark.asyncio
    async def test_write_cancelled_after_the_disk_write_forces_a_reconcile(
        self, manual_vault, monkeypatch
    ):
        await _paths(manual_vault, "anything")  # built; manual mode stays fresh
        parked = asyncio.Event()

        async def parked_read_note(path):
            parked.set()
            await asyncio.Event().wait()  # the client interrupts the call here

        monkeypatch.setattr(manual_vault, "read_note", parked_read_note)
        write = asyncio.create_task(
            manual_vault.write_note("late.md", "# Late\n\nsloth\n")
        )
        await asyncio.wait_for(parked.wait(), timeout=5)
        write.cancel()
        with pytest.raises(asyncio.CancelledError):
            await write
        monkeypatch.undo()

        assert (manual_vault.vault_path / "late.md").exists()  # the write landed
        assert manual_vault._index_dirty
        assert await _paths(manual_vault, "sloth") == {"late.md"}

    @pytest.mark.asyncio
    async def test_write_failing_after_the_disk_write_forces_a_reconcile(
        self, manual_vault, monkeypatch
    ):
        await _paths(manual_vault, "anything")

        async def failing_read_note(path):
            raise RuntimeError("unreadable after the write")

        monkeypatch.setattr(manual_vault, "read_note", failing_read_note)
        with pytest.raises(RuntimeError, match="unreadable"):
            await manual_vault.write_note("late.md", "# Late\n\nsloth\n")
        monkeypatch.undo()

        assert (manual_vault.vault_path / "late.md").exists()
        assert await _paths(manual_vault, "sloth") == {"late.md"}

    @pytest.mark.asyncio
    async def test_delete_cancelled_after_the_unlink_forces_a_reconcile(
        self, manual_vault, monkeypatch
    ):
        _write(manual_vault, "doomed.md", "ephemeral words")
        await manual_vault.sync_index()
        parked = asyncio.Event()

        async def parked_note_mutated(relpath, content):
            parked.set()
            await asyncio.Event().wait()  # the client interrupts the call here

        monkeypatch.setattr(manual_vault.cache, "note_mutated", parked_note_mutated)
        delete = asyncio.create_task(manual_vault.delete_note("doomed.md"))
        await asyncio.wait_for(parked.wait(), timeout=5)
        delete.cancel()
        with pytest.raises(asyncio.CancelledError):
            await delete
        monkeypatch.undo()

        assert not (manual_vault.vault_path / "doomed.md").exists()  # it landed
        assert await _paths(manual_vault, "ephemeral") == set()


class TestQueriesWaitForARunningPass:
    """A pass commits note by note: a query that read the index meanwhile would
    answer from a half-updated state, so it waits for the pass instead."""

    @pytest.mark.asyncio
    async def test_search_during_a_repair_pass_waits_for_it(
        self, manual_vault, park_index_file
    ):
        await _paths(manual_vault, "anything")  # built; manual mode stays fresh
        _write(manual_vault, "late.md", "sloth arrives outside the server")
        manual_vault._index_dirty = True  # as after a failed or interrupted write
        parked, release = park_index_file("late.md")
        repair = asyncio.create_task(manual_vault.search_notes("sloth"))
        await asyncio.wait_for(parked.wait(), timeout=5)  # the pass is mid-flight

        second = asyncio.create_task(manual_vault.search_notes("sloth"))
        done, _ = await asyncio.wait({second}, timeout=0.1)
        assert not done  # held back behind the pass

        release.set()
        assert [r["path"] for r in await second] == ["late.md"]
        assert [r["path"] for r in await repair] == ["late.md"]

    @pytest.mark.asyncio
    async def test_search_during_an_explicit_sync_waits_for_it(
        self, manual_vault, park_index_file
    ):
        await _paths(manual_vault, "anything")  # built; manual mode stays fresh
        _write(manual_vault, "late.md", "sloth arrives outside the server")
        parked, release = park_index_file("late.md")
        sync = asyncio.create_task(manual_vault.sync_index())
        await asyncio.wait_for(parked.wait(), timeout=5)

        search = asyncio.create_task(manual_vault.search_notes("sloth"))
        done, _ = await asyncio.wait({search}, timeout=0.1)
        assert not done

        release.set()
        assert [r["path"] for r in await search] == ["late.md"]
        assert (await sync)["added"] == 1

    @pytest.mark.asyncio
    async def test_search_gathered_with_a_sync_sees_the_synced_edit(self, auto_vault):
        _write(auto_vault, "seed.md", "seed words")
        await _paths(auto_vault, "seed")  # index built, fresh for 300 s
        _write(auto_vault, "outside.md", "capybara facts")

        stats, hits = await asyncio.gather(
            auto_vault.sync_index(), auto_vault.search_notes("capybara")
        )

        assert stats["added"] == 1
        assert [hit["path"] for hit in hits] == ["outside.md"]


def _distinct_spellings(relpath: str) -> tuple[str, str]:
    """(NFC, NFD) spellings of relpath, proven to be different strings."""
    nfc = unicodedata.normalize("NFC", relpath)
    nfd = unicodedata.normalize("NFD", relpath)
    assert nfc != nfd, "relpath has no composable accent"
    return nfc, nfd


async def _ascii_paths(vault, query: str) -> set[str]:
    """_paths with non-ASCII escaped, so two spellings of one name stay
    distinguishable in an assertion failure."""
    return {ascii(path) for path in await _paths(vault, query)}


class TestWriteThroughKeepsTheOnDiskSpelling:
    """APFS and NTFS accept several spellings of one file (case, Unicode
    normalization) while the pass's walk keys the index by the spelling the
    disk lists. Write-through must never key a row by another spelling -- a
    duplicate or ghost row until the next pass -- so it forces a reconcile
    instead. Each test probes the filesystem and skips where the spellings
    name different files (e.g. Linux ext4)."""

    @pytest.mark.asyncio
    async def test_nfc_spelled_update_of_an_nfd_note_leaves_one_correct_row(
        self, manual_vault
    ):
        nfc, nfd = _distinct_spellings("notas/café.md")
        _write(manual_vault, nfd, "cafezinho quente")
        if not (manual_vault.vault_path / nfc).exists():
            pytest.skip("filesystem distinguishes NFC and NFD spellings")
        assert await _ascii_paths(manual_vault, "cafezinho") == {ascii(nfd)}

        await update_note(nfc, "# Café\n\nlatte morno")

        assert await _ascii_paths(manual_vault, "latte") == {ascii(nfd)}
        assert await _ascii_paths(manual_vault, "cafezinho") == set()

    @pytest.mark.asyncio
    async def test_nfc_spelled_delete_of_an_nfd_note_leaves_no_ghost_row(
        self, manual_vault
    ):
        nfc, nfd = _distinct_spellings("notas/café.md")
        _write(manual_vault, nfd, "cafezinho quente")
        if not (manual_vault.vault_path / nfc).exists():
            pytest.skip("filesystem distinguishes NFC and NFD spellings")
        assert await _ascii_paths(manual_vault, "cafezinho") == {ascii(nfd)}

        await delete_note(nfc)

        assert not (manual_vault.vault_path / nfd).exists()
        assert await _ascii_paths(manual_vault, "cafezinho") == set()

    @pytest.mark.asyncio
    async def test_matching_spelling_is_written_and_dropped_directly(
        self, manual_vault
    ):
        """The normal path stays direct: the row is written (and dropped) by
        write-through itself and no reconcile is forced. Asserted on the index,
        not through a search, which would reconcile and hide the difference
        (e.g. a delete that checks the spelling after the unlink sees a missing
        entry and takes the reconcile path every time)."""
        await _paths(manual_vault, "anything")  # build the index
        index = manual_vault._require_persistent_index()
        built_at = manual_vault._index_timestamp

        await create_note("Plain.md", "# Plain\n\nbadger")
        assert "Plain.md" in await index.get_file_stats()

        await delete_note("Plain.md")
        assert "Plain.md" not in await index.get_file_stats()
        assert manual_vault._index_timestamp == built_at
        assert not manual_vault._index_dirty

    @pytest.mark.asyncio
    async def test_case_variant_write_keeps_the_on_disk_spelling(self, manual_vault):
        _write(manual_vault, "Notes/Foo.md", "giraffe original")
        if not (manual_vault.vault_path / "notes" / "foo.md").exists():
            pytest.skip("filesystem is case-sensitive")
        assert await _paths(manual_vault, "giraffe") == {"Notes/Foo.md"}

        await update_note("notes/foo.md", "# Foo\n\nokapi replacement")

        assert await _paths(manual_vault, "okapi") == {"Notes/Foo.md"}
        assert await _paths(manual_vault, "giraffe") == set()


class TestCancellation:
    """A client interrupt cancels the awaiting task with CancelledError, which
    is a BaseException: `except Exception` handlers do not see it."""

    @pytest.mark.asyncio
    async def test_cancelled_pass_rolls_the_note_back_and_the_next_sync_repairs_it(
        self, manual_vault, monkeypatch
    ):
        _write(manual_vault, "doc.md", "---\nstatus: draft\n---\n\nbody")
        await manual_vault.sync_index()
        _write(manual_vault, "doc.md", "---\nstatus: published\n---\n\nbody")

        def cancelled(self, value):
            raise asyncio.CancelledError

        # index_file has already replaced the note's stored mtime/size and
        # deleted its property rows when it reaches this call.
        monkeypatch.setattr(
            PersistentSearchIndex, "_determine_property_type", cancelled
        )
        with pytest.raises(asyncio.CancelledError):
            await manual_vault.sync_index()
        monkeypatch.undo()

        stats = await manual_vault.sync_index()

        assert stats["updated"] == 1
        index = manual_vault._require_persistent_index()
        published = await index.search_by_property("status", "=", "published")
        assert [hit["filepath"] for hit in published] == ["doc.md"]
        assert await index.search_by_property("status", "=", "draft") == []

    @pytest.mark.asyncio
    async def test_write_through_cancelled_mid_write_forces_a_reconcile(
        self, manual_vault, monkeypatch
    ):
        await _paths(manual_vault, "anything")

        async def cancelled_index_file(self, *args, **kwargs):
            raise asyncio.CancelledError

        monkeypatch.setattr(PersistentSearchIndex, "index_file", cancelled_index_file)
        with pytest.raises(asyncio.CancelledError):
            await create_note("late.md", "# Late\n\nsloth")
        monkeypatch.undo()

        assert await _paths(manual_vault, "sloth") == {"late.md"}

    @pytest.mark.asyncio
    async def test_pass_that_does_not_finish_keeps_an_earlier_dirty_mark(
        self, manual_vault, monkeypatch
    ):
        """A pass settles the mark only by finishing: one cancelled midway (a
        client interrupt) must leave the index flagged for the next query."""
        await _paths(manual_vault, "anything")  # built; manual mode stays fresh

        async def failing_index_file(self, *args, **kwargs):
            raise RuntimeError("disk full")

        monkeypatch.setattr(PersistentSearchIndex, "index_file", failing_index_file)
        await create_note("kept.md", "# Kept\n\nresilient")  # write-through fails
        monkeypatch.undo()

        async def cancelled_index_file(self, *args, **kwargs):
            raise asyncio.CancelledError

        monkeypatch.setattr(PersistentSearchIndex, "index_file", cancelled_index_file)
        with pytest.raises(asyncio.CancelledError):
            await manual_vault.search_notes("resilient")  # the due pass dies midway
        monkeypatch.undo()

        assert await _paths(manual_vault, "resilient") == {"kept.md"}


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
