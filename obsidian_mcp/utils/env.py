"""Pure env-var readers backing the vault's OBSIDIAN_* configuration knobs.

Extracted from ObsidianVault so these are directly unit-testable without a
vault on disk. ObsidianVault keeps thin @staticmethod delegators
(_read_choice_env, _read_bool_env, _read_int_env) — tests call them
directly on the class (e.g. ObsidianVault._read_int_env(...)), and __init__
calls them via self — see filesystem.py.
"""

import logging
import os

logger = logging.getLogger(__name__)


def read_choice_env(name: str, choices: tuple, default: str) -> str:
    """Read an enum-like env var; fall back to `default` (with a
    warning) if set but not one of `choices` — config never crashes
    the boot."""
    value = os.getenv(name, default)
    if value not in choices:
        logger.warning(
            "Invalid %s=%r; must be one of %s. Falling back to %r.",
            name,
            value,
            choices,
            default,
        )
        return default
    return value


def read_bool_env(name: str, default: bool) -> bool:
    """Read a bool-valued env var (true/1/yes/on, case-insensitive,
    surrounding whitespace ignored); anything else (including unset)
    falls back to `default` — same truthy convention already used for
    OBSIDIAN_AUTO_INDEX_UPDATE, just factored out for reuse."""
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("true", "1", "yes", "on")


def read_int_env(name: str, default: int) -> int:
    """Read an int-valued env var; fall back to `default` (with a
    warning) if unset, blank, or not a valid integer after stripping
    surrounding whitespace (env vars are always strings — ".strip()"
    makes " 500 " coerce the same as "500") — config never crashes
    the boot."""
    raw = os.getenv(name)
    if raw is None:
        return default
    stripped = raw.strip()
    try:
        return int(stripped)
    except ValueError:
        logger.warning(
            "Invalid %s=%r; must be an integer. Falling back to %d.",
            name,
            raw,
            default,
        )
        return default
