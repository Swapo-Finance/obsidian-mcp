#!/usr/bin/env python3
"""Security regression tests for search_by_regex.

Covers two pre-existing findings fixed together:

1. ReDoS / event-loop-blocking DoS: a catastrophic-backtracking pattern must
   never block the caller indefinitely. Fixed by running the actual regex
   match in a ProcessPoolExecutor worker under a wall-clock timeout (see
   persistent_index.py's REGEX_MATCH_TIMEOUT_SECONDS and _process_file_regex),
   plus a fast pre-check in tools/search_regex.py for the most obvious
   nested-quantifier shapes and an overall pattern length cap.

   A ThreadPoolExecutor was tried first and rejected: CPython's `re` engine
   holds the GIL for the entire duration of a match, so a thread-based
   timeout frees nothing -- verified empirically during investigation, a
   catastrophic match froze the event loop for its full duration even when
   dispatched via loop.run_in_executor to a thread. Only a separate process
   (its own GIL) actually keeps the event loop responsive.

2. FTS5 query-language injection: extract_literal_prefix() used to let a
   literal '"' through into the FTS5 pre-filter phrase, letting a pattern
   like 'foo" OR bar' escape the intended quoting and be reinterpreted as
   FTS5 query syntax (confirmed pre-fix: raises sqlite3.OperationalError:
   unterminated string). Fixed by treating '"' as a stop character during
   literal-prefix extraction, so the extracted literal can never contain one.
"""

import asyncio
import time

import pytest
import pytest_asyncio

from obsidian_mcp.tools.search_regex import search_by_regex
from obsidian_mcp.utils import persistent_index as persistent_index_module
from obsidian_mcp.utils.index_text import extract_literal_prefix
from obsidian_mcp.utils.persistent_index import PersistentSearchIndex


@pytest_asyncio.fixture
async def test_index(tmp_path):
    index = PersistentSearchIndex(tmp_path)
    await index.initialize()
    yield index
    await index.close()


