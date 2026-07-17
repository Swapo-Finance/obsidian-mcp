#!/usr/bin/env python3
"""Path-normalization matrix (spec section 2) and OBSIDIAN_FOLDER_TEMPLATES /
OBSIDIAN_DAILY_DIR boot fail-safe (spec section 1) — the full 3-forms x
separators x '~' matrix that the sanity pass did not cover, plus
longest-prefix-match and "one bad entry doesn't sink the others" behavior.
"""

import shutil
import tempfile
from pathlib import Path

import pytest
import pytest_asyncio

from obsidian_mcp.utils.filesystem import init_vault
from obsidian_mcp.utils.vault_config import (
    find_template_rule,
    normalize_vault_relative_path,
    parse_folder_templates,
    resolve_path_maybe_outside_vault,
)


class TestNormalizeVaultRelativePathForms:
    """The 3 accepted forms, each with POSIX and Windows separators."""

    def test_vault_relative_posix(self, tmp_path):
        assert normalize_vault_relative_path("01-projects/sub", tmp_path) == "01-projects/sub"

    def test_vault_relative_windows_separator(self, tmp_path):
        assert normalize_vault_relative_path("01-projects\\sub", tmp_path) == "01-projects/sub"

    def test_vault_basename_prefixed_posix(self, tmp_path):
        raw = f"{tmp_path.name}/01-projects"
        assert normalize_vault_relative_path(raw, tmp_path) == "01-projects"

    def test_vault_basename_prefixed_windows_separator(self, tmp_path):
        raw = f"{tmp_path.name}\\01-projects\\sub"
        assert normalize_vault_relative_path(raw, tmp_path) == "01-projects/sub"

    def test_absolute_posix_inside_vault(self, tmp_path):
        raw = str(tmp_path / "01-projects" / "sub")
        assert normalize_vault_relative_path(raw, tmp_path) == "01-projects/sub"

    def test_absolute_windows_style_inside_vault(self, tmp_path):
        # Windows absolute paths use backslashes; the function must normalize
        # separators before deciding absoluteness/resolving.
        raw = str(tmp_path / "01-projects").replace("/", "\\")
        assert normalize_vault_relative_path(raw, tmp_path) == "01-projects"

    def test_tilde_expansion_inside_vault(self, tmp_path, monkeypatch):
        # Anchor HOME at the vault's parent so "~/<vault-name>/sub" expands
        # to a real path inside the vault.
        monkeypatch.setenv("HOME", str(tmp_path.parent))
        raw = f"~/{tmp_path.name}/01-projects"
        assert normalize_vault_relative_path(raw, tmp_path) == "01-projects"

    def test_tilde_expansion_outside_vault_is_none(self, tmp_path, monkeypatch):
        other_home = tempfile.mkdtemp(prefix="obsidian_home_")
        try:
            monkeypatch.setenv("HOME", other_home)
            assert normalize_vault_relative_path("~/somewhere-else", tmp_path) is None
        finally:
            shutil.rmtree(other_home)

    def test_absolute_outside_vault_is_none(self, tmp_path):
        outside = tempfile.mkdtemp(prefix="obsidian_outside_")
        try:
            assert normalize_vault_relative_path(outside, tmp_path) is None
        finally:
            shutil.rmtree(outside)

    def test_root_and_empty_forms_normalize_to_empty_string(self, tmp_path):
        assert normalize_vault_relative_path("", tmp_path) == ""
        assert normalize_vault_relative_path(".", tmp_path) == ""

    def test_trailing_slash_stripped_on_vault_relative_form(self, tmp_path):
        assert normalize_vault_relative_path("01-projects/", tmp_path) == "01-projects"

    def test_leading_slash_is_treated_as_absolute_not_stripped(self, tmp_path):
        # A leading "/" makes this form-3 (absolute), per spec section 2's
        # own example ("/Users/x/vaults/v/01-projects") — it must NOT be
        # silently reinterpreted as a vault-relative path by stripping the
        # slash. Since "/01-projects" (filesystem root) is not inside our
        # tmp_path vault, this must resolve to None, not "01-projects".
        assert normalize_vault_relative_path("/01-projects", tmp_path) is None

    def test_none_input_returns_none(self, tmp_path):
        assert normalize_vault_relative_path(None, tmp_path) is None


