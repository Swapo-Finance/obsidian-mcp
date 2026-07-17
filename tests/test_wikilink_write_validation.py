#!/usr/bin/env python3
"""Write-time wikilink validation (spec section 4): OBSIDIAN_WIKILINK_POLICY
strict/warn/off; malformed-format errors that always raise regardless of
policy; embeds and code-fenced/inline links ignored; fuzzy suggestions in
strict-mode errors; and OBSIDIAN_SLUG_STYLE=kebab-aware target resolution
that rewrites a link to the real (accented) filename. None of this had any
test coverage before this pass — the sanity suite didn't touch section 4 at
all.

REQUIRE_FRONTMATTER is turned off for this whole module: wikilink validation
is orthogonal to the frontmatter-requirement feature, and coupling the two
in every test body would only obscure what's being verified.
"""

import os
import shutil
import tempfile

import pytest

from obsidian_mcp.tools.link_management import validate_wikilinks_for_write
from obsidian_mcp.tools.note_management import create_note, read_note
from obsidian_mcp.utils.filesystem import init_vault


@pytest.fixture
def make_vault():
    """Factory fixture: env vars that ObsidianVault.__init__ reads (policy,
    slug style) must be set BEFORE construction — a fixed pre-built vault
    instance can't have its policy changed after the fact by a later
    os.environ write, since __init__ only reads env vars once."""
    created_dirs = []

    def _make(policy="warn", slug_style="as-is"):
        temp_dir = tempfile.mkdtemp(prefix="obsidian_wikilink_")
        created_dirs.append(temp_dir)
        os.environ["OBSIDIAN_REQUIRE_FRONTMATTER"] = "false"
        os.environ["OBSIDIAN_WIKILINK_POLICY"] = policy
        os.environ["OBSIDIAN_SLUG_STYLE"] = slug_style
        return init_vault(temp_dir)

    yield _make

    os.environ.pop("OBSIDIAN_REQUIRE_FRONTMATTER", None)
    os.environ.pop("OBSIDIAN_WIKILINK_POLICY", None)
    os.environ.pop("OBSIDIAN_SLUG_STYLE", None)
    for d in created_dirs:
        shutil.rmtree(d, ignore_errors=True)


class TestMalformedWikilinksAlwaysRaise:
    """Format errors are validated independent of OBSIDIAN_WIKILINK_POLICY —
    even 'off' rejects them."""

    @pytest.mark.asyncio
    async def test_empty_target_raises_under_off_policy(self, make_vault):
        make_vault(policy="off")
        with pytest.raises(ValueError, match="empty target"):
            await create_note("Note.md", "See [[]] here.")

    @pytest.mark.asyncio
    async def test_nested_brackets_raise_under_off_policy(self, make_vault):
        make_vault(policy="off")
        with pytest.raises(ValueError, match="nested or unbalanced"):
            await create_note("Note.md", "See [[a[[b]]c]] here.")

    @pytest.mark.asyncio
    async def test_same_note_heading_reference_is_always_valid(self, make_vault):
        make_vault(policy="strict")
        # [[#Heading]] has no note name — never treated as a broken link,
        # even in strict mode with an empty vault.
        result = await create_note("Note.md", "See [[#Some Heading]] below.")
        assert result["success"] is True


class TestWikilinkPolicyOff:
    @pytest.mark.asyncio
    async def test_broken_target_written_silently(self, make_vault):
        make_vault(policy="off")
        result = await create_note("Note.md", "Link to [[Nonexistent]].")
        assert result["success"] is True
        assert "warnings" not in result

    @pytest.mark.asyncio
    async def test_content_unchanged_when_target_missing(self, make_vault):
        make_vault(policy="off")
        await create_note("Note.md", "Link to [[Nonexistent]].")
        note = await read_note("Note.md")
        assert "[[Nonexistent]]" in note["details"]["content"]


class TestWikilinkPolicyWarn:
    @pytest.mark.asyncio
    async def test_broken_target_written_with_warning(self, make_vault):
        make_vault(policy="warn")
        result = await create_note("Note.md", "Link to [[Nonexistent]].")
        assert result["success"] is True
        assert any("Nonexistent" in w for w in result["warnings"])

    @pytest.mark.asyncio
    async def test_multiple_broken_targets_produce_one_warning_each(self, make_vault):
        make_vault(policy="warn")
        result = await create_note(
            "Note.md", "Links: [[Missing One]] and [[Missing Two]]."
        )
        assert len(result["warnings"]) == 2

    @pytest.mark.asyncio
    async def test_valid_target_produces_no_warning(self, make_vault):
        make_vault(policy="warn")
        await create_note("Target.md", "# Target\n")
        result = await create_note("Note.md", "Link to [[Target]].")
        assert "warnings" not in result


