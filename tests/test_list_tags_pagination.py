#!/usr/bin/env python3
"""Pagination coverage for list_tags (obsidian_mcp/tools/organization.py) —
the offset/limit slicing added on top of the existing sort_by/include_counts/
include_files behavior (already covered elsewhere, e.g.
test_onda1_sanity.py's TestVaultCacheIncrementalUpdate).

Calls the list_tags impl function directly rather than the list_tags_tool
MCP wrapper: the wrapper just forwards offset/limit straight through (see
server.py), so testing the impl is sufficient and avoids server.py's
import-time OBSIDIAN_VAULT_PATH bootstrap dance that test_server_tool_wrappers.py
needs for wrapper-level (ValueError->ToolError) behavior.

Fixture pattern (tempfile.mkdtemp + init_vault + shutil.rmtree teardown) is
the same one every other test file in this suite uses.

Also covers three fixes to the same function:
- max_files_per_tag: per-tag `files` truncation + `files_total` (true
  per-tag count, set independent of include_counts).
- sort_by="count" now honored in the include_counts=False/include_files=False
  shortcut branch (previously silently ignored, always name-sorted).
- sort_by="count" in the item-building branch now keys off the
  authoritative tag_counts dict instead of the optional per-item "count"
  field (previously x.get("count", 0), which silently fell back to
  insertion order once include_counts=False dropped "count" from every
  item). TestListTagsSortOrderFlagMatrix below pins both this and the
  prior fix across the full (include_counts, include_files) matrix so
  neither bug class can silently return.
"""

import os
import shutil
import tempfile

import pytest
import pytest_asyncio

from obsidian_mcp.tools.note_management import create_note
from obsidian_mcp.tools.organization import list_tags
from obsidian_mcp.utils.filesystem import init_vault

# 14 distinct tags: comfortably over the ">=12" needed to exceed a small
# limit, and 14 % 5 = 4 gives a clean partial-last-page case at limit=5
# (pages of 5, 5, 4) without needing a second fixture just for that.
TAG_COUNT = 14
TAG_NAMES = [f"tag-{i:02d}" for i in range(TAG_COUNT)]  # zero-padded -> already name-sorted

# 8 files on one tag: comfortably over a small max_files_per_tag (e.g. 3) to
# exercise truncation, zero-padded so sorted() order is predictable.
POPULAR_FILES = [f"Popular{i:02d}.md" for i in range(8)]

# All 4 combinations of the two flags that gate which optional keys
# list_tags puts on each item ("count" from include_counts; "files"/
# "files_total" from include_files). These flags must only affect which
# fields are present -- never the sort -- so every ordering assertion in
# TestListTagsSortOrderFlagMatrix loops over all 4.
FLAG_COMBINATIONS = [
    (True, True),
    (True, False),
    (False, True),
    (False, False),
]


@pytest_asyncio.fixture
async def many_tags_vault():
    """One tag per note, 14 notes -> 14 distinct tags, each with count=1."""
    temp_dir = tempfile.mkdtemp(prefix="obsidian_list_tags_page_")
    os.environ["OBSIDIAN_REQUIRE_FRONTMATTER"] = "false"
    vault = init_vault(temp_dir)

    for i, tag in enumerate(TAG_NAMES):
        await create_note(f"Note{i:02d}.md", f"---\ntags: [{tag}]\n---\n# Note {i}\n")

    yield vault

    os.environ.pop("OBSIDIAN_REQUIRE_FRONTMATTER", None)
    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest_asyncio.fixture
