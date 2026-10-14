---
paths:
  - "**/*"
---
# Verification before completion

Before claiming a task is done:

- Lint: `ruff check obsidian_mcp tests`
- Typecheck: `pyright`
- Test: `OBSIDIAN_VAULT_PATH=<an existing dir> pytest` — without that env var `server.py` raises at import and the entire collection fails.

Lint and typecheck cover the whole tree (`obsidian_mcp` and `tests`), not only the
files you changed. Both must report zero findings.

Both tools live in `.venv/bin/`. If `ruff` or `pyright` is "not found", the venv
is not on PATH — call `.venv/bin/ruff` / `.venv/bin/pyright` or use `uv run`.
Pyright is configured in `pyproject.toml` (`[tool.pyright]`, mode `standard`).
Ruff runs its full default ruleset. The `ignore` list in `[tool.ruff.lint]` gives a
rationale for each entry. Fix a new finding in the code. Do not add an `ignore` entry.

Report the actual command output. Do not assert success without running them.
