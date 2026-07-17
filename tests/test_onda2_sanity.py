#!/usr/bin/env python3
"""Sanity tests for the Onda 2 spec (§10: robust int parsing, VaultCache
name/description, OBSIDIAN_REQUIRE_FRONTMATTER, search result mode).

Minimal by design, same spirit as test_onda1_sanity.py — covers the pieces
judged highest-risk-of-being-wrong with no prior test-file precedent to lean
on for regression safety: OBSIDIAN_REQUIRE_FRONTMATTER's default-on
behavior (name/description enforcement, the add_daily auto-seed exemption)
and the search index-mode threshold switch. The full matrix (spec section 8)
is owned by a separate pass.
"""

import os
import shutil
import tempfile
from pathlib import Path

import pytest
import pytest_asyncio

from obsidian_mcp.tools.note_management import create_note, read_note, update_note
from obsidian_mcp.tools.daily_notes import add_daily_note
from obsidian_mcp.tools.search_discovery import search_notes
from obsidian_mcp.utils.filesystem import ObsidianVault, init_vault
from obsidian_mcp.utils.vault_config import derive_note_description, derive_note_name


class TestReadIntEnvRobustness:
    """_read_int_env: whitespace-padded values coerce; non-numeric falls
    back to default (spec section 10.1)."""

    def test_whitespace_padded_value_coerces(self, monkeypatch):
        monkeypatch.setenv("OBSIDIAN_MAX_NOTE_LINES", "  500 ")
        assert ObsidianVault._read_int_env("OBSIDIAN_MAX_NOTE_LINES", 999) == 500

    def test_non_numeric_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("OBSIDIAN_MAX_NOTE_LINES", "not-a-number")
        assert ObsidianVault._read_int_env("OBSIDIAN_MAX_NOTE_LINES", 999) == 999

    def test_unset_uses_default(self, monkeypatch):
        monkeypatch.delenv("OBSIDIAN_MAX_NOTE_LINES", raising=False)
        assert ObsidianVault._read_int_env("OBSIDIAN_MAX_NOTE_LINES", 42) == 42


class TestDeriveNoteNameDescription:
    """Pure-function fallback chains feeding the VaultCache — no vault
    needed (spec section 10.2)."""

    def test_name_from_frontmatter(self):
        assert derive_note_name("folder/Foo.md", {"name": "Custom Name"}) == "Custom Name"

    def test_name_falls_back_to_filename_stem(self):
        assert derive_note_name("folder/Foo.md", {}) == "Foo"

    def test_description_from_frontmatter(self):
        assert derive_note_description({"description": "Explicit"}, "# Heading\nBody") == "Explicit"

    def test_description_falls_back_to_first_non_heading_line(self):
        content = "# Heading\n\nFirst real line.\n\nMore.\n"
        assert derive_note_description({}, content) == "First real line."

    def test_description_falls_back_to_first_h2_heading_when_no_prose(self):
        content = "# Title\n\n## Objetivo\n\n## Status\n"
        assert derive_note_description({}, content) == "Objetivo"

    def test_description_empty_when_nothing_usable(self):
        assert derive_note_description({}, "") == ""


class TestVaultCacheNameDescription:
    """VaultCache serves name/description from its own in-memory index —
    the pre-requisite for search index mode (spec section 10.2)."""

    @pytest_asyncio.fixture
    async def vault(self):
        temp_dir = tempfile.mkdtemp(prefix="obsidian_onda2_cache_")
        # Isolate from section 10.3 — these fixture files predate the note
        # and have no `description`, which is unrelated to what this class
        # is testing (cache population, not the requirement itself).
        os.environ["OBSIDIAN_REQUIRE_FRONTMATTER"] = "false"
        (Path(temp_dir) / "WithMeta.md").write_text(
            "---\nname: Custom\ndescription: A custom description.\n---\n\n# Body\n"
        )
        (Path(temp_dir) / "NoFrontmatter.md").write_text("# Title\n\nFirst prose line here.\n")
        v = init_vault(temp_dir)
        yield v
        os.environ.pop("OBSIDIAN_REQUIRE_FRONTMATTER", None)
        shutil.rmtree(temp_dir)

    @pytest.mark.asyncio
    async def test_cache_serves_frontmatter_name_description(self, vault):
        meta = await vault.cache.get_note_meta("WithMeta.md")
        assert meta == {"name": "Custom", "description": "A custom description."}

    @pytest.mark.asyncio
    async def test_cache_falls_back_when_no_frontmatter(self, vault):
        meta = await vault.cache.get_note_meta("NoFrontmatter.md")
        assert meta["name"] == "NoFrontmatter"
        assert meta["description"] == "First prose line here."

    @pytest.mark.asyncio
    async def test_get_all_note_meta_covers_every_indexed_note(self, vault):
        all_meta = await vault.cache.get_all_note_meta()
        assert set(all_meta.keys()) == {"WithMeta.md", "NoFrontmatter.md"}


