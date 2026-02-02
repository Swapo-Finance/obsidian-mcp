"""Simple test to verify property search functionality works."""

import asyncio
import tempfile
from pathlib import Path
import pytest
from obsidian_mcp.utils.filesystem import ObsidianVault
from obsidian_mcp.tools.search_discovery import _search_by_property, _parse_property_query


@pytest.mark.asyncio
async def test_property_search():
    """Test basic property search functionality."""
    with tempfile.TemporaryDirectory() as temp_dir:
        vault_path = Path(temp_dir)
        
        # Create a test note with properties
        test_note = vault_path / "test.md"
        test_note.write_text("""---
title: Test Note
status: active
priority: 3
---

# Test Note

This is a test note with properties.
""")
        
        # Initialize vault
        vault = ObsidianVault(vault_path)
        
        # Test property search
        results = await _search_by_property(vault, "property:status:active", 100)
        print(f"Found {len(results)} results for status:active")
        assert len(results) == 1
        assert results[0]['path'] == "test.md"
        assert results[0]['property_value'] == "active"
        
        # Test numeric comparison
        results = await _search_by_property(vault, "property:priority:>2", 100)
        print(f"Found {len(results)} results for priority>2")
        assert len(results) == 1
        
        # Test property exists
        results = await _search_by_property(vault, "property:title:*", 100)
        print(f"Found {len(results)} results for title exists")
        assert len(results) == 1
        
        # Cleanup
        if hasattr(vault, 'persistent_index') and vault.persistent_index:
            await vault.persistent_index.close()
        
        print("All tests passed!")


def test_parse_property_query():
    """Test property query parsing."""
    # Test equals
    result = _parse_property_query("property:status:active")
    assert result == {'name': 'status', 'operator': '=', 'value': 'active'}
    print("✓ Parse equals test passed")
    
    # Test greater than
    result = _parse_property_query("property:priority:>3")
    assert result == {'name': 'priority', 'operator': '>', 'value': '3'}
    print("✓ Parse greater than test passed")
    
    # Test contains
    result = _parse_property_query("property:title:*test*")
    assert result == {'name': 'title', 'operator': 'contains', 'value': 'test'}
    print("✓ Parse contains test passed")
    
    # Test exists
    result = _parse_property_query("property:tags:*")
    assert result == {'name': 'tags', 'operator': 'exists', 'value': None}
    print("✓ Parse exists test passed")


if __name__ == "__main__":
    print("Testing property query parser...")
    test_parse_property_query()
    print("\nTesting property search...")
    asyncio.run(test_property_search())