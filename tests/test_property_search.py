"""Tests for property-based search functionality."""

import pytest
import pytest_asyncio
import tempfile
import shutil
from pathlib import Path
from datetime import datetime
from obsidian_mcp.utils.filesystem import ObsidianVault
from obsidian_mcp.tools.search_discovery import (
    search_notes,
    search_by_property,
    _parse_property_query,
    _search_by_property
)


@pytest_asyncio.fixture
async def test_vault():
    """Create a temporary vault with test notes containing properties."""
    with tempfile.TemporaryDirectory() as temp_dir:
        vault_path = Path(temp_dir)
        
        # Create test notes with various properties
        notes = [
            {
                "path": "projects/website.md",
                "content": """---
title: Website Redesign
status: active
priority: 3
assignee: John Doe
deadline: 2024-12-31
tags: [web, design]
budget: 50000
---

# Website Redesign Project

This is the main project for redesigning our company website.
"""
            },
            {
                "path": "projects/mobile-app.md",
                "content": """---
title: Mobile App Development
status: active
priority: 5
assignee: Jane Smith
deadline: 2024-06-30
tags: [mobile, development]
budget: 75000
---

# Mobile App Development

Creating a new mobile application for iOS and Android.
"""
            },
            {
                "path": "projects/documentation.md",
                "content": """---
title: Documentation Update
status: completed
priority: 2
assignee: Bob Wilson
completed_date: 2024-01-15
tags: [docs]
budget: 10000
---

# Documentation Update

Updated all technical documentation.
"""
            },
            {
                "path": "ideas/ai-integration.md",
                "content": """---
title: AI Integration Ideas
status: planning
priority: 4
tags: [ai, future]
---

# AI Integration Ideas

Exploring ways to integrate AI into our products.
"""
            },
            {
                "path": "notes/meeting-notes.md",
                "content": """---
title: Q1 Planning Meeting
date: 2024-01-10
attendees: ["John Doe", "Jane Smith", "Bob Wilson"]
---

# Q1 Planning Meeting

Notes from the quarterly planning session.
"""
            }
        ]
        
        # Create notes
        for note in notes:
            note_path = vault_path / note["path"]
            note_path.parent.mkdir(parents=True, exist_ok=True)
            note_path.write_text(note["content"])
        
        # Create vault
        vault = ObsidianVault(vault_path)
        
        yield vault
        
        # Cleanup
        if hasattr(vault, 'persistent_index') and vault.persistent_index:
            await vault.persistent_index.close()


class TestPropertyQueryParser:
    """Test the property query parser."""
    
    def test_parse_equals(self):
        result = _parse_property_query("property:status:active")
        assert result == {'name': 'status', 'operator': '=', 'value': 'active'}
    
    def test_parse_greater_than(self):
        result = _parse_property_query("property:priority:>3")
        assert result == {'name': 'priority', 'operator': '>', 'value': '3'}
    
    def test_parse_less_than_equal(self):
        result = _parse_property_query("property:budget:<=50000")
        assert result == {'name': 'budget', 'operator': '<=', 'value': '50000'}
    
    def test_parse_not_equal(self):
        result = _parse_property_query("property:status:!=completed")
        assert result == {'name': 'status', 'operator': '!=', 'value': 'completed'}
    
    def test_parse_contains(self):
        result = _parse_property_query("property:assignee:*john*")
        assert result == {'name': 'assignee', 'operator': 'contains', 'value': 'john'}
    
    def test_parse_exists(self):
        result = _parse_property_query("property:deadline:*")
        assert result == {'name': 'deadline', 'operator': 'exists', 'value': None}
    
    def test_parse_invalid(self):
        with pytest.raises(ValueError):
            _parse_property_query("property:invalid")


