/**
 * Testes do guard-main-branch.mjs.
 *
 * O hook roda como o Claude Code o executa: JSON no stdin com `cwd` e
 * `tool_input.command`. O branch atual vem de repositórios git temporários
 * (sem commits, só o HEAD apontando para o branch), então nada do repositório
 * real é lido.
 *
 * Executar: node --test .claude/hooks/guard-main-branch.test.mjs
 */
import { test, after } from "node:test";
import assert from "node:assert/strict";
import { execFileSync, spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HOOK = path.join(path.dirname(fileURLToPath(import.meta.url)), "guard-main-branch.mjs");

/** Cria um repositório temporário cujo HEAD aponta para `branch`. */
function makeRepo(branch) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "guard-main-branch-"));
  execFileSync("git", ["init", "-q"], { cwd: dir });
  execFileSync("git", ["symbolic-ref", "HEAD", `refs/heads/${branch}`], { cwd: dir });
  return dir;
}

const repos = { main: makeRepo("main"), feature: makeRepo("feat/work") };
after(() => {
  for (const dir of Object.values(repos)) fs.rmSync(dir, { recursive: true, force: true });
});

/** Roda o hook e devolve o `hookSpecificOutput` do deny, ou null quando libera. */
function run(repo, command) {
  const input = JSON.stringify({
    hook_event_name: "PreToolUse",
    tool_name: "Bash",
    cwd: repos[repo],
    tool_input: { command },
  });
  const { status, stdout } = spawnSync(process.execPath, [HOOK], { input, encoding: "utf8" });
  assert.equal(status, 0, "the hook always exits 0 (deny goes through the JSON output)");
  return stdout ? JSON.parse(stdout).hookSpecificOutput : null;
}

/** @param {"main"|"feature"} repo @param {string[]} commands */
function expectAllowed(repo, commands) {
  for (const command of commands) {
    test(`${repo}: allows ${JSON.stringify(command)}`, () => {
      assert.equal(run(repo, command), null);
    });
  }
}

/** @param {"main"|"feature"} repo @param {string[]} commands */
function expectBlocked(repo, commands) {
  for (const command of commands) {
    test(`${repo}: blocks ${JSON.stringify(command)}`, () => {
      assert.equal(run(repo, command)?.permissionDecision, "deny");
    });
  }
}

// --- Em `main`: o branch criado antes do verbo na mesma cadeia `&&` libera ---

expectAllowed("main", [
  'git checkout -b docs/x && git add CLAUDE.md && git commit -m "docs: update"',
  "git checkout -B docs/x && git commit -m x",
  "git switch -c docs/x && git commit -m x",
  "git switch -C docs/x && git commit -m x",
  "git checkout -b feat/x && git push -u origin feat/x",
  "git checkout -b feat/x && git commit -m y && git push -u origin feat/x",
  "git checkout -b feat/x &&\n git commit -m y",
  "git status",
]);

expectBlocked("main", [
  // sem troca de branch
  "git commit -m x",
  "git push",
  "git push origin main",
  // troca vem depois do verbo
  "git commit -m x && git checkout -b y",
  // troca para o próprio branch protegido
  "git checkout -b main && git commit -m x",
  "git switch -c main && git commit -m x",
  "git checkout -b master && git commit -m x",
  // push para main é direto, qualquer que seja o branch atual
  "git checkout -b feat/x && git push origin main",
  "git switch -c feat/x && git push origin HEAD:main",
  "git checkout -b feat/x && git commit -m y && git push origin main",
  // texto entre aspas é dado, não comando
  'echo "git checkout -b x" && git commit -m y',
  // sem `&&` o verbo roda mesmo que a troca falhe, e o commit cairia em main
  "git checkout -b feat/x; git commit -m y",
  "git checkout -b feat/x || git commit -m y",
  "git checkout -b feat/x\ngit commit -m y",
  // nome vindo de variável: não dá para saber se é main
  "git checkout -b $BRANCH && git commit -m y",
]);

// --- Comportamento que não pode mudar ---

expectAllowed("feature", [
  "git commit -m x",
  "git push origin feat/work",
  "git checkout -b y && git commit -m x",
  "ls -la",
]);

expectBlocked("feature", [
  "git push origin main",
  "git checkout -b y && git push origin main",
]);

test("deny messages are unchanged", () => {
  const commit = run("main", "git commit -m x");
  assert.equal(commit.hookEventName, "PreToolUse");
  assert.equal(
    commit.permissionDecisionReason,
    [
      "guard-main-branch: blocked direct commit to `main`.",
      "Direct commits/pushes to the main branch bypass code review and CI gates.",
      "Create a feature branch instead:",
      "  git checkout -b feat/your-feature-name",
      "Then open a PR to merge into main.",
    ].join("\n"),
  );
  assert.match(run("feature", "git push origin main").permissionDecisionReason, /blocked direct push to `main`/);
});

test("fails open on malformed stdin", () => {
  const { status, stdout } = spawnSync(process.execPath, [HOOK], { input: "not json", encoding: "utf8" });
  assert.equal(status, 0);
  assert.equal(stdout, "");
});
