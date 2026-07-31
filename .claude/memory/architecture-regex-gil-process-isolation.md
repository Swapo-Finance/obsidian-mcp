---
name: architecture-regex-gil-process-isolation
description: ThreadPoolExecutor cannot bound a runaway regex in CPython — only ProcessPoolExecutor frees the event loop
metadata:
  type: architecture
---

CPython's `re` engine holds the GIL for the **entire duration of a single match call** — it never
returns to the bytecode-dispatch loop where the GIL-release check happens. Therefore
`ThreadPoolExecutor` + `asyncio.wait_for` does **not** bound a catastrophic-backtracking regex: the
timeout stops *us* from waiting, but the event loop stays frozen and every other concurrent task
starves. Only `ProcessPoolExecutor` actually frees the loop.

**Why:** measured directly in this repo while fixing the `search_by_regex` ReDoS. A catastrophic
`(a+)+$` match with a 0.1s heartbeat coroutine running alongside it produced **1 heartbeat tick in
9.18s** under `ThreadPoolExecutor`, versus **79 ticks in 7.92s** under `ProcessPoolExecutor` — the
loop stayed responsive in the process case even after our own `wait_for` timeout fired. The
thread-based approach looks correct in code review and fails only under load, which is why it needs
measuring rather than reasoning about.

**How to apply:** any time CPU-bound work written in C (regex, some compression/parsing/crypto
extensions) must not block the asyncio loop, reach for process isolation, not threads. Threads only
help when the C extension explicitly releases the GIL (most I/O does; `re` does not). Caveat that
comes with the process approach: a stuck worker cannot be force-killed without touching
`ProcessPoolExecutor`'s private `_processes`, so budget for permanent worker degradation and cap the
pool. Current implementation lives in `obsidian_mcp/utils/persistent_index.py`
(`REGEX_MATCH_TIMEOUT_SECONDS`), with the matcher functions kept plain/picklable in
`obsidian_mcp/utils/index_text.py`.
