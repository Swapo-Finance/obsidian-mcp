"""End-to-end regression test: ctx.info notifications are awaited and delivered.

Before this fix, obsidian_mcp/tools/*.py called `ctx.info(...)` without `await`.
Context methods are coroutines, so an un-awaited call just creates an orphaned
coroutine object: the log notification is silently never sent to the client, and
depending on GC timing a `RuntimeWarning: coroutine ... was never awaited` may
surface.

This test drives the real MCP server (`obsidian_mcp.server.mcp`) through
FastMCP's in-memory `Client` transport (no subprocess/stdio needed) and asserts
the notification is genuinely delivered to a registered `log_handler` - not just
"no warning was raised".

Note on the warning check: in fastmcp==2.12.0 / mcp's lowlevel server, every
inbound message is handled inside the library's own `warnings.catch_warnings()`
block, which captures any warnings (including "was never awaited") and
re-emits them via `logger.info(...)` instead of leaving them on Python's
warnings registry (see `mcp.server.lowlevel.server.Server._handle_message`).
That means a `warnings.catch_warnings()` wrapped around the Client call never
observes it - `caplog` does, because the redirect goes through standard
`logging`. Confirmed empirically: reverting the `await` on
`read_note_tool` -> `read_note`'s `ctx.info(...)` call makes this test fail on
both assertions (log_handler receives nothing, and caplog captures the
"was never awaited" line).
"""
import os
import shutil
import tempfile

import pytest
import pytest_asyncio
from fastmcp import Client

from obsidian_mcp.server import mcp
from obsidian_mcp.utils.filesystem import init_vault


@pytest_asyncio.fixture
async def vault_with_note():
    """Temp vault (OBSIDIAN_VAULT_PATH) containing one note to read.

    `obsidian_mcp.server` calls `init_vault()` once at import time, bound to
    whatever OBSIDIAN_VAULT_PATH was set then. Setting the env var here would
    not repoint the already-constructed global vault singleton, so re-init it
    explicitly (same pattern as tests/test_filesystem_integration.py).
    """
    temp_dir = tempfile.mkdtemp(prefix="obsidian_ctx_e2e_")
    os.environ["OBSIDIAN_VAULT_PATH"] = temp_dir
    os.environ["OBSIDIAN_REQUIRE_FRONTMATTER"] = "false"

    note_relpath = "probe.md"
    with open(os.path.join(temp_dir, note_relpath), "w") as f:
        f.write("# Probe\n\nRegression test note.\n")

    init_vault(temp_dir)

    yield note_relpath

    os.environ.pop("OBSIDIAN_REQUIRE_FRONTMATTER", None)
    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_read_note_tool_awaits_and_delivers_ctx_info(vault_with_note, caplog):
    """read_note_tool's `await ctx.info(...)` must actually reach the client.

    Two independent proofs that the call is real `await`ed delivery, not a
    fire-and-forgotten coroutine:
    1. The Client's log_handler receives a notification whose text matches
       `ctx.info(f"Reading note: {path}")` from note_management.py.
    2. No "was never awaited" warning was logged by mcp's message handler.
    """
    received = []

    async def log_handler(message):
        received.append(message)

    caplog.set_level("INFO")

    async with Client(mcp, log_handler=log_handler) as client:
        result = await client.call_tool("read_note_tool", {"path": vault_with_note})

    assert result.data["success"] is True

    messages = [m.data.get("msg", "") for m in received]
    assert any(f"Reading note: {vault_with_note}" in m for m in messages), (
        f"expected a delivered ctx.info notification for the read path, got: {messages}"
    )

    never_awaited = [r for r in caplog.records if "was never awaited" in r.getMessage()]
    assert not never_awaited, (
        f"un-awaited coroutine warning(s) logged: {[r.getMessage() for r in never_awaited]}"
    )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