async def count_ordered_vault():
    """3 tags whose alphabetical order is the reverse of their count order:
    alpha=1 file, bravo=2 files, charlie=3 files.

    name-asc:   alpha, bravo, charlie
    count-desc: charlie(3), bravo(2), alpha(1)  <- reverse of name order

    Isolated from many_tags_vault so proving sort_by="count" slices don't
    need 100+ files just to force a distinguishable count spread.
    """
    temp_dir = tempfile.mkdtemp(prefix="obsidian_list_tags_count_")
    os.environ["OBSIDIAN_REQUIRE_FRONTMATTER"] = "false"
    vault = init_vault(temp_dir)

    await create_note("A0.md", "---\ntags: [alpha]\n---\n# A0\n")
    await create_note("B0.md", "---\ntags: [bravo]\n---\n# B0\n")
    await create_note("B1.md", "---\ntags: [bravo]\n---\n# B1\n")
    await create_note("C0.md", "---\ntags: [charlie]\n---\n# C0\n")
    await create_note("C1.md", "---\ntags: [charlie]\n---\n# C1\n")
    await create_note("C2.md", "---\ntags: [charlie]\n---\n# C2\n")

    yield vault

    os.environ.pop("OBSIDIAN_REQUIRE_FRONTMATTER", None)
    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest_asyncio.fixture
async def heavy_tag_vault():
    """One tag ('popular') on 8 files -> truncation target for
    max_files_per_tag; one tag ('rare') on 1 file -> untruncated control.

    Isolated from the other fixtures so cap tests don't have to reason about
    14 unrelated tags or the count-ordering fixture's file counts.
    """
    temp_dir = tempfile.mkdtemp(prefix="obsidian_list_tags_cap_")
    os.environ["OBSIDIAN_REQUIRE_FRONTMATTER"] = "false"
    vault = init_vault(temp_dir)

    for name in POPULAR_FILES:
        await create_note(name, "---\ntags: [popular]\n---\n# Popular\n")
    await create_note("Rare0.md", "---\ntags: [rare]\n---\n# Rare\n")

    yield vault

    os.environ.pop("OBSIDIAN_REQUIRE_FRONTMATTER", None)
    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest_asyncio.fixture
async def adversarial_order_vault():
    """3 tags whose discovery order, name-asc order, and count-desc order
    are all mutually different, so a result matching any one of the three
    orders can't be mistaken for matching another:

      discovery (vault cache insertion order): zulu, mike, alpha
      count-desc:                              zulu(3), alpha(2), mike(1)
      name-asc:                                alpha, mike, zulu

    Verified empirically (.claude/tmp scratch script, not checked in) that
    discovery order here is creation-call order, NOT filesystem walk order
    -- and that the two only coincide because of the priming read below.
    VaultCache.note_mutated() is a no-op until the cache has been built
    once (see vault_cache.py); left alone, the first create_note() call
    would NOT build it, so all 6 notes get indexed together in one deferred
    _full_scan_locked() pass ordered by os.walk()'s filesystem directory
    enumeration -- unspecified by Python, observed to differ from both
    creation order AND name/count order on this machine, and not
    guaranteed to reproduce the same way on Linux CI. The
    `await vault.cache.get_tags_index()` call below, issued while the
    vault is still empty, forces that full scan early (0 files, so it's a
    no-op index-wise) and flips _built=True *before* any note exists.
    Every create_note() after that routes through the incremental
    note_mutated()->_index_note() path instead, which does
    `self._tags_index.setdefault(tag, set()).add(relpath)` on a plain
    dict -- deterministic, creation-order-driven, and portable. That is
    what pins discovery order to zulu (1st note), mike (2nd note), alpha
    (3rd note), regardless of platform.

    This is the exact fixture shape used to verify the round-2 fix
    (organization.py sorting off tag_counts instead of the optional
    per-item "count" key).

    Unlike count_ordered_vault (discovery order there happens to equal
    name-asc order, since notes were created in already-alphabetical
    order), this fixture keeps all three orders distinct -- required so
    TestListTagsSortOrderFlagMatrix can't accidentally pass by matching
    the wrong order.
    """
    temp_dir = tempfile.mkdtemp(prefix="obsidian_list_tags_matrix_")
    os.environ["OBSIDIAN_REQUIRE_FRONTMATTER"] = "false"
    vault = init_vault(temp_dir)

    # Prime the cache while the vault is empty (see docstring) so tag
    # discovery order below is deterministic creation-call order instead
    # of unspecified filesystem walk order.
    await vault.cache.get_tags_index()

    await create_note("Zulu0.md", "---\ntags: [zulu]\n---\n# Zulu0\n")
    await create_note("Mike0.md", "---\ntags: [mike]\n---\n# Mike0\n")
    await create_note("Alpha0.md", "---\ntags: [alpha]\n---\n# Alpha0\n")
    await create_note("Zulu1.md", "---\ntags: [zulu]\n---\n# Zulu1\n")
    await create_note("Zulu2.md", "---\ntags: [zulu]\n---\n# Zulu2\n")
    await create_note("Alpha1.md", "---\ntags: [alpha]\n---\n# Alpha1\n")

    yield vault

    os.environ.pop("OBSIDIAN_REQUIRE_FRONTMATTER", None)
    shutil.rmtree(temp_dir, ignore_errors=True)


