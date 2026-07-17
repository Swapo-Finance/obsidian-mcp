"""Pure link-extraction helpers shared by tools/link_management.py and
utils/vault_cache.py. Moved out of tools/link_management.py so the cache
(utils layer) can reuse the exact same extraction logic without importing
from tools/ (this codebase's modules import utils -> never the reverse).

tools/link_management.py re-exports WIKI_LINK_PATTERN, MARKDOWN_LINK_PATTERN,
and extract_links_from_content from here, so the one pre-existing external
import (`from ..tools.link_management import get_backlinks, WIKI_LINK_PATTERN`
in organization.py) keeps working unchanged.
"""

import re
from typing import List, Dict

# Regular expressions for matching different link types.
WIKI_LINK_PATTERN = re.compile(r'\[\[([^\]|]+)(\|([^\]]+))?\]\]')
MARKDOWN_LINK_PATTERN = re.compile(r'\[([^\]]+)\]\(([^)]+)\)')


def extract_links_from_content(content: str) -> List[Dict[str, str]]:
    """
    Extract all links from note content.

    Finds both wiki-style ([[Link]]) and markdown-style ([text](link)) links.
    This is the pre-existing extraction used by get_backlinks/get_outgoing_links/
    find_broken_links — it does NOT exclude embeds (![[...]]) or code, by
    design, so their behavior stays identical to before the cache existed.
    (The new write-time wikilink *validation* path in link_management.py
    uses a separate extractor that does exclude those, per spec section 4.)

    Args:
        content: The note content to extract links from

    Returns:
        List of link dictionaries with path, display text, and type
    """
    links = []

    # Extract wiki-style links
    for match in WIKI_LINK_PATTERN.finditer(content):
        link_path = match.group(1).strip()
        alias = match.group(3)

        # Ensure .md extension for internal links
        if not link_path.endswith('.md') and not link_path.startswith('http'):
            link_path += '.md'

        links.append({
            'path': link_path,
            'display_text': alias.strip() if alias else match.group(1).strip(),
            'type': 'wiki'
        })

    # Extract markdown-style links (only internal links, not URLs)
    for match in MARKDOWN_LINK_PATTERN.finditer(content):
        link_path = match.group(2).strip()

        # Skip external URLs
        if link_path.startswith('http://') or link_path.startswith('https://'):
            continue

        # Ensure .md extension
        if not link_path.endswith('.md'):
            link_path += '.md'

        links.append({
            'path': link_path,
            'display_text': match.group(1).strip(),
            'type': 'markdown'
        })

    return links
