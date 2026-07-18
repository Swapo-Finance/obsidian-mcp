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
from obsidian_mcp.tools.vault_meta import _first_line  # noqa: E402
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

    @pytest.mark.asyncio
    async def test_tools_catalog_matches_live_registry_no_drift(self, vault):
        # get_help derives `tools` from the live FastMCP registry (server.py's
        # `mcp`) instead of a hand-maintained list. This is the test whose
        # whole point is to fail the moment someone registers/renames a tool
        # without get_help picking it up automatically — if it ever fails,
        # that drift (not a broken fixture) is the cause.
        from obsidian_mcp.server import mcp

        result = await help_tool.fn()
        help_names = {t["name"] for t in result["tools"]}
        registry_names = set((await mcp.get_tools()).keys())

        assert help_names == registry_names, (
            "help_tool's tools catalog has drifted from the live FastMCP tool "
            "registry (obsidian_mcp.server.mcp.get_tools()) - a tool was "
            "registered or renamed without get_help's derivation picking it "
            f"up. Symmetric difference: {help_names ^ registry_names}"
        )

    @pytest.mark.asyncio
    async def test_each_tool_purpose_is_a_nonempty_single_line(self, vault):
        result = await help_tool.fn()

        for entry in result["tools"]:
            assert entry["purpose"], f"{entry['name']} has an empty purpose"
            assert "\n" not in entry["purpose"], f"{entry['name']} purpose spans multiple lines"

    @pytest.mark.asyncio
    async def test_log_level_env_var_present_with_info_default(self, vault):
        result = await help_tool.fn()
        row = next(r for r in result["env_vars"] if r["name"] == "OBSIDIAN_LOG_LEVEL")

        assert row["default"] == "INFO"
        assert row["current"] == "INFO"

    @pytest.mark.asyncio
    async def test_log_level_env_var_current_tracks_env_override(self, vault, monkeypatch):
        monkeypatch.setenv("OBSIDIAN_LOG_LEVEL", "DEBUG")

        result = await help_tool.fn()
        row = next(r for r in result["env_vars"] if r["name"] == "OBSIDIAN_LOG_LEVEL")

        assert row["current"] == "DEBUG"


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


