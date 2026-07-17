#!/usr/bin/env python3
"""add_daily_note (spec section 5's add_daily_note_tool, mechanics in
tools/daily_notes.py): template seeding on first creation, append-not-
overwrite on repeat calls, per-call date rotation, NFC normalization, and
that wikilink validation still applies. The note-size exemption is already
covered by test_note_size_policy_writes.py's
TestDailyNotesAlwaysExemptFromSizePolicy and OBSIDIAN_REQUIRE_FRONTMATTER's
auto-seeding is already covered by test_onda2_sanity.py's
TestAddDailyNoteWithRequireFrontmatter — neither is repeated here, except
where this file's own template-seeding scenario also needs to observe how
the two features compose (frontmatter auto-seeded ON TOP of a template
skeleton, preserving the template's own keys).
"""

import os
import shutil
import tempfile
import unicodedata
from pathlib import Path

import pytest

from obsidian_mcp.tools.daily_notes import add_daily_note
from obsidian_mcp.tools.note_management import read_note
from obsidian_mcp.utils.filesystem import init_vault


@pytest.fixture
def make_vault():
    created_dirs = []

    def _make(**env_overrides):
        temp_dir = tempfile.mkdtemp(prefix="obsidian_daily_")
        created_dirs.append(temp_dir)
        os.environ.setdefault("OBSIDIAN_REQUIRE_FRONTMATTER", "false")
        for key, value in env_overrides.items():
            os.environ[key] = value
        return init_vault(temp_dir)

    yield _make

    for key in (
        "OBSIDIAN_REQUIRE_FRONTMATTER",
        "OBSIDIAN_FOLDER_TEMPLATES",
        "OBSIDIAN_DAILY_DIR",
        "OBSIDIAN_WIKILINK_POLICY",
    ):
        os.environ.pop(key, None)
    for d in created_dirs:
        shutil.rmtree(d, ignore_errors=True)


class TestCreateWithoutTemplate:
    @pytest.mark.asyncio
    async def test_default_skeleton_is_date_heading(self, make_vault):
        make_vault()
        result = await add_daily_note("First entry of the day.")
        assert result["created"] is True

        note = await read_note(result["path"])
        content = note["details"]["content"]
        expected_date = result["path"].rsplit("/", 1)[-1][:-3]
        assert content.startswith(f"# {expected_date}")
        assert "First entry of the day." in content


class TestCreateWithTemplate:
    @pytest.mark.asyncio
    async def test_daily_file_created_from_daily_dir_template(self, make_vault):
        # Built directly (not via the make_vault factory) so a templates/
        # dir can be seeded on disk before OBSIDIAN_FOLDER_TEMPLATES is
        # parsed at init_vault() time.
        temp_dir = tempfile.mkdtemp(prefix="obsidian_daily_tmpl_")
        try:
            templates_dir = Path(temp_dir) / "templates"
            templates_dir.mkdir()
            (templates_dir / "daily.md").write_text("---\nmood: \n---\n\n## Log\n")

            os.environ["OBSIDIAN_REQUIRE_FRONTMATTER"] = "true"
            os.environ["OBSIDIAN_FOLDER_TEMPLATES"] = (
                '[{"folder":"daily","template":"templates/daily.md"}]'
            )
            init_vault(temp_dir)

            result = await add_daily_note("First entry.")
            assert result["created"] is True

            note = await read_note(result["path"])
            content = note["details"]["content"]
            frontmatter = note["details"]["metadata"]["frontmatter"]

            assert "## Log" in content
            assert "First entry." in content
            # Template's own key preserved...
            assert "mood" in frontmatter
            # ...and REQUIRE_FRONTMATTER's auto-seed applied on top.
            expected_date = result["path"].rsplit("/", 1)[-1][:-3]
            assert frontmatter["name"] == expected_date
            assert frontmatter["description"]  # non-empty, auto-generated
        finally:
            os.environ.pop("OBSIDIAN_FOLDER_TEMPLATES", None)
            os.environ.pop("OBSIDIAN_REQUIRE_FRONTMATTER", None)
            shutil.rmtree(temp_dir, ignore_errors=True)


class TestAppendOnSecondCall:
    @pytest.mark.asyncio
    async def test_second_call_same_day_appends_not_overwrites(self, make_vault):
        make_vault()
        first = await add_daily_note("Morning entry.")
        assert first["created"] is True

        second = await add_daily_note("Afternoon entry.")
        assert second["created"] is False
        assert second["path"] == first["path"]

        note = await read_note(second["path"])
        content = note["details"]["content"]
        assert "Morning entry." in content
        assert "Afternoon entry." in content
        # Original content preserved, not clobbered.
        assert content.index("Morning entry.") < content.index("Afternoon entry.")


class TestDateOverrideRotation:
    @pytest.mark.asyncio
    async def test_override_date_creates_separate_file_from_today(self, make_vault):
        make_vault()
        override = await add_daily_note("Backfilled entry.", date="2020-01-01")
        today = await add_daily_note("Today's entry.")

        assert override["path"] != today["path"]
        assert override["path"].endswith("2020-01-01.md")

        override_note = await read_note(override["path"])
        today_note = await read_note(today["path"])
        assert "Backfilled entry." in override_note["details"]["content"]
        assert "Backfilled entry." not in today_note["details"]["content"]
        assert "Today's entry." in today_note["details"]["content"]
        assert "Today's entry." not in override_note["details"]["content"]

    @pytest.mark.asyncio
    async def test_repeated_override_date_appends_to_same_backfilled_file(self, make_vault):
        make_vault()
        first = await add_daily_note("Entry A.", date="2020-01-01")
        second = await add_daily_note("Entry B.", date="2020-01-01")

        assert first["path"] == second["path"]
        assert second["created"] is False

        note = await read_note(second["path"])
        assert "Entry A." in note["details"]["content"]
        assert "Entry B." in note["details"]["content"]

    @pytest.mark.asyncio
    async def test_invalid_date_format_raises_actionable_error(self, make_vault):
        make_vault()
        with pytest.raises(ValueError, match="Invalid date"):
            await add_daily_note("Some content.", date="not-a-real-date")


class TestNfcNormalization:
    @pytest.mark.asyncio
    async def test_nfd_decomposed_content_is_normalized_to_nfc(self, make_vault):
        make_vault()
        decomposed = unicodedata.normalize("NFD", "café")
        assert decomposed != "café"  # sanity: genuinely different code points

        result = await add_daily_note(f"Nota sobre {decomposed} hoje.")
        note = await read_note(result["path"])
        content = note["details"]["content"]

        assert "café" in content
        assert decomposed not in content


class TestWikilinkValidationStillApplies:
    @pytest.mark.asyncio
    async def test_broken_link_raises_under_strict_policy(self, make_vault):
        make_vault(OBSIDIAN_WIKILINK_POLICY="strict")
        with pytest.raises(ValueError, match="Broken wikilink target"):
            await add_daily_note("Link to [[Nonexistent Note]].")

    @pytest.mark.asyncio
    async def test_broken_link_written_with_warning_under_warn_policy(self, make_vault):
        make_vault(OBSIDIAN_WIKILINK_POLICY="warn")
        result = await add_daily_note("Link to [[Nonexistent Note]].")
        assert any("Nonexistent Note" in w for w in result["warnings"])


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
