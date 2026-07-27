# obsidian-mcp

> Project memory for Claude Code. Keep this file short and high-signal —
> bloated memory gets ignored. Put hard guarantees in hooks, not prose.

## Behavioral guidelines
<!-- aia-harness:behavioral — non-negotiable; do not edit, reorder, or remove during enrichment -->

1. **Think before coding** — state assumptions explicitly; if multiple interpretations exist, present them instead of picking silently; say so when a simpler approach exists; if something is unclear, stop and ask.
2. **Simplicity first** — minimum code that solves the problem. No speculative features, no abstractions for single-use code, no unrequested configurability, no error handling for impossible scenarios. If 200 lines could be 50, rewrite.
3. **Surgical changes** — touch only what the request requires; match existing style; don't refactor, reformat, or "improve" adjacent code. Remove orphans *your* change created; leave pre-existing dead code alone (mention it, don't delete it). Every changed line should trace directly to the user's request.
4. **Goal-driven execution** — turn tasks into verifiable goals ("fix the bug" → "write a test that reproduces it, then make it pass"). For multi-step work, state a brief plan with a verify check per step, then loop until verified.
5. **Main session = orchestrator — it does not implement.** Plan, decide, coordinate; ALL delegable implementation and analysis goes to a specialist subagent via `Agent`, parallel when scopes don't conflict.

## Stack
Python · uv

Architecture: **flat**.

## Canonical commands
Always use these exact commands (do not guess):

- **Install:** `uv sync --extra dev`
- **Lint:** `ruff check <the .py files you changed>`
- **Format:** `ruff format`
- **Typecheck:** `pyright <the .py files you changed>` (config in `[tool.pyright]`, mode `standard`)
- **Test:** `OBSIDIAN_VAULT_PATH=/path/to/vault pytest` (the env var is mandatory — see Conventions)
- **Build:** `uv build`
- **Run/Dev:** `obsidian-mcp` (console script → `obsidian_mcp.server:main`)

## Workflow & Agents

Invoke `superpowers:subagent-driven-development` for **non-trivial** implementation — trigger it when the request meets **≥2** of:
- touches **3+ files** or **2+ domains/layers** (UI + agent, API + DB…)
- is a **new feature / epic / cross-cutting refactor** (not a one-line or single-function change)
- needs a **multi-step plan** or ordered tasks, each with its own verification
- has **unclear scope or root cause** and needs exploration before coding

Skip it — implement inline — for typo/copy fixes, single-function edits, config tweaks, or one-file bugs with an obvious cause.

When dispatching subagents, you MUST use the matching specialist agent from the list below — never the generic agent when a specialist covers the domain. Pass the exact name as `subagent_type`.

Model dispatch: an agent's frontmatter `model` wins; a generic dispatch or a project/user agent with no `model` in frontmatter is force-set to `sonnet` by a PreToolUse hook, so it never silently inherits this session's model — except namespaced plugin agents (`plugin:name`), left unrewritten since their frontmatter isn't reliably hook-resolvable. Pass `model` explicitly yourself for those, or to override for complex work: `haiku` for search/exploration, `sonnet` for implementation, `opus` for architectural judgment — cheapest tier that fits.

Full "when to use" routing conditions live in each agent's frontmatter (`.claude/agents/<name>.md`) — Claude Code already loads these to route dispatch. Names + a domain hint, so every agent stays discoverable from here:

- `orchestrator` — multi-agent / cross-domain coordination
- `code-reviewer` — general code review (bugs, error handling, tests)
- `security-reviewer` — OWASP / secrets / auth review
- `python-reviewer` — Python-specific review (injection, typing, idioms)
- `mcp-tool-contract-reviewer` — MCP tool/wrapper contract checks
- `qa-automation-engineer` — E2E tests, CI/CD quality gates
- `test-engineer` — unit/integration tests, TDD
- `database-architect` — schemas, migrations, query design
- `devops-engineer` — deploys, CI/CD, infra, incidents
- `backend-specialist` — API endpoints, server-side logic
- `performance-optimizer` — profiling, bottleneck fixes
- `product-manager` — requirements clarification, prioritization
- `product-owner` — acceptance criteria, specs
- `project-planner` — feature/epic task breakdown
- `code-archaeologist` — reverse-engineer legacy code
- `debugger` — root-cause bug investigation
- `explorer-agent` — map an unfamiliar codebase
- `documentation-writer` — READMEs, API docs, guides
- `penetration-tester` — offensive security / pentest
- `security-auditor` — defensive SAST, threat modeling

