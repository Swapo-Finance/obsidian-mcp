---
description: Complete post-implementation review against rules, security, clean code, and quality — single parallel wave, main agent as pure orchestrator.
argument-hint: '[files | git-range | #issue]'
model: sonnet
---

# /code-review — Post-Implementation Review (orchestrated)

$ARGUMENTS

## Execution Architecture

| Phase | Name                          | Mode                                   | Executor                              |
| ----- | ----------------------------- | -------------------------------------- | ------------------------------------- |
| 0     | Pre-flight + Context Pack     | SEQUENTIAL · ~30s                      | main agent (bash + graphify)          |
| 1     | Integrity Gate                | **BACKGROUND** (parallel to Phase 2)   | main agent (1 background bash)        |
| 2     | Single Review Wave (R1–R10)   | **PARALLEL · ALL IN THE SAME MESSAGE** | 5–10 sonnet/haiku subagents           |
| 3     | Consolidation + Batch Scoring | PARALLEL                               | main agent + 1–3 haiku explorer-agent |
| 4     | Fixes                         | PARALLEL by domain · 1 file/agent      | specialist subagents                  |
| 5     | Final Gate + Report           | SEQUENTIAL · MANDATORY                 | main agent                            |

**Latency:** no reviewer depends on another's finding — anything that only needs diff + Context Pack fires **together**, one message. Barrier only on real dependency: scoring needs all findings; fixing needs scoring; report needs the gate.

**Main agent = pure orchestrator.** Bash, graphify, dispatch, dedup, synthesis. NEVER reads whole rules, NEVER analyzes code, NEVER fixes. Each reviewer reads its own rules and files (context is not a broadcast of source — only the compact Context Pack).

---

## Absolute Rules

> 🔴 **Code fixes found in review MUST be done by a subagent. NEVER inline in the main agent.**
>
> 🔴 **PARALLELISM IS DEFAULT.** PARALLEL phase = multiple `Agent` calls in the **same message** — never one at a time.
>
> 🔴 **`graphify` is mandatory before exploring source code — for the main agent AND subagents.** This is not a convention of this command: it's an active `PreToolUse` hook that intercepts Read/Bash over code files. `graphify` is a plain CLI (not MCP) — each reviewer runs `graphify query "<question>"` on its own, on demand. The main agent only runs `graphify update .` once (Phase 0.0); it does not pre-digest the graph into anyone's prompt.

### Fix-via-Subagent Protocol

**Routing** (full table in Phase 4): `obsidian_mcp/tools/**`, `server.py`, `app.py`, `mcp_*.py` → `backend-specialist` · broken MCP tool contract → `backend-specialist` implements the fix (`mcp-tool-contract-reviewer` only audits, never fixes) · `utils/persistent_index.py` / `utils/vault_cache.py` → `database-architect` · tests → `test-engineer` · security → `security-reviewer` · docs (`README.md`, `CLAUDE.md`, docstrings) → `documentation-writer` · fallback → `orchestrator`. Multiple domains → parallel. **1 file = 1 agent** (never 2 agents in parallel on the same file).

**Compilation and Syntax Errors — MANDATORY:** everything `ruff check`, `pyright`, or `pytest` report on the changed files = **all** fixed before closing out — any file among those changed, any author. **Never** report as "pre-existing, not related" and close out.

> ⚠️ **Scope (differs from the source command):** lint and typecheck run **only on the changed files**, never on the whole tree — see root `CLAUDE.md`, "Conventions" section, last item. But whatever the **scoped** commands report is 100% mandatory to fix, no "pre-existing" exception. Genuinely out of scope (broken external dependency, auto-generated file) → explain explicitly why.
>
> Inherited debt, measured — not the stale figures still quoted in `CLAUDE.md` and `.claude/rules/verification.md` (~717 ruff / ~133 pyright, from before a cleanup pass): **18 ruff** and **9 pyright** findings tree-wide, plus **25 `C901`** complexity findings (21 in `obsidian_mcp/`, 4 in vendored harness scripts). The scoping rule still holds — the backlog is separate work — but it is small enough that a finding in a file you touched is almost certainly worth fixing rather than deferring.

### Violations — Process

