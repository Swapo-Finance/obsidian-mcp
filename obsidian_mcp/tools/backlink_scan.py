"""Backlink-matching helpers used by link_management.get_backlinks.

Split out of link_management.py to bring that file back under the
project's 350-line limit after the C901 complexity-reduction pass added
these as private helpers of get_backlinks. Safe to split: neither function
calls get_vault() (_check_note_for_backlinks takes vault as a plain
parameter instead), and neither is patched by module-qualified name
anywhere in tests/ -- only called directly from get_backlinks, which keeps
working regardless of which module defines them. get_backlinks and
get_outgoing_links themselves stay in link_management.py because they do
call get_vault() directly (see that module's docstring).
"""

from ..utils.links import MARKDOWN_LINK_PATTERN, WIKI_LINK_PATTERN
from .link_index import get_link_context


def _target_name_variants(path: str) -> list[str]:
    """Build the name variations (with/without extension, full path vs.
    bare filename) that a wiki/markdown link's target must match to count
    as a backlink to `path`."""
    target_names = [path]
    if path.endswith(".md"):
        target_names.append(path[:-3])

    filename = path.split("/")[-1]
    if filename not in target_names:
        target_names.append(filename)
    if filename.endswith(".md"):
        filename_no_ext = filename[:-3]
        if filename_no_ext not in target_names:
            target_names.append(filename_no_ext)

    return target_names


async def _check_note_for_backlinks(
    vault,
    note_path: str,
    target_path: str,
    target_names: list[str],
    include_context: bool,
    context_length: int,
) -> list[dict]:
    """Scan one note's content for wiki/markdown links matching
    target_names, returning one backlink dict per match.

    Module-level rather than a get_backlinks() closure so it has no captured
    state and can be unit-tested directly; takes vault/target_path/
    target_names explicitly since it no longer inherits them from the
    enclosing scope.
    """
    if note_path == target_path:
        return []

    try:
        note = await vault.read_note(note_path)

        content = note.content
        note_backlinks = []

        # Check for wiki-style links
        for match in WIKI_LINK_PATTERN.finditer(content):
            linked_path = match.group(1).strip()

            # Check if this link matches our target
            is_match = False
            if linked_path in target_names or linked_path + ".md" in target_names:
                is_match = True

            if is_match:
                alias = match.group(3)
                link_text = alias.strip() if alias else match.group(1).strip()

                backlink_info = {
                    "source_path": note_path,
                    "link_text": link_text,
                    "link_type": "wiki",
                }

                if include_context:
                    backlink_info["context"] = get_link_context(
                        content, match, context_length
                    )

                note_backlinks.append(backlink_info)

        # Check for markdown-style links
        for match in MARKDOWN_LINK_PATTERN.finditer(content):
            link_path = match.group(2).strip()
            if link_path in target_names:
                backlink_info = {
                    "source_path": note_path,
                    "link_text": match.group(1).strip(),
                    "link_type": "markdown",
                }

                if include_context:
                    backlink_info["context"] = get_link_context(
                        content, match, context_length
                    )

                note_backlinks.append(backlink_info)

        return note_backlinks

    except Exception:
        return []
