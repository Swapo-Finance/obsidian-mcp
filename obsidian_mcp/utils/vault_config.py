"""Vault-wide write policies: folder-to-template mapping, cross-platform
path normalization, slug/tag kebab normalization, template conformance, and
note-size enforcement.

Every knob here is optional (see ObsidianVault.__init__) and defaults to
today's behavior — nothing in this module changes what happens when none of
the new OBSIDIAN_* env vars are set.
"""

import json
import logging
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional

import yaml

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Path normalization (spec section 2)
# ---------------------------------------------------------------------------

# Windows absolute paths: drive-letter ("C:\..." / "C:/...") or UNC
# ("\\server\share\..."). pathlib.Path.is_absolute() only recognizes a
# leading "/" (or "~"), so on a POSIX host these silently fall through to
# the vault-relative branch below instead of being rejected as
# absolute-and-outside-the-vault. Must be matched against the raw,
# pre-"\\"->"/"-normalization text: UNC's leading "\\\\" disappears after
# that replace, becoming indistinguishable from it.
_WINDOWS_ABS_RE = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\)")


def _is_windows_absolute_path(raw_text: str) -> bool:
    return bool(_WINDOWS_ABS_RE.match(raw_text))


def normalize_vault_relative_path(raw: str, vault_path: Path) -> Optional[str]:
    """Accept vault-relative, vault-basename-prefixed, or absolute/`~` paths
    and return the canonical POSIX path relative to the vault root.

    Returns None if the resolved path falls outside the vault — callers that
    require an in-vault path (folders, daily dir) treat None as invalid;
    callers that allow out-of-vault paths (templates) use
    resolve_path_maybe_outside_vault instead.
    """
    if raw is None:
        return None
    text = raw.replace("\\", "/").strip()
    if text in ("", "."):
        return ""

    # Absoluteness (and "~") must be checked on the un-stripped text: a
    # leading "/" is what makes a POSIX path absolute in the first place
    # (e.g. "/Users/x/vaults/v/01-projects", straight from spec section 2's
    # own example). Stripping slashes first — as this used to do — silently
    # turns a real absolute path into a bogus vault-relative one instead of
    # resolving it and checking vault membership.
    candidate = Path(text)
    if candidate.is_absolute() or text.startswith("~") or _is_windows_absolute_path(raw.strip()):
        resolved = Path(text).expanduser().resolve()
    else:
        text = text.strip("/")
        if text == "":
            return ""
        # Detect and strip a leading "<vault-basename>/" prefix, e.g.
        # "brain-swapo/01-projects" when the vault itself is ".../brain-swapo".
        parts = PurePosixPath(text).parts
        if parts and parts[0] == vault_path.name:
            text = "/".join(parts[1:])
        resolved = (vault_path / text).resolve() if text else vault_path.resolve()

    try:
        rel = resolved.relative_to(vault_path.resolve())
    except ValueError:
        return None
    rel_str = str(rel).replace("\\", "/")
    return "" if rel_str == "." else rel_str


def resolve_path_maybe_outside_vault(raw: str, vault_path: Path) -> Path:
    """Like normalize_vault_relative_path, but for paths allowed to live
    outside the vault (templates can be shared across projects). Returns the
    resolved absolute Path without checking vault membership.

    Raises ValueError on an empty path.
    """
    text = (raw or "").replace("\\", "/").strip()
    if not text:
        raise ValueError("Empty path")

    candidate = Path(text)
    if candidate.is_absolute() or text.startswith("~") or _is_windows_absolute_path((raw or "").strip()):
        return Path(text).expanduser().resolve()

    stripped = text.strip("/")
    parts = PurePosixPath(stripped).parts
    if parts and parts[0] == vault_path.name:
        stripped = "/".join(parts[1:])
    return (vault_path / stripped).resolve() if stripped else vault_path.resolve()


# ---------------------------------------------------------------------------
# Folder -> template mapping (spec section 1 + 3)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FolderTemplateRule:
    folder: str            # canonical vault-relative POSIX path, e.g. "01-projects"
    template_path: Path    # absolute path to the template file (may be outside the vault)
    template_display: str  # the original configured template string, for messages