- 🔴 VIOLATION: code fix directly in the main agent = FAILED
- 🔴 VIOLATION: Phase 2 reviewers dispatched in separate messages = UNNECESSARY LATENCY
- 🔴 VIOLATION: main agent reading whole rules/skills or analyzing code = ORCHESTRATOR WASTE
- 🔴 VIOLATION: using a generic agent in R1–R6/R9 instead of the listed specialist = FAILED
- 🔴 VIOLATION: skipping R5 (`mcp-tool-contract-reviewer`) when the diff touches `obsidian_mcp/tools/**`, `server.py`, `app.py`, or `mcp_*.py` = FAILED
- 🔴 VIOLATION: skipping R7 (ponytail) = FAILED
- 🔴 VIOLATION: non-empty "Test gaps in diff files" without dispatching R9 (`test-engineer`) = FAILED
- 🔴 VIOLATION: not running the `test-triage` skill before deciding whether R9 joins the wave = FAILED
- 🔴 VIOLATION: scoring with 1 subagent per finding (instead of batched) = WASTE
- 🔴 VIOLATION: 2 fix subagents on the same file in parallel = CONFLICT
- 🔴 VIOLATION: assuming/simulating a user sign-off that was not received to unblock the report = FAILED — see Phase 5 turn-boundary

### Violations — Code (blockers; include VERBATIM in every R1–R6 reviewer's prompt)

> Grounded in `.claude/agents/mcp-tool-contract-reviewer.md`, `obsidian_mcp/tools/CLAUDE.md`, and `obsidian_mcp/utils/CLAUDE.md` — these are not hypotheses, they are the rules already documented for this code.