def _tag_names(result: dict) -> list:
    """Normalize a list_tags() result to a plain list of tag-name strings.

    The item-building branch (include_counts or include_files truthy)
    yields {"name": ..., ...} dicts; the names-only shortcut branch
    (both False) yields bare name strings instead. Matrix assertions need
    both shapes reduced to the same thing to compare apples to apples.
    """
    return [item if isinstance(item, str) else item["name"] for item in result["items"]]


class TestListTagsPaginationDefaultPage:
    @pytest.mark.asyncio
    async def test_small_limit_returns_exactly_limit_items_with_total_unclipped(self, many_tags_vault):
        result = await list_tags(offset=0, limit=5)

        assert len(result["items"]) == 5
        assert result["returned"] == 5
        assert result["total"] == TAG_COUNT  # total is vault-wide, never just the page
        assert result["offset"] == 0
        assert result["limit"] == 5


class TestListTagsPaginationWalksFullSet:
    @pytest.mark.asyncio
    async def test_concatenated_pages_equal_full_list_no_overlap_no_gaps(self, many_tags_vault):
        full = await list_tags(sort_by="name", offset=0, limit=1000)
        all_names = [t["name"] for t in full["items"]]
        assert all_names == TAG_NAMES  # sanity: fixture built the vault we think it did

        page1 = await list_tags(sort_by="name", offset=0, limit=5)
        page2 = await list_tags(sort_by="name", offset=5, limit=5)
        page3 = await list_tags(sort_by="name", offset=10, limit=5)

        walked = (
            [t["name"] for t in page1["items"]]
            + [t["name"] for t in page2["items"]]
            + [t["name"] for t in page3["items"]]
        )

        assert walked == all_names
        assert len(walked) == len(set(walked)) == TAG_COUNT

    @pytest.mark.asyncio
    async def test_last_page_is_partial_when_total_not_a_multiple_of_limit(self, many_tags_vault):
        last_page = await list_tags(sort_by="name", offset=10, limit=5)

        assert last_page["returned"] == 4  # 14 - 10 = 4 < limit
        assert len(last_page["items"]) == 4
        assert last_page["total"] == TAG_COUNT


class TestListTagsPaginationOffsetBeyondEnd:
    @pytest.mark.asyncio
    async def test_offset_past_total_returns_empty_page_with_total_unchanged(self, many_tags_vault):
        result = await list_tags(offset=100, limit=5)

        assert result["items"] == []
        assert result["returned"] == 0
        assert result["total"] == TAG_COUNT


class TestListTagsPaginationOrderingBeforeSlicing:
    @pytest.mark.asyncio
    async def test_slice_is_correct_alphabetical_window_when_sorted_by_name(self, many_tags_vault):
        result = await list_tags(sort_by="name", offset=3, limit=4)

        assert [t["name"] for t in result["items"]] == TAG_NAMES[3:7]

    @pytest.mark.asyncio
    async def test_slice_is_correct_count_window_when_sorted_by_count(self, count_ordered_vault):
        result = await list_tags(sort_by="count", offset=0, limit=2)

        assert [t["name"] for t in result["items"]] == ["charlie", "bravo"]