@pytest.mark.asyncio
class TestPropertySearch:
    """Test property search functionality."""
    
    async def test_search_by_exact_match(self, test_vault):
        # Search for active status
        results = await _search_by_property(test_vault, "property:status:active", 100)
        assert len(results) == 2
        paths = [r['path'] for r in results]
        assert "projects/website.md" in paths
        assert "projects/mobile-app.md" in paths
    
    async def test_search_by_greater_than(self, test_vault):
        # Search for priority > 3
        results = await _search_by_property(test_vault, "property:priority:>3", 100)
        assert len(results) == 2
        for result in results:
            assert result['property_value'] in [4, 5, '4', '5']
    
    async def test_search_by_less_than(self, test_vault):
        # Search for priority < 3
        results = await _search_by_property(test_vault, "property:priority:<3", 100)
        assert len(results) == 1
        assert results[0]['property_value'] in [2, '2']
    
    async def test_search_by_contains(self, test_vault):
        # Search for assignee containing "john"
        results = await _search_by_property(test_vault, "property:assignee:*john*", 100)
        assert len(results) == 1
        assert "John Doe" in results[0]['property_value']
    
    async def test_search_by_exists(self, test_vault):
        # Search for notes with deadline property
        results = await _search_by_property(test_vault, "property:deadline:*", 100)
        assert len(results) == 2
        paths = [r['path'] for r in results]
        assert "projects/website.md" in paths
        assert "projects/mobile-app.md" in paths
    
    async def test_search_by_not_equal(self, test_vault):
        # Search for status != completed
        results = await _search_by_property(test_vault, "property:status:!=completed", 100)
        assert len(results) >= 2  # Should find active and planning statuses
        for result in results:
            assert result['property_value'] != 'completed'
    
    async def test_search_notes_with_property_syntax(self, test_vault):
        # Test using search_notes with property syntax
        from unittest.mock import patch
        with patch('obsidian_mcp.tools.search_discovery.get_vault', return_value=test_vault):
            result = await search_notes("property:status:active", 100)
            assert result['count'] == 2
            assert len(result['results']) == 2
    
    async def test_numeric_comparison(self, test_vault):
        # Test numeric comparison for budget
        results = await _search_by_property(test_vault, "property:budget:>=50000", 100)
        assert len(results) == 2  # Website (50000) and Mobile App (75000)
        
        results = await _search_by_property(test_vault, "property:budget:<50000", 100)
        assert len(results) == 1  # Documentation (10000)


@pytest.mark.asyncio
class TestSearchByPropertyTool:
    """Test the search_by_property tool function."""
    
    async def test_equals_operator(self, test_vault):
        # We need to mock the context, as the search_by_property function uses get_vault()
        from unittest.mock import patch
        with patch('obsidian_mcp.tools.search_discovery.get_vault', return_value=test_vault):
            result = await search_by_property("status", "active", "=", 100)
            assert result['count'] == 2
            assert result['query']['property'] == "status"
            assert result['query']['operator'] == "="
            assert result['query']['value'] == "active"
    
    async def test_greater_than_operator(self, test_vault):
        from unittest.mock import patch
        with patch('obsidian_mcp.tools.search_discovery.get_vault', return_value=test_vault):
            result = await search_by_property("priority", "3", ">", 100)
            assert result['count'] == 2
            assert all(int(r['property_value']) > 3 for r in result['results'])
    
    async def test_contains_operator(self, test_vault):
        from unittest.mock import patch
        with patch('obsidian_mcp.tools.search_discovery.get_vault', return_value=test_vault):
            result = await search_by_property("title", "Redesign", "contains", 100)
            assert result['count'] >= 1  # Should find "Website Redesign"
    
    async def test_exists_operator(self, test_vault):
        from unittest.mock import patch
        with patch('obsidian_mcp.tools.search_discovery.get_vault', return_value=test_vault):
            result = await search_by_property("deadline", operator="exists")
            assert result['count'] == 2
            assert result['query']['value'] is None
    
    async def test_invalid_operator(self, test_vault):
        with pytest.raises(ValueError):
            await search_by_property("status", "active", "invalid_op")
    
    async def test_sorting_numeric_results(self, test_vault):
        from unittest.mock import patch
        with patch('obsidian_mcp.tools.search_discovery.get_vault', return_value=test_vault):
            result = await search_by_property("priority", "0", ">", 100)
            # Results should be sorted by priority value
            priorities = [int(r['property_value']) for r in result['results']]
            assert priorities == sorted(priorities, reverse=True)


@pytest.mark.asyncio
class TestPropertyTypes:
    """Test handling of different property types."""
    
    async def test_list_property(self, test_vault):
        # Search for notes with tags property (which is a list)
        results = await _search_by_property(test_vault, "property:tags:*", 100)
        assert len(results) >= 4  # Most test notes have tags
    
    async def test_date_property(self, test_vault):
        # Search for deadline after a specific date
        results = await _search_by_property(test_vault, "property:deadline:>2024-06-01", 100)
        assert len(results) >= 1
    
    async def test_case_insensitive_search(self, test_vault):
        # Search should be case-insensitive for text values
        results = await _search_by_property(test_vault, "property:status:ACTIVE", 100)
        assert len(results) == 2


@pytest.mark.asyncio
class TestEdgeCases:
    """Test edge cases and error handling."""
    
    async def test_nonexistent_property(self, test_vault):
        results = await _search_by_property(test_vault, "property:nonexistent:value", 100)
        assert len(results) == 0
    
    async def test_empty_vault(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            vault = ObsidianVault(Path(temp_dir))
            
            results = await _search_by_property(vault, "property:any:value", 100)
            assert len(results) == 0
    
    async def test_notes_without_frontmatter(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            vault_path = Path(temp_dir)
            
            # Create a note without frontmatter
            note_path = vault_path / "plain.md"
            note_path.write_text("# Plain Note\n\nNo frontmatter here.")
            
            vault = ObsidianVault(vault_path)
            
            results = await _search_by_property(vault, "property:any:*", 100)
            assert len(results) == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])