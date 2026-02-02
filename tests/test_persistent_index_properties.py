"""Tests for persistent index property storage and search."""

import pytest
import pytest_asyncio
import tempfile
import asyncio
from pathlib import Path
from obsidian_mcp.utils.persistent_index import PersistentSearchIndex


@pytest_asyncio.fixture
async def test_index():
    """Create a test persistent index."""
    with tempfile.TemporaryDirectory() as temp_dir:
        index_path = Path(temp_dir) / "test-index.db"
        index = PersistentSearchIndex(Path(temp_dir), index_path)
        await index.initialize()
        yield index
        await index.close()


@pytest.mark.asyncio
class TestPropertyIndexing:
    """Test property indexing functionality."""
    
    async def test_index_file_with_properties(self, test_index):
        # Index a file with properties
        content = """---
title: Test Note
status: active
priority: 3
tags: [test, example]
---

# Test Note

This is test content.
"""
        metadata = {
            'frontmatter': {
                'title': 'Test Note',
                'status': 'active',
                'priority': 3,
                'tags': ['test', 'example']
            }
        }
        
        await test_index.index_file("test.md", content, 1234567890, 100, metadata)
        
        # Verify properties were indexed
        results = await test_index.search_by_property("status", "=", "active")
        assert len(results) == 1
        assert results[0]['filepath'] == "test.md"
        assert results[0]['property_value'] == "active"
    
    async def test_property_type_detection(self, test_index):
        # Test different property types
        assert test_index._determine_property_type("text") == "text"
        assert test_index._determine_property_type(123) == "number"
        assert test_index._determine_property_type(123.45) == "number"
        assert test_index._determine_property_type(True) == "boolean"
        assert test_index._determine_property_type(["a", "b"]) == "list"
        assert test_index._determine_property_type({"key": "value"}) == "object"
        assert test_index._determine_property_type("2024-01-15") == "date"
    
    async def test_update_properties(self, test_index):
        # Index a file
        metadata1 = {
            'frontmatter': {
                'status': 'draft',
                'priority': 1
            }
        }
        await test_index.index_file("note.md", "Content 1", 1000, 50, metadata1)
        
        # Update with new properties
        metadata2 = {
            'frontmatter': {
                'status': 'published',
                'priority': 3,
                'author': 'John Doe'
            }
        }
        await test_index.index_file("note.md", "Content 2", 2000, 60, metadata2)
        
        # Verify old properties are replaced
        results = await test_index.search_by_property("status", "=", "draft")
        assert len(results) == 0
        
        results = await test_index.search_by_property("status", "=", "published")
        assert len(results) == 1
        
        # Verify new property was added
        results = await test_index.search_by_property("author", "exists")
        assert len(results) == 1


