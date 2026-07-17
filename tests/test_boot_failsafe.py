#!/usr/bin/env python3
"""Boot fail-safe for the choice- and int-valued OBSIDIAN_* configs (spec
section 1's fail-safe rule): an invalid value never crashes ObsidianVault
boot, it falls back to the documented default (with a warning). Path-shaped
configs (OBSIDIAN_FOLDER_TEMPLATES, OBSIDIAN_DAILY_DIR) have their own
fail-safe matrix in test_vault_config_paths.py — this file covers the other
7: the 4 choice enums and 3 integers.
"""

import shutil
import tempfile

import pytest

from obsidian_mcp.utils.filesystem import ObsidianVault, init_vault


@pytest.fixture
def vault_dir():
    temp_dir = tempfile.mkdtemp(prefix="obsidian_bootfailsafe_")
    yield temp_dir
    shutil.rmtree(temp_dir)


CHOICE_CONFIGS = [
    ("OBSIDIAN_WIKILINK_POLICY", "wikilink_policy", "warn"),
    ("OBSIDIAN_NOTE_SIZE_POLICY", "note_size_policy", "warn"),
    ("OBSIDIAN_TAG_STYLE", "tag_style", "as-is"),
    ("OBSIDIAN_SLUG_STYLE", "slug_style", "as-is"),
]

INT_CONFIGS = [
    ("OBSIDIAN_MAX_NOTE_LINES", "max_note_lines", 500),
    ("OBSIDIAN_APPEND_HEADROOM_LINES", "append_headroom_lines", 100),
    ("OBSIDIAN_CACHE_STAT_TTL_SECONDS", "cache_stat_ttl_seconds", 30),
]


class TestChoiceConfigFailSafe:
    @pytest.mark.parametrize("env_name,attr,default", CHOICE_CONFIGS)
    def test_invalid_enum_value_falls_back_to_default(self, vault_dir, env_name, attr, default):
        import os

        os.environ[env_name] = "not-a-real-choice"
        try:
            vault = init_vault(vault_dir)
            assert getattr(vault, attr) == default
        finally:
            os.environ.pop(env_name, None)

    @pytest.mark.parametrize("env_name,attr,default", CHOICE_CONFIGS)
    def test_unset_uses_default(self, vault_dir, env_name, attr, default):
        import os

        os.environ.pop(env_name, None)
        vault = init_vault(vault_dir)
        assert getattr(vault, attr) == default

    @pytest.mark.parametrize("env_name,attr,default", CHOICE_CONFIGS)
    def test_empty_string_falls_back_to_default(self, vault_dir, env_name, attr, default):
        import os

        os.environ[env_name] = ""
        try:
            vault = init_vault(vault_dir)
            assert getattr(vault, attr) == default
        finally:
            os.environ.pop(env_name, None)


class TestIntConfigFailSafe:
    @pytest.mark.parametrize("env_name,attr,default", INT_CONFIGS)
    def test_non_numeric_value_falls_back_to_default(self, vault_dir, env_name, attr, default):
        import os

        os.environ[env_name] = "not-a-number"
        try:
            vault = init_vault(vault_dir)
            assert getattr(vault, attr) == default
        finally:
            os.environ.pop(env_name, None)

    @pytest.mark.parametrize("env_name,attr,default", INT_CONFIGS)
    def test_whitespace_padded_numeric_value_coerces(self, vault_dir, env_name, attr, default):
        import os

        os.environ[env_name] = "  42 "
        try:
            vault = init_vault(vault_dir)
            assert getattr(vault, attr) == 42
        finally:
            os.environ.pop(env_name, None)

    @pytest.mark.parametrize("env_name,attr,default", INT_CONFIGS)
    def test_blank_value_falls_back_to_default(self, vault_dir, env_name, attr, default):
        import os

        os.environ[env_name] = "   "
        try:
            vault = init_vault(vault_dir)
            assert getattr(vault, attr) == default
        finally:
            os.environ.pop(env_name, None)

    @pytest.mark.parametrize("env_name,attr,default", INT_CONFIGS)
    def test_float_looking_value_falls_back_to_default(self, vault_dir, env_name, attr, default):
        # int() rejects "12.5" outright (no implicit float truncation) —
        # must fail safe to the default, not crash.
        import os

        os.environ[env_name] = "12.5"
        try:
            vault = init_vault(vault_dir)
            assert getattr(vault, attr) == default
        finally:
            os.environ.pop(env_name, None)


