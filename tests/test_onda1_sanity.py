#!/usr/bin/env python3
"""Sanity tests for the Onda 1 spec (templates enforcement, VaultCache, daily
notes, wikilink validation, kebab normalization).

Minimal by design — the full matrix (spec section 8) is owned by a separate
pass. This file only covers the pieces judged highest-risk-of-being-wrong
while implementing: slug/tag transliteration edge cases, template heading
ordering, and the VaultCache incremental-update path (the one piece with no
prior-test-file precedent to lean on for regression safety).
"""

import shutil
import tempfile
from pathlib import Path

import pytest
import pytest_asyncio

from obsidian_mcp.tools.note_management import create_note
from obsidian_mcp.tools.organization import list_tags
from obsidian_mcp.tools.link_management import build_vault_notes_index
from obsidian_mcp.utils.filesystem import init_vault
from obsidian_mcp.utils.vault_config import (
    normalize_tag_kebab,
    slugify_kebab,
    check_template_conformance,
)


class TestSlugifyKebab:
    """Pure-function transliteration — no vault needed."""

    def test_accented_portuguese_transliterates(self):
        assert slugify_kebab("Café da Manhã") == "cafe-da-manha"
        assert slugify_kebab("São Paulo Reunião") == "sao-paulo-reuniao"

    def test_symbols_and_spaces_collapse_to_single_hyphen(self):
        assert slugify_kebab("Projeto: Fase 1") == "projeto-fase-1"

    def test_non_latin_script_is_non_normalizable(self):
        assert slugify_kebab("日本語") is None

    def test_pure_emoji_is_non_normalizable(self):
        assert slugify_kebab("🎉🎊") is None

    def test_empty_string_is_non_normalizable(self):
        assert slugify_kebab("") is None

    def test_partial_alphanumeric_survives(self):
        # Only the emoji is dropped; "emoji" itself is real content.
        assert slugify_kebab("emoji🎉") == "emoji"


class TestNormalizeTagKebab:
    """Hierarchical ('/'-segmented) tag normalization."""

    def test_hierarchical_segments_normalize_independently(self):
        assert normalize_tag_kebab("Projeto/Fase-1") == "projeto/fase-1"
        assert normalize_tag_kebab("café/manhã") == "cafe/manha"

    def test_empty_segment_rejected(self):
        # "a//b" splits into ["a", "", "b"] — the empty segment can't normalize.
        assert normalize_tag_kebab("a//b") is None

    def test_all_emoji_segment_rejected(self):
        assert normalize_tag_kebab("🎉") is None


