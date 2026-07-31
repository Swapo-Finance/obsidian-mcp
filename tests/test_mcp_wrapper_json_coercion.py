#!/usr/bin/env python3
"""Regression tests for the MCP wrapper -> tools/ JSON-coercion contract fix,
plus get_help()'s empty-catalog guard added in the same review pass.

batch_update_properties_tool (mcp_properties.py) and find_orphaned_notes_tool
(mcp_links.py) used to coerce a JSON-string add_tags/remove_tags/
exclude_folders parameter to a list INSIDE the wrapper -- with nested
try/except and manual ValueError/ToolError raises -- violating this repo's
tool contract (obsidian_mcp/tools/CLAUDE.md: a *_tool wrapper is only the
schema + docstring + a try/except that converts to ToolError; no logic).
That coercion moved into tools/batch_properties.py's _coerce_tag_list and
tools/find_orphaned_notes.py's _coerce_exclude_folders, so the wrappers are
now pure passthroughs. These tests pin, for both tools:
  (a) a real list keeps working
  (b) a JSON-encoded list string keeps working (parsed the same as (a))
  (c) malformed JSON raises the exact same ToolError text as before the move
  (d) valid JSON that isn't a list raises the exact same ToolError text

(c) and (d) matter because of a subtlety: the pre-refactor wrapper raised
ToolError (not ValueError) directly for malformed JSON, which skipped its
own `except ValueError` branch and fell into its generic `except Exception
as e: raise ToolError(f"Failed to X: {e!s}")` fallback -- double-wrapping
the message. Moving the raise into tools/ as a plain ValueError would only
get the wrapper's single `except ValueError` wrap, silently changing the
client-visible text. The fix bakes the "Failed to X: " prefix into the
ValueError message itself in that one branch specifically to reproduce the
exact legacy string (see the docstrings on _coerce_tag_list /
_coerce_exclude_folders) -- the tests below assert that literal contract,
built the same way the production code builds it, rather than a hardcoded
guess, so they track a deliberate wording change but not an incidental one.

get_help() (tools/vault_meta.py) gained a defensive guard: if the tool list
derived from the live FastMCP registry is ever empty, it now raises instead
of silently returning an empty catalog. See TestGetHelpEmptyCatalogGuard for
why that guard (not the `..server` vs `..app` import line itself) is the
only part of this that can still be exercised by a test in this suite.

Per this repo's test conventions (see test_server_tool_wrappers.py):
OBSIDIAN_VAULT_PATH must be set to an existing directory before
obsidian_mcp.server is imported, since it raises ValueError at import time
otherwise. tests/conftest.py exists but deliberately doesn't set that var or
import obsidian_mcp.server itself (see its own module docstring).
"""

import json
import os
import shutil
import tempfile

import pytest

# ponytail: one throwaway bootstrap dir for the whole test session, never
# rmtree'd -- satisfies server.py's import-time OBSIDIAN_VAULT_PATH check
# only; every test below repoints the vault to its own tmp dir anyway.
os.environ["OBSIDIAN_VAULT_PATH"] = tempfile.mkdtemp(
    prefix="obsidian_json_coercion_bootstrap_"
)

from fastmcp.exceptions import ToolError

from obsidian_mcp.server import batch_update_properties_tool, find_orphaned_notes_tool
from obsidian_mcp.tools.note_management import create_note
from obsidian_mcp.utils.filesystem import init_vault


@pytest.fixture
def vault():
    temp_dir = tempfile.mkdtemp(prefix="obsidian_json_coercion_")
    os.environ["OBSIDIAN_REQUIRE_FRONTMATTER"] = "false"
    v = init_vault(temp_dir)
    yield v
    os.environ.pop("OBSIDIAN_REQUIRE_FRONTMATTER", None)
    shutil.rmtree(temp_dir, ignore_errors=True)


def _json_decode_error_text(bad_json: str) -> str:
    """The real message json.loads raises for BAD_JSON below, so the
    expected-message builders track the production f-string's shape (the
    contract) rather than pinning json's own wording as a hardcoded guess.
    """
    try:
        json.loads(bad_json)
    except json.JSONDecodeError as e:
        return str(e)
    raise AssertionError(f"{bad_json!r} parsed as valid JSON; fixture is broken")


BAD_JSON = "not-json{"
_BAD_JSON_ERROR_TEXT = _json_decode_error_text(BAD_JSON)


def _expected_batch_properties_json_error(param_name: str) -> str:
    """Mirrors tools/batch_properties.py's _coerce_tag_list malformed-JSON
    branch exactly (including the baked-in "Failed to batch update
    properties: " prefix -- see that function's docstring)."""
    return (
        f"Failed to batch update properties: Invalid JSON in {param_name} "
        f'parameter: {_BAD_JSON_ERROR_TEXT}. Expected format: \'["tag1", '
        '"tag2"]\' or use a list directly.'
    )