@pytest.mark.asyncio
class TestPropertySearch:
    """Test property search functionality."""
    
    async def test_search_equals(self, test_index):
        # Index test data
        await test_index.index_file("note1.md", "Content 1", 1000, 50, {
            'frontmatter': {'status': 'active', 'priority': 3}
        })
        await test_index.index_file("note2.md", "Content 2", 2000, 60, {
            'frontmatter': {'status': 'inactive', 'priority': 1}
        })
        
        results = await test_index.search_by_property("status", "=", "active")
        assert len(results) == 1
        assert results[0]['filepath'] == "note1.md"
    
    async def test_search_not_equals(self, test_index):
        # Index test data
        await test_index.index_file("note1.md", "Content 1", 1000, 50, {
            'frontmatter': {'status': 'active'}
        })
        await test_index.index_file("note2.md", "Content 2", 2000, 60, {
            'frontmatter': {'status': 'inactive'}
        })
        await test_index.index_file("note3.md", "Content 3", 3000, 70, {
            'frontmatter': {'title': 'No status'}
        })
        
        results = await test_index.search_by_property("status", "!=", "active")
        assert len(results) >= 1  # Should find inactive and possibly note3
    
    async def test_search_numeric_comparison(self, test_index):
        # Index test data with numeric properties
        await test_index.index_file("low.md", "Low priority", 1000, 50, {
            'frontmatter': {'priority': 1}
        })
        await test_index.index_file("medium.md", "Medium priority", 2000, 60, {
            'frontmatter': {'priority': 3}
        })
        await test_index.index_file("high.md", "High priority", 3000, 70, {
            'frontmatter': {'priority': 5}
        })
        
        # Test greater than
        results = await test_index.search_by_property("priority", ">", "3")
        assert len(results) == 1
        assert results[0]['filepath'] == "high.md"
        
        # Test less than or equal
        results = await test_index.search_by_property("priority", "<=", "3")
        assert len(results) == 2
        paths = [r['filepath'] for r in results]
        assert "low.md" in paths
        assert "medium.md" in paths
    
    async def test_search_contains(self, test_index):
        # Index test data
        await test_index.index_file("john.md", "John's note", 1000, 50, {
            'frontmatter': {'author': 'John Doe'}
        })
        await test_index.index_file("jane.md", "Jane's note", 2000, 60, {
            'frontmatter': {'author': 'Jane Smith'}
        })
        
        results = await test_index.search_by_property("author", "contains", "John")
        assert len(results) == 1
        assert results[0]['filepath'] == "john.md"
    
    async def test_search_exists(self, test_index):
        # Index test data
        await test_index.index_file("with_deadline.md", "Has deadline", 1000, 50, {
            'frontmatter': {'deadline': '2024-12-31', 'status': 'active'}
        })
        await test_index.index_file("no_deadline.md", "No deadline", 2000, 60, {
            'frontmatter': {'status': 'active'}
        })
        
        results = await test_index.search_by_property("deadline", "exists")
        assert len(results) == 1
        assert results[0]['filepath'] == "with_deadline.md"


@pytest.mark.asyncio
class TestPropertyMetadata:
    """Test property metadata functionality."""
    
    async def test_get_all_property_names(self, test_index):
        # Index files with various properties
        await test_index.index_file("note1.md", "Content 1", 1000, 50, {
            'frontmatter': {'status': 'active', 'priority': 3}
        })
        await test_index.index_file("note2.md", "Content 2", 2000, 60, {
            'frontmatter': {'status': 'inactive', 'author': 'John'}
        })
        
        property_names = await test_index.get_all_property_names()
        assert set(property_names) == {'author', 'priority', 'status'}
    
    async def test_get_property_values(self, test_index):
        # Index files with same property different values
        await test_index.index_file("note1.md", "Content 1", 1000, 50, {
            'frontmatter': {'status': 'active'}
        })
        await test_index.index_file("note2.md", "Content 2", 2000, 60, {
            'frontmatter': {'status': 'active'}
        })
        await test_index.index_file("note3.md", "Content 3", 3000, 70, {
            'frontmatter': {'status': 'inactive'}
        })
        
        values = await test_index.get_property_values("status")
        assert len(values) == 2
        # Should be sorted by count desc
        assert values[0] == ('active', 2)
        assert values[1] == ('inactive', 1)


@pytest.mark.asyncio
class TestComplexProperties:
    """Test handling of complex property types."""
    
    async def test_list_properties(self, test_index):
        # Index file with list property
        await test_index.index_file("note.md", "Content", 1000, 50, {
            'frontmatter': {
                'tags': ['python', 'testing', 'async'],
                'authors': ['John Doe', 'Jane Smith']
            }
        })
        
        # Lists are stored as JSON strings
        results = await test_index.search_by_property("tags", "contains", "python")
        assert len(results) == 1
    
    async def test_object_properties(self, test_index):
        # Index file with object property
        await test_index.index_file("note.md", "Content", 1000, 50, {
            'frontmatter': {
                'metadata': {
                    'version': '1.0',
                    'format': 'markdown'
                }
            }
        })
        
        # Objects are stored as JSON strings
        results = await test_index.search_by_property("metadata", "exists")
        assert len(results) == 1
    
    async def test_boolean_properties(self, test_index):
        # Index files with boolean properties
        await test_index.index_file("published.md", "Published", 1000, 50, {
            'frontmatter': {'published': True, 'draft': False}
        })
        await test_index.index_file("draft.md", "Draft", 2000, 60, {
            'frontmatter': {'published': False, 'draft': True}
        })
        
        # Booleans are stored as strings
        results = await test_index.search_by_property("published", "=", "True")
        assert len(results) == 1
        assert results[0]['filepath'] == "published.md"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])