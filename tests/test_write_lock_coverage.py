"""Regression coverage for _serialize_note_writes (obsidian_mcp/tools/write_policy.py)
now applying to the read-modify-write tools that used to bypass it entirely:
move_note, rename_note, move_folder, add_tags, update_tags, remove_tags, and
batch_update_properties. Each does an unlocked vault.read_note() -> mutate ->
vault.write_note()/delete_note() cycle; vault.write_lock (a process-wide
asyncio.Lock) is the only thing that can serialize two such cycles against
the same note.

This proves the lock is effective for three representative newly-decorated
functions -- add_tags, update_tags, and batch_update_properties: concurrent
calls that touch the same note (or, for batch_update_properties, the same
note via two separate batch calls) must not lose either update. That's only
possible if the whole decorated call -- read, mutate, write -- runs
atomically per caller. move_folder and remove_tags are covered by
write_policy.py's shared design/tests rather than a dedicated concurrency
test of their own.
"""

import asyncio

import pytest

from obsidian_mcp.tools.batch_properties import batch_update_properties
from obsidian_mcp.tools.folders import create_folder
from obsidian_mcp.tools.tag_editing import add_tags, update_tags
from obsidian_mcp.utils.filesystem import init_vault


@pytest.mark.asyncio
async def test_concurrent_add_tags_on_same_note_does_not_lose_an_update(tmp_path):
    vault = init_vault(str(tmp_path))
    await vault.write_note("Note.md", "---\ntags: []\n---\nbody", overwrite=False)

    # Widen the read/write race window. Without vault.write_lock, both calls
    # would read the same "tags: []" pre-image and whichever writes last
    # would clobber the other's tag. With the lock, the whole decorated call
    # runs atomically per caller, so the second call's read only happens
    # after the first call's write has already committed.
    original_read_note = vault.read_note

    async def slow_read_note(path):
        note = await original_read_note(path)
        await asyncio.sleep(0.05)
        return note

    vault.read_note = slow_read_note

    await asyncio.gather(
        add_tags("Note.md", ["alpha"]),
        add_tags("Note.md", ["beta"]),
    )

    final = await original_read_note("Note.md")
    assert set(final.metadata.tags) == {"alpha", "beta"}


@pytest.mark.asyncio
async def test_concurrent_update_tags_merge_on_same_note_does_not_lose_an_update(
    tmp_path,
):
    """Same race as add_tags above, through update_tags(merge=True) instead
    -- covers a second of the four functions _serialize_note_writes gained
    this session."""
    vault = init_vault(str(tmp_path))
    await vault.write_note("Note.md", "---\ntags: []\n---\nbody", overwrite=False)

    original_read_note = vault.read_note

    async def slow_read_note(path):
        note = await original_read_note(path)
        await asyncio.sleep(0.05)
        return note

    vault.read_note = slow_read_note

    await asyncio.gather(
        update_tags("Note.md", ["alpha"], merge=True),
        update_tags("Note.md", ["beta"], merge=True),
    )

    final = await original_read_note("Note.md")
    assert set(final.metadata.tags) == {"alpha", "beta"}


@pytest.mark.asyncio
async def test_concurrent_batch_update_properties_on_same_note_does_not_lose_an_update(
    tmp_path,
):
    """batch_update_properties holds the lock for its entire call -- the
    whole notes_to_update loop, not per-note -- and reads each note twice
    (once resolving search_criteria={"files": [...]} into notes_to_update,
    once again in the update loop itself). Both reads go through the same
    slow_read_note patch, which only widens the same read/write race window
    the add_tags test above exercises: two callers must not both read the
    pre-image and have the second writer's version clobber the first's.
    """
    vault = init_vault(str(tmp_path))
    await vault.write_note("Note.md", "---\nstatus: draft\n---\nbody", overwrite=False)

    original_read_note = vault.read_note

    async def slow_read_note(path):
        note = await original_read_note(path)
        await asyncio.sleep(0.05)
        return note

    vault.read_note = slow_read_note

    await asyncio.gather(
        batch_update_properties(
            {"files": ["Note.md"]}, property_updates={"alpha": "one"}
        ),
        batch_update_properties(
            {"files": ["Note.md"]}, property_updates={"beta": "two"}
        ),
    )

    final = await original_read_note("Note.md")
    assert final.metadata.frontmatter.get("alpha") == "one"
    assert final.metadata.frontmatter.get("beta") == "two"


@pytest.mark.asyncio
async def test_create_folder_still_works_after_gaining_the_write_lock(tmp_path):
    """create_folder joined _serialize_note_writes's decorated set this
    session -- previously the only write in tools/ that bypassed it. It
    only calls list_notes() (undecorated) and vault.write_note()/get_vault()
    directly, never another _serialize_note_writes-decorated function, so
    acquiring the lock here cannot self-deadlock. This is a smoke test that
    the decorator didn't break the call (and doesn't hang, since pytest has
    no built-in timeout); it isn't a lost-update race test like the ones
    above because create_folder's write is a pure create (overwrite=False)
    with no read-modify-write cycle -- the worst case without the lock was
    ever a benign FileExistsError race, not a lost update.
    """
    init_vault(str(tmp_path))

    result = await create_folder("Projects/2025", create_placeholder=True)

    assert result["success"] is True
    placeholder = result["details"]["placeholder_file"]
    assert (tmp_path / placeholder).exists()