class TestListTagsPaginationIncludeFiles:
    @pytest.mark.asyncio
    async def test_paged_items_still_carry_their_own_files_list(self, many_tags_vault):
        # Per-tag `files` lists are intentionally NOT capped by limit/offset —
        # limit/offset only page the tag list itself. No cap assertion here.
        result = await list_tags(include_files=True, sort_by="name", offset=0, limit=3)

        assert len(result["items"]) == 3
        for i, item in enumerate(result["items"]):
            assert item["files"] == [f"Note{i:02d}.md"]


class TestListTagsMaxFilesPerTagCap:
    @pytest.mark.asyncio
    async def test_files_capped_and_files_total_is_true_count_when_over_cap(self, heavy_tag_vault):
        result = await list_tags(include_files=True, max_files_per_tag=3)

        popular = next(t for t in result["items"] if t["name"] == "popular")
        assert len(popular["files"]) == 3
        assert popular["files_total"] == 8
        assert popular["files_total"] > len(popular["files"])  # truncated -> detectable via len < files_total

    @pytest.mark.asyncio
    async def test_untruncated_tag_has_files_length_equal_to_files_total(self, heavy_tag_vault):
        result = await list_tags(include_files=True, max_files_per_tag=3)

        rare = next(t for t in result["items"] if t["name"] == "rare")
        assert len(rare["files"]) == rare["files_total"] == 1

    @pytest.mark.asyncio
    async def test_files_total_present_even_when_include_counts_false(self, heavy_tag_vault):
        # Explicit contract: files_total is set inside `if include_files:`,
        # independent of include_counts - assert it since `count` itself is
        # absent from the item in this mode.
        result = await list_tags(include_counts=False, include_files=True, max_files_per_tag=3)

        popular = next(t for t in result["items"] if t["name"] == "popular")
        assert "count" not in popular
        assert popular["files_total"] == 8

    @pytest.mark.asyncio
    async def test_truncation_keeps_first_n_of_sorted_full_list(self, heavy_tag_vault):
        result = await list_tags(include_files=True, max_files_per_tag=3)

        popular = next(t for t in result["items"] if t["name"] == "popular")
        assert popular["files"] == POPULAR_FILES[:3]

    @pytest.mark.asyncio
    async def test_default_cap_truncates_above_it_but_not_below(self, heavy_tag_vault):
        # max_files_per_tag's default was tightened 20 -> 3 (FIX B combined
        # cost ceiling: limit=100 x max_files_per_tag=20 could build a
        # ~512KB worst-case response; x3 keeps that at ~74KB). So the
        # default now DOES truncate 'popular' (8 files); 'rare' (1 file, do
        # heavy_tag_vault) stays untruncated -- proving the cap is real
        # without hardcoding a vault where nothing is ever capped.
        result = await list_tags(include_files=True)  # max_files_per_tag defaults to 3

        popular = next(t for t in result["items"] if t["name"] == "popular")
        assert len(popular["files"]) == 3
        assert popular["files_total"] == 8

        rare = next(t for t in result["items"] if t["name"] == "rare")
        assert len(rare["files"]) == rare["files_total"] == 1

    @pytest.mark.asyncio
    async def test_no_files_or_files_total_keys_when_include_files_false(self, heavy_tag_vault):
        result = await list_tags(include_files=False, max_files_per_tag=3)

        popular = next(t for t in result["items"] if t["name"] == "popular")
        assert "files" not in popular
        assert "files_total" not in popular

    @pytest.mark.asyncio
    async def test_cap_applies_per_item_within_a_small_paged_window(self, heavy_tag_vault):
        # 2 tags total ("popular", "rare"); name-asc -> popular first.
        result = await list_tags(include_files=True, max_files_per_tag=2, sort_by="name", offset=0, limit=1)

        assert result["returned"] == 1
        popular = result["items"][0]
        assert popular["name"] == "popular"
        assert len(popular["files"]) == 2
        assert popular["files_total"] == 8


