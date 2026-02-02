"""Test enhanced search that includes both filename and content matches."""

import pytest
from obsidian_mcp.tools.search_discovery import search_notes
from unittest.mock import AsyncMock, MagicMock


@pytest.mark.asyncio
async def test_enhanced_default_search():
    """Test that default search includes both filename and content matches."""
    # Create mock vault
    mock_vault = MagicMock()
    
    # Mock content search results
    content_results = [
        {
            "path": "Notes/Some Other Note.md",
            "score": 0.8,
            "matches": ["tag refactor"],
            "context": "This note mentions tag refactor in the content..."
        },
        {
            "path": "Daily/2024-01-15.md", 
            "score": 0.6,
            "matches": ["tag", "refactor"],
            "context": "Separate mentions of tag and refactor..."
        }
    ]
    
    # Mock the vault methods
    mock_vault.search_notes = AsyncMock(return_value=content_results)
    mock_vault.list_notes = AsyncMock(return_value=[
        {"path": "Obsidian Tag Refactor.md"},
        {"path": "Notes/Some Other Note.md"},
        {"path": "Daily/2024-01-15.md"},
        {"path": "Projects/Tag Management.md"}
    ])
    
    # Mock note reading for path search
    async def mock_read_note(path):
        mock_note = MagicMock()
        mock_note.path = path
        mock_note.content = f"Content of {path}"
        return mock_note
    
    mock_vault.read_note = AsyncMock(side_effect=mock_read_note)
    mock_vault.get_last_search_metadata = MagicMock(return_value={"truncated": False, "total_count": 2})
    
    # Import after mocking to ensure we use mocked vault
    from obsidian_mcp.utils import filesystem
    original_get_vault = filesystem.get_vault
    filesystem.get_vault = lambda: mock_vault
    
    try:
        # Test the enhanced search
        results = await search_notes("tag refactor", context_length=100, max_results=10)
        
        # Verify we got results
        assert results["count"] > 0
        
        # Find the filename match - it should be ranked first
        filename_match = None
        for result in results["results"]:
            if "Obsidian Tag Refactor.md" in result["path"]:
                filename_match = result
                break
        
        assert filename_match is not None, "Should find 'Obsidian Tag Refactor.md' in results"
        assert filename_match["match_type"] == "filename"
        assert filename_match["score"] >= 2.0  # Should be boosted
        
        # Verify filename matches appear before content matches
        if len(results["results"]) > 1:
            first_result = results["results"][0]
            assert "Tag Refactor" in first_result["path"] or "tag refactor" in first_result["path"].lower()
            assert first_result["match_type"] == "filename"
        
        print("✓ Enhanced search test passed!")
        
    finally:
        # Restore original
        filesystem.get_vault = original_get_vault


if __name__ == "__main__":
    import asyncio
    asyncio.run(test_enhanced_default_search())