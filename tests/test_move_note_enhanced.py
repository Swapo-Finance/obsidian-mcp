"""Tests for enhanced move_note functionality with rename support."""

from unittest.mock import AsyncMock, patch

import pytest

from obsidian_mcp.models import Note, NoteMetadata
from obsidian_mcp.tools.organization import move_note


@pytest.fixture
def mock_vault():
    """Create a mock vault for testing."""
    vault = AsyncMock()
    return vault


@pytest.fixture
def mock_get_vault(mock_vault):
    """Patch get_vault to return our mock.

    Also patches write_policy.get_vault: move_note is now wrapped by
    _serialize_note_writes (Finding 2), whose decorator calls get_vault()
    via write_policy.py's own import to reach vault.write_lock -- a
    separate name binding from note_move.py's, so it needs its own patch.
    """
    with (
        patch("obsidian_mcp.tools.note_move.get_vault", return_value=mock_vault),
        patch("obsidian_mcp.tools.link_management.get_vault", return_value=mock_vault),
        patch("obsidian_mcp.tools.write_policy.get_vault", return_value=mock_vault),
    ):
        yield mock_vault


@pytest.mark.asyncio
async def test_move_note_simple_folder_change(mock_get_vault):
    """Test moving a note to a different folder without renaming."""
    vault = mock_get_vault

    # Setup mock note
    note = Note(
        path="Inbox/Note.md",
        content="# My Note\n\nContent here.",
        metadata=NoteMetadata(),
    )
    vault.read_note.side_effect = [note, FileNotFoundError()]
    vault.write_note.return_value = None
    vault.delete_note.return_value = None

    # Test move without rename
    result = await move_note("Inbox/Note.md", "Archive/Note.md")

    assert result["success"] is True
    assert result["source"] == "Inbox/Note.md"
    assert result["destination"] == "Archive/Note.md"
    assert result["renamed"] is False
    assert result["details"]["links_updated"] == 0
    assert result["details"]["notes_updated"] == 0

    # Verify operations
    vault.write_note.assert_called_once_with(
        "Archive/Note.md", note.content, overwrite=False
    )
    vault.delete_note.assert_called_once_with("Inbox/Note.md")


@pytest.mark.asyncio
async def test_move_note_with_rename(mock_get_vault):
    """Test moving a note with a name change, triggering link updates."""
    vault = mock_get_vault

    # Setup mock notes
    moving_note = Note(
        path="Inbox/Old Name.md",
        content="# Old Name\n\nThis is the note being moved.",
        metadata=NoteMetadata(),
    )

    linking_note1 = Note(
        path="Daily/2024-01-15.md",
        content="Today I worked on [[Old Name]] and [[Old Name|the project]].",
        metadata=NoteMetadata(),
    )

    linking_note2 = Note(
        path="Projects/Overview.md",
        content="Related: [[Old Name.md]] and [[Other Note]].",
        metadata=NoteMetadata(),
    )

    # Mock get_backlinks
    with patch("obsidian_mcp.tools.link_management.get_backlinks") as mock_backlinks:
        mock_backlinks.return_value = {
            "findings": [
                {
                    "source_path": "Daily/2024-01-15.md",
                    "link_text": "Old Name",
                    "link_type": "wiki",
                },
                {
                    "source_path": "Daily/2024-01-15.md",
                    "link_text": "the project",
                    "link_type": "wiki",
                },
                {
                    "source_path": "Projects/Overview.md",
                    "link_text": "Old Name.md",
                    "link_type": "wiki",
                },
            ]
        }

        # Setup vault mocks
        vault.read_note.side_effect = [
            moving_note,  # First read of source note
            FileNotFoundError(),  # Check destination doesn't exist
            linking_note1,  # Read first linking note
            linking_note2,  # Read second linking note
            moving_note,  # Re-read of source note before the final move write
        ]
        vault.write_note.return_value = None
        vault.delete_note.return_value = None

        # Test move with rename
        result = await move_note(
            "Inbox/Old Name.md", "Archive/New Name.md", update_links=True
        )

        assert result["success"] is True
        assert result["source"] == "Inbox/Old Name.md"
        assert result["destination"] == "Archive/New Name.md"
        assert result["renamed"] is True
        assert result["details"]["links_updated"] == 3
        assert result["details"]["notes_updated"] == 2
        assert len(result["details"]["link_update_details"]) == 2

        # Verify link updates
        write_calls = vault.write_note.call_args_list
        assert len(write_calls) == 3  # 2 link updates + 1 moved note

        # Check that links were updated correctly
        updated_content1 = write_calls[0][0][1]
        assert "[[New Name]]" in updated_content1
        assert "[[New Name|the project]]" in updated_content1
        assert "[[Old Name" not in updated_content1

        updated_content2 = write_calls[1][0][1]
        assert "[[New Name.md]]" in updated_content2
        assert "[[Old Name.md]]" not in updated_content2


