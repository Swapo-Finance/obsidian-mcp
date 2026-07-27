---
paths:
  - "**/*"
---
# Testing

Test what can break, not every function. Full rubric: `.claude/rules/05-testing.md`.

- Test: business rules, branching logic (CC ≥ 2), money/security/auth, algorithms, edge cases, bug regressions.
- Skip (or integration-only): trivial getters/wrappers, pass-through, config, presentational UI, generated code.
- Run `OBSIDIAN_VAULT_PATH=/path/to/vault pytest` before claiming done — the env var is mandatory, `server.py` raises at import without it.
