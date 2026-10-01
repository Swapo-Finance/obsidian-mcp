"""Shared fixtures and helpers for the search index sync tests.

Not a test module (pytest collects only test_*.py): test_index_sync.py and
test_index_sync_writes.py import from here. It holds the vault-mode fixtures
-- auto_vault (automatic mode, default 300 s interval), always_vault (interval
0: re-check before every query) and manual_vault (built on first use, then only
on demand) -- plus small helpers: _write (edit the vault behind the server's
back), _age_last_pass (pretend the interval elapsed) and _paths (paths of the
search hits).
"""

import os
import shutil
import tempfile

import pytest_asyncio

from obsidian_mcp.utils.filesystem import init_vault


def _new_vault(auto_update: str, interval: str = "300"):
    temp_dir = tempfile.mkdtemp(prefix="obsidian_index_sync_")
    os.environ["OBSIDIAN_REQUIRE_FRONTMATTER"] = "false"
    os.environ["OBSIDIAN_AUTO_INDEX_UPDATE"] = auto_update
    os.environ["OBSIDIAN_INDEX_UPDATE_INTERVAL"] = interval
    return init_vault(temp_dir)


async def _dispose(v) -> None:
    if v.persistent_index:
        await v.persistent_index.close()
    shutil.rmtree(v.vault_path, ignore_errors=True)


@pytest_asyncio.fixture
async def auto_vault():
    """Automatic mode with the default 300 s interval."""
    v = _new_vault("true")
    yield v
    await _dispose(v)


@pytest_asyncio.fixture
async def always_vault():
    """Interval 0: re-check the disk before every query."""
    v = _new_vault("true", interval="0")
    yield v
    await _dispose(v)


@pytest_asyncio.fixture
async def manual_vault():
    """OBSIDIAN_AUTO_INDEX_UPDATE=false: built on first use, then on demand."""
    v = _new_vault("false")
    yield v
    await _dispose(v)


def _write(vault, relpath: str, content: str) -> None:
    """Edit the vault behind the server's back (no MCP tool involved)."""
    path = vault.vault_path / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _age_last_pass(vault) -> None:
    """Pretend the interval elapsed (white-box, same approach as
    test_vault_cache_freshness.py) instead of sleeping 300 s."""
    vault._index_timestamp -= vault._index_update_interval + 1


async def _paths(vault, query: str) -> set[str]:
    return {r["path"] for r in await vault.search_notes(query)}