@pytest.mark.asyncio
async def test_move_note_with_rename_links_disabled(mock_get_vault):
    """Test moving with rename but update_links=False."""
    vault = mock_get_vault

    note = Note(path="Inbox/Old.md", content="Content", metadata=NoteMetadata())
    vault.read_note.side_effect = [note, FileNotFoundError()]
    vault.write_note.return_value = None
    vault.delete_note.return_value = None

    # Test move with rename but links disabled
    result = await move_note("Inbox/Old.md", "Archive/New.md", update_links=False)

    assert result["success"] is True
    assert result["renamed"] is True
    assert result["details"]["links_updated"] == 0
    assert result["details"]["notes_updated"] == 0

    # Should not have called get_backlinks
    with patch("obsidian_mcp.tools.link_management.get_backlinks") as mock_backlinks:
        mock_backlinks.assert_not_called()


@pytest.mark.asyncio
async def test_move_note_preserve_aliases(mock_get_vault):
    """Test that link aliases are preserved during move with rename."""
    vault = mock_get_vault

    moving_note = Note(
        path="Technical Name.md", content="# Technical Name", metadata=NoteMetadata()
    )

    linking_note = Note(
        path="Index.md",
        content="See [[Technical Name|User Friendly Display]] for details.",
        metadata=NoteMetadata(),
    )

    with patch("obsidian_mcp.tools.link_management.get_backlinks") as mock_backlinks:
        mock_backlinks.return_value = {
            "findings": [
                {
                    "source_path": "Index.md",
                    "link_text": "User Friendly Display",
                    "link_type": "wiki",
                }
            ]
        }

        vault.read_note.side_effect = [
            moving_note,
            FileNotFoundError(),
            linking_note,
            moving_note,  # Re-read of source note before the final move write
        ]
        vault.write_note.return_value = None
        vault.delete_note.return_value = None

        result = await move_note(
            "Technical Name.md", "Archive/Better Name.md", update_links=True
        )

        # Check that alias was preserved
        write_calls = vault.write_note.call_args_list
        updated_content = write_calls[0][0][1]
        assert "[[Better Name|User Friendly Display]]" in updated_content
        assert result["details"]["links_updated"] == 1


@pytest.mark.asyncio
async def test_move_note_complex_path_changes(mock_get_vault):
    """Test moving between nested directories with rename."""
    vault = mock_get_vault

    note = Note(
        path="Projects/Active/2024/Q1/Sprint Planning.md",
        content="Sprint planning notes",
        metadata=NoteMetadata(),
    )

    linking_note = Note(
        path="Daily/2024-01-15.md",
        content="Review [[Sprint Planning]] today",
        metadata=NoteMetadata(),
    )

    with patch("obsidian_mcp.tools.link_management.get_backlinks") as mock_backlinks:
        mock_backlinks.return_value = {
            "findings": [
                {
                    "source_path": "Daily/2024-01-15.md",
                    "link_text": "Sprint Planning",
                    "link_type": "wiki",
                }
            ]
        }

        vault.read_note.side_effect = [
            note,
            FileNotFoundError(),
            linking_note,
            note,  # Re-read of source note before the final move write
        ]
        vault.write_note.return_value = None
        vault.delete_note.return_value = None

        result = await move_note(
            "Projects/Active/2024/Q1/Sprint Planning.md",
            "Archive/2024/Q1/Completed Sprints.md",
        )

        assert result["renamed"] is True
        assert result["details"]["links_updated"] == 1

        # Verify the link was updated correctly
        write_calls = vault.write_note.call_args_list
        updated_content = write_calls[0][0][1]
        assert "[[Completed Sprints]]" in updated_content