- 🔴 business logic inside the `*_tool` wrapper (`server.py` or `mcp_*.py`) instead of delegated to `tools/` — the wrapper only declares the schema + a `try/except` that converts to `ToolError`
- 🔴 vault access outside `get_vault()` — never construct `ObsidianVault()` directly nor touch the filesystem without going through it
- 🔴 inline error message instead of `constants.ERROR_MESSAGES`
- 🔴 `ToolError` raised inside `tools/` — there it's always `ValueError`/`FileNotFoundError`/`FileExistsError`; converting to `ToolError` is the wrapper's job exclusively
- 🔴 `ctx: Optional[Context] = None` is not the last parameter, or some code path doesn't tolerate `ctx is None`
- 🔴 a write that doesn't go through `_apply_write_checks` + the `_serialize_note_writes` decorator — reintroduces the concurrent-write race they exist to prevent
- 🔴 new configurable behavior reading `os.getenv` outside `utils/vault_config.py` (project rule: one `OBSIDIAN_*` in exactly one place)
- 🔴 a path argument reaching the filesystem without going through `sanitize_path` / `validate_note_path` / `resolve_path_maybe_outside_vault`
- 🔴 editing `validate_note_path` in `utils/validation.py` expecting an effect on callers — they resolve to the version in `utils/validators.py`; confirm which file is the real one before touching it
- 🔴 a new MCP operation missing any of the 3 contract points: function in `tools/<feature>.py`, export in `tools/__init__.py` + `__all__`, `<op>_tool` wrapper in the right module
- 🔴 `utils/**` importing MCP concepts (`Context`, `ToolError`, tool names) — breaks the infrastructure boundary (infra doesn't know about the interface layer)
- 🔴 `print()` instead of `await ctx.info(...)` (progress) or `logging` (internal diagnostics)

---

## Phase 0 — Pre-flight + Context Pack `[SEQUENTIAL · main agent]`

> **First step (before 0.1):** the main agent creates the todo list for the 6 phases (0–5) to track progress — no phase can be skipped. Every `🔴 VIOLATION` line in this command is a verifiable item on that list; confirm all of them before issuing the report (Phase 5). The **first executable action** (before git, Read, or dispatch) is 0.0.

### 0.0 Graph Update `[SEQUENTIAL · FIRST STEP]`

> 🔴 Before anything else, the main agent updates the graph that feeds the review. Stale graph → `graphify query` operates on old data. CLI unavailable → log `⚪` and move on; does not block the review.

```bash
graphify update .   # incremental, AST-only, no API cost (project is --code-only)
```

### 0.1 Git + Issue (bash, ~5s)

```bash
BASE=$(git merge-base origin/main HEAD 2>/dev/null || echo HEAD~1)   # on main → HEAD~1; $ARGUMENTS with git-range → use the range
git diff --name-only $BASE          # includes working tree — feeds everything
git diff --stat $BASE | tail -1
git branch --show-current
git log --oneline -10
```

- **Shape of `$ARGUMENTS`** (parsing heuristic): `#<num>` = issue · contains `..` = git-range · otherwise = file list.
- `$ARGUMENTS` with explicit files → review only those.
- **Issue** (detection order): `#<num>` in `$ARGUMENTS` → branch (`feat/<num>-…`) → commits (`(#<num>)`, `Closes #<num>`). Found → `gh issue view <num> --json title,body,comments,labels` and **extract paths of docs cited** in the body/comments, if any (cited spec = source of truth; acceptance criteria = gate, not the full design). Main agent does NOT read the docs — passes the issue JSON + paths to R1. No source → R1 = `⚪ N/A — no issue/plan` (**only valid justification for N/A**).
- **Eligibility (inline, no subagent):** empty diff → close out "nothing to review". Change ≤3 non-functional lines (docs/comments) → short report, close out.

### 0.2 Graph Intelligence (graphify — all in the main agent)

```bash
# graphify update already ran in 0.0 — do not repeat
graphify query "what the changed files affect and who calls them"   # broad context of the diff
graphify explain "<central module or symbol changed>"               # structural role of the node
graphify path "<changed symbol>" "<suspect consumer>"                # confirm specific coupling
```

Also read `graphify-out/GRAPH_REPORT.md`, sections **God Nodes (most connected - your core abstractions)** and **Suggested Questions** (generated by `graphify update` itself), filtering to those that cite files/symbols from the diff.

Test gaps: invoke the `test-triage` skill on the changed symbols — returns TEST / integration-only / SKIP per symbol, using the graph when `graphify-out/` exists.

Fallback: `graphify` unavailable → `wc -l` on the diff files for sizes, log `⚪ graph unavailable` and move on.

> **Honesty about what does NOT exist here** (the source command used a proprietary MCP with structured tools): there's no aggregated "risk score X/10", no concept of "impacted flows" (that was specific to step-functions), no automatic detection of functions >N lines scoped to the diff, and **`graphify affected` does not exist** (real subcommands: `query`, `path`, `explain`, `update`, `add`, `watch`, `cluster-only`, `label`, `diagnose`, `clone`, `merge-graphs`, `merge-driver`, `install`, `uninstall`). None of that should be fabricated. What graphify actually gives you: neighborhood (`query`), route between two symbols (`path`), structural role (`explain`), precomputed questions (`GRAPH_REPORT.md`).
>
> ⚠️ The `SKILL.md` of the `test-triage` skill documents a `graphify affected` that doesn't exist — known bug in the skill's doc. If it fails because of this, fall back to `graphify query` + judgment via the rubric in `.claude/rules/05-testing.md`.

### 0.3 Context Pack (≤60 lines — goes in EVERY subagent's prompt)

```text
## Context Pack — /code-review
- Base: <BASE> | Branch: <branch> | Issue: #<n> or ⚪
- Diff files by domain: [literal ABSOLUTE paths — never glob]
- Coupling (graphify query/path): [symbol → consumers | ⚪ graph unavailable]
- Test gaps in diff files (via test-triage): [TEST list | ∅]
- Sizes IN DIFF (wc -l — no per-function tool): files >800L [..] (200–400 is typical, see ecc/common/coding-style.md)
- Relevant God Nodes / Suggested Questions (GRAPH_REPORT.md): [..]
- Selected questions: [max 3 touching the diff]
- Applicable rules by path: [subset of table below]
```

**Path → rule table** (main agent maps inline — no subagent):

| Rule / Skill                                                                                       | When                                                             |
| -------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------- |
| `.claude/rules/03-coding-principles.md`, `04-code-quality.md`, skill `uncle-bob-craft`             | always                                                           |
| `.claude/rules/python/coding-standards.md`, `ecc/python/coding-style.md`, `ecc/python/patterns.md` | always (project is 100% Python)                                  |
| `.claude/rules/06-security.md`, `ecc/python/security.md`, skill `vulnerability-scanner`            | always                                                           |
| `.claude/rules/01-ddd.md`, `02-design-patterns.md`                                                 | `obsidian_mcp/utils/**` (infrastructure), cross-module refactors |
| `obsidian_mcp/tools/CLAUDE.md`                                                                     | `obsidian_mcp/tools/**`                                          |
| `obsidian_mcp/utils/CLAUDE.md`                                                                     | `obsidian_mcp/utils/**`                                          |
| `mcp-tool-contract-reviewer` agent                                                                 | `obsidian_mcp/tools/**`, `server.py`, `app.py`, `mcp_*.py`       |
| `database-design` skill                                                                            | `obsidian_mcp/utils/persistent_index.py`, `utils/vault_cache.py` |
| `.claude/rules/05-testing.md`, `.claude/rules/testing.md`, `ecc/python/testing.md`                 | any file with a detected test gap                                |

**Post-pack decisions (main agent):**

- Test gaps in the diff ≠ ∅ → include R9 in the wave.
- Global questions unrelated to the diff → `⚪ out of diff scope` (report only, no investigation).

---

## Phase 1 — Integrity Gate `[BACKGROUND · fires BEFORE the wave, collected in Phase 5]`

One bash with `run_in_background: true` (does not block Phase 2):

```bash
ruff check --extend-select C901 <changed files> && \
pyright <changed files> && \
OBSIDIAN_VAULT_PATH=$(mktemp -d) pytest
```

> `OBSIDIAN_VAULT_PATH` just needs to point to an **existing** directory — `server.py` raises on import if it's missing and takes down the entire collection, not just one test. The tests create their own temporary vaults, so `$(mktemp -d)` is enough; there's no canonical project vault. If `ruff`/`pyright` say "not found", the venv isn't on PATH — use `uv run ruff` / `uv run pyright`.

**Inviolable rule (evaluated at collection, Phase 5):** failure → group errors by domain → parallel fix subagents (Protocol) → re-run until exit 0. Review does NOT close out without green on the scoped files.

### Complexity gate (`C901`)

This replaces the source command's `js-quality-gates`, of which only the cyclomatic-complexity half has an equivalent here. `C901` is **not** in ruff's default ruleset and is **not** enabled in `pyproject.toml` — it is turned on by `--extend-select` **in this command only**, deliberately:

- Enabling it globally in `[tool.ruff.lint]` would make the Stop hook (`verify-on-stop.mjs`, which runs `ruff check` scoped to changed `.py` files) and CI fail on **21 pre-existing violations** in `obsidian_mcp/` the moment anyone touches those files — including `search_property_engine.py` (`_search_by_property`, CC 46), `batch_properties.py` (CC 37), and `find_orphaned_notes.py` (CC 30), each a real refactor, not a quick fix.
- As a review-only flag, the gate still bites exactly where it should — a file you changed that exceeds `max-complexity=10` blocks the review — without turning every unrelated session into a refactoring project.

A `C901` finding on a changed file is a **finding**, not a score: route it to Phase 4 like any blocker (splitting the function is the fix; raising the threshold is not). A `C901` on a file merely *adjacent* to the diff is inherited debt — report only.

> No equivalent exists for the other three halves of `js-quality-gates`: dependency cycles, duplication, and coverage have no tooling installed here (`pyproject.toml` configures neither). Do not fabricate a fake gate for those.

---

## Phase 2 — Single Review Wave `[ALL REVIEWERS IN THE SAME MESSAGE]`

**Every reviewer prompt MUST contain:** (1) Context Pack; (2) absolute literal paths of the files in its domain; (3) mandatory reads + log line `✅ <files> loaded` (no log = invalid review); (4) the "Violations — Code" block verbatim (R1–R6); (5) **REVIEW-ONLY: editing forbidden** (exception: R9 creates tests); (6) "run `graphify query`/`explain` before exploring source code — mandatory via this project's hook, applies to you too"; (7) finding format.

**Standard finding format (all except R1/R8):**

```text
| path:line | 🔴/🟡/🟢 | problem | suggested fix | rule/source |
```

Report **only real violations** — no praise, no "this is correct". No findings → `No violations`.

> ⚠️ **Paths with brackets** (`[id]`, `[slug]`): use `Read` with the exact path — `find`/`ls` fail with brackets (zsh glob). File not found → **report the failure**, never conclude "file doesn't exist".

### Reviewers

| #   | Reviewer                    | Agent · model                            | Condition                                             |
| --- | --------------------------- | ---------------------------------------- | ----------------------------------------------------- |
| R1  | Spec/Issue/Plan Compliance  | `code-reviewer` · sonnet                 | there is an issue/plan (otherwise `⚪ N/A`)           |
| R2  | Rules & Architecture        | `code-reviewer` · sonnet                 | always                                                |
| R3  | Security                    | `security-reviewer` · sonnet             | always                                                |
| R4  | Python Idioms & Quality     | `python-reviewer` · sonnet               | always                                                |
| R5  | MCP Tool Contract           | `mcp-tool-contract-reviewer` · sonnet    | `tools/**`, `server.py`, `app.py`, `mcp_*.py` in diff |
| R6  | Index/Cache (SQLite)        | `database-architect` · sonnet            | `persistent_index.py` or `vault_cache.py` in diff     |
| R7  | Ponytail (over-engineering) | `explorer-agent` · haiku                 | always                                                |
| R8  | Review Questions            | `code-archaeologist` · haiku · ≤3/agent  | selected questions ≠ ∅                                |
| R9  | Diff test gaps              | `test-engineer` · sonnet · ≤3 gaps/agent | test gaps in diff ≠ ∅                                 |
| R10 | Complementary scripts       | `explorer-agent` · haiku                 | always                                                |

> **Optional escalation (outside the fixed wave):** if the diff touches path validation/resolution (`sanitize_path`, `validate_note_path`, `resolve_path_maybe_outside_vault`, `vault_config.py`), the main agent may add `security-auditor` (sonnet) as an extra reviewer for trust-boundary threat-modeling. Not a fixed member because the root `CLAUDE.md` mandates `security-reviewer` for routine review.

#### R1 — Spec/Issue/Plan Compliance `[code-reviewer · sonnet]`

Receives: full issue JSON (title, body, comments, labels) + cited doc paths (if any) + diff list. MUST: read the cited docs; extract requirements (acceptance criteria + architectural decisions + files to create/modify + business rules from comments); verify **every requirement** against the actual implementation with evidence (`path:line` + snippet); group by theme.

```markdown
## Compliance — Issue #<num> / <plan>

### Group <N> — <name>

| #   | Requirement | Status | Evidence            |
| --- | ----------- | ------ | ------------------- |
| 1   | ...         | ✅/❌/⚠️ | path:line — snippet |

**Group N: ✅ X/Y** · ## Summary: <total>/<total> — [COMPLETE / N gaps]
```

Gaps: ❌ → implementation dispatch (Phase 4, no score) · ⚠️ → fix OR log if it's a conscious decision documented in the issue · ✅ → nothing.

> Same agent as R2, distinct prompt — the source command already reused 1 agent for both roles. `code-reviewer` has its own step that reads `.claude/rules/**` recursively and appends a "Compliance Check" table; let that feed R2 and keep R1 focused on the per-requirement matrix.
>
> 🔴 VIOLATION: skipping R1 when there's an issue/plan = FAILED · requirement ✅ without evidence = INVALID TRACEABILITY · gap ❌ without dispatch = FAILED. Matrix **always in the report**, even at 100% ✅.

#### R2 — Rules & Architecture `[code-reviewer · sonnet]`

Reads (mandatory, in addition to what the agent already reads on its own): `.claude/rules/03-coding-principles.md`, `.claude/rules/04-code-quality.md`, `.claude/rules/01-ddd.md` (if `utils/**` is in the diff), `.claude/rules/02-design-patterns.md`, `.claude/skills/uncle-bob-craft/SKILL.md`, and the nested `CLAUDE.md` of the touched directory (`obsidian_mcp/tools/CLAUDE.md` and/or `obsidian_mcp/utils/CLAUDE.md`). Applies **ALL checks** from the rules read + Dependency Rule (dependencies point inward) + SOLID in context + smells named with `path:line` (rigidity, fragility, immobility, viscosity, unnecessary complexity/repetition, opacity). Also: `git blame` on the modified files (non-obvious rules) + comments vs. intent. Sizes (LOC): use Context Pack data — don't repeat `wc -l`.

#### R3 — Security `[security-reviewer · sonnet]`

Reads (mandatory, with log): `.claude/rules/06-security.md`, `.claude/rules/ecc/python/security.md`, `.claude/skills/vulnerability-scanner/SKILL.md`, `.claude/skills/vulnerability-scanner/checklists.md`. Runs (script missing → `⚪` and move on):

```bash
python .claude/skills/vulnerability-scanner/scripts/security_scan.py .
```

> The script takes **one directory**, not a file list (argparse, `nargs="?"`, default `.`). Run it at the root and **filter the output to the diff files** — a finding outside the diff is global debt, report-only.

Apply the OWASP Top 10:2025 checklist from `checklists.md` to the diff files. This project's focus: (1) path traversal — does every path argument go through `sanitize_path`/`validate_note_path`/`resolve_path_maybe_outside_vault`? (2) are `OBSIDIAN_*` env vars treated as untrusted input (`OBSIDIAN_FOLDER_TEMPLATES`, `OBSIDIAN_DAILY_DIR`)? (3) does image read/write (`image_management.py`) validate type/size? Classify by CVSS.

```markdown
## Security Audit

- Scope: [files] · security_scan.py: ✅/❌ [N errors, N warnings — diff only]

### 🚨 Critical (CVSS ≥ 7.0) | ⚠️ Medium (CVSS 4.0–6.9) | ℹ️ Low risk

| path:line | type | description | rule |

### ✅ OWASP Checklist (checklists.md)

| # | Item | Status | Notes | ← path traversal · OBSIDIAN_* as untrusted input · images · secrets · logging
```

> 🔴 VIOLATION: R3 without reading the mandatory files = INVALID AUDIT · audit inline in the main agent = FAILED

#### R4 — Python Idioms & Quality `[python-reviewer · sonnet]`

Reads (mandatory): `.claude/rules/python/coding-standards.md`, `.claude/rules/ecc/python/coding-style.md`, `.claude/rules/ecc/python/patterns.md`. Applies its own checklist (injection, bare except, mutable default args, `isinstance` vs `type() ==`, type hints, PEP 8) to the `.py` files in the diff. Runs `ruff check --extend-select C901 <diff-files>` and `pyright <diff-files>` — dedup with Phase 1: report only what the integrity gate doesn't catch (idioms and smells that lint doesn't pick up). For any `C901` hit, name the offending function and the branch structure driving the count — the fix is splitting it, never raising the threshold.

