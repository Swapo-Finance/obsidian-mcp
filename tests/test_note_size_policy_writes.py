#!/usr/bin/env python3
"""Note-size policy (spec section 1: OBSIDIAN_MAX_NOTE_LINES /
_APPEND_HEADROOM_LINES / _NOTE_SIZE_POLICY) wired into the write path — no
prior test coverage existed for this at all. Covers strict/warn/off,
the dynamic incremental ceiling (MAX - HEADROOM for append/edit_note_section
vs. MAX directly for create/replace), and the hard daily-note exemption.

REQUIRE_FRONTMATTER is off for this module so content bodies can be
authored as plain line counts without frontmatter noise.
"""

import os
import shutil
import tempfile

import pytest

from obsidian_mcp.tools.daily_notes import add_daily_note
from obsidian_mcp.tools.note_management import create_note, edit_note_section, read_note, update_note
from obsidian_mcp.utils.filesystem import init_vault


def _lines(n: int) -> str:
    """Content with exactly n lines (count_lines semantics: newlines + 1)."""
    return "\n".join(f"L{i}" for i in range(n))


@pytest.fixture
def make_vault():
    created_dirs = []

    def _make(max_lines=None, headroom=None, policy=None, daily_dir=None):
        temp_dir = tempfile.mkdtemp(prefix="obsidian_sizepolicy_")
        created_dirs.append(temp_dir)
        os.environ["OBSIDIAN_REQUIRE_FRONTMATTER"] = "false"
        if max_lines is not None:
            os.environ["OBSIDIAN_MAX_NOTE_LINES"] = str(max_lines)
        if headroom is not None:
            os.environ["OBSIDIAN_APPEND_HEADROOM_LINES"] = str(headroom)
        if policy is not None:
            os.environ["OBSIDIAN_NOTE_SIZE_POLICY"] = policy
        if daily_dir is not None:
            os.environ["OBSIDIAN_DAILY_DIR"] = daily_dir
        return init_vault(temp_dir)

    yield _make

    for key in (
        "OBSIDIAN_REQUIRE_FRONTMATTER",
        "OBSIDIAN_MAX_NOTE_LINES",
        "OBSIDIAN_APPEND_HEADROOM_LINES",
        "OBSIDIAN_NOTE_SIZE_POLICY",
        "OBSIDIAN_DAILY_DIR",
    ):
        os.environ.pop(key, None)
    for d in created_dirs:
        shutil.rmtree(d, ignore_errors=True)


class TestFullWriteCeilingIsMaxDirectly:
    """create_note / update_note(replace) are checked against MAX_NOTE_LINES
    directly (the whole note is being (re)written in one shot)."""

    @pytest.mark.asyncio
    async def test_exactly_at_max_succeeds_under_strict(self, make_vault):
        make_vault(max_lines=10, policy="strict")
        result = await create_note("Note.md", _lines(10))
        assert result["success"] is True
        assert "warnings" not in result

    @pytest.mark.asyncio
    async def test_over_max_raises_under_strict(self, make_vault):
        make_vault(max_lines=10, policy="strict")
        with pytest.raises(ValueError, match="OBSIDIAN_MAX_NOTE_LINES"):
            await create_note("Note.md", _lines(11))

    @pytest.mark.asyncio
    async def test_note_not_written_on_strict_violation(self, make_vault):
        make_vault(max_lines=10, policy="strict")
        with pytest.raises(ValueError):
            await create_note("Note.md", _lines(11))
        with pytest.raises(FileNotFoundError):
            await read_note("Note.md")

    @pytest.mark.asyncio
    async def test_over_max_written_with_warning_under_warn(self, make_vault):
        make_vault(max_lines=10, policy="warn")
        result = await create_note("Note.md", _lines(11))
        assert result["success"] is True
        assert any("11 lines" in w for w in result["warnings"])

    @pytest.mark.asyncio
    async def test_over_max_written_silently_under_off(self, make_vault):
        make_vault(max_lines=10, policy="off")
        result = await create_note("Note.md", _lines(11))
        assert result["success"] is True
        assert "warnings" not in result

    @pytest.mark.asyncio
    async def test_replace_also_checked_against_max_directly(self, make_vault):
        make_vault(max_lines=10, policy="strict")
        await create_note("Note.md", _lines(5))
        with pytest.raises(ValueError, match="OBSIDIAN_MAX_NOTE_LINES"):
            await update_note("Note.md", _lines(11), merge_strategy="replace")