class TestRegexTimeoutProtection:
    """Finding 1 (HIGH): no single search_by_regex call may block indefinitely."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "forced_size", [200, 2_000_000], ids=["small-file-path", "large-file-path"]
    )
    async def test_catastrophic_pattern_returns_within_bounded_time(
        self, test_index, monkeypatch, forced_size
    ):
        # Shrink the per-file timeout: what's under test is "a bound is
        # enforced", not the specific production value of the bound. This
        # keeps the test fast and keeps the orphaned worker's background
        # runtime (it can't be force-killed -- see REGEX_MATCH_TIMEOUT_SECONDS)
        # short too.
        monkeypatch.setattr(persistent_index_module, "REGEX_MATCH_TIMEOUT_SECONDS", 0.2)

        # Classic (a+)+$ trigger: a run of 'a' long enough to blow up
        # combinatorially, followed by a character that forces exhaustive
        # backtracking before the match can fail. n=25 measured ~1.7s
        # unprotected via regex.finditer() on a typical dev machine --
        # comfortably more than the 0.2s timeout above (~8x margin), so a
        # regression (timeout not applied) would fail the elapsed-time
        # assertion below instead of passing by luck.
        trigger = "a" * 25 + "!"
        # `forced_size` is independent of the real content length -- it's
        # what _process_file_regex branches on to pick search_file_content
        # vs. search_large_file_content, so parametrizing it proves BOTH
        # worker functions respect the timeout, not just whichever one
        # small test content would naturally hit.
        await test_index.index_file(
            "pathological.md",
            f"# Notes\n\nOrdinary text, then a long run: {trigger}\n",
            1000.0,
            forced_size,
        )

        start = time.monotonic()
        # Outer safety net: if the timeout mechanism regresses completely,
        # this fails at 20s instead of hanging the whole suite.
        results = await asyncio.wait_for(
            test_index.search_regex(r"(a+)+$", max_parallel=4, limit=10),
            timeout=20.0,
        )
        elapsed = time.monotonic() - start

        # Well under the pattern's true ~1.7s completion time -- proves we
        # returned via the timeout, not by waiting it out.
        assert elapsed < 1.5
        # A timed-out file contributes zero matches rather than an
        # exception: bounded-but-empty is the documented behavior.
        assert results == []

    @pytest.mark.asyncio
    async def test_rejects_obvious_nested_quantifier_pattern_immediately(self):
        # Defense-in-depth pre-check in tools/search_regex.py: must fail
        # fast, well before ever reaching the process pool/timeout.
        start = time.monotonic()
        with pytest.raises(ValueError, match="catastrophic backtracking"):
            await search_by_regex(r"(a+)+$")
        assert time.monotonic() - start < 1.0

    @pytest.mark.asyncio
    async def test_rejects_pattern_over_max_length(self):
        with pytest.raises(ValueError, match="too long"):
            await search_by_regex("a" * 501)

    @pytest.mark.asyncio
    async def test_pool_recycles_after_consecutive_timeouts(
        self, test_index, monkeypatch
    ):
        """Finding 2 (code review): ProcessPoolExecutor only replaces a
        worker on crash, never on "permanently busy" -- so enough
        pathological patterns in a row can wedge every worker forever, and
        every later regex search would silently degrade to an empty result
        for the rest of the process's life. Enough consecutive per-file
        timeouts must recycle the whole pool instead."""
        monkeypatch.setattr(persistent_index_module, "REGEX_MATCH_TIMEOUT_SECONDS", 0.1)
        threshold = persistent_index_module._REGEX_POOL_RECYCLE_THRESHOLD

        # n=28 measured ~5.8s unprotected on a typical dev machine (doubling
        # per +1 'a', consistent with test_catastrophic_pattern_returns_...'s
        # n=25 ~= 1.7s) -- comfortably longer than the timeouts below, so a
        # worker still crunching on this is a reliable stand-in for "wedged"
        # without actually waiting that long anywhere in this test.
        trigger = "a" * 28 + "!"
        for i in range(threshold):
            await test_index.index_file(
                f"pathological_{i}.md",
                f"# Notes\n\n{trigger}\n",
                1000.0 + i,
                200,
            )

        pool_before = test_index._get_regex_process_pool()

        # One batch of exactly `threshold` concurrent pathological files --
        # matches the pool's worker count (search_regex now clamps
        # max_parallel to it), so all of them time out together and should
        # trip the recycle threshold.
        await asyncio.wait_for(
            test_index.search_regex(r"(a+)+$", max_parallel=threshold, limit=10),
            timeout=10.0,
        )
        assert test_index._consecutive_regex_timeouts == 0
        assert test_index._regex_process_pool is None  # discarded by the recycle

        # The real proof: an ordinary, fast pattern searched right after
        # must still find its match. Pre-fix, this call would have reused
        # the same wedged pool (all 4 workers still busy on the n=28
        # pathological pattern above, ~5.8s from finishing naturally) and
        # silently returned an empty result within the timeout window --
        # exactly the "every later regex search... degrades to a
        # timeout-empty-result" failure mode from the review. 2.0s here is
        # deliberately generous relative to the ~0.1-0.2s a fresh pool
        # actually needs to spawn a worker and run this trivial match
        # (measured), while staying far short of the ~5.8s the old,
        # never-recycled pool would need to free up on its own -- so this
        # timeout distinguishes "recycled" from "still wedged" rather than
        # just waiting long enough for either to succeed.
        monkeypatch.setattr(persistent_index_module, "REGEX_MATCH_TIMEOUT_SECONDS", 2.0)
        await test_index.index_file("ordinary.md", "The quick brown fox", 5000.0, 50)
        results = await asyncio.wait_for(
            test_index.search_regex(r"quick", max_parallel=4, limit=10),
            timeout=8.0,
        )
        assert len(results) == 1
        assert results[0]["filepath"] == "ordinary.md"
        assert test_index._regex_process_pool is not None
        assert test_index._regex_process_pool is not pool_before


class TestFts5LiteralPrefixEscaping:
    """Finding 2 (MEDIUM): a '"' in the pattern must not corrupt the FTS5
    pre-filter query built from extract_literal_prefix()'s output."""

    def test_extract_literal_prefix_stops_at_quote(self):
        assert extract_literal_prefix('foo" OR bar') == "foo"
        assert extract_literal_prefix('"section":') is None
        assert extract_literal_prefix('abcd"efgh') == "abcd"
        # Unaffected: no quote at all, unchanged from before the fix.
        assert extract_literal_prefix("hello world") == "hello world"

    @pytest.mark.asyncio
    async def test_quote_containing_pattern_does_not_break_search(self, test_index):
        await test_index.index_file(
            "note.md",
            'This note mentions a foo" OR bar phrase inline for testing.',
            1000.0,
            100,
        )
        await test_index.index_file(
            "other.md", "Unrelated content with neither trigger word.", 1000.0, 100
        )

        # Pre-fix, this raised sqlite3.OperationalError: unterminated string
        # (confirmed against the unpatched code during investigation) -- the
        # unescaped '"' broke out of the intended FTS5 phrase quoting.
        results = await test_index.search_regex(
            r'foo" OR bar', max_parallel=10, limit=20
        )

        assert len(results) == 1
        assert results[0]["filepath"] == "note.md"
        assert results[0]["matches"][0]["match"] == 'foo" OR bar'


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
