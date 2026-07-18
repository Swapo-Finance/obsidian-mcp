"""Vault metadata tools: get_note_template and help.

Both are cheap, read-only, and mostly static — no vault scan involved.
"""

import os
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Optional

from fastmcp import Context
from ..utils.filesystem import get_vault
from ..utils.vault_config import build_template_info, normalize_vault_relative_path
from ..constants import ERROR_MESSAGES


async def get_note_template(path: str, ctx: Optional[Context] = None) -> Dict[str, Any]:
    """
    Describe the template rule (if any) that applies to a note or folder path.

    Use this tool before create_note/update_note in an enforced folder (or
    any time you get a template-conformance error) to see exactly which
    headings and frontmatter keys are required, and the skeleton to start
    from.

    Args:
        path: A note path (e.g. "01-projects/Foo.md") or a folder path
            (e.g. "01-projects") — either works, only the folder matters.
        ctx: MCP context for progress reporting

    Returns:
        {enforced, folder_rule, template_path, required_headings,
         required_frontmatter_keys, skeleton, instructions}. enforced=False
        (with skeleton=None) means the folder has no template rule —
        free-form content is fine there.
    """
    vault = get_vault()
    raw = path or ""

    if ctx:
        await ctx.info(f"Looking up template rule for: {raw or '(vault root)'}")

    is_note = raw.endswith(".md") or raw.endswith(".markdown")
    folder_part = str(PurePosixPath(raw).parent) if is_note else raw
    if folder_part in (".", "/"):
        folder_part = ""

    normalized = normalize_vault_relative_path(folder_part, vault.vault_path) if folder_part else ""
    if normalized is None:
        raise ValueError(
            ERROR_MESSAGES["path_outside_vault"].format(
                path=path, vault_path=vault.vault_path, vault_name=vault.vault_path.name
            )
        )

    return build_template_info(vault, normalized)


def _env_row(name: str, type_: str, default: str, current: str, description: str, example: str) -> Dict[str, str]:
    return {
        "name": name,
        "type": type_,
        "default": default,
        "current": current,
        "description": description,
        "example": example,
    }


def _first_line(text: Optional[str]) -> str:
    """First non-empty stripped line of a (possibly multi-line) docstring."""
    if not text:
        return ""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