class TestWikilinkPolicyStrict:
    @pytest.mark.asyncio
    async def test_broken_target_raises(self, make_vault):
        make_vault(policy="strict")
        with pytest.raises(ValueError, match="Broken wikilink target"):
            await create_note("Note.md", "Link to [[Nonexistent]].")

    @pytest.mark.asyncio
    async def test_note_not_written_on_strict_violation(self, make_vault):
        make_vault(policy="strict")
        with pytest.raises(ValueError):
            await create_note("Note.md", "Link to [[Nonexistent]].")
        with pytest.raises(FileNotFoundError):
            await read_note("Note.md")

    @pytest.mark.asyncio
    async def test_valid_target_succeeds(self, make_vault):
        make_vault(policy="strict")
        await create_note("Target.md", "# Target\n")
        result = await create_note("Note.md", "Link to [[Target]].")
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_only_note_part_of_heading_ref_is_validated(self, make_vault):
        make_vault(policy="strict")
        await create_note("Target.md", "# Target\n## Some Section\n")
        # [[Target#Some Section]] must resolve via "Target" only — the
        # heading itself is never checked (spec section 4).
        result = await create_note("Note.md", "See [[Target#Some Section]].")
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_broken_note_part_of_heading_ref_raises(self, make_vault):
        make_vault(policy="strict")
        with pytest.raises(ValueError, match=r"\[\[Ghost\]\]"):
            await create_note("Note.md", "See [[Ghost#Some Section]].")

    @pytest.mark.asyncio
    async def test_fuzzy_suggestion_included_when_similar_note_exists(self, make_vault):
        make_vault(policy="strict")
        await create_note("Project Alpha.md", "# Project Alpha\n")
        with pytest.raises(ValueError) as excinfo:
            await create_note("Note.md", "See [[Alpha]].")
        message = str(excinfo.value)
        assert "Suggestions:" in message
        assert "Project Alpha" in message

    @pytest.mark.asyncio
    async def test_no_suggestions_section_when_nothing_similar(self, make_vault):
        make_vault(policy="strict")
        await create_note("Completely Unrelated.md", "# Unrelated\n")
        with pytest.raises(ValueError) as excinfo:
            await create_note("Note.md", "See [[Zzyzx Nonexistent Thing]].")
        assert "Suggestions:" not in str(excinfo.value)


class TestEmbedsAndCodeAreIgnored:
    """Extraction excludes embeds (![[...]]) and code (fenced/inline) —
    write-time validation never treats those as real links."""

    @pytest.mark.asyncio
    async def test_wiki_embed_of_missing_file_ignored_under_strict(self, make_vault):
        make_vault(policy="strict")
        result = await create_note("Note.md", "![[nonexistent-image.png]]")
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_broken_link_inside_fenced_code_block_ignored_under_strict(self, make_vault):
        make_vault(policy="strict")
        content = "Some text.\n\n```\n[[Totally Broken]]\n```\n"
        result = await create_note("Note.md", content)
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_broken_link_inside_inline_code_ignored_under_strict(self, make_vault):
        make_vault(policy="strict")
        content = "Reference the syntax `[[Totally Broken]]` in prose."
        result = await create_note("Note.md", content)
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_real_link_outside_code_still_validated_under_strict(self, make_vault):
        make_vault(policy="strict")
        content = "Inline `[[Ignored]]` but also a real [[Also Broken]] link."
        with pytest.raises(ValueError, match="Also Broken"):
            await create_note("Note.md", content)


class TestKebabAwareWikilinkResolution:
    """OBSIDIAN_SLUG_STYLE=kebab: a target that doesn't match directly but
    whose kebab-slug matches an existing (e.g. accented) note is rewritten
    to point at the note's real filename, alias-preserving the original
    text so the note isn't reported broken."""

    @pytest.mark.asyncio
    async def test_ascii_target_resolves_to_accented_filename(self, make_vault):
        vault = make_vault(policy="strict", slug_style="kebab")
        # Written directly to disk to simulate a note that predates the
        # kebab slug config (e.g. created via the Obsidian app).
        (vault.vault_path / "Café Especial.md").write_text("# Café Especial\n")

        result = await create_note("Note.md", "See [[Cafe Especial]] for details.")
        assert result["success"] is True

        note = await read_note("Note.md")
        assert "[[Café Especial|Cafe Especial]]" in note["details"]["content"]

    @pytest.mark.asyncio
    async def test_non_kebab_matching_target_still_reports_broken(self, make_vault):
        vault = make_vault(policy="strict", slug_style="kebab")
        (vault.vault_path / "Café Especial.md").write_text("# Café Especial\n")

        with pytest.raises(ValueError, match="Broken wikilink target"):
            await create_note("Note.md", "See [[Completely Different]] for details.")


class TestValidateWikilinksForWriteDirect:
    """Direct unit coverage of the pure validation function, for the
    no-links-present short-circuit."""

    @pytest.mark.asyncio
    async def test_no_wikilinks_in_content_returns_unchanged_no_warnings(self, make_vault):
        vault = make_vault(policy="warn")
        content = "Just plain prose, no links at all."
        new_content, warnings = await validate_wikilinks_for_write(vault, content)
        assert new_content == content
        assert warnings == []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