class TestBatchUpdatePropertiesTagCoercion:
    """add_tags / remove_tags on batch_update_properties_tool accept a real
    list or a JSON-encoded list string."""

    @pytest.mark.asyncio
    async def test_add_tags_real_list_is_used_as_is(self, vault):
        await create_note("Note.md", "---\ntags: [existing]\n---\n# Note\n")

        result = await batch_update_properties_tool.fn(
            search_criteria={"files": ["Note.md"]}, add_tags=["archived", "2024"]
        )

        assert result["updated"] == 1
        note = await vault.read_note("Note.md")
        assert set(note.metadata.frontmatter["tags"]) == {
            "existing",
            "archived",
            "2024",
        }

    @pytest.mark.asyncio
    async def test_add_tags_json_string_list_is_parsed_and_used(self, vault):
        await create_note("Note.md", "# Note\n")

        result = await batch_update_properties_tool.fn(
            search_criteria={"files": ["Note.md"]}, add_tags='["archived", "2024"]'
        )

        assert result["updated"] == 1
        note = await vault.read_note("Note.md")
        assert set(note.metadata.frontmatter["tags"]) == {"archived", "2024"}

    @pytest.mark.asyncio
    async def test_add_tags_malformed_json_raises_exact_legacy_message(self, vault):
        with pytest.raises(ToolError) as exc_info:
            await batch_update_properties_tool.fn(search_criteria={}, add_tags=BAD_JSON)

        assert str(exc_info.value) == _expected_batch_properties_json_error("add_tags")

    @pytest.mark.asyncio
    async def test_remove_tags_malformed_json_raises_exact_legacy_message(self, vault):
        with pytest.raises(ToolError) as exc_info:
            await batch_update_properties_tool.fn(
                search_criteria={}, remove_tags=BAD_JSON
            )

        assert str(exc_info.value) == _expected_batch_properties_json_error(
            "remove_tags"
        )

    @pytest.mark.asyncio
    async def test_add_tags_valid_json_non_list_raises_exact_legacy_message(
        self, vault
    ):
        with pytest.raises(ToolError) as exc_info:
            await batch_update_properties_tool.fn(
                search_criteria={}, add_tags='{"a": 1}'
            )

        assert (
            str(exc_info.value)
            == "add_tags must be a list when parsed from JSON string"
        )

    @pytest.mark.asyncio
    async def test_remove_tags_valid_json_non_list_raises_exact_legacy_message(
        self, vault
    ):
        with pytest.raises(ToolError) as exc_info:
            await batch_update_properties_tool.fn(
                search_criteria={}, remove_tags='{"a": 1}'
            )

        assert (
            str(exc_info.value)
            == "remove_tags must be a list when parsed from JSON string"
        )


class TestFindOrphanedNotesExcludeFoldersCoercion:
    """exclude_folders on find_orphaned_notes_tool accepts a real list or a
    JSON-encoded list string."""

    @pytest.mark.asyncio
    async def test_real_list_is_used_as_is(self, vault):
        await create_note("Templates/T.md", "# T\n")
        await create_note("Kept.md", "# Kept\n")

        result = await find_orphaned_notes_tool.fn(exclude_folders=["Templates"])

        paths = {n["path"] for n in result["orphaned_notes"]}
        assert "Kept.md" in paths
        assert "Templates/T.md" not in paths

    @pytest.mark.asyncio
    async def test_json_string_list_is_parsed_and_used(self, vault):
        await create_note("Templates/T.md", "# T\n")
        await create_note("Kept.md", "# Kept\n")

        result = await find_orphaned_notes_tool.fn(exclude_folders='["Templates"]')

        paths = {n["path"] for n in result["orphaned_notes"]}
        assert "Kept.md" in paths
        assert "Templates/T.md" not in paths

    @pytest.mark.asyncio
    async def test_malformed_json_raises_exact_legacy_message(self, vault):
        with pytest.raises(ToolError) as exc_info:
            await find_orphaned_notes_tool.fn(exclude_folders=BAD_JSON)

        assert str(exc_info.value) == (
            "Failed to find orphaned notes: Invalid JSON format for "
            'exclude_folders. Expected a JSON array like: ["Daily", "Templates"]'
        )

    @pytest.mark.asyncio
    async def test_valid_json_non_list_raises_exact_legacy_message(self, vault):
        with pytest.raises(ToolError) as exc_info:
            await find_orphaned_notes_tool.fn(exclude_folders='{"a": 1}')

        assert str(exc_info.value) == "exclude_folders must be a list"


class TestGetHelpEmptyCatalogGuard:
    """get_help()'s defensive guard: if the tool list derived from
    mcp.get_tools() is ever empty, it must raise instead of silently
    returning an empty catalog (see the guard and the warning comment on
    `from ..server import mcp` in tools/vault_meta.py's get_help()).

    Not testable as an import-line regression (repointing that import to
    ..app and confirming a test then fails): by the time ANY test in this
    suite runs, some other test module has already imported
    obsidian_mcp.server at module scope (test_server_tool_wrappers.py, this
    file above), so ..app and ..server already resolve to the exact same,
    already-fully-populated `mcp` object -- an import-line-level test would
    pass regardless of which name get_help() imports from, i.e. it would be
    vacuous (this mirrors exactly why the existing drift test can't catch
    that regression either, per the review finding that added this guard).

    Testing the guard directly, by making mcp.get_tools() return empty via
    monkeypatch, still exercises the real `if not tools: raise
    ValueError(...)` code path and fails if that guard is ever removed --
    which is the regression the guard itself defends against, independent
    of import timing.
    """

    @pytest.mark.asyncio
    async def test_raises_value_error_when_tool_registry_is_empty(
        self, vault, monkeypatch
    ):
        from obsidian_mcp.server import mcp
        from obsidian_mcp.tools.vault_meta import get_help

        async def _empty_get_tools():
            return {}

        monkeypatch.setattr(mcp, "get_tools", _empty_get_tools)

        with pytest.raises(ValueError, match="empty tool catalog"):
            await get_help()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