def parse_folder_templates(raw_json: Optional[str], vault_path: Path) -> List["FolderTemplateRule"]:
    """Parse OBSIDIAN_FOLDER_TEMPLATES. Fail-safe by design: any malformed
    item is logged and skipped (that folder degrades to free-form), the
    server never fails to boot because of this config.
    """
    if not raw_json:
        return []

    try:
        raw_items = json.loads(raw_json)
    except json.JSONDecodeError as e:
        logger.warning(
            "OBSIDIAN_FOLDER_TEMPLATES is not valid JSON (%s) — ignoring it entirely; "
            "affected folders fall back to free-form content. Expected an array like: "
            '[{"folder":"01-projects","template":"templates/projeto.md"}]',
            e,
        )
        return []

    if not isinstance(raw_items, list):
        logger.warning(
            "OBSIDIAN_FOLDER_TEMPLATES must be a JSON array, got %s — ignoring it entirely.",
            type(raw_items).__name__,
        )
        return []

    rules: List[FolderTemplateRule] = []
    for item in raw_items:
        if not isinstance(item, dict) or "folder" not in item or "template" not in item:
            logger.warning(
                "Skipping invalid OBSIDIAN_FOLDER_TEMPLATES entry %r: each item needs "
                '"folder" and "template" string keys.',
                item,
            )
            continue

        folder_raw, template_raw = item["folder"], item["template"]
        folder = normalize_vault_relative_path(str(folder_raw), vault_path)
        if folder is None:
            logger.warning(
                "Skipping OBSIDIAN_FOLDER_TEMPLATES entry for folder %r: it must resolve "
                "inside the vault (%s). Accepted forms: vault-relative ('01-projects'), "
                "vault-name-prefixed ('%s/01-projects'), or an absolute/'~' path under the "
                "vault. This folder will use free-form content until fixed.",
                folder_raw, vault_path, vault_path.name,
            )
            continue

        try:
            template_path = resolve_path_maybe_outside_vault(str(template_raw), vault_path)
        except ValueError:
            logger.warning(
                "Skipping OBSIDIAN_FOLDER_TEMPLATES entry for folder %r: empty template path.",
                folder_raw,
            )
            continue

        if not template_path.is_file():
            logger.warning(
                "Skipping OBSIDIAN_FOLDER_TEMPLATES entry for folder %r: template file not "
                "found at %s. Templates may live inside or outside the vault, but the file "
                "must exist. This folder will use free-form content until fixed.",
                folder, template_path,
            )
            continue

        rules.append(FolderTemplateRule(folder=folder, template_path=template_path, template_display=str(template_raw)))

    # Longest-prefix-first so a lookup can stop at the first match (more
    # specific rules — e.g. "04-resources/artigos" — win over "04-resources").
    rules.sort(key=lambda r: len(r.folder), reverse=True)
    return rules


def find_template_rule(note_dir: str, rules: List[FolderTemplateRule]) -> Optional[FolderTemplateRule]:
    """Longest-prefix match: note_dir must equal a rule's folder or be one of
    its subfolders. `rules` is expected pre-sorted longest-folder-first.
    """
    for rule in rules:
        if note_dir == rule.folder or (rule.folder and note_dir.startswith(rule.folder + "/")):
            return rule
    return None


_H2_HEADING_RE = re.compile(r"^##(?!#)[ \t]+(.+?)\s*$", re.MULTILINE)


def extract_required_headings(template_content: str) -> List[str]:
    """Level-2 ("## ") headings from a template, in document order."""
    return [m.group(1).strip() for m in _H2_HEADING_RE.finditer(template_content)]


