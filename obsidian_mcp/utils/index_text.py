"""Pure text/hash helpers for the persistent search index.

Extracted from PersistentSearchIndex so this logic is directly unit-testable
without a database connection. PersistentSearchIndex keeps a thin delegating
method for determine_property_type (test_persistent_index_properties.py calls
it via self); the other functions here had no callers outside
persistent_index.py, so their methods were removed and the call sites there
now import these functions directly.
"""

import hashlib
from datetime import datetime
from typing import Any


def compute_hash(content: str) -> str:
    """Compute SHA256 hash of content."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def determine_property_type(value: Any) -> str:
    """Determine the type of a property value."""
    if isinstance(value, bool):
        return "boolean"
    elif isinstance(value, (int, float)):
        return "number"
    elif isinstance(value, list):
        return "list"
    elif isinstance(value, dict):
        return "object"
    elif isinstance(value, str):
        # Check if it's a date-like string
        try:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
            return "date"
        except ValueError:
            # Narrowed from bare `except:` during the tools/utils split.
            # Verified deliberate: value is already isinstance-narrowed to
            # str, str.replace() can't raise, and fromisoformat() raises
            # only ValueError for a str it can't parse -- there is nothing
            # else for this except to catch.
            return "text"
    else:
        return "text"


def calculate_line_offsets(content: str) -> list[int]:
    """Calculate byte offsets of each line start."""
    line_offsets = [0]
    for i, char in enumerate(content):
        if char == "\n":
            line_offsets.append(i + 1)
    return line_offsets


def extract_literal_prefix(pattern: str) -> str | None:
    """Extract a literal prefix from regex pattern for FTS5 pre-filtering."""
    # Simple extraction - look for literal characters at the start
    literal = ""
    escaped = False

    for char in pattern:
        if escaped:
            # If it's a regex escape sequence, stop extraction
            if char in "dDwWsSbBAZ" or char in "nrtfv":  # Common regex escape sequences
                break
            elif char in ".^$*+?{[|(\\":  # Escaped metacharacters
                literal += char
            else:
                # Unknown escape, stop extraction to be safe
                break
            escaped = False
        elif char == "\\":
            escaped = True
        elif char in ".^$*+?{[|(":
            break  # Regex metacharacter
        elif char == '"':
            # Not a regex metacharacter, but unsafe to carry into the FTS5
            # phrase persistent_index.py builds from this literal
            # (f'"{literal_prefix}"'): an unescaped '"' there breaks out of
            # the intended phrase quoting and is reinterpreted as FTS5 query
            # syntax. Stopping here means the extracted literal can never
            # contain one, so the caller never needs to escape it.
            break
        else:
            literal += char

    return literal if len(literal) >= 3 else None


def find_line_number(line_starts: list[int], position: int) -> int:
    """Find line number using binary search."""
    left, right = 0, len(line_starts) - 1

    while left <= right:
        mid = (left + right) // 2
        if mid + 1 < len(line_starts):
            if line_starts[mid] <= position < line_starts[mid + 1]:
                return mid + 1
            elif position < line_starts[mid]:
                right = mid - 1
            else:
                left = mid + 1
        else:
            return mid + 1

    return len(line_starts)


def _extract_context(text: str, start: int, end: int, context_length: int) -> str:
    """Slice ~context_length characters of context around [start:end),
    trimmed, with an ellipsis prepended/appended on whichever side got
    truncated. Shared by search_file_content and search_large_file_content.
    """
    context_start = max(0, start - context_length // 2)
    context_end = min(len(text), end + context_length // 2)
    context = text[context_start:context_end].strip()
    if context_start > 0:
        context = "..." + context
    if context_end < len(text):
        context = context + "..."
    return context


def search_file_content(
    content: str,
    regex,
    line_offsets: list[int] | None,
    context_length: int,
    max_matches: int = 5,
) -> list[dict[str, Any]]:
    """Search content and return match contexts."""
    match_contexts = []

    # Use finditer for streaming matches
    for match_count, match in enumerate(regex.finditer(content)):
        if match_count >= max_matches:
            break

        match_start = match.start()
        match_end = match.end()

        # Find line number efficiently
        if line_offsets:
            line_num = find_line_number(line_offsets, match_start)
        else:
            # Fallback: count newlines before match
            line_num = content[:match_start].count("\n") + 1

        # Extract context
        context = _extract_context(content, match_start, match_end, context_length)

        match_contexts.append(
            {
                "match": match.group(0),
                "line": line_num,
                "context": context,
                "groups": match.groups() if match.groups() else None,
            }
        )

    return match_contexts


def search_large_file_content(
    content: str,
    regex,
    line_offsets: list[int] | None,
    context_length: int,
    max_matches: int = 5,
) -> list[dict[str, Any]]:
    """Search large file content using a chunked/streaming approach.

    Extracted from PersistentSearchIndex._search_large_file_streaming (a
    method with no `await` anywhere in its body despite being `async def`)
    so persistent_index.py's _process_file_regex can run it in a
    ProcessPoolExecutor worker instead of inline on the event loop. That
    matters because CPython's `re` engine holds the GIL for the entire
    duration of a match: a catastrophic-backtracking pattern here would
    otherwise stall the whole asyncio event loop -- verified empirically,
    not just theorized -- not only this one search.
    """
    match_contexts = []
    chunk_size = 512 * 1024  # 512KB chunks
    overlap = 1024  # 1KB overlap to catch matches at boundaries

    pos = 0
    while pos < len(content) and len(match_contexts) < max_matches:
        # Extract chunk with overlap
        chunk_start = max(0, pos - overlap)
        chunk_end = min(len(content), pos + chunk_size)
        chunk = content[chunk_start:chunk_end]

        # Find matches in chunk
        for match in regex.finditer(chunk):
            # Adjust match position to full content coordinates
            match_start = chunk_start + match.start()
            match_end = chunk_start + match.end()

            # Skip if this match was already found in overlap
            if match_contexts and match_start <= match_contexts[-1]["match_start"]:
                continue

            # Find line number
            if line_offsets:
                line_num = find_line_number(line_offsets, match_start)
            else:
                line_num = content[:match_start].count("\n") + 1

            # Extract context from full content
            context = _extract_context(content, match_start, match_end, context_length)

            match_contexts.append(
                {
                    "match": match.group(0),
                    "line": line_num,
                    "context": context,
                    "groups": match.groups() if match.groups() else None,
                    "match_start": match_start,  # For duplicate detection
                }
            )

            if len(match_contexts) >= max_matches:
                break

        # Move to next chunk
        pos += chunk_size

    # Remove the match_start field used for duplicate detection
    for match_ctx in match_contexts:
        match_ctx.pop("match_start", None)

    return match_contexts