class TestListTagsNamesOnlyBranchSortByCount:
    """include_counts=False + include_files=False takes a shortcut branch
    that returns plain tag-name strings (not {"name": ...} dicts) — unlike
    every other branch in this file. It used to always sort by name,
    silently ignoring sort_by="count"; this is the regression coverage for
    that fix (obsidian_mcp/tools/organization.py)."""

    @pytest.mark.asyncio
    async def test_sort_by_count_orders_by_usage_descending_in_names_only_branch(self, count_ordered_vault):
        result = await list_tags(include_counts=False, include_files=False, sort_by="count")

        assert result["items"] == ["charlie", "bravo", "alpha"], (
            "sort_by='count' was ignored in the include_counts=False/"
            "include_files=False shortcut branch of list_tags — got "
            f"{result['items']!r} instead of count-descending order"
        )

    @pytest.mark.asyncio
    async def test_sort_by_name_is_still_alphabetical_in_names_only_branch(self, count_ordered_vault):
        result = await list_tags(include_counts=False, include_files=False, sort_by="name")

        assert result["items"] == ["alpha", "bravo", "charlie"]

    @pytest.mark.asyncio
    async def test_names_only_branch_count_order_matches_full_branch_count_order(self, count_ordered_vault):
        # Pins the two branches to identical sort_by="count" semantics — the
        # thing that was broken (names-only branch drifted from the main
        # include_counts=True branch's tie-break/ordering behavior).
        names_only = await list_tags(include_counts=False, include_files=False, sort_by="count")
        full = await list_tags(include_counts=True, sort_by="count")

        assert names_only["items"] == [t["name"] for t in full["items"]]


class TestListTagsSortOrderFlagMatrix:
    """Pins list_tags ordering across the full (include_counts, include_files)
    matrix -- not just the two combinations that happened to break so far.

    Round 1: the names-only shortcut (include_counts=False,
    include_files=False) always sorted by name, silently ignoring
    sort_by="count".
    Round 2: the item-building branch sorted via x.get("count", 0); with
    include_counts=False, include_files=True no item had "count", every
    key fell back to 0, and the result was insertion order while
    reporting success.

    Both are now fixed by keying every sort off tag_counts[...] (the
    authoritative counts dict, always populated) instead of the optional
    per-item "count" field. These tests loop over all 4 flag combinations
    so any future refactor that reintroduces an optional-field sort key,
    in either branch, fails immediately.
    """

    @pytest.mark.asyncio
    async def test_sort_by_count_is_true_count_descending_in_every_flag_combination(self, adversarial_order_vault):
        for include_counts, include_files in FLAG_COMBINATIONS:
            result = await list_tags(include_counts=include_counts, include_files=include_files, sort_by="count")

            assert _tag_names(result) == ["zulu", "alpha", "mike"], (
                f"sort_by='count' was ignored for include_counts={include_counts}, "
                f"include_files={include_files} — ordering must key off the authoritative "
                f"counts, not the optional item field. Got {_tag_names(result)!r} instead "
                "of count-descending order."
            )

    @pytest.mark.asyncio
    async def test_sort_by_name_is_alphabetical_in_every_flag_combination(self, adversarial_order_vault):
        for include_counts, include_files in FLAG_COMBINATIONS:
            result = await list_tags(include_counts=include_counts, include_files=include_files, sort_by="name")

            assert _tag_names(result) == ["alpha", "mike", "zulu"], (
                f"sort_by='name' was not alphabetical for include_counts={include_counts}, "
                f"include_files={include_files} — got {_tag_names(result)!r} instead of "
                "name-ascending order."
            )

    @pytest.mark.asyncio
    async def test_count_sort_order_is_identical_across_all_flag_combinations(self, adversarial_order_vault):
        # Flags gate which optional fields are present on each item; they
        # must never change the ORDER those items come back in.
        orders = {}
        for include_counts, include_files in FLAG_COMBINATIONS:
            result = await list_tags(include_counts=include_counts, include_files=include_files, sort_by="count")
            orders[(include_counts, include_files)] = _tag_names(result)

        baseline_combo = FLAG_COMBINATIONS[0]
        baseline_order = orders[baseline_combo]
        for combo, order in orders.items():
            assert order == baseline_order, (
                f"sort_by='count' order for include_counts={combo[0]}, include_files={combo[1]} "
                f"({order!r}) diverges from include_counts={baseline_combo[0]}, "
                f"include_files={baseline_combo[1]} ({baseline_order!r}) — flags that only "
                "control which optional fields are present must never affect ordering."
            )

    @pytest.mark.asyncio
    async def test_name_sort_order_is_identical_across_all_flag_combinations(self, adversarial_order_vault):
        orders = {}
        for include_counts, include_files in FLAG_COMBINATIONS:
            result = await list_tags(include_counts=include_counts, include_files=include_files, sort_by="name")
            orders[(include_counts, include_files)] = _tag_names(result)

        baseline_combo = FLAG_COMBINATIONS[0]
        baseline_order = orders[baseline_combo]
        for combo, order in orders.items():
            assert order == baseline_order, (
                f"sort_by='name' order for include_counts={combo[0]}, include_files={combo[1]} "
                f"({order!r}) diverges from include_counts={baseline_combo[0]}, "
                f"include_files={baseline_combo[1]} ({baseline_order!r}) — flags that only "
                "control which optional fields are present must never affect ordering."
            )


