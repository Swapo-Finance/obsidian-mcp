"""Boot sequence and shared FastMCP server instance for the Obsidian MCP server.

This is a strict leaf module: it imports nothing from `.tools` or `.server`.
The `mcp_*` registration modules import `mcp` from here (never the reverse) so
that registering tools can never depend on a partially-initialized `server`
module.
"""

import logging
import os

from fastmcp import FastMCP

from .utils.filesystem import ObsidianVault, get_vault, init_vault

# Configure logging
logging.basicConfig(
    level=os.getenv("OBSIDIAN_LOG_LEVEL", "INFO"),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

# Check for vault path
if not os.getenv("OBSIDIAN_VAULT_PATH"):
    raise ValueError("OBSIDIAN_VAULT_PATH environment variable must be set")

# Initialize vault
init_vault()


def _build_instructions(vault: ObsidianVault) -> str:
    """FastMCP server instructions, built from the live index config. Clients
    such as Claude Code add them to the agent's system prompt -- the lever
    that gets agents to call sync_vault_index_tool after editing the vault
    outside this server."""
    outside = (
        "your own file edit tools, shell, git, the Obsidian app, sync clients, "
        "other agents"
    )
    if vault._auto_index_update:
        recheck = max(vault._index_update_interval, vault.cache_stat_ttl_seconds)
        freshness = (
            "Index freshness: this server's own write tools keep search, tag, and "
            f"link results current. Files changed any other way ({outside}) are "
            f"picked up at most every {recheck} seconds. After changing vault "
            "files outside this server, call sync_vault_index_tool once before "
            "the next search, tag, or link query."
        )
    else:
        freshness = (
            "Index freshness: automatic re-checks are off "
            "(OBSIDIAN_AUTO_INDEX_UPDATE=false). This server's own write tools "
            "keep results current, but after vault files change any other way "
            f"({outside}) you MUST call sync_vault_index_tool before relying on "
            "search, tag, or link results."
        )
    return f"MCP server for direct filesystem access to Obsidian vaults.\n\n{freshness}"


# Create FastMCP server instance
mcp = FastMCP("obsidian-mcp", instructions=_build_instructions(get_vault()))


def main():
    """Entry point for packaged distribution."""
    try:
        mcp.run()
    finally:
        # None until the first search initializes the index (any vault size).
        index = get_vault().persistent_index
        if index is not None:
            index.kill_regex_pool()
