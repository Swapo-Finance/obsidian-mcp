"""Frontmatter and inline tag rewriting helpers for Obsidian MCP server."""

import re

import yaml


def _format_tag_for_flow_list(tag: str) -> str:
    """Render one tag for a `tags: [...]` flow-sequence line.

    Double-quotes it (escaping '\\' and '"') only when it contains a literal
    comma -- otherwise an unquoted comma would be misread as the separator
    between two tags the next time this line is parsed (Finding 2). Every
    other tag is left exactly as before: plain, unquoted text.
    """
    if "," not in tag:
        return tag
    escaped = tag.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _parse_flow_list_tags(line: str) -> list[str]:
    """Parse a `tags: [tag1, tag2]` flow-sequence line into a tag list.

    YAML-aware (yaml.safe_load) so a quoted tag containing a literal comma
    (e.g. "has, comma") stays one tag instead of being split on every ','
    (Finding 2). Falls back to a naive comma-split only when the bracketed
    text isn't valid YAML on its own. Returns [] if the line has no closing
    ']' to match against.
    """
    match = re.search(r"\[(.*?)\]", line)
    if not match:
        return []
    inner = match.group(1)
    try:
        parsed = yaml.safe_load(f"[{inner}]")
    except yaml.YAMLError:
        parsed = None
    if isinstance(parsed, list):
        return [str(t).strip() for t in parsed if str(t).strip()]
    return [t.strip().strip('"').strip("'") for t in inner.split(",") if t.strip()]


def _parse_bullet_list_tags(lines: list[str], start: int) -> tuple[list[str], int]:
    """Parse a `tags:` bullet-list block ("- tag" on consecutive lines)
    starting at index `start` (the line right after `tags:`). Returns
    (tags, index of the last bullet line consumed) so the caller's
    line-scan loop can resume from there -- mirrors the original inline
    while-loop's index bookkeeping exactly (caller's loop increments once
    more after this returns).
    """
    tags = []
    i = start
    while i < len(lines) and lines[i].strip().startswith("- "):
        tag = lines[i].strip()[2:].strip()
        if tag:
            tags.append(tag)
        i += 1
    return tags, i - 1


def _apply_tag_operation(
    existing_tags: list[str], tags: list[str], operation: str
) -> list[str]:
    """Add/remove/replace existing_tags with tags per operation -- the same
    add (dedup)/replace/remove semantics add_tags/update_tags/remove_tags
    apply via their own tools."""
    if operation == "add":
        result = list(existing_tags)
        for tag in tags:
            if tag not in result:
                result.append(tag)
        return result
    if operation == "replace":
        return list(tags)
    return [t for t in existing_tags if t not in tags]  # remove


def _update_frontmatter_tags(content: str, tags: list[str], operation: str) -> str:
    """
    Update tags in YAML frontmatter.

    Args:
        content: Note content
        tags: Tags to add, remove, or replace with
        operation: "add", "remove", or "replace"

    Returns:
        Updated content
    """
    # Check if frontmatter exists
    if not content.startswith("---\n"):
        # Create frontmatter if it doesn't exist
        if operation in ["add", "replace"]:
            frontmatter = f"---\ntags: {tags}\n---\n\n"
            return frontmatter + content
        else:
            # Nothing to remove if no frontmatter
            return content

    # Parse existing frontmatter
    try:
        end_index = content.index("\n---\n", 4) + 5
        frontmatter = content[4 : end_index - 5]
        rest_of_content = content[end_index:]
    except ValueError:
        # Invalid frontmatter
        return content

    # Parse YAML manually (simple approach for tags)
    lines = frontmatter.split("\n")
    new_lines = []
    tags_found = False
    existing_tags = []

    i = 0
    while i < len(lines):
        line = lines[i]

        if line.startswith("tags:"):
            tags_found = True
            # Check if tags are on the same line
            if "[" in line:
                # Array format: tags: [tag1, tag2]
                existing_tags = _parse_flow_list_tags(line)
            elif line.strip() != "tags:":
                # Inline format: tags: tag1 tag2
                existing_tags = line.split(":", 1)[1].strip().split()
            else:
                # Bullet list format on next lines
                existing_tags, i = _parse_bullet_list_tags(lines, i + 1)

            existing_tags = _apply_tag_operation(existing_tags, tags, operation)

            # Format updated tags
            if existing_tags:
                formatted = ", ".join(
                    _format_tag_for_flow_list(t) for t in existing_tags
                )
                new_lines.append(f"tags: [{formatted}]")
            # Skip line if no tags remain

        else:
            new_lines.append(line)

        i += 1

    # If no tags were found and we're adding or replacing, add them
    if not tags_found and operation in ["add", "replace"]:
        formatted = ", ".join(_format_tag_for_flow_list(t) for t in tags)
        new_lines.insert(0, f"tags: [{formatted}]")

    # Reconstruct content
    new_frontmatter = "\n".join(new_lines)
    return f"---\n{new_frontmatter}\n---\n{rest_of_content}"


def _remove_inline_tags(content: str, tags_to_remove: list[str]) -> tuple[str, int]:
    """
    Remove inline tags from note body while preserving frontmatter.

    Args:
        content: Full note content
        tags_to_remove: List of tags to remove (without # prefix)

    Returns:
        Tuple of (updated content, number of tags removed)
    """
    if not tags_to_remove:
        return content, 0

    # Skip frontmatter if it exists
    body_start = 0
    if content.startswith("---\n"):
        try:
            end_index = content.index("\n---\n", 4) + 5
            body_start = end_index
        except ValueError:
            pass

    frontmatter = content[:body_start] if body_start > 0 else ""
    body = content[body_start:]

    # Remove code blocks to avoid modifying tags in code
    code_blocks = []

    # Remove fenced code blocks
    def replace_code_block(match):
        code_blocks.append(match.group(0))
        return f"__CODE_BLOCK_{len(code_blocks) - 1}__"

    body_no_code = re.sub(r"```[\s\S]*?```", replace_code_block, body)

    # Remove inline code
    inline_code = []

    def replace_inline_code(match):
        inline_code.append(match.group(0))
        return f"__INLINE_CODE_{len(inline_code) - 1}__"

    body_no_code = re.sub(r"`[^`]+`", replace_inline_code, body_no_code)

    # Remove tags
    tags_removed = 0
    for tag in tags_to_remove:
        # Escape special regex characters
        escaped_tag = re.escape(tag)
        # Match #tag at word boundaries (not part of URL or other text)
        pattern = rf"(^|\s)#{escaped_tag}(?=\s|$|\.|,|;|:|!|\?|\))"

        # Count matches before replacing
        matches = re.findall(pattern, body_no_code)
        tags_removed += len(matches)

        # Remove the tags (keep the whitespace before the tag)
        body_no_code = re.sub(pattern, r"\1", body_no_code)

    # Clean up multiple spaces left by removal
    body_no_code = re.sub(r"  +", " ", body_no_code)

    # Restore code blocks
    for i, block in enumerate(code_blocks):
        body_no_code = body_no_code.replace(f"__CODE_BLOCK_{i}__", block)

    for i, code in enumerate(inline_code):
        body_no_code = body_no_code.replace(f"__INLINE_CODE_{i}__", code)

    return frontmatter + body_no_code, tags_removed
