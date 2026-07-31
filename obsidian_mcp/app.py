"""Boot sequence and shared FastMCP server instance for the Obsidian MCP server.

This is a strict leaf module: it imports nothing from `.tools` or `.server`.
The `mcp_*` registration modules import `mcp` from here (never the reverse) so
that registering tools can never depend on a partially-initialized `server`
module.
"""

import logging
import os

from fastmcp import FastMCP

from .utils.filesystem import init_vault

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

# Create FastMCP server instance
mcp = FastMCP(
    "obsidian-mcp",
    instructions="MCP server for direct filesystem access to Obsidian vaults",
)


def main():
    """Entry point for packaged distribution."""
    mcp.run()
