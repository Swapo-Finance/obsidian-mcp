#!/usr/bin/env python3
"""Regression tests for obsidian_mcp/tools/batch_properties.py.

Finding 1 (tag-normalization parity): batch_update_properties's add_tags/
remove_tags used to be only JSON-decoded (_coerce_tag_list) and never passed
through _clean_tags -- the '#'-stripping / OBSIDIAN_TAG_STYLE=kebab
normalization that add_tags/update_tags/remove_tags (tag_editing.py) all
apply. That meant add_tags_tool("N.md", ["#urgent"]) stored "urgent" while
batch_update_properties_tool({"files": ["N.md"]}, add_tags=["#urgent"])
stored the literal "#urgent" in the same frontmatter field. Fixed by
cleaning add_tags/remove_tags through the same _clean_tags(vault, tags) as
soon as they're coerced, before anything else uses them.

Finding 3 (max_results bound bypass): search_criteria is a bare dict with
no nested MCP schema, so the `ge=1, le=500` bound search_notes_tool/
search_by_regex_tool enforce at the pydantic schema level never applied to
batch_update_properties's query-based selection -- search_criteria.get(
"max_results", 500) flowed straight into search_notes(max_results=...),
which has no internal upper-bound check of its own. Fixed by validating
max_results in batch_update_properties itself, before it ever reaches
search_notes.
"""

import os
import shutil
import tempfile

import pytest

from obsidian_mcp.tools.batch_properties import batch_update_properties
from obsidian_mcp.tools.note_management import create_note, read_note
from obsidian_mcp.tools.tag_editing import add_tags
from obsidian_mcp.utils.filesystem import init_vault


@pytest.fixture
def make_vault():
    created_dirs = []

    def _make(tag_style="as-is"):
        temp_dir = tempfile.mkdtemp(prefix="obsidian_batch_props_")
        created_dirs.append(temp_dir)
        os.environ["OBSIDIAN_REQUIRE_FRONTMATTER"] = "false"
        os.environ["OBSIDIAN_TAG_STYLE"] = tag_style
        return init_vault(temp_dir)

    yield _make

    for key in ("OBSIDIAN_REQUIRE_FRONTMATTER", "OBSIDIAN_TAG_STYLE"):
        os.environ.pop(key, None)
    for d in created_dirs:
        shutil.rmtree(d, ignore_errors=True)


class TestBatchUpdatePropertiesTagNormalizationParity:
    @pytest.mark.asyncio
    async def test_hash_prefixed_add_tag_matches_add_tags_tool_output(self, make_vault):
        make_vault()
        await create_note("A.md", "# A\n")
        await create_note("B.md", "# B\n")

        direct_result = await add_tags("A.md", ["#urgent"])
        await batch_update_properties({"files": ["B.md"]}, add_tags=["#urgent"])

        note_b = await read_note("B.md")
        # Same input tag through both paths -> same stored frontmatter.
        assert direct_result["tags"]["after"] == ["urgent"]
        assert note_b["details"]["metadata"]["tags"] == ["urgent"]

    @pytest.mark.asyncio
    async def test_hash_prefixed_remove_tag_matches_remove_tags_tool_semantics(
        self, make_vault
    ):
        make_vault()
        await create_note("Note.md", "---\ntags: [urgent]\n---\n\nBody\n")

        result = await batch_update_properties(
            {"files": ["Note.md"]}, remove_tags=["#urgent"]
        )
        assert result["updated"] == 1

        note = await read_note("Note.md")
        assert note["details"]["metadata"]["tags"] == []

    @pytest.mark.asyncio
    async def test_kebab_tag_style_normalizes_the_batch_path_too(self, make_vault):
        make_vault(tag_style="kebab")
        await create_note("Note.md", "# Note\n")

        result = await batch_update_properties(
            {"files": ["Note.md"]}, add_tags=["Café Manhã"]
        )
        assert result["updated"] == 1

        note = await read_note("Note.md")
        assert note["details"]["metadata"]["tags"] == ["cafe-manha"]

    @pytest.mark.asyncio
    async def test_kebab_non_normalizable_tag_raises_like_add_tags_tool(
        self, make_vault
    ):
        make_vault(tag_style="kebab")
        await create_note("Note.md", "# Note\n")

        with pytest.raises(ValueError, match="kebab-case"):
            await batch_update_properties({"files": ["Note.md"]}, add_tags=["🎉"])


class TestBatchUpdatePropertiesMaxResultsBound:
    @pytest.mark.asyncio
    async def test_rejects_max_results_over_500(self, make_vault):
        make_vault()

        with pytest.raises(ValueError, match="max_results"):
            await batch_update_properties(
                {"query": "irrelevant", "max_results": 100_000},
                property_updates={"status": "done"},
            )

    @pytest.mark.asyncio
    async def test_rejects_non_integer_max_results(self, make_vault):
        make_vault()

        with pytest.raises(ValueError, match="max_results"):
            await batch_update_properties(
                {"query": "irrelevant", "max_results": "500"},
                property_updates={"status": "done"},
            )

    @pytest.mark.asyncio
    async def test_accepts_max_results_at_the_500_boundary(self, make_vault):
        make_vault()

        # Query matches nothing -- only proves the boundary value is
        # accepted and forwarded to search_notes without raising.
        result = await batch_update_properties(
            {"query": "no-such-note-zzz", "max_results": 500},
            property_updates={"status": "done"},
        )
        assert result["total_notes"] == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