class TestResolvePathMaybeOutsideVault:
    """Templates are allowed to live outside the vault (shared across
    projects), unlike folders/daily-dir."""

    def test_absolute_outside_vault_resolves(self, tmp_path):
        outside = tempfile.mkdtemp(prefix="obsidian_templates_")
        try:
            template_file = Path(outside) / "shared.md"
            template_file.write_text("# Shared\n")
            resolved = resolve_path_maybe_outside_vault(str(template_file), tmp_path)
            assert resolved == template_file.resolve()
        finally:
            shutil.rmtree(outside)

    def test_vault_relative_resolves_inside(self, tmp_path):
        resolved = resolve_path_maybe_outside_vault("templates/foo.md", tmp_path)
        assert resolved == (tmp_path / "templates" / "foo.md").resolve()

    def test_empty_path_raises(self, tmp_path):
        with pytest.raises(ValueError):
            resolve_path_maybe_outside_vault("", tmp_path)


class TestParseFolderTemplatesFailSafe:
    """OBSIDIAN_FOLDER_TEMPLATES: malformed/invalid entries degrade to
    free-form (skipped + logged), never crash the boot; a bad entry among
    good ones doesn't sink the good ones (spec section 1's fail-safe rule)."""

    def test_invalid_json_yields_empty_list(self, tmp_path):
        assert parse_folder_templates("{not valid json", tmp_path) == []

    def test_non_array_json_yields_empty_list(self, tmp_path):
        assert parse_folder_templates('{"folder": "x", "template": "y"}', tmp_path) == []

    def test_missing_keys_in_one_item_skips_only_that_item(self, tmp_path):
        (tmp_path / "templates").mkdir()
        (tmp_path / "templates" / "good.md").write_text("## Heading\n")
        (tmp_path / "01-projects").mkdir()

        raw = (
            '[{"folder": "01-projects", "template": "templates/good.md"}, '
            '{"folder": "no-template-key"}]'
        )
        rules = parse_folder_templates(raw, tmp_path)
        assert len(rules) == 1
        assert rules[0].folder == "01-projects"

    def test_folder_outside_vault_is_skipped(self, tmp_path):
        (tmp_path / "templates").mkdir()
        (tmp_path / "templates" / "good.md").write_text("## Heading\n")

        raw = '[{"folder": "/etc/outside", "template": "templates/good.md"}]'
        assert parse_folder_templates(raw, tmp_path) == []

    def test_missing_template_file_is_skipped(self, tmp_path):
        (tmp_path / "01-projects").mkdir()
        raw = '[{"folder": "01-projects", "template": "templates/does-not-exist.md"}]'
        assert parse_folder_templates(raw, tmp_path) == []

    def test_template_outside_vault_is_allowed(self, tmp_path):
        outside = tempfile.mkdtemp(prefix="obsidian_shared_templates_")
        try:
            shared_template = Path(outside) / "shared.md"
            shared_template.write_text("## Objetivo\n")
            (tmp_path / "01-projects").mkdir()

            raw_json = (
                '[{"folder": "01-projects", "template": %r}]' % str(shared_template)
            ).replace("'", '"')
            rules = parse_folder_templates(raw_json, tmp_path)
            assert len(rules) == 1
            assert rules[0].template_path == shared_template.resolve()
        finally:
            shutil.rmtree(outside)

    def test_one_bad_entry_does_not_sink_valid_entries(self, tmp_path):
        (tmp_path / "templates").mkdir()
        (tmp_path / "templates" / "good.md").write_text("## Heading\n")
        (tmp_path / "01-projects").mkdir()

        raw = (
            '[{"folder": "01-projects", "template": "templates/good.md"}, '
            '{"folder": "/outside/vault", "template": "templates/good.md"}, '
            '{"folder": "02-areas", "template": "templates/missing.md"}]'
        )
        rules = parse_folder_templates(raw, tmp_path)
        assert len(rules) == 1
        assert rules[0].folder == "01-projects"

    def test_boot_never_crashes_on_malformed_config(self, tmp_path):
        # Full ObsidianVault.__init__ integration: malformed JSON must not
        # raise during boot.
        import os

        os.environ["OBSIDIAN_FOLDER_TEMPLATES"] = "{not valid json at all"
        try:
            vault = init_vault(str(tmp_path))
            assert vault.folder_templates == []
        finally:
            os.environ.pop("OBSIDIAN_FOLDER_TEMPLATES", None)


