"""Regression tests for regex-pool workers outliving the obsidian-mcp server.

Every spawned pool worker holds BOTH ends of the pool's call-queue pipe, so
when the server dies abruptly (SIGKILL, or SIGTERM with the default
disposition: no Python cleanup runs) a worker never sees EOF and stays blocked
in call_queue.get() forever -- observed as 4 workers plus the multiprocessing
resource tracker still alive, parented to PID 1, 10+ hours after the server
was gone. Cleanup in the dying process can never cover SIGKILL, so the workers
have to notice the parent's death themselves.

Cases:
  A  idle workers and the resource tracker exit after the server is killed
  B  so does a worker wedged inside `re` (it holds the GIL, which starves the
     watchdog thread, so the kernel-enforced backstop has to end it)
  C  once that backstop has killed a worker the pool is rebuilt, so the next
     regex search still works
  D  app.main() kills the regex pool however mcp.run() ends
  E  when a worker dies with several tasks in flight, only the first to
     notice recycles the pool and the rest retry on its replacement; a file
     whose worker dies on both attempts is skipped instead of failing the
     search

A and B run the server as a real subprocess (_regex_pool_orphan_helper.py),
because the failure only exists when the whole process dies.

Platforms: A and B probe PIDs with os.kill(pid, 0), which TERMINATES the
process on Windows, so they are POSIX-only. B and C need the worker backstop,
which needs signal.setitimer (POSIX only). D and E run everywhere.
"""

import asyncio
import contextlib
import json
import logging
import os
import select
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
import pytest_asyncio

# Bare sibling import: relies on pytest's default "prepend" import mode (no
# tests/__init__.py, no ini config); breaks under --import-mode=importlib.
from _regex_pool_orphan_helper import (
    COLD_START_TIMEOUT_SECONDS,
    NORMAL_NOTE_COUNT,
    PATHOLOGICAL_PATTERN,
    WEDGE_TIMEOUT_SECONDS,
    populate_index,
)

from obsidian_mcp.utils import persistent_index as persistent_index_module
from obsidian_mcp.utils.persistent_index import (
    _REGEX_POOL_MAX_WORKERS,
    PersistentSearchIndex,
)

HELPER_SCRIPT = Path(__file__).with_name("_regex_pool_orphan_helper.py")
# Helper start-up + pool warm-up + (in wedged mode) one full timeout.
HELPER_REPORT_TIMEOUT_SECONDS = 60.0
# Slack on top of a process's own deadline. Only ever paid in full by a
# failing run: the polls below return as soon as everything is gone.
DEADLINE_MARGIN_SECONDS = 10.0
# Idle workers exit off a blocking wait on the parent sentinel (no polling),
# so this bound has ample slack even on a loaded runner.
IDLE_EXIT_DEADLINE_SECONDS = 5.0

posix_only = pytest.mark.skipif(
    sys.platform == "win32",
    reason="PID liveness probe via os.kill(pid, 0) is POSIX-only",
)
needs_backstop = pytest.mark.skipif(
    not hasattr(signal, "setitimer"),
    reason="the worker backstop needs signal.setitimer (POSIX only)",
)


def _is_alive(pid: int) -> bool:
    """POSIX only: on Windows os.kill(pid, 0) TERMINATES the process.

    A zombie counts as dead: orphaned workers are reparented to PID 1, which
    in a bare container may never reap them.
    """
    try:
        os.kill(pid, 0)
        stat = Path(f"/proc/{pid}/stat").read_text()
    except ProcessLookupError:
        return False
    except OSError:  # no /proc (macOS): the kill above already vouched for it
        return True
    # The process name (field 2) may contain spaces and parentheses, so parse
    # what follows the last ")".
    return stat.rpartition(")")[2].split()[0] != "Z"


