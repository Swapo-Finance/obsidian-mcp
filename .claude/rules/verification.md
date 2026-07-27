---
paths:
  - "**/*"
---
# Verification before completion

Before claiming a task is done:

- Lint: `ruff check <the .py files you changed>`
- Typecheck: `pyright <the .py files you changed>`
- Test: `OBSIDIAN_VAULT_PATH=/path/to/vault pytest` — without that env var `server.py` raises at import and the entire collection fails.

Lint and typecheck are scoped to your own files on purpose: the tree carries
inherited debt (~717 ruff, ~133 pyright findings), so running them across the
whole project reports problems you did not introduce. Your files must be clean;
the backlog is separate work. The Stop hook and CI apply the same scoping.

Both tools live in `.venv/bin/`. If `ruff` or `pyright` is "not found", the venv
is not on PATH — call `.venv/bin/ruff` / `.venv/bin/pyright` or use `uv run`.
Pyright is configured in `pyproject.toml` (`[tool.pyright]`, mode `standard`);
ruff has no `[tool.ruff]` section and runs on its full default ruleset.

Report the actual command output. Do not assert success without running them.
