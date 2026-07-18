#!/usr/bin/env python3
"""Regression tests for _parse_frontmatter's invalid-YAML fallback path.

Root cause: any field in the frontmatter block that yaml.safe_load can't
parse (most commonly an unquoted ':' inside a plain-scalar value, e.g. a
`description` that itself contains ": ") makes yaml.safe_load raise
YAMLError for the WHOLE block, not just that field. The fallback parser
used to treat every field's value as an opaque string, so a perfectly
valid `tags: [a, b, c]` line collapsed into a single string
'"[a, b, c]"' instead of a 3-element list — both when merely reading the
note (get_note_info/read_note) and, worse, when OBSIDIAN_TAG_STYLE=kebab
is set: normalize_frontmatter_tags_for_kebab reads that string as "one
tag", kebab-slugifies the whole bracket expression into a single combined
tag, and rewrites the note with the real tags destroyed on disk.
"""

import os
import shutil
import tempfile

import pytest
import pytest_asyncio

from obsidian_mcp.utils.filesystem import init_vault, get_vault


# A description with an unquoted ':' — this is what makes yaml.safe_load
# reject the whole frontmatter block and fall back to the naive parser.
COLON_DESCRIPTION = "Diagnóstico via Blnk: graceful degradation para falhas"


def _frontmatter_with_tags(tags_line: str | None) -> str:
    lines = ["---", "name: colon-repro", f"description: {COLON_DESCRIPTION}"]
    if tags_line is not None:
        lines.append(tags_line)
    lines += ["---", "", "# colon-repro", ""]
    return "\n".join(lines)


class TestFrontmatterFallbackArrayParsing:
    """Unit tests: ObsidianVault._parse_frontmatter directly."""

    @pytest_asyncio.fixture
    async def vault(self):
        temp_dir = tempfile.mkdtemp(prefix="obsidian_fm_fallback_")
        os.environ["OBSIDIAN_VAULT_PATH"] = temp_dir
        v = init_vault(temp_dir)
        yield v
        shutil.rmtree(temp_dir, ignore_errors=True)

    def test_fallback_triggers_on_colon_in_description(self, vault):
        """Sanity check: the fixture content genuinely breaks yaml.safe_load,
        so these tests exercise the fallback path, not the normal one."""
        import yaml

        content = _frontmatter_with_tags("tags: [alpha]")
        fm_text = content[4:content.find("\n---\n", 4)]
        with pytest.raises(yaml.YAMLError):
            yaml.safe_load(fm_text)

    def test_one_tag(self, vault):
        content = _frontmatter_with_tags("tags: [alpha]")
        frontmatter, _ = vault._parse_frontmatter(content)
        assert frontmatter["tags"] == ["alpha"]

    def test_n_tags(self, vault):
        content = _frontmatter_with_tags("tags: [alpha, beta, gamma]")
        frontmatter, _ = vault._parse_frontmatter(content)
        assert frontmatter["tags"] == ["alpha", "beta", "gamma"]

    def test_tags_with_space_and_hyphen(self, vault):
        content = _frontmatter_with_tags("tags: [foo bar, baz-qux, my-tag]")
        frontmatter, _ = vault._parse_frontmatter(content)
        assert frontmatter["tags"] == ["foo bar", "baz-qux", "my-tag"]

    def test_tags_absent(self, vault):
        content = _frontmatter_with_tags(None)
        frontmatter, _ = vault._parse_frontmatter(content)
        assert "tags" not in frontmatter

    def test_other_fields_still_parsed_as_strings(self, vault):
        """The fallback must only special-case bracketed values — a plain
        scalar field (like the colon-breaking description itself) must stay
        an ordinary string, unaffected by the array-parsing fix."""
        content = _frontmatter_with_tags("tags: [alpha]")
        frontmatter, _ = vault._parse_frontmatter(content)
        assert frontmatter["name"] == "colon-repro"
        assert frontmatter["description"] == COLON_DESCRIPTION

    def test_real_production_note_content(self, vault):
        """Exact frontmatter shape reported in the bug: real description
        text with an embedded colon, 6-element tags array."""
        content = (
            "---\n"
            "name: withdrawal-blnk-bugs\n"
            "description: Diagnóstico e fix de bugs críticos em saque via Blnk: "
            "graceful degradation para transações não encontradas e "
            "double-spend por skip_queue ausente\n"
            "type: project\n"
            "tags: [withdraw, blnk, inngest, bug, critical, ledger]\n"
            "date: 2026-06-17\n"
            "---\n\n# withdrawal-blnk-bugs\n"
        )
        frontmatter, _ = vault._parse_frontmatter(content)
        assert frontmatter["tags"] == [
            "withdraw", "blnk", "inngest", "bug", "critical", "ledger",
        ]


class TestFrontmatterFallbackEndToEnd:
    """Integration test: the write-time corruption via create_note_tool
    when OBSIDIAN_TAG_STYLE=kebab is set (matches swapo-app's real config)
    — this is the more severe manifestation, since it destroys the tags on
    disk rather than just misreporting them."""

    @pytest_asyncio.fixture
    async def kebab_vault(self):
        temp_dir = tempfile.mkdtemp(prefix="obsidian_fm_fallback_kebab_")
        os.environ["OBSIDIAN_VAULT_PATH"] = temp_dir
        os.environ["OBSIDIAN_TAG_STYLE"] = "kebab"
        os.environ["OBSIDIAN_REQUIRE_FRONTMATTER"] = "false"
        os.environ["OBSIDIAN_WIKILINK_POLICY"] = "warn"
        init_vault(temp_dir)
        yield
        os.environ.pop("OBSIDIAN_TAG_STYLE", None)
        os.environ.pop("OBSIDIAN_REQUIRE_FRONTMATTER", None)
        os.environ.pop("OBSIDIAN_WIKILINK_POLICY", None)
        shutil.rmtree(temp_dir, ignore_errors=True)

    @pytest.mark.asyncio
    async def test_create_note_does_not_collapse_tags_on_colon_description(
        self, kebab_vault
    ):
        from obsidian_mcp.tools.note_management import create_note

        content = _frontmatter_with_tags(
            "tags: [withdraw, blnk, inngest, bug, critical, ledger]"
        )
        result = await create_note("colon-repro.md", content)
        tags = result["details"]["metadata"]["tags"]
        assert sorted(tags) == [
            "blnk", "bug", "critical", "inngest", "ledger", "withdraw",
        ]

        vault = get_vault()
        raw = (vault.vault_path / "colon-repro.md").read_text(encoding="utf-8")
        tags_line = next(
            line for line in raw.splitlines() if line.startswith("tags:")
        )
        # Must stay a 6-element flow-sequence — not one combined
        # "withdraw-blnk-inngest-bug-critical-ledger" slug.
        assert tags_line.count(",") == 5


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