### Superpowers → Project Specialists (mandatory bridging)
<!-- aia-harness:agent-routing — superpowers→specialist bridge; do not remove -->

Superpowers skills (`superpowers:dispatching-parallel-agents`, `superpowers:subagent-driven-development`,
`superpowers:executing-plans`, `superpowers:systematic-debugging`) show `general-purpose` as the default
`subagent_type` in their examples. **Never dispatch `general-purpose` (or a generic
implementer) when a specialist above covers the domain** — pass the specialist's exact
name as `subagent_type` instead.

> Basis: superpowers itself states "User's explicit instructions (CLAUDE.md) — highest
> priority." This section applies that priority over the agent types its examples suggest.
> The normal flow is unchanged (`superpowers:brainstorming` → `superpowers:writing-plans` → `superpowers:subagent-driven-development`);
> only the dispatched `subagent_type` changes.

### Parallel wave execution (subagent-driven-development)
<!-- aia-harness:parallel-sdd — parallel wave execution override; do not remove -->

Override `superpowers:subagent-driven-development`'s serial one-implementer-at-a-time default with
parallel waves of independent tasks. Its "never dispatch implementers in parallel" red flag is
superseded here because its two premises are removed: disjoint file ownership per wave, and
controller-serialized commits instead of implementer self-commits. During planning, tag each task
`Files:` / `Depends-on:`; batch tasks with disjoint `Files` and no mutual dependency into one wave,
and dispatch their implementers in a single message using the specialist types from the list above.
Keep the skill's implementer/reviewer prompt contracts intact — the only change is implementers do
NOT self-commit. Untagged or uncertain tasks run serial (no regression). Full protocol:
`.claude/rules/08-parallel-subagent-driven-development.md`.

## Architecture map

MCP server (FastMCP) exposing an Obsidian vault over **direct filesystem access** — no Obsidian REST API, no plugin.

- `obsidian_mcp/server.py` — entrypoint. ~30 `@mcp.tool()` wrappers named `*_tool` that only declare the schema and translate exceptions into `ToolError`; every wrapper delegates to `tools/`. Raises at import time if `OBSIDIAN_VAULT_PATH` is unset, then calls `init_vault()`.
- `obsidian_mcp/tools/` — one async function per MCP operation, re-exported from `tools/__init__.py`; all reach the vault through `get_vault()`.
  - `note_management.py` — note CRUD + section editing; runs the write checks (template, frontmatter, slug style, size policy) and serializes writes via `_serialize_note_writes`.
  - `search_discovery.py` — search by text, date, regex, and frontmatter property; note listing.
  - `organization.py` — tags, move/rename, folders, batch property updates. Largest module (1728 lines).
  - `link_management.py` — backlinks, outgoing/broken links, and `validate_wikilinks_for_write` consumed by `note_management`.
  - `daily_notes.py` · `find_orphaned_notes.py` · `image_management.py` · `view_note_images.py` · `vault_meta.py` — one feature each.
- `obsidian_mcp/utils/filesystem.py` — `ObsidianVault`, the single I/O choke point (1071 lines), plus the `init_vault()` / `get_vault()` singleton pair. Owns the search index and cache.
- `obsidian_mcp/utils/persistent_index.py` — SQLite (aiosqlite) search index; used above the `OBSIDIAN_SEARCH_INDEX_THRESHOLD` vault size.
- `obsidian_mcp/utils/vault_cache.py` — stat cache with TTL, feeding `filesystem.py`.
- `obsidian_mcp/utils/vault_config.py` — reads every `OBSIDIAN_*` env var, normalizes vault-relative paths, parses folder templates. Consumed by `filesystem.py` and `note_management.py`.
- `obsidian_mcp/utils/validation.py` — validators returning `(ok, error)` plus the `validate_params` decorator.
- `obsidian_mcp/utils/validators.py` — `validate_note_path`, `sanitize_path`, `is_markdown_file`; this is what `tools/` imports via `..utils`. ⚠️ `validate_note_path` is duplicated across both files — check which one you are changing.
- `obsidian_mcp/models/obsidian.py` — `Note` / `NoteMetadata` pydantic models, the shape every tool returns.
- `obsidian_mcp/constants.py` — `ERROR_MESSAGES` (actionable, numbered) and `RESPONSE_STRUCTURES` (the response contract each tool category follows).
- `obsidian_mcp/configure.py` — the `obsidian-mcp-configure` console script.
- `tests/` — flat, one file per feature, 386 tests, no `conftest.py`.

