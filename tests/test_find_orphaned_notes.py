"""Test find_orphaned_notes functionality."""

import pytest
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime, timedelta


@pytest.mark.asyncio
async def test_find_orphaned_notes_no_backlinks():
    """Test finding notes with no backlinks."""
    # Create mock vault
    mock_vault = MagicMock()
    
    # Mock list_notes to return test notes
    mock_vault.list_notes = AsyncMock(return_value=[
        {"path": "Note1.md"},
        {"path": "Note2.md"},
        {"path": "Templates/Template.md"},  # Should be excluded
        {"path": "Note3.md"}
    ])
    
    # Mock read_note
    async def mock_read_note(path):
        mock_note = MagicMock()
        mock_note.path = path
        mock_note.metadata.modified = datetime.now().isoformat()
        mock_note.metadata.size = 100
        mock_note.metadata.word_count = 50
        mock_note.metadata.tags = ["test"]
        mock_note.metadata.frontmatter = {"title": "Test"}
        return mock_note
    
    mock_vault.read_note = AsyncMock(side_effect=mock_read_note)
    
    # Mock get_backlinks - Note2 has backlinks, others don't
    async def mock_get_backlinks(path):
        if path == "Note2.md":
            return [{"source": "Note1.md", "context": "Links to [[Note2]]"}]
        return []
    
    mock_vault.get_backlinks = AsyncMock(side_effect=mock_get_backlinks)
    
    # Import after mocking
    from obsidian_mcp.utils import filesystem
    original_get_vault = filesystem.get_vault
    filesystem.get_vault = lambda: mock_vault
    
    try:
        # Import the function after mocking
        from obsidian_mcp.tools.find_orphaned_notes import find_orphaned_notes
        
        # Test finding orphaned notes
        result = await find_orphaned_notes(
            orphan_type="no_backlinks",
            exclude_folders=["Templates"]
        )
        
        # Verify results
        assert result["count"] == 2  # Note1 and Note3 (Note2 has backlinks, Template excluded)
        assert len(result["orphaned_notes"]) == 2
        
        paths = [note["path"] for note in result["orphaned_notes"]]
        assert "Note1.md" in paths
        assert "Note3.md" in paths
        assert "Note2.md" not in paths  # Has backlinks
        assert "Templates/Template.md" not in paths  # Excluded
        
        # Check note details
        for note in result["orphaned_notes"]:
            assert note["reason"] == "No incoming links"
            assert "modified" in note
            assert "size" in note
            assert "word_count" in note
        
        print("✓ Find orphaned notes (no backlinks) test passed!")
        
    finally:
        filesystem.get_vault = original_get_vault


@pytest.mark.asyncio
async def test_find_orphaned_notes_with_age_filter():
    """Test finding orphaned notes with age filter."""
    mock_vault = MagicMock()
    
    # Create notes with different ages
    # Use dates that will work regardless of current time
    old_date = "2023-01-01T10:00:00Z"  # Definitely older than 30 days
    recent_date = datetime.now().isoformat()  # Current time
    
    mock_vault.list_notes = AsyncMock(return_value=[
        {"path": "OldNote.md"},
        {"path": "RecentNote.md"}
    ])
    
    async def mock_read_note(path):
        mock_note = MagicMock()
        mock_note.path = path
        mock_note.metadata.modified = old_date if path == "OldNote.md" else recent_date
        mock_note.metadata.size = 100
        mock_note.metadata.word_count = 50
        mock_note.metadata.tags = []
        mock_note.metadata.frontmatter = {}
        return mock_note
    
    mock_vault.read_note = AsyncMock(side_effect=mock_read_note)
    mock_vault.get_backlinks = AsyncMock(return_value=[])  # All orphaned
    
    from obsidian_mcp.utils import filesystem
    original_get_vault = filesystem.get_vault
    filesystem.get_vault = lambda: mock_vault
    
    try:
        # Import the function after mocking
        from obsidian_mcp.tools.find_orphaned_notes import find_orphaned_notes
        
        # Test with 30 day age filter
        result = await find_orphaned_notes(
            orphan_type="no_backlinks",
            min_age_days=30
        )
        
        # Should only find the old note
        assert result["count"] == 1
        assert result["orphaned_notes"][0]["path"] == "OldNote.md"
        
        print("✓ Find orphaned notes with age filter test passed!")
        
    finally:
        filesystem.get_vault = original_get_vault


if __name__ == "__main__":
    import asyncio
    asyncio.run(test_find_orphaned_notes_no_backlinks())
    # asyncio.run(test_find_orphaned_notes_with_age_filter())  # Skip for now due to date issues