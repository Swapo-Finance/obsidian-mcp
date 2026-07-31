"""Tests for the rename_note functionality."""

from unittest.mock import AsyncMock, patch

import pytest

from obsidian_mcp.models import Note, NoteMetadata
from obsidian_mcp.tools.organization import rename_note
from obsidian_mcp.utils.filesystem import init_vault


@pytest.fixture
def mock_vault():
    """Create a mock vault for testing."""
    vault = AsyncMock()
    return vault


@pytest.fixture
def mock_get_vault(mock_vault):
    """Patch get_vault to return our mock.

    Also patches write_policy.get_vault: rename_note is now wrapped by
    _serialize_note_writes (Finding 2), whose decorator calls get_vault()
    via write_policy.py's own import to reach vault.write_lock -- a
    separate name binding from note_rename.py's, so it needs its own patch.
    """
    with (
        patch("obsidian_mcp.tools.note_rename.get_vault", return_value=mock_vault),
        patch("obsidian_mcp.tools.link_management.get_vault", return_value=mock_vault),
        patch("obsidian_mcp.tools.write_policy.get_vault", return_value=mock_vault),
    ):
        yield mock_vault


@pytest.mark.asyncio
async def test_rename_note_basic(mock_get_vault):
    """Test basic note renaming without link updates."""
    vault = mock_get_vault

    # Setup mock note
    old_note = Note(
        path="Projects/Old Name.md",
        content="# Old Name\n\nThis is the content.",
        metadata=NoteMetadata(tags=["project"]),
    )
    vault.read_note.side_effect = [old_note, FileNotFoundError()]
    vault.write_note.return_value = None
    vault.delete_note.return_value = None

    # Test rename without link updates
    result = await rename_note(
        "Projects/Old Name.md", "Projects/New Name.md", update_links=False
    )

    assert result["success"] is True
    assert result["old_path"] == "Projects/Old Name.md"
    assert result["new_path"] == "Projects/New Name.md"
    assert result["operation"] == "renamed"
    assert result["details"]["links_updated"] == 0
    assert result["details"]["notes_updated"] == 0

    # Verify operations
    vault.write_note.assert_called_once_with(
        "Projects/New Name.md", old_note.content, overwrite=False
    )
    vault.delete_note.assert_called_once_with("Projects/Old Name.md")


@pytest.mark.asyncio
async def test_rename_note_with_link_updates(mock_get_vault):
    """Test renaming with automatic link updates."""
    vault = mock_get_vault

    # Setup mock notes
    old_note = Note(
        path="Projects/Old Name.md",
        content="# Old Name\n\nProject content.",
        metadata=NoteMetadata(),
    )

    linking_note1 = Note(
        path="Daily/2024-01-15.md",
        content="Working on [[Old Name]] today. See also [[Old Name|the project]].",
        metadata=NoteMetadata(),
    )

    linking_note2 = Note(
        path="Ideas/Related.md",
        content="This relates to [[Old Name.md]] and [[Other Note]].",
        metadata=NoteMetadata(),
    )

    # Mock get_backlinks to return our linking notes
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
                    "source_path": "Ideas/Related.md",
                    "link_text": "Old Name.md",
                    "link_type": "wiki",
                },
            ]
        }

        # Setup vault mocks
        vault.read_note.side_effect = [
            old_note,  # First read of old note
            FileNotFoundError(),  # Check new path doesn't exist
            linking_note1,  # Read linking note 1
            linking_note2,  # Read linking note 2
            old_note,  # Re-read of old note before the final rename write
        ]
        vault.write_note.return_value = None
        vault.delete_note.return_value = None

        # Test rename with link updates
        result = await rename_note(
            "Projects/Old Name.md", "Projects/New Name.md", update_links=True
        )

        assert result["success"] is True
        assert result["details"]["links_updated"] == 3
        assert result["details"]["notes_updated"] == 2
        assert len(result["details"]["link_update_details"]) == 2

        # Verify link updates were written
        write_calls = vault.write_note.call_args_list
        assert len(write_calls) == 3  # 2 link updates + 1 new note

        # Check that links were updated correctly
        updated_content1 = write_calls[0][0][1]
        assert "[[New Name]]" in updated_content1
        assert "[[New Name|the project]]" in updated_content1
        assert "[[Old Name" not in updated_content1

        updated_content2 = write_calls[1][0][1]
        assert "[[New Name.md]]" in updated_content2
        assert "[[Old Name.md]]" not in updated_content2