def build_template_info(vault, note_dir: str) -> Dict[str, Any]:
    """Describe the template rule (if any) applying to note_dir. This shape
    is returned as-is by get_note_template_tool, and is embedded in
    template-conformance violation errors so the LLM can retry with the
    exact skeleton it needs.

    `required_frontmatter_keys` always folds in "description" when
    vault.require_frontmatter is on: that vault-wide contract (spec section
    10.3) applies to every write independent of whether a folder template
    even matches, so omitting it here — as this used to — left the caller to
    discover it the hard way, via a second, avoidable error from
    apply_frontmatter_requirements after already clearing this check. This
    folding is informational only: check_template_conformance's own
    pass/fail gate deliberately keeps using `template_frontmatter_keys`
    (the template's own declared keys, un-unioned) below, so a missing
    "description" alone still surfaces as apply_frontmatter_requirements's
    value-quality error, not a template-conformance one — the two stay
    separate, composable gates (spec section 10.3's "template-aware, sem
    duplicar" bullet), only the LLM-facing summary is unioned so the caller
    sees the full contract in one pass. "name" is deliberately never added
    to either list this way: it's always auto-injected from the filename
    (see `instructions`), never something the caller needs to supply.
    """
    always_required = ["description"] if vault.require_frontmatter else []

    rule = find_template_rule(note_dir, vault.folder_templates)
    if rule is None:
        return {
            "enforced": False,
            "folder_rule": None,
            "template_path": None,
            "required_headings": [],
            "required_frontmatter_keys": always_required,
            "template_frontmatter_keys": [],
            "skeleton": None,
            "instructions": "No template is configured for this folder; free-form content is fine.",
        }

    skeleton = rule.template_path.read_text(encoding="utf-8")
    headings = extract_required_headings(skeleton)
    frontmatter, _ = vault._parse_frontmatter(skeleton)
    frontmatter_keys = list(frontmatter.keys())
    required_frontmatter_keys = always_required + [k for k in frontmatter_keys if k not in always_required]

    return {
        "enforced": True,
        "folder_rule": rule.folder,
        "template_path": rule.template_display,
        "required_headings": headings,
        "required_frontmatter_keys": required_frontmatter_keys,
        "template_frontmatter_keys": frontmatter_keys,
        "skeleton": skeleton,
        "instructions": (
            f"Notes under '{rule.folder}' must include every required heading below, in the "
            "same relative order (extra headings are allowed anywhere), plus every required "
            "frontmatter key (values are free). 'name' is auto-injected from the filename — "
            "never supply it yourself, even if it's listed above. Use the skeleton as your "
            "starting point."
        ),
    }


def check_template_conformance(vault, relpath: str, content: str) -> None:
    """Raise ValueError (caught upstream and surfaced as a ToolError) if
    `content` violates the template rule for relpath's folder. No-op if no
    rule applies. Only meant for full-content writes (create_note,
    update_note replace) — incremental edits are exempt by design (spec
    section 3).
    """
    note_dir = str(PurePosixPath(relpath).parent)
    note_dir = "" if note_dir == "." else note_dir

    info = build_template_info(vault, note_dir)
    if not info["enforced"]:
        return

    required_headings = info["required_headings"]
    content_headings = [m.group(1).strip() for m in _H2_HEADING_RE.finditer(content)]

    missing = [h for h in required_headings if h not in content_headings]
    present_required_in_order = [h for h in content_headings if h in required_headings]
    expected_order = [h for h in required_headings if h not in missing]
    out_of_order = present_required_in_order != expected_order

    frontmatter, _ = vault._parse_frontmatter(content)
    frontmatter_keys = set(frontmatter.keys())
    # Template-declared keys only (NOT the unioned info["required_frontmatter_keys"]):
    # a missing "description" alone must stay apply_frontmatter_requirements's
    # error to raise, not this function's — see build_template_info's docstring.
    missing_frontmatter_keys = [k for k in info["template_frontmatter_keys"] if k not in frontmatter_keys]

    if not missing and not out_of_order and not missing_frontmatter_keys:
        return

    message_parts = [
        f"Content does not conform to the template configured for folder '{info['folder_rule']}' "
        f"({info['template_path']}).",
    ]
    if missing:
        message_parts.append(f"Missing headings: {missing}.")
    if out_of_order:
        message_parts.append(
            f"Headings out of order: found {present_required_in_order}, expected {expected_order}."
        )
    if missing_frontmatter_keys:
        message_parts.append(f"Missing frontmatter keys: {missing_frontmatter_keys}.")
    message_parts.append(
        f"Resend the FULL content following the template at '{info['template_path']}'. "
        f"Required headings in order: {required_headings}. "
        f"Required frontmatter keys: {info['required_frontmatter_keys']}. "
        f"Skeleton:\n{info['skeleton']}"
    )
    raise ValueError(" ".join(message_parts))


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
) -> Optional[str]:
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


