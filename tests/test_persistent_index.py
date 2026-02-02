#!/usr/bin/env python3
"""Test persistent search index functionality."""

import os
import asyncio
import tempfile
import shutil
from pathlib import Path
import pytest
import pytest_asyncio

# Add parent directory to path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from obsidian_mcp.utils.filesystem import ObsidianVault
from obsidian_mcp.utils.persistent_index import PersistentSearchIndex


class TestPersistentIndex:
    """Test suite for persistent search index."""
    
    @pytest_asyncio.fixture
    async def test_vault_dir(self):
        """Create a temporary vault directory."""
        temp_dir = tempfile.mkdtemp(prefix="obsidian_test_index_")
        yield temp_dir
        shutil.rmtree(temp_dir)
    
    @pytest.mark.asyncio
    async def test_index_creation(self, test_vault_dir):
        """Test that index is created properly."""
        index = PersistentSearchIndex(Path(test_vault_dir))
        await index.initialize()
        
        # Check that database file was created
        db_path = Path(test_vault_dir) / ".obsidian" / "mcp-search-index.db"
        assert db_path.exists()
        
        await index.close()
    
    @pytest.mark.asyncio
    async def test_file_indexing(self, test_vault_dir):
        """Test indexing a file."""
        index = PersistentSearchIndex(Path(test_vault_dir))
        await index.initialize()
        
        # Index a test file
        await index.index_file(
            "test.md",
            "# Test Note\n\nThis is test content.",
            1234567890.0,
            100,
            {"tags": ["test"]}
        )
        
        # Check that file was indexed
        file_info = await index.get_file_info("test.md")
        assert file_info is not None
        assert file_info["mtime"] == 1234567890.0
        assert file_info["size"] == 100
        
        await index.close()
    
    @pytest.mark.asyncio
    async def test_incremental_update(self, test_vault_dir):
        """Test that only changed files are re-indexed."""
        index = PersistentSearchIndex(Path(test_vault_dir))
        await index.initialize()
        
        # Index a file
        await index.index_file("test.md", "Content 1", 1000.0, 10)
        
        # Check that it doesn't need update with same mtime/size
        assert not await index.needs_update("test.md", 1000.0, 10)
        
        # Check that it needs update with different mtime
        assert await index.needs_update("test.md", 2000.0, 10)
        
        # Check that it needs update with different size
        assert await index.needs_update("test.md", 1000.0, 20)
        
        await index.close()
    
    @pytest.mark.asyncio
    async def test_search_functionality(self, test_vault_dir):
        """Test searching indexed content."""
        index = PersistentSearchIndex(Path(test_vault_dir))
        await index.initialize()
        
        # Index some test files
        await index.index_file("note1.md", "This is about Python programming", 1000.0, 100)
        await index.index_file("note2.md", "This is about JavaScript", 1000.0, 100)
        await index.index_file("note3.md", "Python is great for data science", 1000.0, 100)
        
        # Search for Python
        result_data = await index.search_simple("python", 10)
        assert "results" in result_data
        assert "total_count" in result_data
        assert "truncated" in result_data
        assert "limit" in result_data
        
        results = result_data["results"]
        assert len(results) == 2
        assert result_data["total_count"] == 2
        assert result_data["truncated"] == False
        assert result_data["limit"] == 10
        assert any(r["filepath"] == "note1.md" for r in results)
        assert any(r["filepath"] == "note3.md" for r in results)
        
        await index.close()
    
    
    
    @pytest.mark.asyncio
    async def test_search_simple_truncation(self, test_vault_dir):
        """Test that search_simple correctly reports truncation."""
        index = PersistentSearchIndex(Path(test_vault_dir))
        await index.initialize()
        
        # Index many files with same content
        for i in range(100):
            await index.index_file(
                f"note_{i:03d}.md", 
                "This is a test note with common content", 
                1000.0 + i, 
                100
            )
        
        # Search with small limit
        result_data = await index.search_simple("common", 10)
        assert result_data["total_count"] == 100
        assert len(result_data["results"]) == 10
        assert result_data["truncated"] == True
        assert result_data["limit"] == 10
        
        # Search with large limit
        result_data = await index.search_simple("common", 200)
        assert result_data["total_count"] == 100
        assert len(result_data["results"]) == 100
        assert result_data["truncated"] == False
        assert result_data["limit"] == 200
        
        await index.close()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])