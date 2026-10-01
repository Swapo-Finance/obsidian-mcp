#!/usr/bin/env python3
"""SQLite search index write path (spec:
docs/superpowers/specs/2026-09-30-vault-index-sync-design.md).

This server's own writes are searchable immediately (write-through): create,
update, delete and move reach the index without waiting for a pass and key each
row by the spelling found on disk. A write or a pass that is cancelled or
interrupted leaves the index dirty, so the next pass repairs it. Also covers
queries and writes issued while a pass is running: a query waits for the pass,
a write finishes without waiting for it.

The reconcile pass itself (sync, interval gate, edge cases, property search) is
in test_index_sync.py; the shared fixtures and helpers are in
_index_sync_helpers.py.
"""

import asyncio
import os
import unicodedata

import pytest

# Bare sibling import: relies on pytest's default "prepend" import mode (no
# tests/__init__.py, no ini config); breaks under --import-mode=importlib.
from _index_sync_helpers import (
    _paths,
    _write,
    auto_vault,
    manual_vault,
)

from obsidian_mcp.tools.note_management import create_note, delete_note, update_note
from obsidian_mcp.tools.organization import move_note
from obsidian_mcp.utils.persistent_index import PersistentSearchIndex

# pytest resolves the vault fixtures by name, as test parameters. Ruff does not
# count that as a use of the import (F401/F811), so they are listed as exports.
__all__ = ["auto_vault", "manual_vault"]


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