class TestListTagsParameterValidation:
    """FIX A: offset/limit/max_files_per_tag must validate inside list_tags()
    itself. The pydantic Field(ge=...) bounds on list_tags_tool only protect
    calls made through that MCP wrapper -- this whole file calls the impl
    directly by design (see module docstring), so without this guard a
    negative offset/limit silently mis-slices instead of erroring (e.g.
    offset=-5 on a 14-tag list used to return the LAST 5 tags, no exception)."""

    @pytest.mark.asyncio
    async def test_negative_offset_raises_value_error(self, many_tags_vault):
        with pytest.raises(ValueError, match="offset"):
            await list_tags(offset=-5)

    @pytest.mark.asyncio
    async def test_limit_below_one_raises_value_error(self, many_tags_vault):
        with pytest.raises(ValueError, match="limit"):
            await list_tags(limit=0)

    @pytest.mark.asyncio
    async def test_max_files_per_tag_below_one_raises_value_error(self, many_tags_vault):
        with pytest.raises(ValueError, match="max_files_per_tag"):
            await list_tags(include_files=True, max_files_per_tag=0)


class TestListTagsFileCostCeiling:
    """FIX B: limit * max_files_per_tag is capped at 300 when
    include_files=True. Without this, the worst schema-valid combo
    (limit=1000, max_files_per_tag=1000) builds a ~1,000,000-path response
    -- tens of MB, measured -- in one call; even the OLD default
    (limit=100 x max_files_per_tag=20) measured ~512KB worst case, the same
    order of magnitude as the 177KB response that originally overflowed the
    MCP client."""

    @pytest.mark.asyncio
    async def test_combo_at_ceiling_succeeds(self, many_tags_vault):
        result = await list_tags(include_files=True, limit=100, max_files_per_tag=3)
        assert result["returned"] == TAG_COUNT  # 14 tags exist, well under limit

    @pytest.mark.asyncio
    async def test_combo_over_ceiling_raises_value_error(self, many_tags_vault):
        with pytest.raises(ValueError, match="300"):
            await list_tags(include_files=True, limit=100, max_files_per_tag=4)

    @pytest.mark.asyncio
    async def test_ceiling_not_enforced_when_include_files_false(self, many_tags_vault):
        # Same over-ceiling product, but include_files=False means no file
        # lists are built at all -- nothing to cap.
        result = await list_tags(include_files=False, limit=1000, max_files_per_tag=1000)
        assert result["total"] == TAG_COUNT


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
