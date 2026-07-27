#!/usr/bin/env node
/**
 * PreToolUse guard: block hand-editing uv.lock. The lock is generated — a manual
 * edit silently desyncs it from pyproject.toml and breaks `uv sync` reproducibility.
 * Exit code 2 blocks the tool call; exit 0 allows it.
 * Wired in .claude/settings.json under PreToolUse with matcher "Edit|Write|MultiEdit".
 */
import fs from "node:fs";
import path from "node:path";

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

const file = event?.tool_input?.file_path ?? event?.tool_input?.path;
if (typeof file !== "string" || path.basename(file) !== "uv.lock") process.exit(0);

process.stderr.write(
  "uv.lock is generated — do not edit it by hand. Change the dependency in pyproject.toml, " +
    "then run `uv lock` (or `uv add <pkg>`) to regenerate it.\n",
);
process.exit(2);