class TestIncrementalCeilingIsMaxMinusHeadroom:
    """update_note(append) / edit_note_section are checked against the
    lower MAX - HEADROOM ceiling (early-warning margin)."""

    @pytest.mark.asyncio
    async def test_append_within_ceiling_succeeds_no_warning(self, make_vault):
        # MAX=20, HEADROOM=5 -> incremental ceiling=15.
        make_vault(max_lines=20, headroom=5, policy="strict")
        await create_note("Note.md", "Base")  # 1 line
        # base(1) + blank-line-join(+1) + fragment(F) => total = F + 2.
        # Target total 15 -> F = 13.
        result = await update_note("Note.md", _lines(13), merge_strategy="append")
        assert result["success"] is True
        assert "warnings" not in result

    @pytest.mark.asyncio
    async def test_append_over_ceiling_raises_under_strict(self, make_vault):
        make_vault(max_lines=20, headroom=5, policy="strict")
        await create_note("Note.md", "Base")
        # Target total 16 (over the 15 ceiling) -> F = 14.
        with pytest.raises(ValueError, match="append ceiling"):
            await update_note("Note.md", _lines(14), merge_strategy="append")

    @pytest.mark.asyncio
    async def test_append_over_ceiling_preserves_original_content_under_strict(self, make_vault):
        make_vault(max_lines=20, headroom=5, policy="strict")
        await create_note("Note.md", "Base")
        with pytest.raises(ValueError):
            await update_note("Note.md", _lines(14), merge_strategy="append")
        note = await read_note("Note.md")
        assert note["details"]["content"] == "Base"

    @pytest.mark.asyncio
    async def test_append_over_ceiling_written_with_warning_under_warn(self, make_vault):
        make_vault(max_lines=20, headroom=5, policy="warn")
        await create_note("Note.md", "Base")
        result = await update_note("Note.md", _lines(14), merge_strategy="append")
        assert result["success"] is True
        assert any("append ceiling" in w for w in result["warnings"])

    @pytest.mark.asyncio
    async def test_edit_note_section_over_ceiling_raises_under_strict(self, make_vault):
        make_vault(max_lines=10, headroom=3, policy="strict")  # ceiling = 7
        await create_note("Note.md", "## Notes\n\nshort")
        with pytest.raises(ValueError, match="append ceiling"):
            await edit_note_section(
                "Note.md", "## Notes", _lines(10), operation="append_to_section"
            )


class TestDailyNotesAlwaysExemptFromSizePolicy:
    """spec section 1's hard rule: a note under OBSIDIAN_DAILY_DIR is never
    subject to MAX_NOTE_LINES / APPEND_HEADROOM_LINES, regardless of policy."""

    @pytest.mark.asyncio
    async def test_create_note_directly_under_daily_dir_is_exempt(self, make_vault):
        make_vault(max_lines=3, policy="strict", daily_dir="daily")
        result = await create_note("daily/2020-01-01.md", _lines(50))
        assert result["success"] is True
        assert "warnings" not in result

    @pytest.mark.asyncio
    async def test_add_daily_note_tool_is_exempt_even_appended_repeatedly(self, make_vault):
        make_vault(max_lines=3, policy="strict", daily_dir="daily")
        first = await add_daily_note(_lines(50))
        assert "warnings" not in first
        second = await add_daily_note(_lines(50))
        assert "warnings" not in second

        note = await read_note(second["path"])
        # Both huge fragments actually landed — exemption isn't silently
        # truncating content, it's just skipping the size check entirely.
        assert note["details"]["content"].count("L49") == 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