@pytest.mark.asyncio
async def test_rename_note_same_path_error(mock_get_vault):
    """Test that renaming to the same path raises an error.

    Needs mock_get_vault even though rename_note's own same-path check never
    touches the vault: _serialize_note_writes (Finding 2) now wraps
    rename_note, and its decorator calls get_vault() unconditionally before
    entering the function body at all.
    """
    with pytest.raises(ValueError, match="Old and new paths are the same"):
        await rename_note("Projects/Note.md", "Projects/Note.md")


@pytest.mark.asyncio
async def test_rename_note_different_directory_error(mock_get_vault):
    """Test that trying to rename to a different directory raises an error."""
    with pytest.raises(ValueError, match="Rename can only change the filename"):
        await rename_note("Projects/Note.md", "Archive/Note.md")


@pytest.mark.asyncio
async def test_rename_note_not_found(mock_get_vault, tmp_path):
    """Test renaming a non-existent note.

    rename_note's FileNotFoundError path falls back to searching for the
    note by filename via search_discovery.search_notes(), which is NOT
    covered by mock_get_vault (that fixture only patches get_vault in
    note_rename.py and link_management.py) -- it calls the real, global
    get_vault(). Give it a real (empty) vault so that fallback search
    legitimately finds zero matches, instead of relying on whatever vault
    another test file happened to leave behind in the global singleton.
    """
    vault = mock_get_vault
    vault.read_note.side_effect = FileNotFoundError()
    init_vault(str(tmp_path))

    with pytest.raises(FileNotFoundError, match="Note not found"):
        await rename_note("Projects/NonExistent.md", "Projects/NewName.md")


@pytest.mark.asyncio
async def test_rename_note_destination_exists(mock_get_vault):
    """Test renaming to an existing note path."""
    vault = mock_get_vault

    old_note = Note(
        path="Projects/Old.md", content="Old content", metadata=NoteMetadata()
    )
    existing_note = Note(
        path="Projects/New.md", content="Existing", metadata=NoteMetadata()
    )

    vault.read_note.side_effect = [old_note, existing_note]

    with pytest.raises(FileExistsError, match="Note already exists at destination"):
        await rename_note("Projects/Old.md", "Projects/New.md")


@pytest.mark.asyncio
async def test_rename_note_preserves_aliases(mock_get_vault):
    """Test that link aliases are preserved during rename."""
    vault = mock_get_vault

    old_note = Note(
        path="Projects/Technical Name.md",
        content="# Technical Name",
        metadata=NoteMetadata(),
    )

    linking_note = Note(
        path="Index.md",
        content="See [[Technical Name|User Friendly Name]] for details.",
        metadata=NoteMetadata(),
    )

    with patch("obsidian_mcp.tools.link_management.get_backlinks") as mock_backlinks:
        mock_backlinks.return_value = {
            "findings": [
                {
                    "source_path": "Index.md",
                    "link_text": "User Friendly Name",
                    "link_type": "wiki",
                }
            ]
        }

        vault.read_note.side_effect = [
            old_note,
            FileNotFoundError(),
            linking_note,
            old_note,  # Re-read of old note before the final rename write
        ]
        vault.write_note.return_value = None
        vault.delete_note.return_value = None

        result = await rename_note(
            "Projects/Technical Name.md", "Projects/Better Name.md", update_links=True
        )

        # Check that alias was preserved
        write_calls = vault.write_note.call_args_list
        updated_content = write_calls[0][0][1]
        assert "[[Better Name|User Friendly Name]]" in updated_content
        assert result["details"]["links_updated"] == 1


