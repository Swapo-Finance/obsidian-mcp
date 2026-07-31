"""Shared fixtures for the flat tests/ suite.

No sub-packages, no per-file conftest — see repo conventions (root CLAUDE.md).

This file must NOT set OBSIDIAN_VAULT_PATH or import obsidian_mcp.server:
several test files (test_server_tool_wrappers.py, test_image_tool_wrapper_bugs.py)
set OBSIDIAN_VAULT_PATH at module scope before importing obsidian_mcp.server,
because server.py raises ValueError at import time if that var is unset.
conftest.py is imported by pytest before those test modules during collection,
so anything done here at import time runs first — this file only sets a var
those modules never touch, and never imports server.py itself.
"""

import os

import pytest

import obsidian_mcp.utils.filesystem as _filesystem

# Cheap defense-in-depth, not a fix for any specific flake: default the
# fire-and-forget background reindex task off for the whole test session.
# No test asserts this value (verified), and every test that needs a warm
# index already awaits vault._update_search_index() itself.
os.environ.setdefault("OBSIDIAN_AUTO_INDEX_UPDATE", "false")


@pytest.fixture(autouse=True)
def _isolate_env_and_vault():
    """Reset process-global state leaked by tests that mutate os.environ
    directly and by obsidian_mcp.utils.filesystem's module-global `vault`.

    Individual test fixtures set OBSIDIAN_* vars via os.environ[...] = ...
    and don't all pop every var they set on teardown. And get_vault() only
    checks `vault is not None` — never liveness — so a vault left behind by
    one test file (even one whose temp dir was already rmtree'd) lets a
    later test that forgets its own init_vault() call pass by accident
    instead of failing loudly. Restoring the full env snapshot and clearing
    the vault singleton after every test closes both leaks.

    Runs only around test functions (setup/teardown), never during
    collection/import — see module docstring above for why that matters.
    """
    env_before = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(env_before)
    _filesystem.vault = None
