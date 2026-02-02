"""Tests for auto-search functionality in rename_note and move_note."""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from obsidian_mcp.tools.organization import rename_note, move_note
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
async def test_rename_note_auto_search_single_match(mock_get_vault):
    """Test rename_note auto-searches when exact path not found and finds single match."""
    vault = mock_get_vault
    
    # Setup mock note
    note = Note(
        path="Projects/My Note.md",
        content="# My Note\n\nContent here.",
        metadata=NoteMetadata()
    )
    
    # Mock search_notes to return single result
    with patch('obsidian_mcp.tools.organization.search_notes') as mock_search:
        mock_search.return_value = {
            "results": [{"path": "Projects/My Note.md"}],
            "count": 1
        }
        
        # First read fails, second succeeds after search
        vault.read_note.side_effect = [
            FileNotFoundError(),  # Initial path not found
            note,  # Found after search
            FileNotFoundError()  # Destination doesn't exist
        ]
        vault.write_note.return_value = None
        vault.delete_note.return_value = None
        
        # Test rename with just filename
        result = await rename_note(
            "My Note.md",
            "New Note.md",
            update_links=False
        )
        
        assert result["success"] is True
        assert result["old_path"] == "Projects/My Note.md"
        assert result["new_path"] == "Projects/New Note.md"
        
        # Verify search was called
        mock_search.assert_called_once_with("path:My Note.md", max_results=10, ctx=None)


@pytest.mark.asyncio
async def test_rename_note_auto_search_multiple_matches(mock_get_vault):
    """Test rename_note shows helpful error when multiple matches found."""
    vault = mock_get_vault
    
    # Mock search_notes to return multiple results
    with patch('obsidian_mcp.tools.organization.search_notes') as mock_search:
        mock_search.return_value = {
            "results": [
                {"path": "Projects/My Note.md"},
                {"path": "Archive/My Note.md"},
                {"path": "Daily/My Note.md"}
            ],
            "count": 3
        }
        
        vault.read_note.side_effect = FileNotFoundError()
        
        # Test rename with just filename
        with pytest.raises(ValueError) as exc_info:
            await rename_note("My Note.md", "New Note.md")
        
        error_msg = str(exc_info.value)
        assert "Multiple notes found with name 'My Note.md'" in error_msg
        assert "Projects/My Note.md" in error_msg
        assert "Archive/My Note.md" in error_msg
        assert "Daily/My Note.md" in error_msg


@pytest.mark.asyncio
async def test_rename_note_auto_search_no_matches(mock_get_vault):
    """Test rename_note fails appropriately when no matches found."""
    vault = mock_get_vault
    
    # Mock search_notes to return no results
    with patch('obsidian_mcp.tools.organization.search_notes') as mock_search:
        mock_search.return_value = {
            "results": [],
            "count": 0
        }
        
        vault.read_note.side_effect = FileNotFoundError()
        
        # Test rename with just filename
        with pytest.raises(FileNotFoundError) as exc_info:
            await rename_note("NonExistent.md", "New Note.md")
        
        assert "Note not found" in str(exc_info.value)


@pytest.mark.asyncio
async def test_move_note_auto_search_single_match(mock_get_vault):
    """Test move_note auto-searches when source not found and finds single match."""
    vault = mock_get_vault
    
    # Setup mock note
    note = Note(
        path="Inbox/Todo.md",
        content="# Todo List\n\nItems here.",
        metadata=NoteMetadata()
    )
    
    # Mock search_notes
    with patch('obsidian_mcp.tools.organization.search_notes') as mock_search:
        mock_search.return_value = {
            "results": [{"path": "Inbox/Todo.md"}],
            "count": 1
        }
        
        vault.read_note.side_effect = [
            FileNotFoundError(),  # Initial path not found
            note,  # Found after search
            FileNotFoundError()  # Destination doesn't exist
        ]
        vault.write_note.return_value = None
        vault.delete_note.return_value = None
        
        # Test move with just filename
        result = await move_note(
            "Todo.md",
            "Archive/Todo.md"
        )
        
        assert result["success"] is True
        assert result["source"] == "Inbox/Todo.md"
        assert result["destination"] == "Archive/Todo.md"
        
        # Verify operations
        vault.write_note.assert_called_once_with("Archive/Todo.md", note.content, overwrite=False)
        vault.delete_note.assert_called_once_with("Inbox/Todo.md")


