#!/usr/bin/env python3
"""VaultCache freshness mechanics (spec section 4b) — the gaps the sanity
pass explicitly didn't cover: proving MCP mutations update the cache
WITHOUT a full re-scan (only "reflects immediately" was asserted before,
not "without re-scanning"), that changes made outside the MCP server (a
direct disk write, simulating the Obsidian app or a git checkout) are only
picked up via stat-diff once OBSIDIAN_CACHE_STAT_TTL_SECONDS has elapsed,
and that TTL=0 re-stats on every single access. name/description caching
is already covered by test_onda2_sanity.py's TestVaultCacheNameDescription
and is not repeated here.
"""

import os
import shutil
import tempfile
from pathlib import Path

import pytest
import pytest_asyncio

from obsidian_mcp.tools.link_management import build_vault_notes_index, get_backlinks
from obsidian_mcp.tools.note_management import create_note
from obsidian_mcp.tools.organization import list_tags
from obsidian_mcp.utils.filesystem import init_vault
from obsidian_mcp.utils.vault_cache import VaultCache


@pytest_asyncio.fixture
async def vault():
    temp_dir = tempfile.mkdtemp(prefix="obsidian_cache_freshness_")
    os.environ["OBSIDIAN_REQUIRE_FRONTMATTER"] = "false"
    v = init_vault(temp_dir)
    yield v
    os.environ.pop("OBSIDIAN_REQUIRE_FRONTMATTER", None)
    os.environ.pop("OBSIDIAN_CACHE_STAT_TTL_SECONDS", None)
    shutil.rmtree(temp_dir)


def _count_scans(monkeypatch):
    """Wrap VaultCache._full_scan_locked/_stat_diff_locked with call
    counters while preserving real behavior, so assertions can check HOW
    the cache became fresh, not just that it did."""
    counts = {"full_scan": 0, "stat_diff": 0}
    original_full_scan = VaultCache._full_scan_locked
    original_stat_diff = VaultCache._stat_diff_locked

    async def counting_full_scan(self):
        counts["full_scan"] += 1
        return await original_full_scan(self)

    async def counting_stat_diff(self):
        counts["stat_diff"] += 1
        return await original_stat_diff(self)

    monkeypatch.setattr(VaultCache, "_full_scan_locked", counting_full_scan)
    monkeypatch.setattr(VaultCache, "_stat_diff_locked", counting_stat_diff)
    return counts


class TestMcpMutationSkipsRescan:
    @pytest.mark.asyncio
    async def test_create_note_updates_index_without_full_scan_or_stat_diff(self, vault, monkeypatch):
        counts = _count_scans(monkeypatch)

        # First access lazily builds the cache — exactly one full scan.
        index_before = await build_vault_notes_index(vault)
        assert counts["full_scan"] == 1
        assert "B.md" not in index_before

        # MCP mutation goes through note_mutated(), which never calls
        # _ensure_fresh (and therefore never _full_scan_locked or
        # _stat_diff_locked) — it indexes only the touched note directly.
        await create_note("B.md", "# B\n")
        assert counts["full_scan"] == 1
        assert counts["stat_diff"] == 0

        # A subsequent read (still within the default 30s TTL) must see the
        # new note without triggering a stat-diff either.
        index_after = await build_vault_notes_index(vault)
        assert index_after.get("B.md") == "B.md"
        assert counts["full_scan"] == 1
        assert counts["stat_diff"] == 0


class TestExternalChangeViaStatDiffAfterTtl:
    @pytest.mark.asyncio
    async def test_external_file_invisible_within_ttl_then_picked_up_after(self, vault, monkeypatch):
        counts = _count_scans(monkeypatch)

        (vault.vault_path / "A.md").write_text("# A\n")
        index = await build_vault_notes_index(vault)  # lazy full scan
        assert "A.md" in index
        assert counts["full_scan"] == 1

        # A file created OUTSIDE the MCP server (no note_mutated hook).
        (vault.vault_path / "External.md").write_text("# External\n")

        # Still within the (default, ~30s) TTL window: no re-stat yet, so
        # the externally-created file is not visible.
        index_within_ttl = await build_vault_notes_index(vault)
        assert "External.md" not in index_within_ttl
        assert counts["stat_diff"] == 0

        # Simulate the TTL having elapsed (white-box: push the snapshot
        # timestamp back rather than sleeping in the test suite).
        vault.cache._snapshot_time -= vault.cache_stat_ttl_seconds + 1

        index_after_ttl = await build_vault_notes_index(vault)
        assert "External.md" in index_after_ttl
        assert counts["stat_diff"] == 1

    @pytest.mark.asyncio
    async def test_externally_deleted_file_removed_after_stat_diff(self, vault):
        (vault.vault_path / "ToDelete.md").write_text("# Bye\n")
        index = await build_vault_notes_index(vault)
        assert "ToDelete.md" in index

        (vault.vault_path / "ToDelete.md").unlink()
        vault.cache._snapshot_time -= vault.cache_stat_ttl_seconds + 1

        index_after = await build_vault_notes_index(vault)
        assert "ToDelete.md" not in index_after


