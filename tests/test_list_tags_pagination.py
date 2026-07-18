#!/usr/bin/env python3
"""Pagination coverage for list_tags (obsidian_mcp/tools/organization.py) —
the offset/limit slicing added on top of the existing sort_by/include_counts/
include_files behavior (already covered elsewhere, e.g.
test_onda1_sanity.py's TestVaultCacheIncrementalUpdate).

Calls the list_tags impl function directly rather than the list_tags_tool
MCP wrapper: the wrapper just forwards offset/limit straight through (see
server.py), so testing the impl is sufficient and avoids server.py's
import-time OBSIDIAN_VAULT_PATH bootstrap dance that test_server_tool_wrappers.py
needs for wrapper-level (ValueError->ToolError) behavior.

Fixture pattern (tempfile.mkdtemp + init_vault + shutil.rmtree teardown) is
the same one every other test file in this suite uses.
"""

import os
import shutil
import tempfile

import pytest
import pytest_asyncio

from obsidian_mcp.tools.note_management import create_note
from obsidian_mcp.tools.organization import list_tags
from obsidian_mcp.utils.filesystem import init_vault

# 14 distinct tags: comfortably over the ">=12" needed to exceed a small
# limit, and 14 % 5 = 4 gives a clean partial-last-page case at limit=5
# (pages of 5, 5, 4) without needing a second fixture just for that.
TAG_COUNT = 14
TAG_NAMES = [f"tag-{i:02d}" for i in range(TAG_COUNT)]  # zero-padded -> already name-sorted


@pytest_asyncio.fixture
async def many_tags_vault():
    """One tag per note, 14 notes -> 14 distinct tags, each with count=1."""
    temp_dir = tempfile.mkdtemp(prefix="obsidian_list_tags_page_")
    os.environ["OBSIDIAN_REQUIRE_FRONTMATTER"] = "false"
    vault = init_vault(temp_dir)

    for i, tag in enumerate(TAG_NAMES):
        await create_note(f"Note{i:02d}.md", f"---\ntags: [{tag}]\n---\n# Note {i}\n")

    yield vault

    os.environ.pop("OBSIDIAN_REQUIRE_FRONTMATTER", None)
    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest_asyncio.fixture
async def count_ordered_vault():
    """3 tags whose alphabetical order is the reverse of their count order:
    alpha=1 file, bravo=2 files, charlie=3 files.

    name-asc:   alpha, bravo, charlie
    count-desc: charlie(3), bravo(2), alpha(1)  <- reverse of name order

    Isolated from many_tags_vault so proving sort_by="count" slices don't
    need 100+ files just to force a distinguishable count spread.
    """
    temp_dir = tempfile.mkdtemp(prefix="obsidian_list_tags_count_")
    os.environ["OBSIDIAN_REQUIRE_FRONTMATTER"] = "false"
    vault = init_vault(temp_dir)

    await create_note("A0.md", "---\ntags: [alpha]\n---\n# A0\n")
    await create_note("B0.md", "---\ntags: [bravo]\n---\n# B0\n")
    await create_note("B1.md", "---\ntags: [bravo]\n---\n# B1\n")
    await create_note("C0.md", "---\ntags: [charlie]\n---\n# C0\n")
    await create_note("C1.md", "---\ntags: [charlie]\n---\n# C1\n")
    await create_note("C2.md", "---\ntags: [charlie]\n---\n# C2\n")

    yield vault

    os.environ.pop("OBSIDIAN_REQUIRE_FRONTMATTER", None)
    shutil.rmtree(temp_dir, ignore_errors=True)


class TestListTagsPaginationDefaultPage:
    @pytest.mark.asyncio
    async def test_small_limit_returns_exactly_limit_items_with_total_unclipped(self, many_tags_vault):
        result = await list_tags(offset=0, limit=5)

        assert len(result["items"]) == 5
        assert result["returned"] == 5
        assert result["total"] == TAG_COUNT  # total is vault-wide, never just the page
        assert result["offset"] == 0
        assert result["limit"] == 5


class TestListTagsPaginationWalksFullSet:
    @pytest.mark.asyncio
    async def test_concatenated_pages_equal_full_list_no_overlap_no_gaps(self, many_tags_vault):
        full = await list_tags(sort_by="name", offset=0, limit=1000)
        all_names = [t["name"] for t in full["items"]]
        assert all_names == TAG_NAMES  # sanity: fixture built the vault we think it did

        page1 = await list_tags(sort_by="name", offset=0, limit=5)
        page2 = await list_tags(sort_by="name", offset=5, limit=5)
        page3 = await list_tags(sort_by="name", offset=10, limit=5)

        walked = (
            [t["name"] for t in page1["items"]]
            + [t["name"] for t in page2["items"]]
            + [t["name"] for t in page3["items"]]
        )

        assert walked == all_names
        assert len(walked) == len(set(walked)) == TAG_COUNT

    @pytest.mark.asyncio
    async def test_last_page_is_partial_when_total_not_a_multiple_of_limit(self, many_tags_vault):
        last_page = await list_tags(sort_by="name", offset=10, limit=5)

        assert last_page["returned"] == 4  # 14 - 10 = 4 < limit
        assert len(last_page["items"]) == 4
        assert last_page["total"] == TAG_COUNT


class TestListTagsPaginationOffsetBeyondEnd:
    @pytest.mark.asyncio
    async def test_offset_past_total_returns_empty_page_with_total_unchanged(self, many_tags_vault):
        result = await list_tags(offset=100, limit=5)

        assert result["items"] == []
        assert result["returned"] == 0
        assert result["total"] == TAG_COUNT


class TestListTagsPaginationOrderingBeforeSlicing:
    @pytest.mark.asyncio
    async def test_slice_is_correct_alphabetical_window_when_sorted_by_name(self, many_tags_vault):
        result = await list_tags(sort_by="name", offset=3, limit=4)

        assert [t["name"] for t in result["items"]] == TAG_NAMES[3:7]

    @pytest.mark.asyncio
    async def test_slice_is_correct_count_window_when_sorted_by_count(self, count_ordered_vault):
        result = await list_tags(sort_by="count", offset=0, limit=2)

        assert [t["name"] for t in result["items"]] == ["charlie", "bravo"]


class TestListTagsPaginationIncludeFiles:
    @pytest.mark.asyncio
    async def test_paged_items_still_carry_their_own_files_list(self, many_tags_vault):
        # Per-tag `files` lists are intentionally NOT capped by limit/offset —
        # limit/offset only page the tag list itself. No cap assertion here.
        result = await list_tags(include_files=True, sort_by="name", offset=0, limit=3)

        assert len(result["items"]) == 3
        for i, item in enumerate(result["items"]):
            assert item["files"] == [f"Note{i:02d}.md"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