#### R5 — MCP Tool Contract `[mcp-tool-contract-reviewer · sonnet · conditional]`

Only fires if the diff touches `obsidian_mcp/tools/**`, `obsidian_mcp/server.py`, `obsidian_mcp/app.py`, or `obsidian_mcp/mcp_*.py`. Follows its own contract (already starts with `graphify query "<operation name>"`). Applies the blockers from the "Violations — Code" section. Output in the agent's own format (`[BLOCK|HIGH|MEDIUM]`), closing with **Contract complete** or **Contract broken: `<missing point>`**.

> **Architecture note (confirmed in the actual code; the root `CLAUDE.md` is stale on this point):** the project is mid uncommitted refactor — `server.py` still exists, but `app.py` (leaf module with `mcp = FastMCP(...)`, `init_vault()`, `main()`) and the modules `mcp_discovery.py` / `mcp_links.py` / `mcp_media_meta.py` / `mcp_notes.py` / `mcp_organization.py` / `mcp_properties.py` / `mcp_search.py` / `mcp_tags.py` (each importing `mcp` from `app.py` and registering their `*_tool`s) coexist with it. R5 must check the wrapper **wherever it actually is** — don't assume `server.py`.

#### R6 — Index/Cache (SQLite) `[database-architect · sonnet · conditional]`

