"""Tests for the rename_note functionality."""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from obsidian_mcp.tools.organization import rename_note
from obsidian_mcp.tools.link_management import get_backlinks
from obsidian_mcp.models import Note, NoteMetadata


@pytest.fixture
def mock_vault():
    """Create a mock vault for testing."""
    vault = AsyncMock()
    return vault


@pytest.fixture
def mock_get_vault(mock_vault):
    """Patch get_vault to return our mock."""
    with patch('obsidian_mcp.tools.organization.get_vault', return_value=mock_vault):
        with patch('obsidian_mcp.tools.link_management.get_vault', return_value=mock_vault):
            yield mock_vault


@pytest.mark.asyncio
async def test_rename_note_basic(mock_get_vault):
    """Test basic note renaming without link updates."""
    vault = mock_get_vault
    
    # Setup mock note
    old_note = Note(
        path="Projects/Old Name.md",
        content="# Old Name\n\nThis is the content.",
        metadata=NoteMetadata(tags=["project"])
    )
    vault.read_note.side_effect = [old_note, FileNotFoundError()]
    vault.write_note.return_value = None
    vault.delete_note.return_value = None
    
    # Test rename without link updates
    result = await rename_note(
        "Projects/Old Name.md",
        "Projects/New Name.md",
        update_links=False
    )
    
    assert result["success"] is True
    assert result["old_path"] == "Projects/Old Name.md"
    assert result["new_path"] == "Projects/New Name.md"
    assert result["operation"] == "renamed"
    assert result["details"]["links_updated"] == 0
    assert result["details"]["notes_updated"] == 0
    
    # Verify operations
    vault.write_note.assert_called_once_with("Projects/New Name.md", old_note.content, overwrite=False)
    vault.delete_note.assert_called_once_with("Projects/Old Name.md")


@pytest.mark.asyncio
async def test_rename_note_with_link_updates(mock_get_vault):
    """Test renaming with automatic link updates."""
    vault = mock_get_vault
    
    # Setup mock notes
    old_note = Note(
        path="Projects/Old Name.md",
        content="# Old Name\n\nProject content.",
        metadata=NoteMetadata()
    )
    
    linking_note1 = Note(
        path="Daily/2024-01-15.md",
        content="Working on [[Old Name]] today. See also [[Old Name|the project]].",
        metadata=NoteMetadata()
    )
    
    linking_note2 = Note(
        path="Ideas/Related.md",
        content="This relates to [[Old Name.md]] and [[Other Note]].",
        metadata=NoteMetadata()
    )
    
    # Mock get_backlinks to return our linking notes
    with patch('obsidian_mcp.tools.organization.get_backlinks') as mock_backlinks:
        mock_backlinks.return_value = {
            'findings': [
                {'source_path': 'Daily/2024-01-15.md', 'link_text': 'Old Name', 'link_type': 'wiki'},
                {'source_path': 'Daily/2024-01-15.md', 'link_text': 'the project', 'link_type': 'wiki'},
                {'source_path': 'Ideas/Related.md', 'link_text': 'Old Name.md', 'link_type': 'wiki'}
            ]
        }
        
        # Setup vault mocks
        vault.read_note.side_effect = [
            old_note,  # First read of old note
            FileNotFoundError(),  # Check new path doesn't exist
            linking_note1,  # Read linking note 1
            linking_note2,  # Read linking note 2
        ]
        vault.write_note.return_value = None
        vault.delete_note.return_value = None
        
        # Test rename with link updates
        result = await rename_note(
            "Projects/Old Name.md",
            "Projects/New Name.md",
            update_links=True
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
    """Test that renaming to the same path raises an error."""
    with pytest.raises(ValueError, match="Old and new paths are the same"):
        await rename_note(
            "Projects/Note.md",
            "Projects/Note.md"
        )


@pytest.mark.asyncio
async def test_rename_note_different_directory_error(mock_get_vault):
    """Test that trying to rename to a different directory raises an error."""
    with pytest.raises(ValueError, match="Rename can only change the filename"):
        await rename_note(
            "Projects/Note.md",
            "Archive/Note.md"
        )


@pytest.mark.asyncio
async def test_rename_note_not_found(mock_get_vault):
    """Test renaming a non-existent note."""
    vault = mock_get_vault
    vault.read_note.side_effect = FileNotFoundError()
    
    with pytest.raises(FileNotFoundError, match="Note not found"):
        await rename_note(
            "Projects/NonExistent.md",
            "Projects/NewName.md"
        )


@pytest.mark.asyncio
async def test_rename_note_destination_exists(mock_get_vault):
    """Test renaming to an existing note path."""
    vault = mock_get_vault
    
    old_note = Note(path="Projects/Old.md", content="Old content", metadata=NoteMetadata())
    existing_note = Note(path="Projects/New.md", content="Existing", metadata=NoteMetadata())
    
    vault.read_note.side_effect = [old_note, existing_note]
    
    with pytest.raises(FileExistsError, match="Note already exists at destination"):
        await rename_note(
            "Projects/Old.md",
            "Projects/New.md"
        )


@pytest.mark.asyncio
async def test_rename_note_preserves_aliases(mock_get_vault):
    """Test that link aliases are preserved during rename."""
    vault = mock_get_vault
    
    old_note = Note(
        path="Projects/Technical Name.md",
        content="# Technical Name",
        metadata=NoteMetadata()
    )
    
    linking_note = Note(
        path="Index.md",
        content="See [[Technical Name|User Friendly Name]] for details.",
        metadata=NoteMetadata()
    )
    
    with patch('obsidian_mcp.tools.organization.get_backlinks') as mock_backlinks:
        mock_backlinks.return_value = {
            'findings': [
                {'source_path': 'Index.md', 'link_text': 'User Friendly Name', 'link_type': 'wiki'}
            ]
        }
        
        vault.read_note.side_effect = [old_note, FileNotFoundError(), linking_note]
        vault.write_note.return_value = None
        vault.delete_note.return_value = None
        
        result = await rename_note(
            "Projects/Technical Name.md",
            "Projects/Better Name.md",
            update_links=True
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
        metadata=NoteMetadata()
    )
    
    with patch('obsidian_mcp.tools.organization.get_backlinks') as mock_backlinks:
        # Return all the different link types found
        mock_backlinks.return_value = {
            'findings': [
                {'source_path': 'Complex.md', 'link_text': 'Note', 'link_type': 'wiki'},
                {'source_path': 'Complex.md', 'link_text': 'Note.md', 'link_type': 'wiki'},
                {'source_path': 'Complex.md', 'link_text': 'Display Name', 'link_type': 'wiki'},
                {'source_path': 'Complex.md', 'link_text': 'Another Name', 'link_type': 'wiki'},
                {'source_path': 'Complex.md', 'link_text': 'Note', 'link_type': 'wiki'},
                {'source_path': 'Complex.md', 'link_text': 'see this', 'link_type': 'wiki'},
            ]
        }
        
        vault.read_note.side_effect = [old_note, FileNotFoundError(), complex_note]
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
        assert result["details"]["links_updated"] == 6


if __name__ == "__main__":
    pytest.main([__file__, "-v"])