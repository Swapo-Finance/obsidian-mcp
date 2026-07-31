#!/usr/bin/env python3
"""Light cache-sourced enrichment (spec section 10.4's closing sentence):
list_notes, get_backlinks, and find_broken_links each gain fields from the
VaultCache's per-note name/description index — no test coverage existed for
any of these three before this pass.

REQUIRE_FRONTMATTER is off for this module: some fixtures deliberately omit
frontmatter to exercise the description fallback chain (first non-heading
line), which the requirement would otherwise block.
"""

import os
import shutil
import tempfile

import pytest
import pytest_asyncio

from obsidian_mcp.tools.broken_links import _resolve_scope
from obsidian_mcp.tools.link_management import find_broken_links, get_backlinks
from obsidian_mcp.tools.search_discovery import list_notes
from obsidian_mcp.utils.filesystem import init_vault


@pytest_asyncio.fixture
async def vault():
    temp_dir = tempfile.mkdtemp(prefix="obsidian_enrichment_")
    os.environ["OBSIDIAN_REQUIRE_FRONTMATTER"] = "false"
    v = init_vault(temp_dir)
    yield v
    os.environ.pop("OBSIDIAN_REQUIRE_FRONTMATTER", None)
    shutil.rmtree(temp_dir)


class TestListNotesEnrichment:
    @pytest.mark.asyncio
    async def test_items_gain_description_from_cache(self, vault):
        (vault.vault_path / "WithDescription.md").write_text(
            "---\ndescription: An explicit summary.\n---\n\n# Body\n"
        )
        (vault.vault_path / "NoFrontmatter.md").write_text(
            "# Title\n\nFirst prose line as fallback.\n"
        )

        result = await list_notes(recursive=True)
        by_path = {item["path"]: item for item in result["items"]}

        assert by_path["WithDescription.md"]["description"] == "An explicit summary."
        assert (
            by_path["NoFrontmatter.md"]["description"]
            == "First prose line as fallback."
        )

    @pytest.mark.asyncio
    async def test_name_field_stays_the_literal_filename_not_cache_name(self, vault):
        # list_notes' own "name" has always meant the filename — enrichment
        # must not overwrite it with the frontmatter-derived cache name,
        # even when the note declares a different one.
        (vault.vault_path / "real-filename.md").write_text(
            "---\nname: A Totally Different Title\ndescription: x\n---\n\nBody\n"
        )

        result = await list_notes(recursive=True)
        item = result["items"][0]
        assert item["name"] == "real-filename.md"

    @pytest.mark.asyncio
    async def test_empty_vault_returns_no_items_without_error(self, vault):
        result = await list_notes(recursive=True)
        assert result["items"] == []
        assert result["total"] == 0


class TestGetBacklinksEnrichment:
    @pytest.mark.asyncio
    async def test_findings_carry_source_notes_name_and_description(self, vault):
        (vault.vault_path / "Target.md").write_text("# Target\n")
        (vault.vault_path / "Source.md").write_text(
            "---\nname: The Source Note\ndescription: Links to Target.\n---\n\n"
            "See [[Target]] for details.\n"
        )

        result = await get_backlinks("Target.md")
        assert result["summary"]["backlink_count"] == 1
        finding = result["findings"][0]
        assert finding["source_path"] == "Source.md"
        assert finding["name"] == "The Source Note"
        assert finding["description"] == "Links to Target."

    @pytest.mark.asyncio
    async def test_no_backlinks_returns_empty_findings_without_error(self, vault):
        (vault.vault_path / "Lonely.md").write_text("# Lonely\n")
        result = await get_backlinks("Lonely.md")
        assert result["findings"] == []


class TestFindBrokenLinksEnrichment:
    @pytest.mark.asyncio
    async def test_findings_carry_source_notes_name_and_description(self, vault):
        (vault.vault_path / "Source.md").write_text(
            "---\ndescription: Has a dangling link.\n---\n\n"
            "See [[Does Not Exist]] here.\n"
        )

        result = await find_broken_links()
        assert result["summary"]["broken_link_count"] == 1
        finding = result["findings"][0]
        assert finding["source_path"] == "Source.md"
        assert finding["description"] == "Has a dangling link."
        assert finding["name"] == "Source"  # falls back to filename stem

    @pytest.mark.asyncio
    async def test_no_broken_links_returns_empty_findings_without_error(self, vault):
        (vault.vault_path / "Target.md").write_text("# Target\n")
        (vault.vault_path / "Source.md").write_text("See [[Target]].\n")
        result = await find_broken_links()
        assert result["findings"] == []


