#!/usr/bin/env python3
"""OBSIDIAN_SEARCH_RESULT_MODE / OBSIDIAN_SEARCH_INDEX_THRESHOLD (spec
section 10.4) wired into search_by_regex, search_by_property, and
search_by_date — test_onda2_sanity.py's TestSearchResultMode only exercised
search_notes. Each tool's index-mode item shape differs (search_by_regex
repurposes match_count as score; search_by_property keeps property_value
even in index mode; search_by_date only ever adds name/description, it
never had a content snippet to strip), so each needs its own coverage.
"""

import os
import shutil
import tempfile
from pathlib import Path

import pytest
import pytest_asyncio

from obsidian_mcp.tools.search_discovery import search_by_date, search_by_property, search_by_regex
from obsidian_mcp.utils.filesystem import init_vault

NOTE_COUNT = 12  # > the default OBSIDIAN_SEARCH_INDEX_THRESHOLD of 10


@pytest_asyncio.fixture
async def vault_many_notes():
    temp_dir = tempfile.mkdtemp(prefix="obsidian_searchmode_")
    os.environ["OBSIDIAN_REQUIRE_FRONTMATTER"] = "false"
    for i in range(NOTE_COUNT):
        (Path(temp_dir) / f"note{i}.md").write_text(
            f"---\nstatus: active\n---\n\nTODO: fix item {i}\n"
        )
    v = init_vault(temp_dir)
    # search_by_regex blocks on a stale/None persistent-index timestamp and
    # would self-update anyway, but pre-warming here (same pattern as
    # test_onda2_sanity.py's fixture) keeps every test in this file
    # deterministic regardless of call order.
    await v._update_search_index()
    yield v
    if v.persistent_index:
        await v.persistent_index.close()
    os.environ.pop("OBSIDIAN_REQUIRE_FRONTMATTER", None)
    shutil.rmtree(temp_dir)


class TestSearchByRegexIndexMode:
    @pytest.mark.asyncio
    async def test_auto_mode_switches_to_index_above_threshold(self, vault_many_notes):
        result = await search_by_regex(r"TODO")
        assert result["count"] > 10
        assert result["query"]["mode"] == "index"
        item = result["results"][0]
        assert set(item.keys()) == {"path", "name", "description", "score", "match_type"}
        assert item["match_type"] == "regex"

    @pytest.mark.asyncio
    async def test_explicit_content_mode_overrides_auto(self, vault_many_notes):
        result = await search_by_regex(r"TODO", mode="content")
        assert result["query"]["mode"] == "content"
        assert "matches" in result["results"][0]

    @pytest.mark.asyncio
    async def test_small_result_count_stays_content_under_auto(self, vault_many_notes):
        result = await search_by_regex(r"item 7\b")
        assert result["count"] <= 10
        assert result["query"]["mode"] == "content"


class TestSearchByPropertyIndexMode:
    @pytest.mark.asyncio
    async def test_auto_mode_switches_to_index_above_threshold(self, vault_many_notes):
        result = await search_by_property("status", "active", "=")
        assert result["count"] > 10
        assert result["query"]["mode"] == "index"
        item = result["results"][0]
        assert set(item.keys()) == {
            "path", "name", "description", "score", "match_type", "property_value",
        }
        assert item["property_value"] == "active"

    @pytest.mark.asyncio
    async def test_explicit_content_mode_overrides_auto(self, vault_many_notes):
        result = await search_by_property("status", "active", "=", mode="content")
        assert result["query"]["mode"] == "content"
        assert "context" in result["results"][0]

    @pytest.mark.asyncio
    async def test_explicit_index_mode_overrides_small_count(self, vault_many_notes):
        # A property value nothing matches -> under the threshold (0) ->
        # auto would stay content, but an explicit mode="index" call must
        # still win regardless of how few (or zero) results come back.
        result_small = await search_by_property(
            "status", "no-such-status-value", "=", mode="index"
        )
        assert result_small["count"] == 0
        assert result_small["query"]["mode"] == "index"


class TestSearchByDateIndexMode:
    @pytest.mark.asyncio
    async def test_auto_mode_enriches_with_name_description_above_threshold(self, vault_many_notes):
        result = await search_by_date(date_type="modified", days_ago=7, operator="within")
        assert result["count"] > 10
        assert result["query"]["mode"] == "index"
        item = result["results"][0]
        # search_by_date's item never carried a content snippet to strip —
        # index mode only ADDS name/description, nothing is removed.
        assert set(item.keys()) == {"path", "date", "days_ago", "name", "description"}

    @pytest.mark.asyncio
    async def test_explicit_content_mode_omits_name_description(self, vault_many_notes):
        result = await search_by_date(
            date_type="modified", days_ago=7, operator="within", mode="content"
        )
        assert result["query"]["mode"] == "content"
        item = result["results"][0]
        assert set(item.keys()) == {"path", "date", "days_ago"}


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
