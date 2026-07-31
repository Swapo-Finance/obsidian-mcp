# utils — vault I/O, path resolution, and write policy

Infrastructure for the tool layer: the single filesystem choke point, the search index, the
stat cache, path normalization, and every `OBSIDIAN_*` configuration knob. Security-sensitive —
this is where a path stops being a string and becomes a real file.

## Responsibility

Filesystem access, indexing, caching, path resolution, and config parsing belong here. MCP
operations do not — those live in `tools/`, and the MCP schema lives in the `mcp_*.py`
registration modules. Everything in this directory must be callable without a `Context` and
without knowing which tool invoked it.

## Key patterns

- `ObsidianVault` (`filesystem.py`, 823 lines) is the only class that touches the vault. It is
  a process singleton created by `init_vault()` and fetched by `get_vault()`. Deliberately over
  the 350-line guideline: it's one class, and splitting it into mixins would cost pyright its
  view of `self` for a marginal navigation gain.
- `__init__.py` re-exports exactly six names (`ObsidianVault`, `get_vault`, `init_vault`,
  `validate_note_path`, `sanitize_path`, `is_markdown_file`). Anything else is imported from
  its module explicitly.
- Validators return `(ok, error_message)` — they do not raise. `validation.py` also ships the
  `validate_params` decorator and `ValidationError` (a `ValueError` subclass).
- ⚠️ `validate_note_path` exists in **both** `validation.py` and `validators.py`. `__init__.py`
  and every caller in `tools/` resolve to the `validators.py` one — editing only `validation.py`
  changes nothing that callers see. Check which file you are in.
- Path resolution lives in `vault_paths.py` (`normalize_vault_relative_path` for in-vault paths,
  `resolve_path_maybe_outside_vault` for the escape-checking variant backed by
  `ERROR_MESSAGES["path_outside_vault"]`, `_WINDOWS_ABS_RE` for the Windows-absolute case),
  re-exported from `vault_config.py` for backward compatibility.
- Write policy is a chain of pure functions, all re-exported from `vault_config.py`:
  `build_template_info` → `check_template_conformance` (`templates.py`) →
  `apply_frontmatter_requirements` (`frontmatter_requirements.py`) → `check_note_size_policy`
  (owned directly by `vault_config.py`). `slugify_kebab` / `normalize_tag_kebab` (`slugs.py`)
  implement the slug and tag styles.
- Every `OBSIDIAN_*` var is read in one place, `ObsidianVault.__init__` (`filesystem.py`) —
  either a direct `os.getenv` (vault path, index tuning, daily dir, folder templates) or the
  `_read_bool_env`/`_read_choice_env`/`_read_int_env` delegators to `env.py`'s pure, directly
  unit-testable readers. The one exception is `OBSIDIAN_LOG_LEVEL`, read in `app.py` before the
  vault exists.
- `PersistentSearchIndex` (`persistent_index.py`, 735 lines — same 350-line exception as
  `filesystem.py` and for the same reason; a composition split was evaluated and rejected as
  highest-risk with no driver) and `VaultCache` (stat cache with TTL) are owned by
  `ObsidianVault` — tools never touch them directly. Regex search runs each file's match in a
  `ProcessPoolExecutor` worker under a hard timeout (`REGEX_MATCH_TIMEOUT_SECONDS`), so a
  pathological pattern can't hang the event loop.
- `PersistentSearchIndex._require_db()` and `ObsidianVault._require_persistent_index()` narrow
  an `Optional` attribute to its non-`None` type by raising if it's still `None` instead of
  asserting or ignoring — follow that pattern for any new `Optional` attribute instead of
  reaching for `# type: ignore`.
- Link parsing is two module-level regexes in `links.py`: `WIKI_LINK_PATTERN` and
  `MARKDOWN_LINK_PATTERN`. Extend those rather than writing a new link regex elsewhere.

## Applied rules

Rules active in this directory — read them before touching code here:

- @.claude/rules/06-security.md — path traversal is the live threat. `resolve_path_maybe_outside_vault`
  is the escape check; any new path entry point must go through it or `normalize_vault_relative_path`.
  Never trust an `OBSIDIAN_*` env value as a safe path either — `OBSIDIAN_FOLDER_TEMPLATES` and
  `OBSIDIAN_DAILY_DIR` are user-supplied and already have a dedicated error message for this.
- @.claude/rules/01-ddd.md — this is the infrastructure layer. Keep it free of MCP concepts:
  no `Context`, no `ToolError`, no tool names.
- @.claude/rules/python/coding-standards.md — `pathlib.Path` (already the convention in
  `vault_config.py`) over `os.path`, and context managers for file handles.
- @.claude/rules/05-testing.md — the pure functions here (`slugify_kebab`, `count_lines`,
  `normalize_vault_relative_path`, `extract_required_headings`) are cheap to test and have many
  callers; test them directly rather than only through a tool.

## Local conventions

- Adding a new `OBSIDIAN_*` knob means reading it in `ObsidianVault.__init__` the same way as
  its neighbors (direct `os.getenv` or an `env.py` delegator) and documenting it — not calling
  `os.getenv` from a new module.
- Policy helpers are pure: they take `(vault, relpath, content)` and return a value or raise.
  Keep I/O in `filesystem.py` so the policy chain stays testable without a vault on disk.
- New public helpers are exported from their own module, not added to `__init__.py`, unless
  `tools/` genuinely needs them everywhere.
- Run `pyright` on anything you touch here — the whole tree gates at zero findings now (see
  root `CLAUDE.md`). For a new `Optional` attribute, follow the `_require_*` narrowing pattern
  above instead of reaching for `# type: ignore`.

<!-- Generated by aia-harness revise-claude-md. Re-run /aia-harness:revise-claude-md to update. -->
