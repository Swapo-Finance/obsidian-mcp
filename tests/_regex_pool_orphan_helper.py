"""Helper subprocess for test_regex_pool_orphans.py (pytest does not collect it).

Indexes a few notes into $OBSIDIAN_VAULT_PATH, brings the regex process pool
up, prints ONE JSON line {"workers": [pid, ...], "tracker": pid-or-null} and
then sleeps until the test kills it (or MAX_LIFETIME_SECONDS pass).

    python _regex_pool_orphan_helper.py idle
        every worker is idle when the line is printed.
    python _regex_pool_orphan_helper.py wedged
        one worker is stuck inside a catastrophic-backtracking match (holding
        the GIL) when the line is printed.

"tracker" is null where multiprocessing has no resource tracker (Windows).

The spawned workers re-import this file as __mp_main__, hence the
__name__ guard at the bottom.
"""

import asyncio
import json
import logging
import os
import sys
from multiprocessing import resource_tracker
from pathlib import Path
from typing import Any

from obsidian_mcp.utils import persistent_index as persistent_index_module
from obsidian_mcp.utils.persistent_index import PersistentSearchIndex

# "(a+)+$" over a run of 'a' ended by '!' backtracks exponentially: n=28
# already takes ~6s, so n=40 outlasts every deadline in the tests.
PATHOLOGICAL_PATTERN = r"(a+)+$"
PATHOLOGICAL_CONTENT = "a" * 40 + "!\n"
NORMAL_NOTE_COUNT = 5
# Per-file timeout while a pool is still cold: spawning an interpreter and
# importing the package can take seconds on a loaded CI runner.
COLD_START_TIMEOUT_SECONDS = 30.0
# Per-file timeout in "wedged" mode. The worker backstop fires at twice this,
# so the test gets this long (minus startup jitter) to kill the helper while
# the wedged worker is still alive.
WEDGE_TIMEOUT_SECONDS = 2.0
# How long the helper stays up if the test never kills it (say, `kill -9` on
# pytest): long enough for any test, short enough not to leave a server and
# its workers around.
MAX_LIFETIME_SECONDS = 120.0


async def populate_index(index: PersistentSearchIndex) -> None:
    """Index one pathological note and NORMAL_NOTE_COUNT ordinary ones.

    search_regex batches files by ascending size, 4 at a time, so the
    pathological note (size 1) lands in the first batch next to 3 ordinary
    notes. Only the ordinary ones contain "quick".
    """
    await index.index_file("pathological.md", PATHOLOGICAL_CONTENT, 1000.0, 1)
    for i in range(NORMAL_NOTE_COUNT):
        await index.index_file(
            f"normal-{i}.md", f"The quick brown fox {i}\n", 2000.0 + i, 100 + i
        )


class _TimeoutLog(logging.Handler):
    """Counts the per-file timeouts _process_file_regex logs."""

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.timeouts = 0

    def emit(self, record: logging.LogRecord) -> None:
        if "possible catastrophic backtracking" in record.getMessage():
            self.timeouts += 1


async def run(mode: str) -> None:
    index = PersistentSearchIndex(Path(os.environ["OBSIDIAN_VAULT_PATH"]))
    await index.initialize()
    await populate_index(index)

    # Bring every worker up while the timeout still leaves room for the
    # cold-start latency of a spawned interpreter.
    persistent_index_module.REGEX_MATCH_TIMEOUT_SECONDS = COLD_START_TIMEOUT_SECONDS
    await index.search_regex("quick", max_parallel=4, limit=10)

    if mode == "wedged":
        timeouts = _TimeoutLog()
        logging.getLogger(persistent_index_module.__name__).addHandler(timeouts)
        persistent_index_module.REGEX_MATCH_TIMEOUT_SECONDS = WEDGE_TIMEOUT_SECONDS
        await index.search_regex(PATHOLOGICAL_PATTERN, max_parallel=4, limit=10)
        # Guards against a vacuous scenario: the pathological note MUST have
        # timed out (exactly once, so the recycle threshold stays out of play).
        if timeouts.timeouts != 1:
            sys.exit(f"expected exactly 1 timed-out file, saw {timeouts.timeouts}")

    # Private attributes on purpose: neither exposes a public handle. The
    # tracker is Any-typed because typeshed declares no `_pid` on it.
    pool = index._regex_process_pool
    tracker: Any = resource_tracker._resource_tracker
    if pool is None:
        sys.exit("regex pool was never started")
    print(
        json.dumps({"workers": sorted(pool._processes), "tracker": tracker._pid}),
        flush=True,
    )
    await asyncio.sleep(MAX_LIFETIME_SECONDS)
    # Close explicitly so that exiting never depends on garbage collection: a
    # still-referenced aiosqlite connection keeps the interpreter alive.
    await index.close()


if __name__ == "__main__":
    asyncio.run(run(sys.argv[1]))
