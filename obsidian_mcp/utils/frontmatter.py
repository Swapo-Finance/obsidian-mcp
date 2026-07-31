"""Pure frontmatter parsing/normalization helpers (no vault dependency).

Extracted from ObsidianVault so this logic is directly unit-testable without
a vault on disk. ObsidianVault keeps thin delegating methods
(_parse_frontmatter, _normalize_frontmatter, _extract_tags) for existing
callers — see filesystem.py.
"""

import logging
import re
from typing import Any

import yaml

logger = logging.getLogger(__name__)


def parse_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    """
    Parse YAML frontmatter from markdown content.

    Args:
        content: Full markdown content

    Returns:
        Tuple of (frontmatter dict, content without frontmatter)
    """
    frontmatter = {}
    clean_content = content

    # Check if content starts with frontmatter
    if content.startswith("---\n"):
        try:
            # Find the closing ---
            end_index = content.find("\n---\n", 4)
            if end_index != -1:
                # Extract frontmatter text
                fm_text = content[4:end_index]

                # Parse YAML properly
                try:
                    frontmatter = yaml.safe_load(fm_text) or {}
                    # Ensure it's a dict
                    if not isinstance(frontmatter, dict):
                        frontmatter = {}
                except yaml.YAMLError:
                    # Fall back to simple parsing for invalid YAML (e.g. an
                    # unquoted ':' inside a plain-scalar value like
                    # `description: ...via Blnk: graceful...` makes
                    # yaml.safe_load reject the WHOLE block, not just that
                    # field). Flow-sequence values (`key: [a, b, c]`) are
                    # still split into a real list here — same bracket
                    # parsing organization._update_frontmatter_tags already
                    # uses — so tags/aliases/cssclasses don't collapse into
                    # a single string containing the literal brackets.
                    frontmatter = {}
                    for line in fm_text.split("\n"):
                        if ":" in line and not line.strip().startswith("#"):
                            key, value = line.split(":", 1)
                            key = key.strip()
                            value = value.strip()
                            if not value:
                                continue
                            if value.startswith("[") and value.endswith("]"):
                                frontmatter[key] = [
                                    item.strip().strip('"').strip("'")
                                    for item in value[1:-1].split(",")
                                    if item.strip()
                                ]
                            else:
                                frontmatter[key] = value

                # Remove frontmatter from content
                clean_content = content[end_index + 4 :].lstrip()
        except Exception as e:
            # Malformed frontmatter must never block reading the note —
            # fall back to treating the whole file as plain content.
            logger.debug("Failed to parse frontmatter, returning raw content: %s", e)

    return frontmatter, clean_content


def _migrate_singular_to_plural(
    normalized: dict[str, Any], old_key: str, new_key: str
) -> None:
    """Migrate a legacy singular frontmatter key to its plural form in
    place: pop old_key, wrapping a bare string in a list, or keeping an
    existing list as-is. No-op if old_key is absent, or if new_key is
    already present (the plural form always wins over the legacy singular
    one)."""
    if old_key not in normalized or new_key in normalized:
        return
    value = normalized.pop(old_key)
    if isinstance(value, str):
        normalized[new_key] = [value]
    elif isinstance(value, list):
        normalized[new_key] = value


def normalize_frontmatter(frontmatter: dict[str, Any]) -> dict[str, Any]:
    """
    Normalize frontmatter to handle legacy property names.

    Migrations:
    - tag -> tags (as list)
    - alias -> aliases (as list)
    - cssClass -> cssclasses (as list)

    Args:
        frontmatter: Raw frontmatter dict

    Returns:
        Normalized frontmatter dict
    """
    normalized = frontmatter.copy()

    _migrate_singular_to_plural(normalized, "tag", "tags")
    _migrate_singular_to_plural(normalized, "alias", "aliases")
    _migrate_singular_to_plural(normalized, "cssClass", "cssclasses")

    # Ensure plural properties are lists
    for key in ["tags", "aliases", "cssclasses"]:
        if key in normalized:
            value = normalized[key]
            if isinstance(value, str):
                normalized[key] = [value]
            elif not isinstance(value, list):
                normalized[key] = []

    return normalized


def extract_tags(content: str, frontmatter: dict[str, Any]) -> list[str]:
    """
    Extract all tags from content and frontmatter.

    Args:
        content: Markdown content
        frontmatter: Parsed frontmatter

    Returns:
        List of unique tags (without # prefix)
    """
    tags = set()

    # Get tags from frontmatter (supports both "tag" and "tags")
    fm_tags = frontmatter.get("tags", frontmatter.get("tag", []))
    if isinstance(fm_tags, str):
        fm_tags = [fm_tags]
    elif not isinstance(fm_tags, list):
        fm_tags = []

    for tag in fm_tags:
        if isinstance(tag, str):
            tags.add(tag.lstrip("#"))

    # Remove code blocks from content before extracting tags
    # Remove fenced code blocks
    clean_content = re.sub(r"```[\s\S]*?```", "", content)
    # Remove inline code
    clean_content = re.sub(r"`[^`]+`", "", clean_content)

    # Find inline tags in cleaned content
    # More strict pattern: tag must be preceded by whitespace or start of line
    # Support hierarchical tags with forward slashes (e.g., #parent/child/grandchild)
    inline_tags = re.findall(
        r"(?:^|[\s\n])#([a-zA-Z0-9_\-]+(?:/[a-zA-Z0-9_\-]+)*)(?=\s|$)",
        clean_content,
        re.MULTILINE,
    )
    tags.update(inline_tags)

    return sorted(tags)