def _survivors_after_sigkill(
    tmp_path: Path, helper_mode: str, deadline_seconds: float
) -> list[int]:
    """Run the helper server, kill it once it has reported its pool PIDs, and
    return those PIDs (workers + resource tracker) still alive
    `deadline_seconds` later. POSIX only (see _is_alive).

    Whatever survives is SIGKILLed before returning, so a RED run never
    leaves orphans behind on the dev machine.
    """
    pids: list[int] = []  # stays empty if the helper dies before reporting
    stderr_path = tmp_path / "helper.stderr"
    with (
        stderr_path.open("w") as stderr,
        subprocess.Popen(
            [sys.executable, str(HELPER_SCRIPT), helper_mode],
            env={**os.environ, "OBSIDIAN_VAULT_PATH": str(tmp_path)},
            stdout=subprocess.PIPE,
            stderr=stderr,
            text=True,
        ) as helper,
    ):
        try:
            assert helper.stdout is not None
            # Bounded wait: the helper's workers inherit its stdout, so a
            # blocking readline() could hang the suite if the helper died.
            ready, _, _ = select.select(
                [helper.stdout], [], [], HELPER_REPORT_TIMEOUT_SECONDS
            )
            line = helper.stdout.readline() if ready else ""
            assert line, f"helper never reported its PIDs:\n{stderr_path.read_text()}"
            report = json.loads(line)
            # The resource tracker is null where multiprocessing has none.
            tracker = report["tracker"]
            # Recorded before any assert, so the finally sweeps them even if
            # one of the asserts fails.
            pids = report["workers"] + ([] if tracker is None else [tracker])
            # A pool that never reached full size would weaken the test.
            assert len(report["workers"]) == _REGEX_POOL_MAX_WORKERS

            helper.kill()
            helper.wait()

            deadline = time.monotonic() + deadline_seconds
            while any(map(_is_alive, pids)) and time.monotonic() < deadline:
                time.sleep(0.1)
            return [pid for pid in pids if _is_alive(pid)]
        finally:
            helper.kill()
            # Skip PIDs that already exited.
            for pid in filter(_is_alive, pids):
                with contextlib.suppress(ProcessLookupError):
                    os.kill(pid, signal.SIGKILL)


@pytest_asyncio.fixture
async def test_index(tmp_path):
    index = PersistentSearchIndex(tmp_path)
    await index.initialize()
    yield index
    await index.close()


class TestWorkersDieWithTheServer:
    @posix_only
    def test_idle_workers_and_tracker_exit_when_server_is_killed(self, tmp_path):
        survivors = _survivors_after_sigkill(
            tmp_path, "idle", IDLE_EXIT_DEADLINE_SECONDS
        )

        assert survivors == [], f"pool processes outlived the server: {survivors}"

    @posix_only
    @needs_backstop
    def test_wedged_worker_exits_when_server_is_killed(self, tmp_path):
        deadline = 2 * WEDGE_TIMEOUT_SECONDS + DEADLINE_MARGIN_SECONDS

        survivors = _survivors_after_sigkill(tmp_path, "wedged", deadline)

        assert survivors == [], f"pool processes outlived the server: {survivors}"


class TestPoolRecoversAfterBackstopKill:
    @needs_backstop
    @pytest.mark.asyncio
    async def test_next_search_works_after_a_wedged_worker_is_killed(
        self, test_index, monkeypatch
    ):
        await populate_index(test_index)
        # Bring every worker up while the timeout still leaves room for the
        # cold-start latency of a spawned interpreter.
        monkeypatch.setattr(
            persistent_index_module,
            "REGEX_MATCH_TIMEOUT_SECONDS",
            COLD_START_TIMEOUT_SECONDS,
        )
        await test_index.search_regex("quick", max_parallel=4, limit=10)
        pool = test_index._regex_process_pool
        assert pool is not None
        workers = list(pool._processes.values())
        assert len(workers) == _REGEX_POOL_MAX_WORKERS

        # Wedge one worker. A single pathological file stays well below the
        # consecutive-timeout recycle threshold, so the pool is NOT recycled
        # by that mechanism -- only the worker's own backstop can end it.
        timeout = 0.5
        monkeypatch.setattr(
            persistent_index_module, "REGEX_MATCH_TIMEOUT_SECONDS", timeout
        )
        await test_index.search_regex(PATHOLOGICAL_PATTERN, max_parallel=4, limit=10)

        # The backstop kills the wedged worker; the executor then declares
        # the whole pool broken and terminates the other workers too.
        deadline = time.monotonic() + 2 * timeout + DEADLINE_MARGIN_SECONDS
        while any(worker.is_alive() for worker in workers):
            assert time.monotonic() < deadline, "backstop never killed the worker"
            await asyncio.sleep(0.05)

        # The pool object is still the broken one. A fresh pool needs a cold
        # start, so the timeout goes back to a value that leaves room for it.
        monkeypatch.setattr(
            persistent_index_module,
            "REGEX_MATCH_TIMEOUT_SECONDS",
            COLD_START_TIMEOUT_SECONDS,
        )
        results = await test_index.search_regex("quick", max_parallel=4, limit=10)

        assert {result["filepath"] for result in results} == {
            f"normal-{i}.md" for i in range(NORMAL_NOTE_COUNT)
        }


class _StubIndex:
    def __init__(self) -> None:
        self.kill_calls = 0

    def kill_regex_pool(self) -> None:
        self.kill_calls += 1


class _StubVault:
    def __init__(self, persistent_index: _StubIndex | None) -> None:
        self.persistent_index = persistent_index


