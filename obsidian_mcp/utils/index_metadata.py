"""Pure metadata-extraction helpers that feed the search index.

Extracted from ObsidianVault so this logic is directly unit-testable without
a vault on disk. ObsidianVault keeps thin delegating methods
(_extract_file_metadata, _serialize_metadata) for existing callers — see
filesystem.py.
"""

import logging
import re
from datetime import date, datetime
from typing import Any

import yaml

logger = logging.getLogger(__name__)


def extract_file_metadata(content: str) -> dict[str, Any]:
    """Extract metadata from file content (tags, frontmatter, etc.)."""
    metadata = {}

    # Extract frontmatter
    if content.startswith("---\n"):
        try:
            end_index = content.find("\n---\n", 4)
            if end_index > 0:
                frontmatter_text = content[4:end_index]
                frontmatter = yaml.safe_load(frontmatter_text) or {}
                # Convert dates and other non-serializable objects to strings
                metadata["frontmatter"] = serialize_metadata(frontmatter)
        except Exception as e:
            # Narrowed from a bare `except:` during the refactor (deliberate:
            # a bare except also swallows KeyboardInterrupt/SystemExit, which
            # should still propagate here).
            # Best-effort: a file with unparseable frontmatter still gets
            # indexed for full-text search, just without property/tag
            # enrichment from its frontmatter.
            logger.debug(f"Failed to extract frontmatter for indexing: {e}")

    # Extract tags
    tags = set()
    # Frontmatter tags
    if "frontmatter" in metadata and "tags" in metadata["frontmatter"]:
        fm_tags = metadata["frontmatter"]["tags"]
        if isinstance(fm_tags, list):
            tags.update(fm_tags)
        elif isinstance(fm_tags, str):
            tags.add(fm_tags)

    # Inline tags
    tag_pattern = r"#([a-zA-Z0-9_\-/]+)"
    for match in re.finditer(tag_pattern, content):
        tags.add(match.group(1))

    metadata["tags"] = list(tags)

    return metadata


def serialize_metadata(obj: Any) -> Any:
    """Convert non-serializable objects to JSON-serializable format."""
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    elif isinstance(obj, dict):
        return {k: serialize_metadata(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [serialize_metadata(v) for v in obj]
    else:
        return obj
