"""read_note paging (offset/max_chars), outline, and section reads, plus the
OBSIDIAN_READ_MAX_CHARS knob that sets the default page size.

A long note used to come back whole. A 63,544-character note made Claude Code
save the result to a file. See
docs/superpowers/specs/2026-10-14-read-note-pagination-design.md.
"""

import pytest

from obsidian_mcp.utils.filesystem import init_vault


class TestReadMaxCharsEnv:
    def test_defaults_are_40000(self, tmp_path, monkeypatch):
        monkeypatch.delenv("OBSIDIAN_READ_MAX_CHARS", raising=False)
        monkeypatch.delenv("OBSIDIAN_MAX_NOTE_CHARS", raising=False)

        vault = init_vault(str(tmp_path))

        assert vault.read_max_chars == 40000
        assert vault.max_note_chars == 40000

    @pytest.mark.parametrize("raw", ["1000", "5000", "1000000"])
    def test_value_in_range_is_used(self, tmp_path, monkeypatch, raw):
        monkeypatch.setenv("OBSIDIAN_READ_MAX_CHARS", raw)

        assert init_vault(str(tmp_path)).read_max_chars == int(raw)

    @pytest.mark.parametrize("raw", ["999", "1000001"])
    def test_value_out_of_range_falls_back_to_default(self, tmp_path, monkeypatch, raw):
        monkeypatch.setenv("OBSIDIAN_READ_MAX_CHARS", raw)

        assert init_vault(str(tmp_path)).read_max_chars == 40000