class TestLongestPrefixMatch:
    """More specific folder rules win over shorter parent rules — automatic
    exceptions (spec section 1's '04-resources/artigos' example)."""

    @pytest_asyncio.fixture
    async def vault_with_nested_rules(self):
        temp_dir = tempfile.mkdtemp(prefix="obsidian_prefix_")
        templates_dir = Path(temp_dir) / "templates"
        templates_dir.mkdir()
        (templates_dir / "recurso.md").write_text("## Resumo\n")
        (templates_dir / "artigo.md").write_text("## Fonte\n\n## Resumo\n")
        (Path(temp_dir) / "04-resources").mkdir()
        (Path(temp_dir) / "04-resources" / "artigos").mkdir()

        import os

        os.environ["OBSIDIAN_FOLDER_TEMPLATES"] = (
            '[{"folder":"04-resources","template":"templates/recurso.md"},'
            '{"folder":"04-resources/artigos","template":"templates/artigo.md"}]'
        )
        vault = init_vault(temp_dir)
        yield vault
        os.environ.pop("OBSIDIAN_FOLDER_TEMPLATES", None)
        shutil.rmtree(temp_dir)

    def test_more_specific_subfolder_rule_wins(self, vault_with_nested_rules):
        rule = find_template_rule("04-resources/artigos", vault_with_nested_rules.folder_templates)
        assert rule.folder == "04-resources/artigos"

    def test_deeper_nested_path_still_matches_specific_rule(self, vault_with_nested_rules):
        rule = find_template_rule(
            "04-resources/artigos/2024", vault_with_nested_rules.folder_templates
        )
        assert rule.folder == "04-resources/artigos"

    def test_sibling_folder_falls_back_to_parent_rule(self, vault_with_nested_rules):
        rule = find_template_rule(
            "04-resources/videos", vault_with_nested_rules.folder_templates
        )
        assert rule.folder == "04-resources"

    def test_unrelated_folder_has_no_rule(self, vault_with_nested_rules):
        rule = find_template_rule("02-areas", vault_with_nested_rules.folder_templates)
        assert rule is None


class TestDailyDirNormalization:
    """OBSIDIAN_DAILY_DIR accepts the same 3 forms and fails safe (falls
    back to the 'daily' default) when it resolves outside the vault."""

    def test_default_when_unset(self, tmp_path):
        import os

        os.environ.pop("OBSIDIAN_DAILY_DIR", None)
        vault = init_vault(str(tmp_path))
        assert vault.daily_dir == "daily"

    def test_custom_vault_relative_value(self, tmp_path):
        import os

        os.environ["OBSIDIAN_DAILY_DIR"] = "journal/entries"
        try:
            vault = init_vault(str(tmp_path))
            assert vault.daily_dir == "journal/entries"
        finally:
            os.environ.pop("OBSIDIAN_DAILY_DIR", None)

    def test_outside_vault_falls_back_to_default(self, tmp_path):
        import os

        os.environ["OBSIDIAN_DAILY_DIR"] = "/etc/outside-the-vault"
        try:
            vault = init_vault(str(tmp_path))
            assert vault.daily_dir == "daily"
        finally:
            os.environ.pop("OBSIDIAN_DAILY_DIR", None)

    def test_windows_separator_value(self, tmp_path):
        import os

        os.environ["OBSIDIAN_DAILY_DIR"] = "journal\\entries"
        try:
            vault = init_vault(str(tmp_path))
            assert vault.daily_dir == "journal/entries"
        finally:
            os.environ.pop("OBSIDIAN_DAILY_DIR", None)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