@pytest.mark.asyncio
async def test_move_note_auto_search_with_rename(mock_get_vault):
    """Test move_note with auto-search when also renaming."""
    vault = mock_get_vault
    
    # Setup mock note
    note = Note(
        path="Inbox/Draft.md",
        content="# Draft\n\nDraft content.",
        metadata=NoteMetadata()
    )
    
    linking_note = Note(
        path="Index.md",
        content="Check out [[Draft]] for details.",
        metadata=NoteMetadata()
    )
    
    # Mock search_notes and get_backlinks
    with patch('obsidian_mcp.tools.organization.search_notes') as mock_search:
        with patch('obsidian_mcp.tools.organization.get_backlinks') as mock_backlinks:
            mock_search.return_value = {
                "results": [{"path": "Inbox/Draft.md"}],
                "count": 1
            }
            
            mock_backlinks.return_value = {
                'findings': [
                    {'source_path': 'Index.md', 'link_text': 'Draft', 'link_type': 'wiki'}
                ]
            }
            
            vault.read_note.side_effect = [
                FileNotFoundError(),  # Initial path not found
                note,  # Found after search
                FileNotFoundError(),  # Destination doesn't exist
                linking_note  # For link update
            ]
            vault.write_note.return_value = None
            vault.delete_note.return_value = None
            
            # Test move with rename using just filename
            result = await move_note(
                "Draft.md",
                "Projects/Final Document.md",
                update_links=True
            )
            
            assert result["success"] is True
            assert result["source"] == "Inbox/Draft.md"
            assert result["destination"] == "Projects/Final Document.md"
            assert result["renamed"] is True
            assert result["details"]["links_updated"] == 1


@pytest.mark.asyncio
async def test_auto_search_preserves_directory_context(mock_get_vault):
    """Test that auto-search maintains the found directory when renaming."""
    vault = mock_get_vault
    
    # Note in a nested directory
    note = Note(
        path="Projects/2024/Q1/Sprint Planning.md",
        content="# Sprint Planning",
        metadata=NoteMetadata()
    )
    
    with patch('obsidian_mcp.tools.organization.search_notes') as mock_search:
        mock_search.return_value = {
            "results": [{"path": "Projects/2024/Q1/Sprint Planning.md"}],
            "count": 1
        }
        
        vault.read_note.side_effect = [
            FileNotFoundError(),
            note,
            FileNotFoundError()
        ]
        vault.write_note.return_value = None
        vault.delete_note.return_value = None
        
        # Rename with just filename
        result = await rename_note(
            "Sprint Planning.md",
            "Q1 Retrospective.md",
            update_links=False
        )
        
        # Should preserve the found directory
        assert result["new_path"] == "Projects/2024/Q1/Q1 Retrospective.md"


@pytest.mark.asyncio
async def test_auto_search_with_context_logging(mock_get_vault):
    """Test that auto-search logs helpful context messages."""
    vault = mock_get_vault
    ctx = MagicMock()
    
    note = Note(
        path="Daily/2024-01-15.md",
        content="Daily note",
        metadata=NoteMetadata()
    )
    
    with patch('obsidian_mcp.tools.organization.search_notes') as mock_search:
        mock_search.return_value = {
            "results": [{"path": "Daily/2024-01-15.md"}],
            "count": 1
        }
        
        vault.read_note.side_effect = [
            FileNotFoundError(),
            note,
            FileNotFoundError()
        ]
        vault.write_note.return_value = None
        vault.delete_note.return_value = None
        
        await rename_note(
            "2024-01-15.md",
            "2024-01-15-reviewed.md",
            update_links=False,
            ctx=ctx
        )
        
        # Check that helpful messages were logged
        ctx.info.assert_any_call("Note not found at 2024-01-15.md, searching for filename...")
        ctx.info.assert_any_call("Found unique match at: Daily/2024-01-15.md")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])