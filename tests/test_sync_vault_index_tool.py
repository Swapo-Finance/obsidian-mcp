#!/usr/bin/env python3
"""sync_vault_index_tool through the registered @mcp.tool() wrapper, plus the
server instructions that tell agents when to call it.

server.py raises at import time without OBSIDIAN_VAULT_PATH; the bootstrap
dir below only satisfies that check -- every test repoints the vault with
init_vault(temp_dir), same as test_server_tool_wrappers.py.
"""

import os
import shutil
import tempfile

import pytest
import pytest_asyncio

os.environ["OBSIDIAN_VAULT_PATH"] = tempfile.mkdtemp(
    prefix="obsidian_sync_tool_bootstrap_"
)

from fastmcp.exceptions import ToolError

from obsidian_mcp.app import _build_instructions
from obsidian_mcp.server import mcp, search_notes_tool, sync_vault_index_tool
from obsidian_mcp.utils.filesystem import init_vault


@pytest_asyncio.fixture
async def vault():
    temp_dir = tempfile.mkdtemp(prefix="obsidian_sync_tool_")
    os.environ["OBSIDIAN_REQUIRE_FRONTMATTER"] = "false"
    v = init_vault(temp_dir)
    yield v
    if v.persistent_index:
        await v.persistent_index.close()
    shutil.rmtree(temp_dir, ignore_errors=True)


class TestSyncVaultIndexTool:
    @pytest.mark.asyncio
    async def test_outside_edit_is_searchable_after_the_tool(self, vault):
        (vault.vault_path / "seed.md").write_text("seed")
        await search_notes_tool.fn(query="seed")  # builds the index

        (vault.vault_path / "outside.md").write_text("capybara facts")
        result = await sync_vault_index_tool.fn()

        assert result["success"] is True
        assert (result["added"], result["removed"], result["full"]) == (1, 0, False)
        found = await search_notes_tool.fn(query="capybara")
        assert [r["path"] for r in found["results"]] == ["outside.md"]

    @pytest.mark.asyncio
    async def test_full_reindexes_every_note(self, vault):
        (vault.vault_path / "a.md").write_text("a")
        (vault.vault_path / "b.md").write_text("b")
        await sync_vault_index_tool.fn()

        result = await sync_vault_index_tool.fn(full=True)

        assert (result["scanned"], result["updated"], result["full"]) == (2, 2, True)

    @pytest.mark.asyncio
    async def test_unreachable_vault_becomes_tool_error(self, vault, monkeypatch):
        monkeypatch.setattr(vault, "vault_path", vault.vault_path / "unmounted")

        with pytest.raises(ToolError, match="not reachable"):
            await sync_vault_index_tool.fn()


def _instructions(
    vault, monkeypatch, *, auto: bool, interval: int = 300, ttl: int = 30
) -> str:
    """The server instructions for one explicit configuration: every input is
    pinned, so no OBSIDIAN_* value in the environment changes the text."""
    monkeypatch.setattr(vault, "_auto_index_update", auto)
    monkeypatch.setattr(vault, "_index_update_interval", interval)
    monkeypatch.setattr(vault, "cache_stat_ttl_seconds", ttl)
    return _build_instructions(vault)


class TestServerInstructions:
    def test_registered_instructions_name_the_sync_tool(self):
        assert "sync_vault_index_tool" in (mcp.instructions or "")

    @pytest.mark.asyncio
    async def test_automatic_mode_states_the_recheck_interval(self, vault, monkeypatch):
        text = _instructions(vault, monkeypatch, auto=True)

        assert "at most every 300 seconds" in text
        assert "call sync_vault_index_tool once and wait for its result" in text

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("interval", "ttl", "expected"),
        [
            (10, 45, "at most every 45 seconds"),  # the slower of the two rechecks
            (0, 0, "before every query"),
        ],
    )
    async def test_automatic_mode_bound_is_the_slower_recheck(
        self, vault, monkeypatch, interval, ttl, expected
    ):
        text = _instructions(vault, monkeypatch, auto=True, interval=interval, ttl=ttl)

        assert expected in text
        assert "at most every 0 seconds" not in text

    @pytest.mark.asyncio
    async def test_manual_mode_makes_the_call_mandatory_for_search_only(
        self, vault, monkeypatch
    ):
        text = _instructions(vault, monkeypatch, auto=False)

        assert "you MUST call sync_vault_index_tool" in text
        assert "text, regex, or property search results" in text
        assert "within 30 seconds" in text  # tag, link, and name results

    @pytest.mark.asyncio
    async def test_manual_mode_with_no_cache_ttl_rechecks_before_every_query(
        self, vault, monkeypatch
    ):
        text = _instructions(vault, monkeypatch, auto=False, ttl=0)

        assert "before every query" in text
        assert "within 0 seconds" not in text