async def get_help(ctx: Optional[Context] = None) -> Dict[str, Any]:
    """
    Static + runtime catalog of every env var (with its current effective
    value), path-anchoring rules, and a one-line-per-tool index — without
    the token cost of the full tools/list schema.

    Use this tool when you're unsure which env var controls a behavior, how
    a path you pass will be resolved, or what a tool you haven't used yet
    is for.

    Returns:
        {env_vars: [...], path_anchoring: str, tools: [{name, purpose}, ...]}
    """
    if ctx:
        await ctx.info("Building help catalog...")

    vault = get_vault()

    folder_templates_current = (
        "(none configured)"
        if not vault.folder_templates
        else str([{"folder": r.folder, "template": r.template_display} for r in vault.folder_templates])
    )

    env_vars = [
        _env_row(
            "OBSIDIAN_VAULT_PATH", "string (required)", "(none)", str(vault.vault_path),
            "Absolute (or ~-expanded) path to the Obsidian vault this server exposes.",
            "/Users/you/vaults/brain",
        ),
        _env_row(
            "OBSIDIAN_LOG_LEVEL", "DEBUG|INFO|WARNING|ERROR|CRITICAL", "INFO", os.getenv("OBSIDIAN_LOG_LEVEL", "INFO"),
            "Python logging level for the server's root logger, set once at process "
            "startup via logging.basicConfig.",
            "DEBUG",
        ),
        _env_row(
            "OBSIDIAN_INDEX_UPDATE_INTERVAL", "int seconds", "300", str(vault._index_update_interval),
            "How often the background full-text search index refreshes.", "300",
        ),
        _env_row(
            "OBSIDIAN_INDEX_BATCH_SIZE", "int", "50", str(vault._index_batch_size),
            "How many files the search index (re)indexes per batch.", "50",
        ),
        _env_row(
            "OBSIDIAN_AUTO_INDEX_UPDATE", "bool", "true", str(vault._auto_index_update),
            "Whether the search index refreshes itself automatically.", "true",
        ),
        _env_row(
            "OBSIDIAN_FOLDER_TEMPLATES", "JSON array", "(unset = off)", folder_templates_current,
            "Maps a vault folder to a template file (longest-prefix match). create_note and "
            "update_note(replace) in a mapped folder must conform to the template's headings "
            "and frontmatter keys — see get_note_template_tool.",
            '[{"folder":"01-projects","template":"templates/project.md"}]',
        ),
        _env_row(
            "OBSIDIAN_WIKILINK_POLICY", "strict|warn|off", "warn", vault.wikilink_policy,
            "How [[wikilinks]] pointing at a missing note are handled on write. Malformed "
            "links (empty target, unbalanced brackets) always raise, regardless of this.",
            "strict",
        ),
        _env_row(
            "OBSIDIAN_DAILY_DIR", "string", "daily", vault.daily_dir or "(vault root)",
            "Folder for add_daily_note_tool. Notes inside it are always exempt from the "
            "note-size policy below.",
            "daily",
        ),
        _env_row(
            "OBSIDIAN_MAX_NOTE_LINES", "int", "500", str(vault.max_note_lines),
            "Line-count ceiling per note (create_note / update_note replace). Daily notes are "
            "always exempt.",
            "500",
        ),
        _env_row(
            "OBSIDIAN_APPEND_HEADROOM_LINES", "int", "100", str(vault.append_headroom_lines),
            "Extra safety margin for incremental writes (update_note append, "
            "edit_note_section): they're checked against MAX_NOTE_LINES minus this, so an "
            "append gets flagged before a later one would blow past the hard ceiling.",
            "100",
        ),
        _env_row(
            "OBSIDIAN_NOTE_SIZE_POLICY", "strict|warn|off", "warn", vault.note_size_policy,
            "How a note-size ceiling violation is handled: strict blocks the write, warn "
            "writes anyway and returns a warning, off ignores it entirely.",
            "warn",
        ),
        _env_row(
            "OBSIDIAN_TAG_STYLE", "kebab|as-is", "as-is", vault.tag_style,
            "kebab: add_tags/update_tags/remove_tags (and frontmatter tags in create_note) "
            "are normalized to lower-case, ASCII, hyphen-separated form per '/'-hierarchy "
            "segment; a tag with nothing alphanumeric left (e.g. pure emoji) is rejected.",
            "kebab",
        ),
        _env_row(
            "OBSIDIAN_SLUG_STYLE", "kebab|as-is", "as-is", vault.slug_style,
            "kebab: create_note's filename and any frontmatter 'name' field are transliterated "
            "to ASCII kebab-case (accents stripped). Also makes wikilink validation resolve a "
            "non-slugified target against an existing note's kebab form, rewriting the link to "
            "the real filename.",
            "kebab",
        ),
        _env_row(
            "OBSIDIAN_CACHE_STAT_TTL_SECONDS", "int", "30", str(vault.cache_stat_ttl_seconds),
            "Max age of the in-memory notes/tags/links cache's filesystem snapshot before it "
            "re-stats the vault for changes made outside this MCP server. 0 re-stats every access.",
            "30",
        ),
        _env_row(
            "OBSIDIAN_REQUIRE_FRONTMATTER", "bool", "true", str(vault.require_frontmatter),
            "On (the default): create_note and update_note (replace / create_if_not_exists) force "
            "frontmatter 'name' to match the filename, and require a non-empty 'description' "
            "(missing/empty raises a ToolError instead of writing). Exempt: edit_note_section, "
            "update_note append, and add_daily_note's own append (its file-creation path seeds "
            "name/description automatically instead).",
            "false",
        ),
        _env_row(
            "OBSIDIAN_SEARCH_RESULT_MODE", "content|index|auto", "auto", vault.search_result_mode,
            "Shape of search_notes/search_by_regex/search_by_property/search_by_date results. "
            "content: a text snippet per result (pre-10.4 behavior). index: lightweight "
            "{path, name, description, score, match_type} from the cache, no snippet. auto: index "
            "once a search's result count passes OBSIDIAN_SEARCH_INDEX_THRESHOLD, else content. "
            "Any of these tools' own `mode` parameter overrides this per call.",
            "index",
        ),
        _env_row(
            "OBSIDIAN_SEARCH_INDEX_THRESHOLD", "int", "10", str(vault.search_index_threshold),
            "Result-count cutoff used by OBSIDIAN_SEARCH_RESULT_MODE=auto (or a per-call mode='auto') "
            "to decide index vs. content mode.",
            "10",
        ),
    ]

    # Derived from the live FastMCP registry (not hardcoded) so this list can
    # never drift from the actual set of registered tools.
    from ..server import mcp  # deferred import: avoids circular import at module load

    tools_registry = await mcp.get_tools()
    tools = [
        {"name": name, "purpose": _first_line(tool.description)}
        for name, tool in sorted(tools_registry.items())
    ]

    return {
        "env_vars": env_vars,
        "path_anchoring": (
            "Folder/template/daily-dir config values accept 3 forms, always anchored at the "
            "vault root (never the process working directory): "
            "1) vault-relative, e.g. '01-projects'; "
            "2) vault-name-prefixed, e.g. '<vault-folder-name>/01-projects' — the vault's own "
            "basename is detected and stripped; "
            "3) absolute or '~'-expanded, e.g. '/Users/you/vault/01-projects' or "
            "'~/vault/01-projects'. "
            "Templates may resolve outside the vault (shared across projects, read-only); "
            "folders must resolve inside it or the config entry is ignored at boot "
            "(logged as a warning, never fatal)."
        ),
        "tools": tools,
    }