class TestMainKillsRegexPool:
    @pytest.mark.parametrize("run_raises", [False, True], ids=["returns", "raises"])
    def test_kills_pool_exactly_once_however_run_ends(self, monkeypatch, run_raises):
        from obsidian_mcp import app as app_module

        index = _StubIndex()

        def fake_run(*args, **kwargs):
            if run_raises:
                raise RuntimeError("server crashed")

        monkeypatch.setattr(app_module.mcp, "run", fake_run)
        monkeypatch.setattr(app_module, "get_vault", lambda: _StubVault(index))

        if run_raises:
            with pytest.raises(RuntimeError, match="server crashed"):
                app_module.main()
        else:
            app_module.main()

        assert index.kill_calls == 1

    def test_skips_kill_when_index_was_never_initialized(self, monkeypatch):
        from obsidian_mcp import app as app_module

        monkeypatch.setattr(app_module.mcp, "run", lambda *args, **kwargs: None)
        monkeypatch.setattr(app_module, "get_vault", lambda: _StubVault(None))

        app_module.main()  # must not raise


def _hang_on_first_attempt(marker_dir: str, name: str) -> list:
    """Worker body for the identity-guard test. The first attempt for `name`
    leaves a marker and hangs, like a worker still busy when the pool breaks;
    the retry finds the marker and returns at once. Module-level because
    spawn pickles callables by qualified name.
    """
    marker = Path(marker_dir, name)
    if marker.exists():
        return []
    marker.touch()
    time.sleep(300)
    return []


class _WorkerKillingRegex:
    """Stands in for a compiled regex whose match takes its worker process
    down, as an OOM kill would. Module-level so it can be pickled."""

    def finditer(self, content):
        os._exit(1)


class TestBrokenPoolRecovery:
    @pytest.mark.asyncio
    async def test_only_the_first_task_to_notice_recycles_the_pool(
        self, test_index, monkeypatch, caplog, tmp_path
    ):
        monkeypatch.setattr(
            persistent_index_module,
            "REGEX_MATCH_TIMEOUT_SECONDS",
            COLD_START_TIMEOUT_SECONDS,
        )
        caplog.set_level(logging.WARNING, logger=persistent_index_module.__name__)
        marker_dir = tmp_path / "markers"
        marker_dir.mkdir()
        tasks = [
            asyncio.ensure_future(
                test_index._match_in_regex_pool(
                    _hang_on_first_attempt, str(marker_dir), f"t{i}"
                )
            )
            for i in range(_REGEX_POOL_MAX_WORKERS)
        ]
        # Every task must be inside its worker before one worker is killed.
        deadline = time.monotonic() + COLD_START_TIMEOUT_SECONDS
        while len(list(marker_dir.iterdir())) < _REGEX_POOL_MAX_WORKERS:
            assert time.monotonic() < deadline, "workers never started"
            await asyncio.sleep(0.05)
        pool = test_index._regex_process_pool
        assert pool is not None
        next(iter(pool._processes.values())).kill()

        # The executor breaks, so all four tasks get BrokenProcessPool. Only
        # the first may recycle the pool: the other three must retry on the
        # replacement it created, not recycle that one too.
        results = await asyncio.gather(*tasks, return_exceptions=True)

        assert results == [[]] * _REGEX_POOL_MAX_WORKERS
        recycles = [
            record
            for record in caplog.records
            if "after a worker process died" in record.getMessage()
        ]
        assert len(recycles) == 1
        # And the replacement pool is healthy.
        assert (
            await test_index._match_in_regex_pool(
                _hang_on_first_attempt, str(marker_dir), "t0"
            )
            == []
        )

    @pytest.mark.asyncio
    async def test_file_is_skipped_when_its_worker_dies_on_both_attempts(
        self, test_index, monkeypatch, caplog
    ):
        monkeypatch.setattr(
            persistent_index_module,
            "REGEX_MATCH_TIMEOUT_SECONDS",
            COLD_START_TIMEOUT_SECONDS,
        )
        caplog.set_level(logging.WARNING, logger=persistent_index_module.__name__)

        outcome = await test_index._process_file_regex(
            "doomed.md", "text", 4, None, _WorkerKillingRegex(), 100
        )

        assert outcome == {"filepath": "doomed.md", "match_contexts": []}
        assert "died on two consecutive attempts" in caplog.text
        # The pool it left behind was recycled, so an ordinary search works.
        await test_index.index_file("ok.md", "The quick brown fox", 1000.0, 19)
        results = await test_index.search_regex("quick", max_parallel=4, limit=10)
        assert [hit["filepath"] for hit in results] == ["ok.md"]
