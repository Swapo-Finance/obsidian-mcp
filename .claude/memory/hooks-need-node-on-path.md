---
name: hooks-need-node-on-path
description: All 21 hooks silently do nothing unless Claude Code was launched from a shell with node on PATH — this machine has no node outside fnm's ephemeral dir
metadata:
  type: architecture
---

Every hook in `.claude/settings.json` is wired as `{"command": "node", "args": [...]}`,
and Claude Code resolves that with a plain PATH lookup inheriting the environment of
whatever launched it — no bundled runtime, no PATH augmentation, and there is no
per-hook `env` field to fix it with. On this machine `node` exists **only** at fnm's
per-shell path (`~/.local/state/fnm_multishells/<pid>_<ts>/bin/node`); verified absent
from `/usr/bin`, `/usr/local/bin` and `/opt/homebrew/bin`. The sole stable copy is
`~/.local/share/fnm/aliases/default/bin/node`.

So: launched from a terminal whose profile loaded fnm → all hooks run. Launched from
the Dock/Finder, from cron/launchd, or after the fnm multishell dir is cleaned up →
`spawn` fails with ENOENT, the hook's own JS never executes, and the action proceeds.
Claude Code does print a `<hook> hook error` notice in the transcript, so it is not
strictly silent — but it does **not** block, and nobody reads a transcript in a
headless run.

**Why:** this cost a binary-level investigation to establish, and it is invisible from
the code — every hook file's header promises "fails open, exits 0", which only covers
errors *inside* the script. When the interpreter itself cannot be resolved, that
fail-open logic never gets to run. Three of these hooks are security guards
(`secret-scan`, `guard-main-branch`, `guard-lockfile`) on a public repo, and a guard
that does not run looks identical to one that passed.

**How to apply:** do not diagnose "hook did nothing" as a bug in the hook script until
you have confirmed `command -v node` resolves in the environment Claude Code was
launched from. The mitigation (a committed `run-node.sh` wrapper probing PATH then
fnm/volta/asdf/nvm/homebrew, referenced via `${CLAUDE_PROJECT_DIR}`) was designed and
deliberately declined by the user in Jan 2026 — an absolute path is not an option here
because `.claude/settings.json` is committed to a public repo. Re-raise only if hooks
start failing in practice or another contributor joins.
