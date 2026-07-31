"""Folder-to-template mapping and template conformance checking (spec
sections 1 and 3): parsing OBSIDIAN_FOLDER_TEMPLATES, matching a note's
folder to its configured template, extracting required headings, and
validating a note's content against the matched template.

Split out of vault_config.py; re-exported from there for backward
compatibility.
"""

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from .vault_paths import (
    normalize_vault_relative_path,
    resolve_path_maybe_outside_vault,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FolderTemplateRule:
    folder: str  # canonical vault-relative POSIX path, e.g. "01-projects"
    template_path: Path  # absolute path to the template file (may be outside the vault)
    template_display: str  # the original configured template string, for messages


def parse_folder_templates(
    raw_json: str | None, vault_path: Path
) -> list["FolderTemplateRule"]:
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

    rules: list[FolderTemplateRule] = []
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
                folder_raw,
                vault_path,
                vault_path.name,
            )
            continue

        try:
            template_path = resolve_path_maybe_outside_vault(
                str(template_raw), vault_path
            )
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
                folder,
                template_path,
            )
            continue

        rules.append(
            FolderTemplateRule(
                folder=folder,
                template_path=template_path,
                template_display=str(template_raw),
            )
        )

    # Longest-prefix-first so a lookup can stop at the first match (more
    # specific rules — e.g. "04-resources/artigos" — win over "04-resources").
    rules.sort(key=lambda r: len(r.folder), reverse=True)
    return rules


def find_template_rule(
    note_dir: str, rules: list[FolderTemplateRule]
) -> FolderTemplateRule | None:
    """Longest-prefix match: note_dir must equal a rule's folder or be one of
    its subfolders. `rules` is expected pre-sorted longest-folder-first.
    """
    for rule in rules:
        if note_dir == rule.folder or (
            rule.folder and note_dir.startswith(rule.folder + "/")
        ):
            return rule
    return None


_H2_HEADING_RE = re.compile(r"^##(?!#)[ \t]+(.+?)\s*$", re.MULTILINE)


def extract_required_headings(template_content: str) -> list[str]:
    """Level-2 ("## ") headings from a template, in document order."""
    return [m.group(1).strip() for m in _H2_HEADING_RE.finditer(template_content)]


def build_template_info(vault, note_dir: str) -> dict[str, Any]:
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
    sees the full contract in one pass.

    "name" runs the opposite way: when vault.require_frontmatter is on it is
    *stripped* from both lists even if the template file itself declares it,
    because apply_frontmatter_requirements force-injects it from the filename
    moments later — see the filter below.
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
    # `name` is force-injected from the filename by apply_frontmatter_requirements,
    # which runs right AFTER check_template_conformance's gate (see
    # write_policy._apply_write_checks) — so demanding the caller supply it
    # first flatly contradicts `instructions` below, which tells them not to.
    # Dropped only when that injection is actually going to happen: with
    # require_frontmatter off nothing ever writes `name`, so a template that
    # declares it still legitimately requires the caller to provide it.
    frontmatter_keys = [
        k for k in frontmatter if not (vault.require_frontmatter and k == "name")
    ]
    required_frontmatter_keys = always_required + [
        k for k in frontmatter_keys if k not in always_required
    ]

    name_clause = (
        "'name' is auto-injected from the filename — never supply it yourself. "
        if vault.require_frontmatter
        else ""
    )

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
            f"frontmatter key (values are free). {name_clause}Use the skeleton as your "
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
    missing_frontmatter_keys = [
        k for k in info["template_frontmatter_keys"] if k not in frontmatter_keys
    ]

    if not missing and not out_of_order and not missing_frontmatter_keys:
        return

    message_parts = [
        (
            f"Content does not conform to the template configured for folder '{info['folder_rule']}' "
            f"({info['template_path']})."
        ),
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
