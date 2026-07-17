#!/usr/bin/env python3
"""get_note_template_tool / help_tool / add_daily_note_tool exercised
through the actual @mcp.tool()-registered wrapper in server.py — not just
the internal get_note_template/get_help/add_daily_note functions the other
test files already cover extensively. No test file in this repo imports
server.py at all; this is new ground.

Why this matters: the ValueError -> ToolError conversion (and the generic
Exception -> ToolError fallback) is logic that lives ONLY in server.py's
try/except blocks around each tool. Calling the internal function directly
never exercises that conversion at all.

server.py does `init_vault()` at import time and requires
OBSIDIAN_VAULT_PATH to already be set — the bootstrap dir below only
satisfies that one-time import check; every test still points the shared
global vault at its own fixture via init_vault(temp_dir), the same
mechanism every other test file in this suite uses.
"""

import os
import shutil
import tempfile

import pytest

# ponytail: one throwaway bootstrap dir for the whole test session, never
# rmtree'd — satisfies server.py's import-time OBSIDIAN_VAULT_PATH check
# only; every test below repoints the vault to its own tmp dir anyway.
os.environ["OBSIDIAN_VAULT_PATH"] = tempfile.mkdtemp(prefix="obsidian_server_bootstrap_")

from fastmcp.exceptions import ToolError  # noqa: E402

from obsidian_mcp.server import add_daily_note_tool, get_note_template_tool, help_tool  # noqa: E402
from obsidian_mcp.utils.filesystem import init_vault  # noqa: E402


@pytest.fixture
def vault():
    temp_dir = tempfile.mkdtemp(prefix="obsidian_server_wrappers_")
    os.environ["OBSIDIAN_REQUIRE_FRONTMATTER"] = "false"
    v = init_vault(temp_dir)
    yield v
    os.environ.pop("OBSIDIAN_REQUIRE_FRONTMATTER", None)
    os.environ.pop("OBSIDIAN_FOLDER_TEMPLATES", None)
    shutil.rmtree(temp_dir, ignore_errors=True)


class TestGetNoteTemplateToolWrapper:
    @pytest.mark.asyncio
    async def test_enforced_folder_returns_template_info(self, vault, tmp_path):
        templates_dir = vault.vault_path / "templates"
        templates_dir.mkdir()
        (templates_dir / "projeto.md").write_text("## Objetivo\n")
        (vault.vault_path / "01-projects").mkdir()
        os.environ["OBSIDIAN_FOLDER_TEMPLATES"] = (
            '[{"folder":"01-projects","template":"templates/projeto.md"}]'
        )
        init_vault(str(vault.vault_path))  # re-parse OBSIDIAN_FOLDER_TEMPLATES

        result = await get_note_template_tool.fn(path="01-projects")
        assert result["enforced"] is True
        assert result["required_headings"] == ["Objetivo"]

    @pytest.mark.asyncio
    async def test_unenforced_folder_returns_free_form(self, vault):
        result = await get_note_template_tool.fn(path="anywhere")
        assert result["enforced"] is False

    @pytest.mark.asyncio
    async def test_path_outside_vault_raises_tool_error(self, vault):
        # ValueError -> ToolError conversion only exists in server.py's
        # wrapper; calling get_note_template() directly (as
        # tools/vault_meta.py's own callers do) would surface a bare
        # ValueError instead.
        with pytest.raises(ToolError):
            await get_note_template_tool.fn(path="/etc/definitely-outside-the-vault")


class TestHelpToolWrapper:
    @pytest.mark.asyncio
    async def test_returns_all_expected_env_vars(self, vault):
        result = await help_tool.fn()
        names = {row["name"] for row in result["env_vars"]}
        expected = {
            "OBSIDIAN_VAULT_PATH",
            "OBSIDIAN_FOLDER_TEMPLATES",
            "OBSIDIAN_WIKILINK_POLICY",
            "OBSIDIAN_DAILY_DIR",
            "OBSIDIAN_MAX_NOTE_LINES",
            "OBSIDIAN_APPEND_HEADROOM_LINES",
            "OBSIDIAN_NOTE_SIZE_POLICY",
            "OBSIDIAN_TAG_STYLE",
            "OBSIDIAN_SLUG_STYLE",
            "OBSIDIAN_CACHE_STAT_TTL_SECONDS",
            "OBSIDIAN_REQUIRE_FRONTMATTER",
            "OBSIDIAN_SEARCH_RESULT_MODE",
            "OBSIDIAN_SEARCH_INDEX_THRESHOLD",
        }
        assert expected <= names

    @pytest.mark.asyncio
    async def test_current_value_reflects_actual_vault_config(self, vault):
        os.environ["OBSIDIAN_WIKILINK_POLICY"] = "strict"
        try:
            init_vault(str(vault.vault_path))
            result = await help_tool.fn()
            row = next(r for r in result["env_vars"] if r["name"] == "OBSIDIAN_WIKILINK_POLICY")
            assert row["current"] == "strict"
        finally:
            os.environ.pop("OBSIDIAN_WIKILINK_POLICY", None)

    @pytest.mark.asyncio
    async def test_tools_catalog_has_thirty_entries_including_new_ones(self, vault):
        result = await help_tool.fn()
        assert len(result["tools"]) == 30
        tool_names = {t["name"] for t in result["tools"]}
        assert {"get_note_template_tool", "help_tool", "add_daily_note_tool"} <= tool_names

    @pytest.mark.asyncio
    async def test_path_anchoring_explanation_present(self, vault):
        result = await help_tool.fn()
        assert "vault-relative" in result["path_anchoring"]


class TestAddDailyNoteToolWrapper:
    @pytest.mark.asyncio
    async def test_success_returns_expected_shape(self, vault):
        result = await add_daily_note_tool.fn(content="Entry via wrapper.")
        assert result["created"] is True
        assert result["appended"] is True
        assert "path" in result

    @pytest.mark.asyncio
    async def test_invalid_date_raises_tool_error(self, vault):
        # The internal add_daily_note raises a bare ValueError for this;
        # only server.py's wrapper converts it to ToolError.
        with pytest.raises(ToolError):
            await add_daily_note_tool.fn(content="Entry.", date="not-a-date")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