# ---------------------------------------------------------------------------
# Slug (filename) and tag kebab-normalization (spec section 1: OBSIDIAN_SLUG_STYLE / _TAG_STYLE)
# ---------------------------------------------------------------------------

_NON_SLUG_RE = re.compile(r"[^a-z0-9]+")
_HYPHEN_RUN_RE = re.compile(r"-{2,}")
_TAG_SEGMENT_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def slugify_kebab(text: str) -> Optional[str]:
    """Transliterate arbitrary text to ASCII kebab-case: NFD-decompose,
    strip combining marks (accents — e.g. "a" + U+0301 from decomposed "á"),
    lowercase, collapse any run of non-[a-z0-9] characters to a single '-',
    trim leading/trailing '-'.

    Returns None if nothing alphanumeric survives (e.g. an all-emoji or
    all-CJK string can't be transliterated to ASCII) — callers treat that as
    "non-normalizable" and raise.
    """
    decomposed = unicodedata.normalize("NFD", text)
    # unicodedata.combining() is the correct stdlib tool for "is this
    # codepoint a combining mark" — avoids hardcoding a specific Unicode
    # block via regex (and avoids embedding literal combining characters in
    # source, which are unreadable/fragile in a diff).
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    lowered = stripped.lower()
    slug = _NON_SLUG_RE.sub("-", lowered).strip("-")
    slug = _HYPHEN_RUN_RE.sub("-", slug)
    return slug or None


def normalize_tag_kebab(tag: str) -> Optional[str]:
    """Kebab-normalize a (possibly hierarchical, 'a/b/c') tag, segment by
    segment. Returns None if any segment has nothing alphanumeric left.
    """
    segments = tag.split("/")
    normalized_segments = []
    for segment in segments:
        slug = slugify_kebab(segment)
        if slug is None or not _TAG_SEGMENT_RE.match(slug):
            return None
        normalized_segments.append(slug)
    return "/".join(normalized_segments)


# ---------------------------------------------------------------------------
# Per-note name/description (spec section 10.2) — feeds VaultCache so the
# search index mode (10.4) and the frontmatter-requirement enforcement
# (10.3) share one extraction, parsed once at index time (utils/vault_cache.py
# calls these from _index_note), never re-read from disk per consumer.
# ---------------------------------------------------------------------------

_ANY_HEADING_RE = re.compile(r"^#{1,6}(?!#)[ \t]+")


def derive_note_name(relpath: str, frontmatter: Dict[str, Any]) -> str:
    """frontmatter['name'] if it's a non-empty string, else the filename
    stem (basename without .md) — the same fallback OBSIDIAN_REQUIRE_FRONTMATTER
    forces onto every note (spec section 10.3), applied here unconditionally
    so the cache/index mode has a usable `name` even when that config is off.
    """
    name = frontmatter.get("name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    filename = relpath.rsplit("/", 1)[-1]
    return filename[:-3] if filename.endswith(".md") else filename


def derive_note_description(frontmatter: Dict[str, Any], clean_content: str) -> str:
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

def _serialize_frontmatter_block(frontmatter: Dict[str, Any], clean_content: str) -> str:
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
      already convert ValueError to an actionable ToolError) so the LLM
      supplies it and retries.

    Any other frontmatter keys already in content (e.g. a folder template's
    own required keys, already validated present by check_template_conformance,
    which runs before this in note_management._apply_write_checks) are
    preserved as-is — this only ever touches `name`.
    """
    if not vault.require_frontmatter:
        return content

    filename = relpath.rsplit("/", 1)[-1]
    required_name = filename[:-3] if filename.endswith(".md") else filename

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
