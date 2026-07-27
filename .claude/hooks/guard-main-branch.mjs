#!/usr/bin/env node
/**
 * PreToolUse guard: intercepts `git commit` and `git push` when the current
 * branch (or explicit push target) is `main` or `master`, then asks the user
 * for confirmation via the permission dialog before allowing the operation.
 *
 * Wire in .claude/settings.json under PreToolUse with matcher "Bash".
 * Non-invasive: fails open on any I/O or parse error (exit 0).
 */
import fs from "node:fs";
import { execFileSync } from "node:child_process";

/** @returns {string} */
function readStdin() {
  try {
    return fs.readFileSync(0, "utf8");
  } catch {
    return "";
  }
}

/** @type {any} */
let event = {};
try {
  event = JSON.parse(readStdin() || "{}");
} catch {
  process.exit(0);
}

const command = /** @type {string} */ (event?.tool_input?.command ?? "");

// Quoted text is data, not a command: `node -e '... "git push origin main" ...'`
// must not trip the guard. Blanking quoted runs first is what makes it safe to
// then search for the verb ANYWHERE — anchoring to a separator instead (^, ;, &&)
// silently missed `$(git push …)`, `then git push …`, `env git push …` and
// `xargs … git push …`, none of which start a line.
// Empty quotes are removed with NO separator first: the shell concatenates
// `comm''it` back into `commit`, so blanking them to a space would split the
// verb and let a real commit through.
// Quoted content then becomes a space, because it is data, not a command.
// Known limitation (accepted): quoting the verb itself — `git 'commit'` — still
// evades. Closing it means keeping quoted text, which re-breaks the common case
// of an innocent `node -e '... "git push" ...'`. This guard exists to stop the
// agent committing to main by mistake, not to withstand a deliberate attacker,
// so the false-positive cost is the one worth avoiding.
const stripped = command
  .replace(/''|""/g, "")
  .replace(/'[^']*'|"[^"]*"/g, " ");

// `[^;&|\n]*?` spans git's global options (`-C dir`, `-c k=v`, `--no-pager`)
// without crossing into the next command in a chain.
const GIT_VERB = /\bgit\b[^;&|\n]*?\s(commit|push)\b/;
const verbMatch = stripped.match(GIT_VERB);
const isCommit = verbMatch?.[1] === "commit";
const isPush = verbMatch?.[1] === "push";

if (!isCommit && !isPush) process.exit(0);

/** @returns {string} */
function currentBranch() {
  // event.cwd reflects the session's actual active directory (tracks EnterWorktree);
  // $CLAUDE_PROJECT_DIR stays pinned to the original repo root for the whole session,
  // so preferring it here would check the wrong checkout's branch when the session
  // has switched into a git worktree.
  const dir = event?.cwd || process.env.CLAUDE_PROJECT_DIR || process.cwd();
  try {
    return execFileSync("git", ["branch", "--show-current"], {
      cwd: dir,
      encoding: "utf8",
      timeout: 5000,
      windowsHide: true,
    }).trim();
  } catch {
    return "";
  }
}

const MAIN_BRANCHES = /^(main|master)$/;

const branch = currentBranch();
const onMain = MAIN_BRANCHES.test(branch);

// For push, also guard when the command names main/master as a refspec.
// Tokenise rather than anchoring to end-of-string: `$`-anchoring let
// `git push origin main --force`, `main:main` and `+main` through, because the
// branch is not the last word. Stop at the next command so
// `git push origin feat/x && echo main` does not read `main` as the target.
const REFSPEC = /^\+?(?:[^:\s]*:)?(?:refs\/heads\/)?(main|master)$/;

/** @returns {string} the matched branch name, or "" */
function pushTarget() {
  if (!isPush || !verbMatch) return "";
  // `)` and a backtick close a substitution — without them the last token of
  // `$(git push origin main)` is `main)`, which no refspec pattern matches.
  const after = stripped.slice(verbMatch.index + verbMatch[0].length).split(/[;&|\n)`]/)[0];
  for (const token of after.split(/\s+/)) {
    const m = token && REFSPEC.exec(token);
    if (m) return m[1];
  }
  return "";
}
const pushTargetBranch = pushTarget();
const pushTargetsMain = pushTargetBranch !== "";

if (!onMain && !pushTargetsMain) process.exit(0);

const verb = isCommit ? "commit" : "push";
// Name the refspec when that is what triggered the block, so the message points
// at what was actually pushed rather than at the branch we happen to be on.
const target = (pushTargetsMain ? pushTargetBranch : branch) || "main";

const permissionDecisionReason = [
  `guard-main-branch: blocked direct ${verb} to \`${target}\`.`,
  "Direct commits/pushes to the main branch bypass code review and CI gates.",
  "Create a feature branch instead:",
  "  git checkout -b feat/your-feature-name",
  "Then open a PR to merge into main.",
].join("\n");

process.stdout.write(
  JSON.stringify({
    hookSpecificOutput: {
      hookEventName: "PreToolUse",
      permissionDecision: "deny",
      permissionDecisionReason,
    },
  }),
);

process.exit(0);