class TestTemplateConformance:
    """check_template_conformance: heading presence/order, frontmatter keys,
    the folder-scoping (enforced folder vs free folder), and the
    incremental-edit exemption (spec section 3)."""

    @pytest_asyncio.fixture
    async def templated_vault(self):
        temp_dir = tempfile.mkdtemp(prefix="obsidian_onda1_")
        templates_dir = Path(temp_dir) / "templates"
        templates_dir.mkdir()
        (templates_dir / "projeto.md").write_text(
            "---\nstatus: \n---\n\n## Objetivo\n\n## Status\n"
        )
        (Path(temp_dir) / "01-projects").mkdir()

        import os
        os.environ["OBSIDIAN_FOLDER_TEMPLATES"] = (
            '[{"folder":"01-projects","template":"templates/projeto.md"}]'
        )
        vault = init_vault(temp_dir)

        yield vault

        os.environ.pop("OBSIDIAN_FOLDER_TEMPLATES", None)
        shutil.rmtree(temp_dir)

    def test_conforming_content_passes(self, templated_vault):
        content = "---\nstatus: active\n---\n\n## Objetivo\n\nGoal\n\n## Status\n\nOK\n"
        # Must not raise.
        check_template_conformance(templated_vault, "01-projects/Note.md", content)

    def test_missing_heading_rejected(self, templated_vault):
        with pytest.raises(ValueError, match="Missing headings"):
            check_template_conformance(templated_vault, "01-projects/Note.md", "## Objetivo\n")

    def test_out_of_order_headings_rejected(self, templated_vault):
        with pytest.raises(ValueError, match="out of order"):
            check_template_conformance(
                templated_vault, "01-projects/Note.md", "## Status\n\n## Objetivo\n"
            )

    def test_extra_headings_anywhere_are_allowed(self, templated_vault):
        content = "---\nstatus: active\n---\n\n## Intro\n\n## Objetivo\n\n## Extra\n\n## Status\n"
        check_template_conformance(templated_vault, "01-projects/Note.md", content)

    def test_missing_frontmatter_key_rejected(self, templated_vault):
        with pytest.raises(ValueError, match="frontmatter"):
            check_template_conformance(
                templated_vault, "01-projects/Note.md", "## Objetivo\n\n## Status\n"
            )

    def test_folder_without_rule_is_free_form(self, templated_vault):
        # Must not raise — no rule applies outside 01-projects.
        check_template_conformance(templated_vault, "elsewhere/Note.md", "whatever\n")

    @pytest.mark.asyncio
    async def test_create_note_enforces_template(self, templated_vault):
        with pytest.raises(ValueError):
            await create_note("01-projects/Bad.md", "# Just a note\n")

        # description: is required by the (default-on) OBSIDIAN_REQUIRE_FRONTMATTER
        # on top of the template's own required keys/headings — both checks
        # must pass together.
        result = await create_note(
            "01-projects/Good.md",
            "---\nstatus: active\ndescription: Sample project note\n---\n\n"
            "## Objetivo\n\nGoal\n\n## Status\n\nOK\n",
        )
        assert result["success"] is True


class TestVaultCacheIncrementalUpdate:
    """The one piece with no prior test-file precedent: mutation via the
    MCP write path must be immediately visible through the cache, without
    waiting for the stat-diff TTL or a forced rebuild."""

    @pytest_asyncio.fixture
    async def vault(self):
        temp_dir = tempfile.mkdtemp(prefix="obsidian_onda1_cache_")
        (Path(temp_dir) / "A.md").write_text("---\ntags: [alpha]\n---\n# A\n")

        # This class exercises cache incrementality, not the
        # frontmatter-requirement feature — its create_note calls below
        # don't set a `description`, so turn the (default-on) requirement
        # off for this vault.
        import os
        os.environ["OBSIDIAN_REQUIRE_FRONTMATTER"] = "false"
        v = init_vault(temp_dir)
        yield v
        os.environ.pop("OBSIDIAN_REQUIRE_FRONTMATTER", None)
        shutil.rmtree(temp_dir)

    @pytest.mark.asyncio
    async def test_list_tags_reflects_create_note_immediately(self, vault):
        before = await list_tags(include_counts=True)
        assert {t["name"] for t in before["items"]} == {"alpha"}

        await create_note("B.md", "---\ntags: [beta]\n---\n# B\n")

        after = await list_tags(include_counts=True)
        assert {t["name"] for t in after["items"]} == {"alpha", "beta"}

    @pytest.mark.asyncio
    async def test_notes_index_reflects_create_note_immediately(self, vault):
        index_before = await build_vault_notes_index(vault)
        assert "B.md" not in index_before

        await create_note("B.md", "# B\n")

        index_after = await build_vault_notes_index(vault)
        assert index_after.get("B.md") == "B.md"
        assert index_after.get("B") == "B.md"

    @pytest.mark.asyncio
    async def test_cache_picks_up_pre_existing_files_on_first_access(self, vault):
        # A.md was written directly to disk by the fixture (bypassing
        # vault.write_note / the mutation hook) before the cache was ever
        # built — the lazy first-access full scan must still find it.
        index = await build_vault_notes_index(vault)
        assert index.get("A.md") == "A.md"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