class TestContextAnnotations:
    """Structural test: verify all 30 @mcp.tool() wrappers have ctx: Optional[Context] annotation."""

    def test_all_30_tools_have_ctx_optional_context_annotation(self):
        """Import all 30 tool wrappers and verify ctx parameter has Optional[Context] type."""
        import inspect
        from typing import get_origin, get_args, Union
        from fastmcp import Context

        # Import all 30 tool wrapper functions
        from obsidian_mcp.server import (
            read_note_tool,
            create_note_tool,
            update_note_tool,
            edit_note_section_tool,
            delete_note_tool,
            search_notes_tool,
            search_by_date_tool,
            search_by_regex_tool,
            search_by_property_tool,
            list_notes_tool,
            list_folders_tool,
            move_note_tool,
            rename_note_tool,
            create_folder_tool,
            move_folder_tool,
            add_tags_tool,
            update_tags_tool,
            remove_tags_tool,
            get_note_info_tool,
            get_backlinks_tool,
            get_outgoing_links_tool,
            find_broken_links_tool,
            find_orphaned_notes_tool,
            list_tags_tool,
            batch_update_properties_tool,
            read_image_tool,
            view_note_images_tool,
            get_note_template_tool,
            help_tool,
            add_daily_note_tool,
        )

        all_tools = [
            read_note_tool,
            create_note_tool,
            update_note_tool,
            edit_note_section_tool,
            delete_note_tool,
            search_notes_tool,
            search_by_date_tool,
            search_by_regex_tool,
            search_by_property_tool,
            list_notes_tool,
            list_folders_tool,
            move_note_tool,
            rename_note_tool,
            create_folder_tool,
            move_folder_tool,
            add_tags_tool,
            update_tags_tool,
            remove_tags_tool,
            get_note_info_tool,
            get_backlinks_tool,
            get_outgoing_links_tool,
            find_broken_links_tool,
            find_orphaned_notes_tool,
            list_tags_tool,
            batch_update_properties_tool,
            read_image_tool,
            view_note_images_tool,
            get_note_template_tool,
            help_tool,
            add_daily_note_tool,
        ]

        assert len(all_tools) == 30, f"Expected 30 tools, found {len(all_tools)}"

        for tool in all_tools:
            # .fn unwraps the @mcp.tool() decorator
            sig = inspect.signature(tool.fn)
            assert "ctx" in sig.parameters, f"{tool.fn.__name__} missing 'ctx' parameter"

            ctx_param = sig.parameters["ctx"]
            annotation = ctx_param.annotation

            # Verify annotation is Optional[Context] (which is Union[Context, None])
            # Optional[X] is equivalent to Union[X, None]
            assert annotation != inspect.Parameter.empty, (
                f"{tool.fn.__name__}: ctx parameter has no type annotation. "
                f"Expected Optional[Context], got bare default value."
            )

            # Check if it's Optional[Context] or Union[Context, None]
            origin = get_origin(annotation)

            # Union[X, None] is how typing represents Optional[X]
            if origin is Union:
                args = get_args(annotation)
                # Should be (Context, type(None))
                has_context = Context in args
                has_none = type(None) in args
                assert has_context and has_none, (
                    f"{tool.fn.__name__}: ctx annotation is {annotation}, "
                    f"expected Optional[Context] (Union[Context, None])"
                )
            else:
                raise AssertionError(
                    f"{tool.fn.__name__}: ctx annotation is {annotation}, "
                    f"expected Optional[Context] (Union[Context, None])"
                )

            # Verify default value is None
            assert ctx_param.default is None, (
                f"{tool.fn.__name__}: ctx default is {ctx_param.default}, expected None"
            )


class TestFirstLineHelper:
    """_first_line (obsidian_mcp/tools/vault_meta.py): pure docstring-parsing
    helper backing get_help's per-tool `purpose` field. No vault needed."""

    def test_multiline_docstring_returns_first_nonempty_line_stripped(self):
        assert _first_line("  First line.  \nSecond line.\nThird.\n") == "First line."

    def test_leading_blank_lines_are_skipped(self):
        assert _first_line("\n   \n\nActual first line.\nMore.") == "Actual first line."

    def test_none_returns_empty_string(self):
        assert _first_line(None) == ""

    def test_empty_string_returns_empty_string(self):
        assert _first_line("") == ""


class TestTagToolsDocstringRegressionGuards:
    """Cheap pins for the P2 docstring corrections - not exhaustive docstring
    testing, just guarding the specific false claims that were removed so
    they can't silently come back. FunctionTool wrappers don't proxy
    __doc__ (it's None on the wrapper itself - confirmed via .fn), so these
    read through .fn.__doc__, same as TestContextAnnotations above."""

    def test_list_tags_tool_no_longer_claims_synthesized_hierarchy_paths(self):
        from obsidian_mcp.server import list_tags_tool

        assert 'both "project" and "project/web"' not in list_tags_tool.fn.__doc__

    def test_remove_tags_tool_no_longer_claims_count_of_removed(self):
        from obsidian_mcp.server import remove_tags_tool

        assert "count of removed" not in remove_tags_tool.fn.__doc__

    def test_add_update_remove_tags_examples_mention_real_response_keys(self):
        from obsidian_mcp.tools.organization import add_tags, remove_tags, update_tags

        for fn in (add_tags, update_tags, remove_tags):
            assert "changes" in fn.__doc__, f"{fn.__name__} docstring example missing 'changes'"
            assert "before" in fn.__doc__, f"{fn.__name__} docstring example missing 'before'"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