class TestFindBrokenLinksDirectoryScoping:
    """Regression: the directory filter used to be
    `n.startswith((directory + "/", directory))`, a tuple whose second
    member is redundant (implied by the first) and collapses the whole
    check to `n.startswith(directory)` — which also matches sibling
    folders sharing the prefix, e.g. directory="Projects" wrongly pulling
    in "ProjectsArchive/...".
    """

    @pytest.mark.asyncio
    async def test_directory_filter_excludes_sibling_folder_sharing_prefix(self, vault):
        # Distinct filenames on purpose: build_vault_notes_index maps note
        # *names* to paths, so two notes sharing a filename would collide
        # in that index regardless of folder — an unrelated confound this
        # test must not trip over.
        (vault.vault_path / "Projects").mkdir()
        (vault.vault_path / "ProjectsArchive").mkdir()
        (vault.vault_path / "Projects" / "Alpha.md").write_text(
            "See [[Does Not Exist]].\n"
        )
        (vault.vault_path / "ProjectsArchive" / "Beta.md").write_text(
            "See [[Also Missing]].\n"
        )

        result = await find_broken_links(directory="Projects")

        sources = {f["source_path"] for f in result["findings"]}
        assert sources == {"Projects/Alpha.md"}

    @pytest.mark.asyncio
    async def test_directory_filter_includes_nested_subfolder(self, vault):
        (vault.vault_path / "Projects" / "Sub").mkdir(parents=True)
        (vault.vault_path / "Projects" / "Sub" / "Note.md").write_text(
            "See [[Does Not Exist]].\n"
        )

        result = await find_broken_links(directory="Projects")

        sources = {f["source_path"] for f in result["findings"]}
        assert sources == {"Projects/Sub/Note.md"}

    @pytest.mark.asyncio
    async def test_trailing_slash_directory_is_rejected(self, vault):
        (vault.vault_path / "Projects").mkdir()
        (vault.vault_path / "Projects" / "Note.md").write_text(
            "See [[Does Not Exist]].\n"
        )

        with pytest.raises(ValueError, match="cannot end with"):
            await find_broken_links(directory="Projects/")


class TestResolveScope:
    """Unit tests for the extracted _resolve_scope helper: find_broken_links
    used to resolve the single_note/directory/vault tri-state three
    separate times (the ctx scope message, notes_to_check, and
    all_links_by_note); _resolve_scope now computes all three once.
    """

    @pytest.mark.asyncio
    async def test_single_note_scope(self, vault):
        (vault.vault_path / "Source.md").write_text("See [[Target]].\n")

        label, notes_to_check, all_links_by_note = await _resolve_scope(
            vault, "Source.md", None
        )

        assert label == "note: Source.md"
        assert notes_to_check == ["Source.md"]
        assert all_links_by_note["Source.md"][0]["path"] == "Target.md"

    @pytest.mark.asyncio
    async def test_single_note_missing_raises_file_not_found(self, vault):
        # Regression: this used to be silently swallowed by a bare
        # `except Exception: pass`, reporting "0 broken links" for a note
        # that doesn't exist instead of surfacing the error.
        with pytest.raises(FileNotFoundError, match="Note not found: Missing.md"):
            await _resolve_scope(vault, "Missing.md", None)

    @pytest.mark.asyncio
    async def test_directory_scope_excludes_sibling_folder_sharing_prefix(self, vault):
        # Regression: `n.startswith(directory)` without the trailing slash
        # also matches a sibling folder sharing the prefix, e.g.
        # "ProjectsArchive" wrongly matching directory="Projects".
        (vault.vault_path / "Projects").mkdir()
        (vault.vault_path / "ProjectsArchive").mkdir()
        (vault.vault_path / "Projects" / "Alpha.md").write_text("See [[X]].\n")
        (vault.vault_path / "ProjectsArchive" / "Beta.md").write_text("See [[Y]].\n")

        label, notes_to_check, all_links_by_note = await _resolve_scope(
            vault, None, "Projects"
        )

        assert label == "directory: Projects"
        assert notes_to_check == ["Projects/Alpha.md"]
        assert set(all_links_by_note.keys()) == {"Projects/Alpha.md"}

    @pytest.mark.asyncio
    async def test_whole_vault_scope(self, vault):
        (vault.vault_path / "Only.md").write_text("See [[X]].\n")

        label, notes_to_check, all_links_by_note = await _resolve_scope(
            vault, None, None
        )

        assert label == "entire vault"
        assert notes_to_check == ["Only.md"]
        assert set(all_links_by_note.keys()) == {"Only.md"}


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