Only fires if `obsidian_mcp/utils/persistent_index.py` or `obsidian_mcp/utils/vault_cache.py` is in the diff. Reads `.claude/skills/database-design/SKILL.md` (especially `schema-design.md` and `indexing.md`). Focus: SQLite index schema (aiosqlite), indexing strategy, TTL stat-cache invalidation, poorly-narrowed `Optional` (`persistent_index.py` already has 20 documented `reportOptionalMemberAccess` findings — don't make that number worse).

#### R7 — Ponytail: Over-Engineering & YAGNI `[explorer-agent · haiku · always]`

Invoke `Skill(skill="ponytail:ponytail-review")` on the diff (namespace mandatory — the skill is plugin-provided). Unavailable → apply these criteria inline: unneeded abstractions (factory for 1 product, interface for 1 implementation, config for a fixed value) · dead/unreferenced code · inlineable duplications · "for the future" scaffolding (YAGNI) · trivial functions that should be inlined · single-use helpers. Scope: diff only.

Output: table `| path:line | type (yagni/dead-code/dup/abstraction) | description | fix |`. Base score in Phase 3: **65**; rule explicitly cited (e.g. `.claude/rules/03-coding-principles.md`) → **80**.

#### R8 — Review Questions `[code-archaeologist · haiku · ≤3 questions per agent]`

Only questions selected in Phase 0 (the "Suggested Questions" section of `graphify-out/GRAPH_REPORT.md`, filtered to diff files/symbols; max 3). Per question, the subagent MUST: print the literal question on line 1 · locate and **read** the relevant code (`graphify explain`/`query`, then Read/Grep) · detailed analysis (≥3 lines: what the code does, what the question is questioning, whether there's real risk) · check tests covering the behavior · verdict.

```markdown
### ❓ Question [N]: [literal text]

**Code investigated:** path:line · function
**Analysis:** [≥3 lines]
**Test coverage:** [file or none]
**Verdict:** ✅ No problem / ⚠️ Attention / 🚨 Confirmed problem
**Finding:** [statement or "None"]
```

> 🔴 VIOLATION: answering without reading code, or omitting "Code investigated"/"Test coverage" = INVALID INVESTIGATION · 🚨 confirmed without a Phase 4 fix = FAILED (base score 80; missing test → `test-engineer`)

#### R9 — Diff test gaps `[test-engineer · sonnet · ≤3 gaps per agent]`

For gaps **in the diff files** identified via `test-triage` (Context Pack). Valid-gap criterion: `.claude/rules/05-testing.md` rubric — business rule, real branching, security, algorithm, or bug regression; **not** trivial getters, wrappers, config. MUST: read the target file · write unit tests (main behavior + edge cases, AAA pattern) · `OBSIDIAN_VAULT_PATH=$(mktemp -d) pytest <scope>` until exit 0 · report the test files created. Only exception to review-only: **creates/edits test files only** — never the diff files. Empty list → `⚪ N/A`.

#### R10 — Complementary scripts `[explorer-agent · haiku · always]`

Runs once (dedup: security already in R3; `pytest` is already Phase 1's authority, don't duplicate in parallel). All three take **one directory** as a positional argument:

```bash
python .claude/skills/lint-and-validate/scripts/lint_runner.py .
python .claude/skills/lint-and-validate/scripts/type_coverage.py .
python .claude/skills/database-design/scripts/schema_validator.py .
```

Script missing → `⚪` and move on. Return: ✅/❌ status per script + **only the error/warning lines for diff files** (never full stdout; a finding outside the diff = global debt, report-only). ❌ in the diff → Phase 4 fix by domain.

---

## Phase 3 — Consolidation + Batch Scoring `[barrier: full wave]`

1. **Dedup (main agent):** same finding by 2+ reviewers (file+line+type) = 1 entry, sources annotated.
2. **Outside scoring (direct action, no score):**
   - 🚨 Critical security (CVSS ≥ 7.0) — immediate block (Phase 4).
   - ⚠️ Medium security (CVSS 4.0–6.9) — fix (Phase 4).
   - ❌ items from the `checklists.md` OWASP checklist — fix (Phase 4).
   - ❌ gaps from R1 — direct implementation dispatch.
   - Broken contract (`BLOCK` from R5) — direct dispatch to `backend-specialist`.
   - `C901` on a **changed** file (Phase 1 / R4) — fix by splitting the function (Phase 4). Never scored, never resolved by raising the threshold. `C901` on an adjacent file — inherited debt, report-only.
3. **Batch scoring:** group the rest into batches of ~10 findings → 1 `explorer-agent` (haiku) per batch, **parallel in the same message**. Each batch's prompt: diff of the cited snippets + findings + cited rules + rubric **verbatim**:

   > a. 0: No confidence. False positive that doesn't survive basic scrutiny, or a pre-existing issue unrelated to the current diff.
   > b. 25: Low confidence. Might be real, but couldn't be verified. If stylistic, not explicitly cited in the relevant rule.
   > c. 50: Moderate confidence. Verified as real, but a nitpick or rare in practice. Low importance relative to the diff's other changes.
   > d. 75: High confidence. Verified and very likely real in production. The diff's current approach is insufficient. Directly impacts functionality, or is explicitly cited in a relevant rule/CLAUDE.md.
   > e. 100: Absolute certainty. Confirmed, happens frequently, direct evidence available in the code.

   Per finding, the batch checks: (1) pre-existing (outside the changed lines)? (2) does any rule/CLAUDE.md explicitly cite it? (3) intentionally silenced (`# noqa`, exception comment)? (4) worth reporting — non-trivial, related to the diff, not already fixed? Returns a score (0–100) + 1-line justification. **Base scores** (replace the batch rubric — do not re-score): finding confirmed by a Python script = 70 · R8 question with 🚨 = 80 · ponytail = 65 (80 if a rule is cited).

4. **Filter:** score < 45 → discarded (45 = everything verified as real gets in — rubric level 50, even if lower; only unverified/false-positive drops out). Zero findings remaining → skip Phase 4.

---

## Phase 4 — Fixes `[PARALLEL by domain · 1 file = 1 agent]`

**Inputs:** score ≥ 45 (Phase 3) + ❌ R1 gaps + broken R5 contract + medium security (CVSS 4.0–6.9) + ❌ OWASP checklist items + Phase 1/R10 failures (once collected).

- Group by domain **and by file** — never 2 agents on the same file. Route via the Protocol (top of this document); fallback `orchestrator`. All in the same message.
- Each fixer: applies the fix → scoped verification (`OBSIDIAN_VAULT_PATH=$(mktemp -d) pytest <scope>` / `ruff check <files>` / `pyright <files>`) → reports the applied diff.
- **🚨 Critical (CVSS ≥ 7.0): blocks the report.** Fix via `security-reviewer` → **re-run R3 in full** → only unblocks with zero criticals.
- Medium security and ❌ checklist: confirmed fix mandatory before the report.
- Main agent only validates the fixers' completion — NEVER fixes.

> 🔴 VIOLATION: finding with score ≥ 45, broken contract, ❌ gap, or medium vulnerability without a completed fix = FAILED · critical fixed without re-running R3 = FAILED

---

## Phase 5 — Final Gate + Report `[SEQUENTIAL · main agent]`

1. **Collect Phase 1** (wait if still running). Failure → fix via domain subagents (Phase 4) → re-run the failing command until exit 0.
2. **Any fix applied in any phase?** → re-run `ruff check --extend-select C901 <changed files> && pyright <changed files> && OBSIDIAN_VAULT_PATH=$(mktemp -d) pytest`. **No fix** → Phase 1 results stand; don't re-run.
3. Residual error → NEVER inline → dispatch via the Protocol → repeat the cycle.
4. Issue the report **only** with lint, typecheck, and tests green **confirmed by the main agent**.

> **Sign-off ≠ assumption (turn-boundary).** Lint, typecheck, and tests ALWAYS end up green — no exception (the "Compilation Errors" rule from the root `CLAUDE.md`), scoped to the files changed in the session. Critical/medium security NEVER has a sign-off path — always fix it (BLOCKER at the end of this section). For the remaining findings with score ≥ 45 that a specialist fixer reports as unsafe or genuinely out of scope to fix automatically: the alternative path is "explicit user sign-off" — valid **only** when the user has already waived that exact finding in a real message from them, quoted verbatim in the report. The command **never** assumes, infers from silence, or simulates this sign-off to unblock the report. No safe fix possible **and** no real sign-off: issue the report with the item marked `⏸️ PENDING — awaiting user decision` and **end the turn** — the user's response only arrives in a future turn, never this one; never proceed as if the sign-off had been given.

### Final Report

```markdown
## Result: /code-review

### 🛡️ Integrity (Phase 1 + Phase 5)

| Check                              | Status | Action |
| Lint (ruff, changed files)         | ✅/❌    |        |
| Complexity (ruff C901, changed files) | ✅/❌ | |
| Typecheck (pyright, changed files) | ✅/❌    |        |
| Tests (pytest)                     | ✅/❌    |        |

### 📐 Spec/Issue/Plan Compliance (R1)

> Source: Issue #<n> / <plan> / ⚪ N/A. Full matrix per group (identical to R1's output).
> | Group | Status | → **Total: X/Y — [COMPLETE ✅ / N gaps fixed / N pending]**
> | # | Missing/divergent requirement | Status | Action taken |
> ❌/⚠️ gap without action = VIOLATION. No issue → ⚪ N/A, omit tables.

### 🧠 Graph Intelligence (Phase 0, via graphify)

| Relevant coupling | God Nodes touched | Suggested questions used | Files >800L in diff |

#### ❓ Review Questions (R8)

| #   | Question | Verdict | Finding | Action taken |

> 🚨 without action taken = VIOLATION.

### 🔐 Security (R3)

| Critical (CVSS ≥ 7.0) | ✅ None / 🚨 N | Medium | ✅ / ⚠️ N | security_scan.py | OWASP checklist | Notes |

### 📋 Rule Compliance (R2/R4/R5/R6)

| Rule/Agent | Status ✅/❌/⚪ N/A | Violations |

### 🏋️ Ponytail (R7)

| path:line | Type | Problem | Action |

> No findings → ⚪. Score ≥ 45 without a fix = VIOLATION.

### ❌ Problems (score ≥ 45) · ### ⚠️ Warnings · ### 🧪 Tests added (R9) · ### 🔧 Scripts (R3/R10)
```

> 🔴 BLOCKER: critical CVSS ≥ 7.0 without fix + R3 re-audit → **do not generate the report**. Medium or ❌ OWASP checklist without a completed fix → **do not generate the report**.