class TestRequireFrontmatterDefaultOn:
    """OBSIDIAN_REQUIRE_FRONTMATTER defaults to true — no env var set at
    all still enforces name/description (spec section 10.3)."""

    @pytest_asyncio.fixture
    async def vault(self):
        temp_dir = tempfile.mkdtemp(prefix="obsidian_onda2_require_")
        os.environ.pop("OBSIDIAN_REQUIRE_FRONTMATTER", None)  # exercise the true default (on)
        v = init_vault(temp_dir)
        yield v
        shutil.rmtree(temp_dir)

    @pytest.mark.asyncio
    async def test_missing_description_raises(self, vault):
        with pytest.raises(ValueError, match="description"):
            await create_note("NoDescription.md", "# Just a note\n")

    @pytest.mark.asyncio
    async def test_name_forced_from_filename_overwriting_divergent_value(self, vault):
        await create_note(
            "real-filename.md",
            "---\nname: totally-different\ndescription: Something.\n---\n\nBody\n",
        )
        note = await read_note("real-filename.md")
        assert note["details"]["metadata"]["frontmatter"]["name"] == "real-filename"

    @pytest.mark.asyncio
    async def test_update_append_exempt_from_description_requirement(self, vault):
        await create_note("Existing.md", "---\ndescription: Has one.\n---\n\nBody\n")
        # Appending pure prose (no frontmatter at all) must succeed — append
        # is exempt (spec section 10.3's exemption bullet).
        result = await update_note("Existing.md", "More content, no frontmatter here.", merge_strategy="append")
        assert result["success"] is True


class TestRequireFrontmatterCanBeTurnedOff:
    @pytest.mark.asyncio
    async def test_off_preserves_pre_onda2_behavior(self):
        temp_dir = tempfile.mkdtemp(prefix="obsidian_onda2_require_off_")
        os.environ["OBSIDIAN_REQUIRE_FRONTMATTER"] = "false"
        try:
            init_vault(temp_dir)
            result = await create_note("no-frontmatter-needed.md", "# Just a note\n")
            assert result["success"] is True
        finally:
            os.environ.pop("OBSIDIAN_REQUIRE_FRONTMATTER", None)
            shutil.rmtree(temp_dir)


class TestAddDailyNoteWithRequireFrontmatter:
    """add_daily_note's file-creation path auto-seeds name/description
    instead of raising — the server, not the LLM, creates this file (spec
    section 10.3's add_daily bullet)."""

    @pytest_asyncio.fixture
    async def vault(self):
        temp_dir = tempfile.mkdtemp(prefix="obsidian_onda2_daily_")
        os.environ.pop("OBSIDIAN_REQUIRE_FRONTMATTER", None)  # exercise the true default (on)
        v = init_vault(temp_dir)
        yield v
        shutil.rmtree(temp_dir)

    @pytest.mark.asyncio
    async def test_daily_creation_auto_seeds_frontmatter_without_erroring(self, vault):
        result = await add_daily_note("First entry of the day.")
        assert result["created"] is True

        note = await read_note(result["path"])
        frontmatter = note["details"]["metadata"]["frontmatter"]
        expected_name = result["path"].rsplit("/", 1)[-1][:-3]
        assert frontmatter["name"] == expected_name
        assert frontmatter["description"]  # non-empty, auto-generated
        assert "First entry of the day." in note["details"]["content"]


class TestSearchResultMode:
    """OBSIDIAN_SEARCH_RESULT_MODE=auto (the default) switches to index
    mode once results exceed OBSIDIAN_SEARCH_INDEX_THRESHOLD; an explicit
    per-call `mode` overrides it either way (spec section 10.4)."""

    @pytest_asyncio.fixture
    async def vault_many_notes(self):
        temp_dir = tempfile.mkdtemp(prefix="obsidian_onda2_searchmode_")
        os.environ["OBSIDIAN_REQUIRE_FRONTMATTER"] = "false"
        for i in range(12):  # > the default threshold of 10
            (Path(temp_dir) / f"note{i}.md").write_text(
                f"---\ndescription: Note number {i}.\n---\n\nfindme content {i}\n"
            )
        v = init_vault(temp_dir)
        # Content search is served from the persistent (SQLite) index, which
        # only auto-refreshes in the background on a stale timer — force it
        # synchronously so the assertions below aren't racing a background
        # task (same pattern as test_filesystem_integration.py's fixture).
        await v._update_search_index()
        yield v
        # Close the SQLite connection before teardown — same pattern as
        # test_filesystem_integration.py's fixture. Without this the
        # process can hang on exit waiting for the aiosqlite connection.
        if v.persistent_index:
            await v.persistent_index.close()
        os.environ.pop("OBSIDIAN_REQUIRE_FRONTMATTER", None)
        shutil.rmtree(temp_dir)

    @pytest.mark.asyncio
    async def test_auto_mode_switches_to_index_above_threshold(self, vault_many_notes):
        result = await search_notes("findme", max_results=50)
        assert result["count"] > 10
        assert result["query"]["mode"] == "index"
        item = result["results"][0]
        assert set(item.keys()) == {"path", "name", "description", "score", "match_type"}

    @pytest.mark.asyncio
    async def test_explicit_content_mode_overrides_auto(self, vault_many_notes):
        result = await search_notes("findme", max_results=50, mode="content")
        assert result["query"]["mode"] == "content"
        assert "context" in result["results"][0]  # content-mode snippet still present

    @pytest.mark.asyncio
    async def test_small_result_count_stays_content_under_auto(self, vault_many_notes):
        # A query matching a single note stays under the threshold -> content.
        result = await search_notes("findme content 7", max_results=50)
        assert result["count"] <= 10
        assert result["query"]["mode"] == "content"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
