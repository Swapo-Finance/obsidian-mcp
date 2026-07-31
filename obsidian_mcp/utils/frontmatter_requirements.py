"""Per-note name/description derivation (spec section 10.2) and the
minimal-frontmatter requirement (spec section 10.3:
OBSIDIAN_REQUIRE_FRONTMATTER).

Split out of vault_config.py; re-exported from there for backward
compatibility. Named "_requirements" (not "frontmatter.py") to stay distinct
from the unrelated frontmatter-parsing module of the same era.
"""

import re
from typing import Any

import yaml

# _H2_HEADING_RE is shared with templates.py: derive_note_description's
# heading fallback reuses the same "## heading" pattern used for template
# conformance, rather than duplicating the regex here.
from .templates import _H2_HEADING_RE

# ---------------------------------------------------------------------------
# Per-note name/description (spec section 10.2) — feeds VaultCache so the
# search index mode (10.4) and the frontmatter-requirement enforcement
# (10.3) share one extraction, parsed once at index time (utils/vault_cache.py
# calls these from _index_note), never re-read from disk per consumer.
# ---------------------------------------------------------------------------

_ANY_HEADING_RE = re.compile(r"^#{1,6}(?!#)[ \t]+")


def derive_note_name(relpath: str, frontmatter: dict[str, Any]) -> str:
    """frontmatter['name'] if it's a non-empty string, else the filename
    stem (basename without .md) — the same fallback OBSIDIAN_REQUIRE_FRONTMATTER
    forces onto every note (spec section 10.3), applied here unconditionally
    so the cache/index mode has a usable `name` even when that config is off.
    """
    name = frontmatter.get("name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    filename = relpath.rsplit("/", 1)[-1]
    return filename.removesuffix(".md")


def derive_note_description(frontmatter: dict[str, Any], clean_content: str) -> str:
    """frontmatter['description'] if it's a non-empty string; else the first
    non-blank, non-heading line of the note body; else the first '## '
    heading's text; else "" (spec section 10.2's fallback chain).
    """
    description = frontmatter.get("description")
    if isinstance(description, str) and description.strip():
        return description.strip()

    for line in clean_content.splitlines():
        stripped = line.strip()
        if not stripped or _ANY_HEADING_RE.match(stripped):
            continue
        return stripped

    heading_match = _H2_HEADING_RE.search(clean_content)
    return heading_match.group(1).strip() if heading_match else ""


# ---------------------------------------------------------------------------
# Minimal-frontmatter requirement (spec section 10.3: OBSIDIAN_REQUIRE_FRONTMATTER)
# ---------------------------------------------------------------------------


def _serialize_frontmatter_block(
    frontmatter: dict[str, Any], clean_content: str
) -> str:
    """Render `frontmatter` as the note's YAML block followed by a blank
    line and `clean_content` (already stripped of any previous block).
    Same yaml.safe_load/yaml.dump round-trip already used by
    tools/organization.py's batch property updates — shared here by
    apply_frontmatter_requirements and seed_daily_frontmatter.
    """
    yaml_text = yaml.dump(
        frontmatter, default_flow_style=False, allow_unicode=True, sort_keys=False
    ).rstrip("\n")
    return f"---\n{yaml_text}\n---\n\n{clean_content}"


def apply_frontmatter_requirements(vault, relpath: str, content: str) -> str:
    """OBSIDIAN_REQUIRE_FRONTMATTER=true only (default — see spec section
    10.3): enforce the minimal frontmatter contract on a full-content write
    (create_note, update_note replace, update_note create_if_not_exists).
    No-op, returning content unchanged, when the config is off.

    - `name` is always forced to the note's filename stem — whatever the
      content brought (divergent or absent) is overwritten, since the
      filename is the source of truth. The caller passes `relpath` after
      OBSIDIAN_SLUG_STYLE has already been applied to the path, so this
      does not need to know about slug style itself.
    - `description` must already be present and non-empty; this function
      never invents one — raises ValueError (a request for a template
      conformance-style human message that check_template_conformance
      would recognize as a plain violation; the note_management wrappers
      in write_policy already convert ValueError to an actionable ToolError)
      so the LLM supplies it and retries.

    Any other frontmatter keys already in content (e.g. a folder template's
    own required keys, already validated present by check_template_conformance,
    which runs before this in write_policy._apply_write_checks) are
    preserved as-is — this only ever touches `name`.
    """
    if not vault.require_frontmatter:
        return content

    filename = relpath.rsplit("/", 1)[-1]
    required_name = filename.removesuffix(".md")

    frontmatter, clean_content = vault._parse_frontmatter(content)
    description = frontmatter.get("description")
    if not isinstance(description, str) or not description.strip():
        raise ValueError(
            f"Missing required frontmatter field 'description' for '{relpath}' "
            "(OBSIDIAN_REQUIRE_FRONTMATTER is on, the default). Add a one-line "
            "`description:` to the note's frontmatter — a short summary of what the "
            "note covers — and retry, e.g.:\n"
            f"---\nname: {required_name}\ndescription: <what this note covers>\n---\n\n"
            "'name' is set automatically from the filename; only 'description' is your "
            "responsibility. Turn this requirement off with OBSIDIAN_REQUIRE_FRONTMATTER=false."
        )

    if content.startswith("---\n") and frontmatter.get("name") == required_name:
        return content  # already conformant — avoid a needless YAML round-trip

    frontmatter["name"] = required_name
    return _serialize_frontmatter_block(frontmatter, clean_content)


def seed_daily_frontmatter(vault, base_content: str, date_iso: str) -> str:
    """OBSIDIAN_REQUIRE_FRONTMATTER=true only, called by add_daily_note when
    it creates a new day's file: auto-generates name=<date_iso> and a
    description (the daily-dir template's own, if it already declares a
    non-empty one, else "Daily note {date}") — the LLM is never asked for
    these, since the server itself is the one creating this file (spec
    section 10.3, add_daily bullet). No-op if require_frontmatter is off.
    """
    if not vault.require_frontmatter:
        return base_content

    frontmatter, clean_content = vault._parse_frontmatter(base_content)
    description = frontmatter.get("description")
    if not isinstance(description, str) or not description.strip():
        description = f"Daily note {date_iso}"
    frontmatter["name"] = date_iso
    frontmatter["description"] = description
    return _serialize_frontmatter_block(frontmatter, clean_content)
