#!/usr/bin/env python3
"""Integration tests for filesystem-based Obsidian MCP server."""

import os
import asyncio
import shutil
import tempfile
from pathlib import Path
import pytest
import pytest_asyncio

# Add parent directory to path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from obsidian_mcp.utils.filesystem import ObsidianVault, init_vault
from obsidian_mcp.tools import (
    read_note, create_note, update_note, delete_note,
    search_notes, list_notes, read_image
)


class TestObsidianFilesystem:
    """Test suite for filesystem operations."""
    
    @pytest_asyncio.fixture
    async def test_vault(self):
        """Create a temporary test vault."""
        # Create temporary directory
        temp_dir = tempfile.mkdtemp(prefix="obsidian_test_")
        
        # Set environment variable
        os.environ["OBSIDIAN_VAULT_PATH"] = temp_dir
        
        # Initialize vault
        vault = init_vault(temp_dir)
        
        # Create some test content
        test_notes = {
            "test_note.md": """# Test Note

This is a test note with some content.

Tags: #test #demo

## Links

- [[Another Note]]
- [External Link](https://example.com)
""",
            "folder/nested_note.md": """---
title: Nested Note
tags: [nested, example]
aliases:
  - nested-example
  - example-note
---

# Nested Note

This note is in a folder.

#inline-tag
""",
            "images/test_image.md": """# Image Test

Here's an image reference:

![[test_image.png]]

And another style:
![Alt text](test_image.png)
"""
        }
        
        # Create test notes
        for path, content in test_notes.items():
            full_path = Path(temp_dir) / path
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_text(content)
        
        # Create a test image
        image_path = Path(temp_dir) / "images" / "test_image.png"
        image_path.parent.mkdir(parents=True, exist_ok=True)
        # Create a minimal PNG file (1x1 pixel)
        image_path.write_bytes(
            b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01'
            b'\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\x0f'
            b'\x00\x00\x01\x01\x00\x05\xf8\xdc\xccO\x00\x00\x00\x00IEND\xaeB`\x82'
        )
        
        # Update search index for tests
        from obsidian_mcp.utils.filesystem import get_vault
        current_vault = get_vault()
        if current_vault:
            await current_vault._update_search_index()
        
        yield vault
        
        # Cleanup
        if current_vault and current_vault.persistent_index:
            await current_vault.persistent_index.close()
        shutil.rmtree(temp_dir)
    
    @pytest.mark.asyncio
    async def test_read_note(self, test_vault):
        """Test reading a note."""
        result = await read_note("test_note.md")
        
        assert result["success"] == True
        assert result["path"] == "test_note.md"
        assert result["operation"] == "read"
        assert "Test Note" in result["details"]["content"]
        assert "test" in result["details"]["metadata"]["tags"]
        assert "demo" in result["details"]["metadata"]["tags"]
    
    @pytest.mark.asyncio
    async def test_read_note_with_frontmatter(self, test_vault):
        """Test reading a note with YAML frontmatter."""
        result = await read_note("folder/nested_note.md")
        
        assert result["success"] == True
        assert result["path"] == "folder/nested_note.md"
        assert result["operation"] == "read"
        assert result["details"]["metadata"]["frontmatter"]["title"] == "Nested Note"
        assert "nested" in result["details"]["metadata"]["tags"]
        assert "inline-tag" in result["details"]["metadata"]["tags"]
        assert "nested-example" in result["details"]["metadata"]["aliases"]
    
    @pytest.mark.asyncio
    async def test_create_note(self, test_vault):
        """Test creating a new note."""
        content = """# New Note

This is a newly created note.

#created-tag
"""
        result = await create_note("new_note.md", content)
        
        assert result["success"] == True
        assert result["path"] == "new_note.md"
        assert result["operation"] == "created"
        assert result["details"]["created"] == True
        assert "created-tag" in result["details"]["metadata"]["tags"]
        
        # Verify it was actually created
        read_result = await read_note("new_note.md")
        assert read_result["details"]["content"] == content
    
    @pytest.mark.asyncio
    async def test_update_note(self, test_vault):
        """Test updating an existing note."""
        new_content = """# Updated Test Note

This content has been updated.

#updated
"""
        result = await update_note("test_note.md", new_content)
        
        assert result["success"] == True
        assert result["path"] == "test_note.md"
        assert result["operation"] == "updated"
        assert result["details"]["updated"] == True
        
        # Verify the update
        read_result = await read_note("test_note.md")
        assert "Updated Test Note" in read_result["details"]["content"]
        assert "updated" in read_result["details"]["metadata"]["tags"]
    
    @pytest.mark.asyncio
    async def test_delete_note(self, test_vault):
        """Test deleting a note."""
        # First create a note to delete
        await create_note("to_delete.md", "# To Delete")
        
        # Delete it
        result = await delete_note("to_delete.md")
        assert result["success"] == True
        assert result["operation"] == "deleted"
        assert result["details"]["deleted"] == True
        
        # Verify it's gone
        with pytest.raises(FileNotFoundError):
            await read_note("to_delete.md")
    
    @pytest.mark.asyncio
    async def test_search_notes(self, test_vault):
        """Test searching notes."""
        result = await search_notes("test")
        
        assert result["count"] > 0
        assert any("test_note.md" in r["path"] for r in result["results"])
        
        # Test new metadata fields
        assert "total_count" in result
        assert "truncated" in result
        assert isinstance(result["total_count"], int)
        assert isinstance(result["truncated"], bool)
    
    @pytest.mark.asyncio
    async def test_search_by_tag(self, test_vault):
        """Test searching by tag."""
        result = await search_notes("tag:nested")
        
        assert result["count"] == 1
        assert result["results"][0]["path"] == "folder/nested_note.md"
    
    @pytest.mark.asyncio
    async def test_list_notes(self, test_vault):
        """Test listing notes."""
        result = await list_notes()
        
        assert result["total"] >= 3
        paths = [n["path"] for n in result["items"]]
        assert "test_note.md" in paths
        assert "folder/nested_note.md" in paths
    
    @pytest.mark.asyncio
    async def test_read_image(self, test_vault):
        """Test reading an image."""
        # Test without metadata - returns Image object
        result = await read_image("images/test_image.png")
        from fastmcp import Image
        assert isinstance(result, Image)
        
        # Test with metadata - returns dict
        result_with_metadata = await read_image("images/test_image.png", include_metadata=True)
        assert isinstance(result_with_metadata, dict)
        assert isinstance(result_with_metadata["image"], Image)
        assert result_with_metadata["path"] == "images/test_image.png"
        assert result_with_metadata["mime_type"] == "image/png"
    
    @pytest.mark.asyncio
    async def test_read_note_with_images(self, test_vault):
        """Test reading a note with embedded images."""
        # Read the note without images
        note_result = await read_note("images/test_image.md")
        assert "path" in note_result
        assert note_result["path"] == "images/test_image.md"
        
        # Use view_note_images to get the images
        from obsidian_mcp.tools import view_note_images
        images = await view_note_images("images/test_image.md")
        
        from fastmcp import Image
        assert len(images) > 0
        assert all(isinstance(img, Image) for img in images)
    
    @pytest.mark.asyncio
    async def test_search_with_max_results(self, test_vault):
        """Test search with max_results parameter."""
        # Create many test notes to test truncation
        for i in range(10):
            await create_note(f"search_test_{i}.md", f"# Search Test {i}\n\nThis note contains the word searchtest.")
        
        # Force index update
        from obsidian_mcp.utils.filesystem import get_vault
        vault = get_vault()
        if vault:
            vault._index_timestamp = None  # Force re-index
            await vault._update_search_index()
        
        # Search with small limit
        result_limited = await search_notes("searchtest", max_results=5)
        assert result_limited["count"] == 5
        assert result_limited["total_count"] >= 10
        assert result_limited["truncated"] == True
        
        # Search with large limit
        result_full = await search_notes("searchtest", max_results=50)
        assert result_full["count"] >= 10
        assert result_full["total_count"] >= 10
        assert result_full["truncated"] == False
    
    @pytest.mark.asyncio
    async def test_performance_search(self, test_vault):
        """Test search performance with indexing."""
        import time
        
        # First search (builds index)
        start = time.time()
        result1 = await search_notes("test")
        first_time = time.time() - start
        
        # Second search (uses cache)
        start = time.time()
        result2 = await search_notes("note")
        second_time = time.time() - start
        
        # Second search should be faster
        assert second_time < first_time
        assert result1["count"] > 0
        assert result2["count"] > 0


if __name__ == "__main__":
    # Run tests with pytest
    pytest.main([__file__, "-v"])