class TestRequireFrontmatterBoolCoercion:
    """OBSIDIAN_REQUIRE_FRONTMATTER: default True; recognized truthy tokens
    (case-insensitive, whitespace-tolerant) are True, anything else False —
    never raises regardless of value (spec section 10.3)."""

    def test_default_true_when_unset(self, vault_dir):
        import os

        os.environ.pop("OBSIDIAN_REQUIRE_FRONTMATTER", None)
        assert init_vault(vault_dir).require_frontmatter is True

    @pytest.mark.parametrize("value", ["true", "TRUE", "  True ", "1", "yes", "on"])
    def test_truthy_tokens_are_true(self, vault_dir, value):
        import os

        os.environ["OBSIDIAN_REQUIRE_FRONTMATTER"] = value
        try:
            assert init_vault(vault_dir).require_frontmatter is True
        finally:
            os.environ.pop("OBSIDIAN_REQUIRE_FRONTMATTER", None)

    @pytest.mark.parametrize("value", ["false", "0", "no", "off", "garbage"])
    def test_falsy_or_unrecognized_tokens_are_false(self, vault_dir, value):
        import os

        os.environ["OBSIDIAN_REQUIRE_FRONTMATTER"] = value
        try:
            assert init_vault(vault_dir).require_frontmatter is False
        finally:
            os.environ.pop("OBSIDIAN_REQUIRE_FRONTMATTER", None)


class TestSearchResultModeAndThresholdFailSafe:
    def test_invalid_search_result_mode_falls_back_to_auto(self, vault_dir):
        import os

        os.environ["OBSIDIAN_SEARCH_RESULT_MODE"] = "bogus-mode"
        try:
            assert init_vault(vault_dir).search_result_mode == "auto"
        finally:
            os.environ.pop("OBSIDIAN_SEARCH_RESULT_MODE", None)

    def test_invalid_search_index_threshold_falls_back_to_default(self, vault_dir):
        import os

        os.environ["OBSIDIAN_SEARCH_INDEX_THRESHOLD"] = "not-an-int"
        try:
            assert init_vault(vault_dir).search_index_threshold == 10
        finally:
            os.environ.pop("OBSIDIAN_SEARCH_INDEX_THRESHOLD", None)


class TestMultipleInvalidConfigsSimultaneously:
    """Several invalid configs at once still boot cleanly — fail-safe is
    per-config, not all-or-nothing."""

    def test_all_invalid_at_once_still_boots(self, vault_dir):
        import os

        env_overrides = {
            "OBSIDIAN_WIKILINK_POLICY": "nope",
            "OBSIDIAN_NOTE_SIZE_POLICY": "nope",
            "OBSIDIAN_TAG_STYLE": "nope",
            "OBSIDIAN_SLUG_STYLE": "nope",
            "OBSIDIAN_MAX_NOTE_LINES": "abc",
            "OBSIDIAN_APPEND_HEADROOM_LINES": "abc",
            "OBSIDIAN_CACHE_STAT_TTL_SECONDS": "abc",
        }
        for key, value in env_overrides.items():
            os.environ[key] = value
        try:
            vault = init_vault(vault_dir)
            assert vault.wikilink_policy == "warn"
            assert vault.note_size_policy == "warn"
            assert vault.tag_style == "as-is"
            assert vault.slug_style == "as-is"
            assert vault.max_note_lines == 500
            assert vault.append_headroom_lines == 100
            assert vault.cache_stat_ttl_seconds == 30
        finally:
            for key in env_overrides:
                os.environ.pop(key, None)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
