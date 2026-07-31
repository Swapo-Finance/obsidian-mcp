# tools — MCP operation layer

Every user-facing MCP operation lives here as one async function, re-exported from
`tools/__init__.py` and wrapped by a `*_tool` in one of the 8 `mcp_*.py` registration
modules (`mcp_notes.py`, `mcp_search.py`, `mcp_discovery.py`, `mcp_organization.py`,
`mcp_tags.py`, `mcp_links.py`, `mcp_properties.py`, `mcp_media_meta.py`), which `server.py`
imports and re-exports. This is the trust boundary: paths and content arrive from an LLM
client and end up on a real filesystem.

## Responsibility

Vault operation logic belongs here — not the MCP schema (that is the
`Annotated[…, Field(…)]` block on the `*_tool` wrapper in the matching `mcp_*.py` module),
and not filesystem I/O (that goes through `get_vault()` into `utils/filesystem.py`). Path
normalization, template rules, and frontmatter policy live in `utils/vault_config.py`; call
those helpers instead of reimplementing them. One module owns one feature area.

## Key patterns

- Every operation is `async def name(..., ctx: Optional[Context] = None)` — `ctx` is always
  last, and every code path must work when it is `None`.
- Reach the vault with `get_vault()` from `..utils.filesystem`. Never construct `ObsidianVault`.
- Raise `ValueError` / `FileNotFoundError` / `FileExistsError` with text from
  `constants.ERROR_MESSAGES`. The `*_tool` wrapper converts those into `ToolError` — do not
  raise `ToolError` here.
- A function's `get_vault()` call resolves against the globals of the module it is
  **defined** in, not wherever its name is re-exported to. Several tests monkeypatch
  `get_vault` module-qualified (e.g. `obsidian_mcp.tools.search_discovery.get_vault`), so
  re-exporting a patched function elsewhere makes the patch a silent no-op — the test still
  passes but hits the real vault. This is why `search_notes`/the public `search_by_property`
  stay physically defined in `search_discovery.py` and `get_backlinks`/`get_outgoing_links`
  stay in `link_management.py` even though both files are facades for everything else.
  Before moving a function that calls `get_vault()`, grep the test suite for
  `<module>.get_vault` first.
- Writes run through `write_policy._apply_write_checks` (template conformance, frontmatter,
  slug style, size policy; re-exported from `note_management`) and are serialized by the
  `_serialize_note_writes` decorator from the same module. The lock covers note
  create/update/delete/append plus `move_note`, `rename_note`, `move_folder`,
  `add_tags`/`update_tags`/`remove_tags`, and `batch_update_properties`. Bypassing it
  reintroduces the concurrent-write race it exists to prevent.
- Wikilinks in written content are checked by `wikilink_validation.validate_wikilinks_for_write`
  (re-exported from `link_management`), used by `write_policy._apply_write_checks` and
  directly by `note_management`'s append path — links are validated at write time, not read time.
- Report progress with `await ctx.info(...)` (78 call sites in this directory); never `print()`.

## Applied rules

Rules active in this directory — read them before touching code here:

- @.claude/rules/06-security.md — this directory is the trust boundary. Every `path` argument
  is untrusted input: run it through `sanitize_path` / `validate_note_path` before it reaches
  the filesystem, and never build a vault path by string concatenation.
- @.claude/rules/python/coding-standards.md — `except Exception` (bare or with `as e`)
  appears in several modules here. `pyproject.toml`'s `[tool.ruff.lint] ignore` list
  documents the rationale (the MCP tool-boundary contract, and per-item catches in
  vault-wide bulk scans so one bad note can't abort the whole operation) with a concrete
  file:line count for each pattern. Catch the specific exception in new code; a genuinely
  new blanket catch needs the same bar, not just an entry copied from an existing one.
- @.claude/rules/04-code-quality.md — `search_discovery.py` (556 lines) is the one module
  here deliberately kept over the 350-line guideline — see the `get_vault()` constraint
  above and root `CLAUDE.md`'s Architecture map. Every other module stays under the limit
  by design: `organization.py`, `note_management.py`, and `link_management.py` are thin
  facades so a new feature goes in a new sibling module instead of growing one of them back.
- @.claude/rules/05-testing.md — every function here is a branching business rule with real
  blast radius (it writes to a user's vault). Add a test in `tests/test_<feature>.py`.

## Local conventions

- Adding an operation means three edits or none: the function here, its export in
  `tools/__init__.py`, and the matching `*_tool` wrapper in the relevant `mcp_*.py` module
  (`server.py` just re-exports it — add the wrapper's name to `server.py`'s `__all__`).
- New configurable behavior reads its `OBSIDIAN_*` env var in `utils/` (`ObsidianVault.__init__`
  in `filesystem.py`, or a `vault_config.py`-facade module) — never `os.getenv` in this directory.
- Tests are flat in `tests/`, one file per feature, mirroring the module name.
  `OBSIDIAN_VAULT_PATH` must point at an existing directory or the whole suite fails to collect.

<!-- Generated by aia-harness revise-claude-md. Re-run /aia-harness:revise-claude-md to update. -->