@pytest.mark.asyncio
async def test_move_note_same_name_different_folder(mock_get_vault):
    """Test that moving to different folder with same name doesn't update links."""
    vault = mock_get_vault

    note = Note(
        path="Inbox/Project.md", content="Project content", metadata=NoteMetadata()
    )

    vault.read_note.side_effect = [note, FileNotFoundError()]
    vault.write_note.return_value = None
    vault.delete_note.return_value = None

    # Should not call get_backlinks since name didn't change
    with patch("obsidian_mcp.tools.link_management.get_backlinks") as mock_backlinks:
        result = await move_note("Inbox/Project.md", "Projects/Active/Project.md")

        assert result["renamed"] is False
        assert result["details"]["links_updated"] == 0
        mock_backlinks.assert_not_called()


@pytest.mark.asyncio
async def test_move_note_self_link_survives_backlink_rewrite(mock_get_vault):
    """Regression test: a note that links to itself must keep the rewritten
    link after being moved/renamed.

    move_note reads source_note once *before* the backlink-rewrite loop, but
    that loop also rewrites the moved note itself when it self-links (it is
    never excluded from the backlink set). The final write to the
    destination must reflect that in-place fix, not the stale pre-loop
    snapshot -- otherwise the destination silently keeps a broken [[Foo]]
    link even though the tool reports the link as updated.
    """
    vault = mock_get_vault

    stale_note = Note(
        path="Foo.md",
        content="# Foo\n\nSee [[Foo]] for details.",
        metadata=NoteMetadata(),
    )
    # What the loop reads when it opens Foo.md to check it for backlinks --
    # at that point nothing has written to disk yet, so it's still stale.
    stale_note_at_loop_read = Note(
        path="Foo.md",
        content="# Foo\n\nSee [[Foo]] for details.",
        metadata=NoteMetadata(),
    )
    # What re-reading Foo.md *after* the loop's self-rewrite would return.
    fixed_note_after_loop_write = Note(
        path="Foo.md",
        content="# Foo\n\nSee [[Bar]] for details.",
        metadata=NoteMetadata(),
    )

    with patch("obsidian_mcp.tools.link_management.get_backlinks") as mock_backlinks:
        mock_backlinks.return_value = {
            "findings": [
                {"source_path": "Foo.md", "link_text": "Foo", "link_type": "wiki"}
            ]
        }

        vault.read_note.side_effect = [
            stale_note,  # initial read of source_path
            FileNotFoundError(),  # destination doesn't exist check
            stale_note_at_loop_read,  # loop reads Foo.md (self-link)
            fixed_note_after_loop_write,  # re-read right before final write
        ]
        vault.write_note.return_value = None
        vault.delete_note.return_value = None

        result = await move_note("Foo.md", "Bar.md", update_links=True)

        assert result["renamed"] is True
        assert result["details"]["links_updated"] == 1

        write_calls = vault.write_note.call_args_list
        # write_calls[0]: the loop's in-place self-rewrite of Foo.md
        # write_calls[1]: the final write to the destination
        assert len(write_calls) == 2
        final_path, final_content = write_calls[1][0][0], write_calls[1][0][1]
        assert final_path == "Bar.md"
        assert "[[Bar]]" in final_content
        assert "[[Foo]]" not in final_content


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
