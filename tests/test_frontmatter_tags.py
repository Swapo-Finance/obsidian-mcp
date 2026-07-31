#!/usr/bin/env python3
"""Regression tests for obsidian_mcp/tools/frontmatter_tags.py's
_update_frontmatter_tags.

Finding 2 (comma-corruption bug): the array-format branch used to split the
bracketed tag list on every literal ',' (`match.group(1).split(",")`), so a
tag value containing a comma (e.g. a quoted "has, comma") was silently cut
into two tags on read. The matching write side had the same defect in
reverse: existing_tags was rejoined with a plain ", ".join(...), so even a
correctly-parsed comma-tag would come out unquoted and get re-split on the
very next parse. Both sides are fixed via a small quote-only-when-needed
helper (_format_tag_for_flow_list) and a yaml.safe_load-based parse for the
array-format branch, keeping every other tag's formatting byte-identical to
before.

Finding 4 (dead code): the bullet-list ("- tag" on its own lines) branch's
"skip already-processed items" elif was unreachable -- the inner while loop
right after `tags:` already consumes every consecutive "- " line itself, so
the outer loop never dispatches to that elif while in_tags_list is still
True. TestUpdateFrontmatterTagsBulletListFormat below covers the bullet-list
format itself (no dedicated test existed before), which was used, along
with the rest of this suite, to empirically confirm the branch is never
exercised before deleting it.
"""

import os
import shutil
import tempfile

import pytest

from obsidian_mcp.tools.frontmatter_tags import _update_frontmatter_tags
from obsidian_mcp.tools.note_management import create_note
from obsidian_mcp.tools.tag_editing import add_tags, update_tags
from obsidian_mcp.utils.filesystem import get_vault, init_vault


def _tags_line(content: str) -> str:
    return next(line for line in content.splitlines() if line.startswith("tags:"))


class TestUpdateFrontmatterTagsCommaHandling:
    """Unit tests directly against the helper (no vault needed)."""

    def test_add_preserves_existing_tag_containing_a_comma(self):
        content = '---\ntags: ["has, comma", other]\n---\n\nBody\n'
        result = _update_frontmatter_tags(content, ["new"], "add")
        assert _tags_line(result) == 'tags: ["has, comma", other, new]'

    def test_remove_matches_the_full_comma_containing_tag(self):
        content = '---\ntags: ["has, comma", other]\n---\n\nBody\n'
        result = _update_frontmatter_tags(content, ["has, comma"], "remove")
        assert _tags_line(result) == "tags: [other]"

    def test_replace_with_a_comma_tag_quotes_it_on_write(self):
        content = "---\ntags: [alpha]\n---\n\nBody\n"
        result = _update_frontmatter_tags(content, ["has, comma"], "replace")
        assert _tags_line(result) == 'tags: ["has, comma"]'

    def test_adding_a_comma_tag_when_no_tags_key_exists_yet_quotes_it(self):
        content = "---\nstatus: draft\n---\n\nBody\n"
        result = _update_frontmatter_tags(content, ["has, comma"], "add")
        assert _tags_line(result) == 'tags: ["has, comma"]'
        assert "status: draft" in result

    def test_plain_tags_stay_unquoted(self):
        """Sanity check the fix doesn't change formatting for the common case."""
        content = "---\ntags: [alpha]\n---\n\nBody\n"
        result = _update_frontmatter_tags(content, ["beta"], "add")
        assert _tags_line(result) == "tags: [alpha, beta]"


class TestUpdateFrontmatterTagsBulletListFormat:
    """No test previously exercised the bullet-list ('- tag') branch at all;
    added to prove Finding 4's deletion doesn't regress it."""

    def test_add_to_bullet_list_format(self):
        content = "---\ntags:\n  - alpha\n  - beta\n---\n\nBody\n"
        result = _update_frontmatter_tags(content, ["gamma"], "add")
        assert _tags_line(result) == "tags: [alpha, beta, gamma]"

    def test_remove_from_bullet_list_format(self):
        content = "---\ntags:\n  - alpha\n  - beta\n---\n\nBody\n"
        result = _update_frontmatter_tags(content, ["alpha"], "remove")
        assert _tags_line(result) == "tags: [beta]"

    def test_field_after_bullet_list_survives(self):
        content = "---\ntags:\n  - alpha\n  - beta\nstatus: draft\n---\n\nBody\n"
        result = _update_frontmatter_tags(content, ["gamma"], "add")
        assert "status: draft" in result
        assert _tags_line(result) == "tags: [alpha, beta, gamma]"


@pytest.fixture
def make_vault():
    created_dirs = []

    def _make():
        temp_dir = tempfile.mkdtemp(prefix="obsidian_frontmatter_tags_")
        created_dirs.append(temp_dir)
        os.environ["OBSIDIAN_REQUIRE_FRONTMATTER"] = "false"
        os.environ["OBSIDIAN_TAG_STYLE"] = "as-is"
        return init_vault(temp_dir)

    yield _make

    for key in ("OBSIDIAN_REQUIRE_FRONTMATTER", "OBSIDIAN_TAG_STYLE"):
        os.environ.pop(key, None)
    for d in created_dirs:
        shutil.rmtree(d, ignore_errors=True)


class TestCommaTagSurvivesRealToolRoundTrip:
    """The required regression test: a comma-containing tag survives a real
    add_tags/update_tags round trip, not just the low-level helper -- this
    also exercises vault.read_note's own YAML parsing of the written file,
    which is what actually proves the write side no longer re-corrupts what
    the read-side fix just parsed correctly."""

    @pytest.mark.asyncio
    async def test_add_tags_does_not_corrupt_an_existing_comma_tag(self, make_vault):
        make_vault()
        await create_note("Note.md", '---\ntags: ["has, comma"]\n---\n\nBody\n')

        result = await add_tags("Note.md", ["other"])
        assert set(result["tags"]["after"]) == {"has, comma", "other"}

        # Re-read from disk through the real vault YAML parser -- proves the
        # file on disk is valid, correctly-quoted YAML, not just that the
        # in-memory helper got it right for one call.
        note = await get_vault().read_note("Note.md")
        assert set(note.metadata.tags) == {"has, comma", "other"}

    @pytest.mark.asyncio
    async def test_update_tags_merge_does_not_corrupt_a_comma_tag(self, make_vault):
        make_vault()
        await create_note("Note.md", '---\ntags: ["has, comma"]\n---\n\nBody\n')

        result = await update_tags("Note.md", ["third"], merge=True)
        assert set(result["tags"]["after"]) == {"has, comma", "third"}

    @pytest.mark.asyncio
    async def test_add_tags_with_a_comma_tag_on_a_note_with_no_tags_yet(
        self, make_vault
    ):
        make_vault()
        await create_note("Note.md", "---\nstatus: draft\n---\n\nBody\n")

        result = await add_tags("Note.md", ["has, comma"])
        assert result["tags"]["after"] == ["has, comma"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
