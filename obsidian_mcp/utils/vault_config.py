"""Vault-wide write policy: re-exports path normalization, folder templates,
slug/tag kebab normalization, and frontmatter-requirement helpers from their
own modules, and owns note-size enforcement directly.

This module used to hold all of this logic; it is now a backward-compatible
facade so every existing `from ..utils.vault_config import X` keeps working
unchanged. New code may import from the specific submodule instead:
vault_paths.py (path normalization), templates.py (folder templates),
slugs.py (slug/tag kebab), frontmatter_requirements.py (name/description
derivation and the minimal-frontmatter requirement).

Every knob here is optional (see ObsidianVault.__init__) and defaults to
today's behavior — nothing in this module changes what happens when none of
the new OBSIDIAN_* env vars are set.
"""

# Re-exported for backward compatibility — see module docstring. __all__
# below tells ruff these imports are intentional public re-exports, not
# unused.
from .frontmatter_requirements import (
    apply_frontmatter_requirements,
    derive_note_description,
    derive_note_name,
    seed_daily_frontmatter,
)
from .slugs import normalize_tag_kebab, slugify_kebab
from .templates import (
    FolderTemplateRule,
    build_template_info,
    check_template_conformance,
    extract_required_headings,
    find_template_rule,
    parse_folder_templates,
)
from .vault_paths import (
    normalize_vault_relative_path,
    resolve_path_maybe_outside_vault,
)

__all__ = [
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
]


# ---------------------------------------------------------------------------
# Note-size policy (spec section 1: OBSIDIAN_MAX_NOTE_LINES / _APPEND_HEADROOM_LINES)
# ---------------------------------------------------------------------------


def count_lines(text: str) -> int:
    if text == "":
        return 0
    return text.count("\n") + 1


def check_note_size_policy(
    vault,
    relpath: str,
    resulting_line_count: int,
    is_incremental: bool,
) -> str | None:
    """Check `resulting_line_count` (the note's total line count after the
    write) against OBSIDIAN_MAX_NOTE_LINES.

    is_incremental=True (update append / edit_note_section) uses the lower,
    early-warning ceiling MAX - APPEND_HEADROOM_LINES; False (create_note /
    update replace) uses MAX directly, since the whole note is being
    (re)written in one shot.

    Returns None (ok / off / daily-exempt), a warning message (warn policy —
    caller still writes and surfaces the message), or raises ValueError
    (strict policy — caller must not write).
    """
    if vault.note_size_policy == "off":
        return None
    if vault.is_daily_note_path(relpath):
        return None

    if is_incremental:
        ceiling = vault.max_note_lines - vault.append_headroom_lines
        message = (
            f"Note '{relpath}' would reach {resulting_line_count} lines, over the "
            f"{ceiling}-line append ceiling (OBSIDIAN_MAX_NOTE_LINES={vault.max_note_lines} - "
            f"OBSIDIAN_APPEND_HEADROOM_LINES={vault.append_headroom_lines}). Split the content "
            "into a new note, or raise OBSIDIAN_APPEND_HEADROOM_LINES/OBSIDIAN_MAX_NOTE_LINES."
        )
    else:
        ceiling = vault.max_note_lines
        message = (
            f"Note '{relpath}' would have {resulting_line_count} lines, over "
            f"OBSIDIAN_MAX_NOTE_LINES={vault.max_note_lines}. Split the content into multiple "
            "notes, or raise OBSIDIAN_MAX_NOTE_LINES."
        )

    if resulting_line_count <= ceiling:
        return None

    if vault.note_size_policy == "strict":
        raise ValueError(message)
    return message  # warn
