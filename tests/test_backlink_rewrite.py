"""Unit tests for the shared backlink-rewrite helper (Finding 1's dedup of
move_note/rename_note's near-identical backlink-rewriting blocks).

rewrite_backlinks_to_note takes `vault` as a plain parameter and never calls
get_vault() itself, so a bare AsyncMock is enough here -- unlike
move_note/rename_note's own tests, no get_vault patch is needed.
"""

from unittest.mock import AsyncMock

import pytest

from obsidian_mcp.models import Note, NoteMetadata
from obsidian_mcp.tools.backlink_rewrite import rewrite_backlinks_to_note


def _backlink(source_path: str, link_text: str) -> dict:
    return {"source_path": source_path, "link_text": link_text, "link_type": "wiki"}


@pytest.mark.asyncio
async def test_rewrites_plain_and_alias_links_across_multiple_notes():
    """Mirrors test_move_note_with_rename's fixture data: two notes, one
    with a plain link plus an alias, the other with a `.md`-suffixed link."""
    vault = AsyncMock()
    note1 = Note(
        path="Daily/2024-01-15.md",
        content="Working on [[Old Name]] today. See also [[Old Name|the project]].",
        metadata=NoteMetadata(),
    )
    note2 = Note(
        path="Projects/Overview.md",
        content="Related: [[Old Name.md]] and [[Other Note]].",
        metadata=NoteMetadata(),
    )
    vault.read_note.side_effect = [note1, note2]

    backlinks = [
        _backlink("Daily/2024-01-15.md", "Old Name"),
        _backlink("Daily/2024-01-15.md", "the project"),
        _backlink("Projects/Overview.md", "Old Name.md"),
    ]

    links_updated, notes_updated, details = await rewrite_backlinks_to_note(
        vault, backlinks, "Old Name", "New Name", "Old Name.md", "New Name.md"
    )

    assert links_updated == 3
    assert notes_updated == 2
    assert len(details) == 2

    write_calls = vault.write_note.call_args_list
    assert write_calls[0][0][0] == "Daily/2024-01-15.md"
    assert "[[New Name]]" in write_calls[0][0][1]
    assert "[[New Name|the project]]" in write_calls[0][0][1]
    assert "[[Old Name" not in write_calls[0][0][1]

    assert write_calls[1][0][0] == "Projects/Overview.md"
    assert "[[New Name.md]]" in write_calls[1][0][1]
    assert "[[Old Name.md]]" not in write_calls[1][0][1]


@pytest.mark.asyncio
async def test_note_with_no_actual_content_change_is_not_written():
    """A backlink entry exists for a note, but none of the 6 patterns
    actually match its current content -- must not count it as updated or
    write it back (the `if content != original_content` guard)."""
    vault = AsyncMock()
    note = Note(
        path="Unrelated.md",
        content="No links to rewrite here.",
        metadata=NoteMetadata(),
    )
    vault.read_note.return_value = note

    backlinks = [_backlink("Unrelated.md", "Old Name")]

    links_updated, notes_updated, details = await rewrite_backlinks_to_note(
        vault, backlinks, "Old Name", "New Name", "Old Name.md", "New Name.md"
    )

    assert links_updated == 0
    assert notes_updated == 0
    assert details == []
    vault.write_note.assert_not_called()


@pytest.mark.asyncio
async def test_error_reading_one_note_does_not_abort_the_rest():
    """The per-note try/except must not let one failure stop the batch."""
    vault = AsyncMock()
    good_note = Note(
        path="Good.md", content="See [[Old Name]].", metadata=NoteMetadata()
    )
    vault.read_note.side_effect = [RuntimeError("disk error"), good_note]

    backlinks = [
        _backlink("Broken.md", "Old Name"),
        _backlink("Good.md", "Old Name"),
    ]

    links_updated, notes_updated, details = await rewrite_backlinks_to_note(
        vault, backlinks, "Old Name", "New Name", "Old Name.md", "New Name.md"
    )

    assert links_updated == 1
    assert notes_updated == 1
    assert details == [{"note": "Good.md", "updates": 1}]


@pytest.mark.asyncio
async def test_reports_progress_via_ctx_when_provided():
    vault = AsyncMock()
    note = Note(path="Note.md", content="[[Old]]", metadata=NoteMetadata())
    vault.read_note.return_value = note
    ctx = AsyncMock()

    await rewrite_backlinks_to_note(
        vault, [_backlink("Note.md", "Old")], "Old", "New", "Old.md", "New.md", ctx=ctx
    )

    ctx.info.assert_any_call("Updated 1 links in Note.md")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