Domain-specific guidance lives in nested CLAUDE.md files (loaded on demand):

- `obsidian_mcp/tools/CLAUDE.md` — the MCP operation layer: tool/wrapper contract, write serialization, error conventions.
- `obsidian_mcp/utils/CLAUDE.md` — vault I/O, path resolution, and the write-policy chain.

## Conventions

- **`pytest` needs `OBSIDIAN_VAULT_PATH` set to an existing directory** — `server.py` raises at import time, so an unset or missing path breaks the whole collection, not one test. There is no `conftest.py` supplying it.
- **Every MCP tool is a pair.** `*_tool` in `server.py` carries only the `Annotated[…, Field(description=, pattern=, examples=)]` schema, a "When to use / When NOT to use" docstring, and a try/except that re-raises as `ToolError`. The real work lives in `tools/`. Never put logic in the wrapper.
- **Reach the vault only through `get_vault()`.** Never instantiate `ObsidianVault` or touch the filesystem directly from a tool module — `utils/filesystem.py` is the single I/O choke point.
- **Error text comes from `constants.ERROR_MESSAGES`**, in the actionable numbered form (`"To fix: 1) … 2) …"`). Add a key there instead of inlining a message; response shapes follow `constants.RESPONSE_STRUCTURES`.
- **Validators return `(ok, error_message)` — they do not raise.** The caller decides. See the duplicate-`validate_note_path` warning in the architecture map before editing either validator file.
- **New behavior is configured by an `OBSIDIAN_*` env var read in `utils/vault_config.py`** (18 already: slug/tag style, template enforcement, note size policy, wikilink policy, index tuning). Do not scatter `os.getenv` across modules.
- **Report progress with `await ctx.info(...)`**; `ctx: Optional[Context] = None` is always the last parameter, and every code path must tolerate `ctx` being `None`.
- **Lint and typecheck gate on the files you changed, not the tree.** The repo carries inherited debt (~717 ruff, ~133 pyright findings), so `ruff check <your files>` and `pyright <your files>` must be clean — you are not expected to clear the backlog. Same rule in the Stop hook and in CI.

## Engineering rules
<!-- aia-harness:fixed — non-negotiable; do not edit, reorder, or remove during enrichment -->

- Match the style of surrounding code; do not introduce new patterns unprompted.
- Test what can break — business rules, branching logic, money/security/auth, bug regressions; skip trivial getters, wrappers, config, presentational UI (rubric: `.claude/rules/05-testing.md`).
- Run the lint + test commands above before claiming work is complete.
- Never commit secrets; keep them in gitignored env files (`.env`/`.env.local`) — `.claude/settings.local.json` is only for MCP-server credentials referenced by `.mcp.json`.
- Fix every compilation/syntax/lint error found during a session — regardless of whether you edited the file. Never leave the build broken or label errors "pre-existing, not related".
- When performing a code review (user requests it or a workflow triggers it), always use `code-reviewer` and `security-reviewer` and `python-reviewer`, applying the `uncle-bob-craft` skill's criteria (Dependency Rule, SOLID in context, code smells) alongside their findings.

@.claude/memory/INSTRUCTIONS.md
@.claude/memory/MEMORY.md
<!-- Generated by aia-harness. Edit freely; re-run /aia-harness:doctor to audit. -->

## graphify
<!-- aia-harness:graphify-root — knowledge-graph usage; merged section, do not remove -->

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
- The graph was built with `--code-only` (58 code files, no LLM key). Rebuild the same way: `graphify . --code-only`, otherwise it fails asking for an API key to extract the 8 doc files.
- Investigating code (file search, implementations, call sites, "where is X"): alongside graphify, dispatch specialist subagents (`model: haiku`) in parallel — never one at a time, never generic-only. Cuts investigation time.