class TestCacheStatTtlZeroAlwaysRestats:
    @pytest_asyncio.fixture
    async def zero_ttl_vault(self):
        temp_dir = tempfile.mkdtemp(prefix="obsidian_cache_ttl0_")
        os.environ["OBSIDIAN_REQUIRE_FRONTMATTER"] = "false"
        os.environ["OBSIDIAN_CACHE_STAT_TTL_SECONDS"] = "0"
        v = init_vault(temp_dir)
        yield v
        os.environ.pop("OBSIDIAN_REQUIRE_FRONTMATTER", None)
        os.environ.pop("OBSIDIAN_CACHE_STAT_TTL_SECONDS", None)
        shutil.rmtree(temp_dir)

    @pytest.mark.asyncio
    async def test_every_access_triggers_stat_diff(self, zero_ttl_vault, monkeypatch):
        assert zero_ttl_vault.cache_stat_ttl_seconds == 0
        counts = _count_scans(monkeypatch)

        await build_vault_notes_index(zero_ttl_vault)  # lazy build: full scan
        assert counts["full_scan"] == 1
        assert counts["stat_diff"] == 0

        await build_vault_notes_index(zero_ttl_vault)
        await build_vault_notes_index(zero_ttl_vault)
        assert counts["stat_diff"] == 2  # one per access after the initial build

    @pytest.mark.asyncio
    async def test_external_change_visible_immediately_with_ttl_zero(self, zero_ttl_vault):
        await build_vault_notes_index(zero_ttl_vault)  # build cache first
        (zero_ttl_vault.vault_path / "Fresh.md").write_text("# Fresh\n")

        index = await build_vault_notes_index(zero_ttl_vault)
        assert "Fresh.md" in index


class TestConsumersServedFromCacheNotLiveRescan:
    """get_backlinks and list_tags read the cache, not a fresh vault scan —
    proven by showing they miss an out-of-band disk change until stat-diff
    catches up, the same TTL mechanics as above."""

    @pytest.mark.asyncio
    async def test_get_backlinks_reflects_external_addition_only_after_ttl(self, vault):
        await create_note("Target.md", "# Target\n")
        await create_note("Source.md", "Link to [[Target]].")

        result = await get_backlinks("Target.md")
        assert result["summary"]["backlink_count"] == 1

        # A second linking note added outside the MCP server.
        (vault.vault_path / "Source2.md").write_text("Also links to [[Target]].")

        still_stale = await get_backlinks("Target.md")
        assert still_stale["summary"]["backlink_count"] == 1

        vault.cache._snapshot_time -= vault.cache_stat_ttl_seconds + 1
        fresh = await get_backlinks("Target.md")
        assert fresh["summary"]["backlink_count"] == 2

    @pytest.mark.asyncio
    async def test_list_tags_reflects_external_addition_only_after_ttl(self, vault):
        await create_note("A.md", "---\ntags: [alpha]\n---\n# A\n")
        before = await list_tags(include_counts=True)
        assert {t["name"] for t in before["items"]} == {"alpha"}

        (vault.vault_path / "External.md").write_text("---\ntags: [beta]\n---\n# External\n")

        still_stale = await list_tags(include_counts=True)
        assert {t["name"] for t in still_stale["items"]} == {"alpha"}

        vault.cache._snapshot_time -= vault.cache_stat_ttl_seconds + 1
        fresh = await list_tags(include_counts=True)
        assert {t["name"] for t in fresh["items"]} == {"alpha", "beta"}


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
