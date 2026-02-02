#!/usr/bin/env python3
"""Test regex search for JSON code blocks - simulating the user's actual use case."""

import asyncio
import tempfile
import time
from pathlib import Path
import pytest
import pytest_asyncio

# Add parent directory to path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from obsidian_mcp.utils.filesystem import ObsidianVault


@pytest.mark.asyncio
async def test_json_code_block_search():
    """Test searching for JSON code blocks in markdown files."""
    with tempfile.TemporaryDirectory() as temp_dir:
        vault_path = Path(temp_dir)
        
        # Create test files with various JSON code blocks
        # File 1: API documentation
        (vault_path / "api-docs.md").write_text("""# API Documentation

## User Endpoint

```json
{
  "endpoint": "/api/users",
  "method": "GET",
  "response": {
    "users": [
      {"id": 1, "name": "John"},
      {"id": 2, "name": "Jane"}
    ]
  }
}
```

## Product Endpoint

```json
{
  "endpoint": "/api/products",
  "method": "POST",
  "body": {
    "name": "string",
    "price": "number"
  }
}
```
""")
        
        # File 2: Configuration examples
        (vault_path / "config-examples.md").write_text("""# Configuration Examples

## Database Config

```json
{
  "database": {
    "host": "localhost",
    "port": 5432,
    "name": "myapp"
  }
}
```

## Logger Config

```yaml
logging:
  level: INFO
  file: app.log
```

## App Settings

```json
{
  "app": {
    "name": "My Application",
    "version": "1.0.0",
    "features": ["auth", "api", "admin"]
  }
}
```
""")
        
        # File 3: Large file with many JSON blocks
        large_content = "# Large Document with Many Examples\n\n"
        for i in range(100):
            large_content += f"""
## Section {i}

Some text describing section {i}.

```json
{{
  "section": {i},
  "data": {{
    "value": {i * 10},
    "status": "{'active' if i % 2 == 0 else 'inactive'}"
  }}
}}
```

More text after the JSON block.
"""
        (vault_path / "large-doc.md").write_text(large_content)
        
        # Initialize vault
        vault = ObsidianVault(vault_path)
        
        # Build index
        print("Building index...")
        await vault._update_search_index()
        
        # Test 1: Simple JSON block pattern (what the user was trying)
        print("\nTest 1: Simple JSON block search")
        pattern = r'```json\s*\{[\s\S]*?\}\s*```'
        
        start_time = time.time()
        results = await vault.search_by_regex(pattern, max_results=50)
        search_time = time.time() - start_time
        
        print(f"Found {len(results)} files with JSON blocks in {search_time:.3f} seconds")
        total_matches = sum(r['match_count'] for r in results)
        print(f"Total JSON blocks found: {total_matches}")
        
        # Debug: Print actual results
        for r in results:
            print(f"  - {r['path']}: {r['match_count']} matches")
        
        # Verify results
        assert len(results) == 3  # All three files have JSON blocks
        # Note: We limit matches per file to 5 by default, so we won't see all 100 matches
        assert any(r['path'] == 'large-doc.md' for r in results)
        
        # Test 2: More specific pattern - find JSON with "endpoint" key
        print("\nTest 2: JSON blocks with 'endpoint' key")
        pattern = r'```json\s*\{[^}]*"endpoint"[^}]*\}'
        
        start_time = time.time()
        results = await vault.search_by_regex(pattern, max_results=50)
        search_time = time.time() - start_time
        
        print(f"Found {len(results)} files with endpoint JSON in {search_time:.3f} seconds")
        
        assert len(results) == 1  # Only api-docs.md has endpoint JSONs
        assert results[0]['path'] == 'api-docs.md'
        assert results[0]['match_count'] == 2
        
        # Test 3: Pattern with capture groups - extract section numbers
        print("\nTest 3: Extract section numbers from JSON")
        pattern = r'"section":\s*(\d+)'
        
        start_time = time.time()
        results = await vault.search_by_regex(pattern, max_results=20)
        search_time = time.time() - start_time
        
        print(f"Found section numbers in {search_time:.3f} seconds")
        
        # Check that we got groups
        large_doc_result = next(r for r in results if r['path'] == 'large-doc.md')
        assert large_doc_result['matches'][0]['groups'] is not None
        print(f"First few section numbers: {[m['groups'][0] for m in large_doc_result['matches'][:5]]}")
        
        # Test 4: Performance with complex pattern
        print("\nTest 4: Complex pattern performance")
        # Pattern to find JSON blocks with nested objects
        pattern = r'```json\s*\{[^}]*\{[^}]*\}[^}]*\}'
        
        start_time = time.time()
        results = await vault.search_by_regex(pattern, max_results=100)
        search_time = time.time() - start_time
        
        print(f"Complex pattern search completed in {search_time:.3f} seconds")
        print(f"Found {len(results)} files with nested JSON objects")
        
        # Should complete quickly even with complex pattern
        assert search_time < 2.0  # Should be much faster with optimizations
        
        # No need to close vault anymore
        
        print("\n✅ All JSON search tests passed!")


if __name__ == "__main__":
    asyncio.run(test_json_code_block_search())