#!/usr/bin/env python3
"""OBSIDIAN_TAG_STYLE=kebab and OBSIDIAN_SLUG_STYLE=kebab wired into the
actual write paths (spec section 1). test_onda1_sanity.py already covers
the pure transliteration functions (slugify_kebab/normalize_tag_kebab)
thoroughly — this file covers the integration gap: add_tags/update_tags/
remove_tags, frontmatter tags on create_note, the create_note filename
itself, and the frontmatter `name` field.

REQUIRE_FRONTMATTER is off for this module to isolate slug/tag-style
concerns from the separate frontmatter-requirement feature.
"""

import os
import shutil
import tempfile

import pytest

from obsidian_mcp.tools.note_management import create_note, read_note
from obsidian_mcp.tools.organization import add_tags, remove_tags, update_tags
from obsidian_mcp.utils.filesystem import init_vault


@pytest.fixture
def make_vault():
    created_dirs = []

    def _make(tag_style="as-is", slug_style="as-is"):
        temp_dir = tempfile.mkdtemp(prefix="obsidian_kebab_")
        created_dirs.append(temp_dir)
        os.environ["OBSIDIAN_REQUIRE_FRONTMATTER"] = "false"
        os.environ["OBSIDIAN_TAG_STYLE"] = tag_style
        os.environ["OBSIDIAN_SLUG_STYLE"] = slug_style
        return init_vault(temp_dir)

    yield _make

    for key in ("OBSIDIAN_REQUIRE_FRONTMATTER", "OBSIDIAN_TAG_STYLE", "OBSIDIAN_SLUG_STYLE"):
        os.environ.pop(key, None)
    for d in created_dirs:
        shutil.rmtree(d, ignore_errors=True)


class TestTagStyleKebabViaTagTools:
    @pytest.mark.asyncio
    async def test_add_tags_normalizes_accented_tag(self, make_vault):
        make_vault(tag_style="kebab")
        await create_note("Note.md", "# Note\n")
        result = await add_tags("Note.md", ["Café Manhã"])
        assert result["tags"]["after"] == ["cafe-manha"]

    @pytest.mark.asyncio
    async def test_add_tags_normalizes_hierarchical_tag_segment_by_segment(self, make_vault):
        make_vault(tag_style="kebab")
        await create_note("Note.md", "# Note\n")
        result = await add_tags("Note.md", ["Projeto/Fase 1"])
        assert result["tags"]["after"] == ["projeto/fase-1"]

    @pytest.mark.asyncio
    async def test_add_tags_non_normalizable_raises(self, make_vault):
        make_vault(tag_style="kebab")
        await create_note("Note.md", "# Note\n")
        with pytest.raises(ValueError, match="kebab-case"):
            await add_tags("Note.md", ["🎉"])

    @pytest.mark.asyncio
    async def test_update_tags_replace_normalizes(self, make_vault):
        make_vault(tag_style="kebab")
        await create_note("Note.md", "# Note\n")
        result = await update_tags("Note.md", ["São Paulo"], merge=False)
        assert result["tags"]["after"] == ["sao-paulo"]

    @pytest.mark.asyncio
    async def test_remove_tags_matches_by_normalized_form(self, make_vault):
        make_vault(tag_style="kebab")
        # Created with an already-accented tag: TAG_STYLE=kebab normalizes
        # it on write, so the stored tag is "cafe-manha".
        await create_note("Note.md", "---\ntags: [Café Manhã]\n---\n\nBody\n")

        result = await remove_tags("Note.md", ["Café Manhã"])
        assert result["tags"]["after"] == []

    @pytest.mark.asyncio
    async def test_as_is_style_leaves_tags_untouched(self, make_vault):
        make_vault(tag_style="as-is")
        await create_note("Note.md", "# Note\n")
        result = await add_tags("Note.md", ["Café Manhã"])
        assert result["tags"]["after"] == ["Café Manhã"]


class TestTagStyleKebabFrontmatterOnCreate:
    @pytest.mark.asyncio
    async def test_frontmatter_tags_normalized_in_order_on_create(self, make_vault):
        make_vault(tag_style="kebab")
        content = "---\ntags: [Café, Projeto/Fase 1]\n---\n\nBody\n"
        await create_note("Note.md", content)

        note = await read_note("Note.md")
        assert note["details"]["metadata"]["tags"] == ["cafe", "projeto/fase-1"]

    @pytest.mark.asyncio
    async def test_non_normalizable_frontmatter_tag_raises_and_writes_nothing(self, make_vault):
        make_vault(tag_style="kebab")
        content = "---\ntags: [\"🎉\"]\n---\n\nBody\n"
        with pytest.raises(ValueError, match="kebab-case"):
            await create_note("Note.md", content)
        with pytest.raises(FileNotFoundError):
            await read_note("Note.md")


class TestSlugStyleKebabFilename:
    @pytest.mark.asyncio
    async def test_accented_filename_slugified_on_create(self, make_vault):
        make_vault(slug_style="kebab")
        result = await create_note("Ideias Malucas/Café da Manhã.md", "# Body\n")
        assert result["path"] == "Ideias Malucas/cafe-da-manha.md"

    @pytest.mark.asyncio
    async def test_slugified_note_is_readable_at_new_path(self, make_vault):
        make_vault(slug_style="kebab")
        # ":" is rejected by path validation regardless of slug style, so
        # this uses a raw path that's already valid (no reserved chars) but
        # still needs accent/space transliteration.
        await create_note("São Paulo Reunião.md", "# Body\n")
        note = await read_note("sao-paulo-reuniao.md")
        assert note["success"] is True

    @pytest.mark.asyncio
    async def test_non_normalizable_filename_raises(self, make_vault):
        make_vault(slug_style="kebab")
        with pytest.raises(ValueError, match="kebab-case"):
            await create_note("🎉🎊.md", "# Body\n")

    @pytest.mark.asyncio
    async def test_as_is_style_leaves_filename_untouched(self, make_vault):
        make_vault(slug_style="as-is")
        result = await create_note("Café da Manhã.md", "# Body\n")
        assert result["path"] == "Café da Manhã.md"


class TestSlugStyleKebabFrontmatterName:
    @pytest.mark.asyncio
    async def test_frontmatter_name_slugified_when_require_frontmatter_off(self, make_vault):
        make_vault(slug_style="kebab")  # REQUIRE_FRONTMATTER=false via fixture
        content = "---\nname: Café Especial\n---\n\nBody\n"
        await create_note("Note.md", content)

        note = await read_note("Note.md")
        assert note["details"]["metadata"]["frontmatter"]["name"] == "cafe-especial"

    @pytest.mark.asyncio
    async def test_non_normalizable_frontmatter_name_raises(self, make_vault):
        make_vault(slug_style="kebab")
        content = "---\nname: \"🎉\"\n---\n\nBody\n"
        with pytest.raises(ValueError, match="kebab-case"):
            await create_note("Note.md", content)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
