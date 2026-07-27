#!/usr/bin/env node
/**
 * PreToolUse guard (opt-in): block an Edit/Write/Bash whose payload looks like a
 * committed secret. Exit code 2 blocks the tool call; exit 0 allows it.
 * Wire this in .claude/settings.json under PreToolUse with matcher "Edit|Write|MultiEdit|Bash".
 */
import fs from "node:fs";

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

const ti = event?.tool_input ?? {};
// MultiEdit's payload is tool_input.edits[] ({old_string, new_string}[]), not
// tool_input.new_string — collect every edit's new_string too, or MultiEdit
// writes bypass the scanner entirely.
const editNewStrings = Array.isArray(ti.edits)
  ? ti.edits.filter((e) => e && typeof e === "object").map((e) => e.new_string)
  : [];
const text = [ti.content, ti.new_string, ti.command, ...editNewStrings]
  .filter((v) => typeof v === "string")
  .join("\n");

const patterns = [
  /AKIA[0-9A-Z]{16}/, // AWS access key id
  /-----BEGIN (?:RSA|EC|OPENSSH|PGP|DSA) PRIVATE KEY-----/,
  /sk-(?:ant-api|proj-)[A-Za-z0-9_-]{15,}|sk-[A-Za-z0-9]{20,}/, // Anthropic/OpenAI-style secret (ant-api*/proj-* + legacy)
  /gh[pousr]_[A-Za-z0-9]{36}/, // GitHub classic PAT / OAuth / server / user-to-server
  /github_pat_[A-Za-z0-9_]{22,}/, // GitHub fine-grained PAT
  /glpat-[A-Za-z0-9_-]{20,}/, // GitLab PAT
  /xox[baprs]-[A-Za-z0-9-]{10,}/, // Slack token
  /AIza[0-9A-Za-z_-]{35}/, // Google API key
];

for (const re of patterns) {
  if (re.test(text)) {
    process.stderr.write(
      "aia-harness secret-scan: refusing to write an apparent secret. Use an env var in .claude/settings.local.json instead.\n",
    );
    process.exit(2);
  }
}

process.exit(0);
