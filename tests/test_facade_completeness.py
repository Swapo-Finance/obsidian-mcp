#!/usr/bin/env python3
"""Regression guard for the tools/utils facade split done this session.

Ten names silently stopped being importable through their pre-split module
path once obsidian_mcp/tools/link_management.py and note_management.py were
split into sibling modules: any caller still doing e.g.
`from obsidian_mcp.tools.link_management import find_notes_by_names` would
have started raising ImportError with no other signal (the module still
imports fine, it just no longer has that attribute). That was found and
fixed by hand this session -- each facade now declares an explicit
`__all__` naming everything it re-exports. This test makes sure that
regression, and anything shaped like it in the other four facades that went
through the same kind of split, cannot recur silently.

Method: REQUIRED_EXPORTS below is a frozen snapshot of, for each facade, the
names that must still resolve as an attribute of the current module. It was
generated once by walking the *old* monolithic source (the git blob at
BASE_SHA, before the split): every name that source bound at module level
(functions, classes, and plain top-level constants -- not names it merely
imported from elsewhere) was kept if it was public (no leading underscore),
or if it was private/a constant *and* still listed in the current module's
own `__all__` (that list is what each split explicitly committed to
re-exporting -- a stray `logger = logging.getLogger(__name__)` is
non-underscore too, but was never meant to be imported cross-module, and
indeed wasn't in any of the five `__all__` lists at capture time).

This test used to do that walk live, via `git show {BASE_SHA}:<path>` +
`ast`, on every run. That broke in CI: GitHub Actions' `actions/checkout`
does a shallow clone (fetch-depth 1 by default), so the base commit's blobs
don't exist there and `git show` fails with exit 128. A test that depends
on arbitrary git history silently passes on a full local clone and
hard-fails on any shallow one (CI, a fresh shallow clone, an exported
tarball, an sdist install). REQUIRED_EXPORTS captures the *result* of that
walk instead, so the test itself never touches git or subprocess and runs
identically anywhere.

Regenerating (only needed if a facade's re-export contract intentionally
changes): this file's git history (`git log -p -- tests/test_facade_completeness.py`)
has the live git-show + ast version this snapshot was captured from --
check it out, run it against a new base commit, and hand-merge the
intentional change into the frozenset below. Do not restore that live
version as the test itself; see above for why it doesn't survive a shallow
clone.
"""

from __future__ import annotations

import importlib

import pytest

# Commit the pre-split source was read from to produce REQUIRED_EXPORTS.
# Not used at runtime -- documentation only (see "Regenerating" above).
BASE_SHA = "aeb0f1d9dfd9078405485643ef8a4104f8970e3e"

REQUIRED_EXPORTS: dict[str, frozenset[str]] = {
    "obsidian_mcp.tools.link_management": frozenset(
        {
            "_FENCED_CODE_RE",
            "_INLINE_CODE_RE",
            "_MARKDOWN_EMBED_RE",
            "_VALIDATION_WIKI_LINK_RE",
            "_WIKI_EMBED_RE",
            "_mask_ineligible_regions",
            "_suggest_similar_notes",
            "build_vault_notes_index",
            "check_links_validity_batch",
            "find_broken_links",
            "find_notes_by_names",
            "get_backlinks",
            "get_link_context",
            "get_outgoing_links",
            "validate_wikilinks_for_write",
        }
    ),
    "obsidian_mcp.tools.note_management": frozenset(
        {
            "_apply_write_checks",
            "_serialize_note_writes",
            "_size_policy_warning",
            "apply_slug_style_to_frontmatter_name",
            "apply_slug_style_to_path",
            "create_note",
            "delete_note",
            "edit_note_section",
            "normalize_frontmatter_tags_for_kebab",
            "read_note",
            "update_note",
        }
    ),
    "obsidian_mcp.tools.organization": frozenset(
        {
            "_build_tag_item",
            "_clean_tags",
            "_remove_inline_tags",
            "_update_frontmatter_properties",
            "_update_frontmatter_tags",
            "add_tags",
            "batch_update_properties",
            "create_folder",
            "get_note_info",
            "list_tags",
            "move_folder",
            "move_note",
            "remove_tags",
            "rename_note",
            "update_tags",
        }
    ),
    "obsidian_mcp.tools.search_discovery": frozenset(
        {
            "_enrich_with_note_meta",
            "_parse_property_query",
            "_search_by_property",
            "list_folders",
            "list_notes",
            "search_by_date",
            "search_by_property",
            "search_by_regex",
            "search_notes",
        }
    ),
    "obsidian_mcp.utils.vault_config": frozenset(
        {
            "FolderTemplateRule",
            "apply_frontmatter_requirements",
            "build_template_info",
            "check_note_size_policy",
            "check_template_conformance",
            "count_lines",
            "derive_note_description",
            "derive_note_name",
            "extract_required_headings",
            "find_template_rule",
            "normalize_tag_kebab",
            "normalize_vault_relative_path",
            "parse_folder_templates",
            "resolve_path_maybe_outside_vault",
            "seed_daily_frontmatter",
            "slugify_kebab",
        }
    ),
}


@pytest.mark.parametrize("module_path", sorted(REQUIRED_EXPORTS))
def test_facade_still_exports_every_pre_split_name(module_path: str) -> None:
    """Every name in REQUIRED_EXPORTS for this facade must resolve as an
    attribute of the current module (see module docstring for provenance).
    """
    required_names = REQUIRED_EXPORTS[module_path]

    # A facade with nothing to check would make this test vacuously green
    # and hide a real regression -- guard the guard.
    assert required_names, f"{module_path}: REQUIRED_EXPORTS entry is empty"

    current_module = importlib.import_module(module_path)
    missing = sorted(
        name for name in required_names if not hasattr(current_module, name)
    )
    assert not missing, (
        f"{module_path} no longer re-exports: {missing}. These names were defined "
        f"directly in this module at {BASE_SHA[:12]} (pre-split) or were listed in "
        f"the module's __all__ as of that snapshot, and existing callers may still "
        f"import them from this exact path."
    )
