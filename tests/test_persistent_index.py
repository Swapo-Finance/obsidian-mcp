#!/usr/bin/env python3
"""Test persistent search index functionality."""

import shutil

# Add parent directory to path
import sys
import tempfile
from pathlib import Path

import pytest
import pytest_asyncio

sys.path.insert(0, str(Path(__file__).parent.parent))

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
            {"tags": ["test"]},
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
        await index.index_file(
            "note1.md", "This is about Python programming", 1000.0, 100
        )
        await index.index_file("note2.md", "This is about JavaScript", 1000.0, 100)
        await index.index_file(
            "note3.md", "Python is great for data science", 1000.0, 100
        )

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
                100,
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

    @pytest.mark.asyncio
    async def test_idx_property_value_dropped_idx_mtime_kept(self, test_vault_dir):
        """Finding 4 (code review): idx_property_value is dead weight --
        EXPLAIN QUERY PLAN against every search_by_property predicate shape
        (LIKE '%x%', LOWER(x)=LOWER(?), CAST(x AS REAL) <op> CAST(?), plain
        exists/!=) confirmed the planner never picks it, since each one
        wraps property_value in a function or a leading wildcard. idx_mtime
        is kept: EXPLAIN QUERY PLAN showed the '!=' operator's LEFT JOIN
        path uses it (SCAN f USING INDEX idx_mtime) to satisfy
        ORDER BY f.mtime DESC without a separate temp-b-tree sort.
        """
        index = PersistentSearchIndex(Path(test_vault_dir))
        await index.initialize()
        db = index._require_db()

        cursor = await db.execute("PRAGMA index_list(file_properties)")
        property_indexes = {row[1] for row in await cursor.fetchall()}
        assert "idx_property_value" not in property_indexes
        assert "idx_property_name" in property_indexes

        cursor = await db.execute("PRAGMA index_list(file_index)")
        file_index_indexes = {row[1] for row in await cursor.fetchall()}
        assert "idx_mtime" in file_index_indexes
        assert "idx_size" in file_index_indexes

        await index.close()

    @pytest.mark.asyncio
    async def test_index_file_rolls_back_on_mid_sequence_error(
        self, test_vault_dir, monkeypatch
    ):
        """Finding 3 (code review): a failure partway through index_file()'s
        statement sequence must not leave a partial write sitting in an open
        transaction for a later, unrelated commit() to silently fold in."""
        index = PersistentSearchIndex(Path(test_vault_dir))
        await index.initialize()
        db = index._require_db()

        original_execute = db.execute
        calls = {"n": 0}

        async def flaky_execute(sql, *args, **kwargs):
            calls["n"] += 1
            # Let the file_index INSERT OR REPLACE (1st statement) succeed,
            # then fail on the very next one (the file_search DELETE).
            if calls["n"] == 2:
                raise RuntimeError("simulated failure")
            return await original_execute(sql, *args, **kwargs)

        monkeypatch.setattr(db, "execute", flaky_execute)
        with pytest.raises(RuntimeError):
            await index.index_file("note.md", "content", 1000.0, 10)
        monkeypatch.undo()

        # Without rollback, the file_index row from the failed call above
        # would still be pending in an open transaction and get silently
        # committed by this next, unrelated write.
        await index.index_file("other.md", "other content", 2000.0, 20)

        assert await index.get_file_info("note.md") is None, (
            "partial write from the failed call leaked through"
        )

        await index.close()

    @pytest.mark.asyncio
    async def test_remove_file_rolls_back_on_mid_sequence_error(
        self, test_vault_dir, monkeypatch
    ):
        """Finding 3 (code review): same rollback guarantee for remove_file()
        -- a failure partway through must not leave file_index missing a row
        while file_search/file_properties still have it (worse than doing
        nothing: a corrupted, inconsistent index)."""
        index = PersistentSearchIndex(Path(test_vault_dir))
        await index.initialize()
        await index.index_file(
            "note.md", "content", 1000.0, 10, {"frontmatter": {"status": "active"}}
        )
        db = index._require_db()

        original_execute = db.execute
        calls = {"n": 0}

        async def flaky_execute(sql, *args, **kwargs):
            calls["n"] += 1
            # Let the file_index DELETE (1st statement) succeed, then fail
            # on the very next one (the file_search DELETE).
            if calls["n"] == 2:
                raise RuntimeError("simulated failure")
            return await original_execute(sql, *args, **kwargs)

        monkeypatch.setattr(db, "execute", flaky_execute)
        with pytest.raises(RuntimeError):
            await index.remove_file("note.md")
        monkeypatch.undo()

        await index.index_file("other.md", "other content", 2000.0, 20)

        assert await index.get_file_info("note.md") is not None, (
            "partial delete leaked through despite the error"
        )

        await index.close()

    @pytest.mark.asyncio
    async def test_clear_orphaned_entries_rolls_back_on_mid_sequence_error(
        self, test_vault_dir, monkeypatch
    ):
        """Finding 3 (code review): clear_orphaned_entries() batches all
        orphans into one commit/rollback -- a failure partway through the
        batch must not leave some orphans deleted and others not."""
        index = PersistentSearchIndex(Path(test_vault_dir))
        await index.initialize()
        await index.index_file("keep.md", "keep", 1000.0, 10)
        await index.index_file("orphan_a.md", "a", 1000.0, 10)
        await index.index_file("orphan_b.md", "b", 1000.0, 10)
        db = index._require_db()

        original_execute = db.execute
        calls = {"n": 0}

        async def flaky_execute(sql, *args, **kwargs):
            calls["n"] += 1
            # Call 1 is the initial SELECT; calls 2-4 are the first orphan's
            # complete (file_index, file_search, file_properties) delete
            # trio -- fully executed but not yet committed. Failing on call
            # 5 (the second orphan's first delete) proves the FIRST orphan's
            # already-executed deletes get rolled back too, not just the
            # interrupted second one.
            if calls["n"] == 5:
                raise RuntimeError("simulated failure")
            return await original_execute(sql, *args, **kwargs)

        monkeypatch.setattr(db, "execute", flaky_execute)
        with pytest.raises(RuntimeError):
            await index.clear_orphaned_entries({"keep.md"})
        monkeypatch.undo()

        await index.index_file("other.md", "other content", 2000.0, 20)

        assert await index.get_file_info("orphan_a.md") is not None
        assert await index.get_file_info("orphan_b.md") is not None

        await index.close()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