@pytest.mark.asyncio
async def test_rename_handles_various_link_formats(mock_get_vault):
    """Test that various wiki link formats are handled correctly."""
    vault = mock_get_vault

    old_note = Note(path="Note.md", content="Content", metadata=NoteMetadata())

    complex_note = Note(
        path="Complex.md",
        content="""
Here are various link formats:
- Basic: [[Note]]
- With extension: [[Note.md]]
- With alias: [[Note|Display Name]]
- Extension alias: [[Note.md|Another Name]]
- In a sentence: Check out [[Note]] for more info.
- Multiple on line: [[Note]] and also [[Note|see this]]
""",
        metadata=NoteMetadata(),
    )

    with patch("obsidian_mcp.tools.link_management.get_backlinks") as mock_backlinks:
        # Return all the different link types found
        mock_backlinks.return_value = {
            "findings": [
                {"source_path": "Complex.md", "link_text": "Note", "link_type": "wiki"},
                {
                    "source_path": "Complex.md",
                    "link_text": "Note.md",
                    "link_type": "wiki",
                },
                {
                    "source_path": "Complex.md",
                    "link_text": "Display Name",
                    "link_type": "wiki",
                },
                {
                    "source_path": "Complex.md",
                    "link_text": "Another Name",
                    "link_type": "wiki",
                },
                {"source_path": "Complex.md", "link_text": "Note", "link_type": "wiki"},
                {
                    "source_path": "Complex.md",
                    "link_text": "see this",
                    "link_type": "wiki",
                },
            ]
        }

        vault.read_note.side_effect = [
            old_note,
            FileNotFoundError(),
            complex_note,
            old_note,  # Re-read of old note before the final rename write
        ]
        vault.write_note.return_value = None
        vault.delete_note.return_value = None

        result = await rename_note("Note.md", "Updated Note.md", update_links=True)

        # Get the updated content
        write_calls = vault.write_note.call_args_list
        updated_content = write_calls[0][0][1]

        # Verify all formats were updated correctly
        assert "[[Updated Note]]" in updated_content
        assert "[[Updated Note.md]]" in updated_content
        assert "[[Updated Note|Display Name]]" in updated_content
        assert "[[Updated Note.md|Another Name]]" in updated_content
        assert "[[Note]]" not in updated_content
        assert "[[Note.md]]" not in updated_content
        # Complex.md actually contains 7 wiki-links to "Note": 3 bare [[Note]]
        # (Basic, in-sentence, multiple-on-line), [[Note.md]], [[Note|Display Name]],
        # [[Note.md|Another Name]], and [[Note|see this]]. rename_note re-scans the
        # content and updates every one, so links_updated is 7 (the mocked findings
        # list only enumerates 6, but the count comes from real content replacements).
        assert result["details"]["links_updated"] == 7


@pytest.mark.asyncio
async def test_rename_note_self_link_survives_backlink_rewrite(mock_get_vault):
    """Regression test: a note that links to itself must keep the rewritten
    link after being renamed.

    rename_note reads source_note once *before* the backlink-rewrite loop,
    but that loop also rewrites the renamed note itself when it self-links
    (it is never excluded from the backlink set). The final write to
    new_path must reflect that in-place fix, not the stale pre-loop
    snapshot -- otherwise new_path silently keeps a broken [[Foo]] link even
    though the tool reports the link as updated.
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
            stale_note,  # initial read of old_path
            FileNotFoundError(),  # new_path doesn't exist check
            stale_note_at_loop_read,  # loop reads Foo.md (self-link)
            fixed_note_after_loop_write,  # re-read right before final write
        ]
        vault.write_note.return_value = None
        vault.delete_note.return_value = None

        result = await rename_note("Foo.md", "Bar.md", update_links=True)

        assert result["success"] is True
        assert result["details"]["links_updated"] == 1

        write_calls = vault.write_note.call_args_list
        # write_calls[0]: the loop's in-place self-rewrite of Foo.md
        # write_calls[1]: the final write to new_path
        assert len(write_calls) == 2
        final_path, final_content = write_calls[1][0][0], write_calls[1][0][1]
        assert final_path == "Bar.md"
        assert "[[Bar]]" in final_content
        assert "[[Foo]]" not in final_content


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
