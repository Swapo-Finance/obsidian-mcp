"""Regex-based note search."""

import re
from collections.abc import Sequence

from ..utils.filesystem import get_vault
from ..utils.validation import validate_context_length
from .search_result_modes import _resolve_search_mode, _to_index_items

# Business-logic-layer twin of the schema-level max_length on mcp_search.py's
# pattern Field (same convention as validate_context_length's 10..500 bound:
# duplicated rather than shared, since search_by_regex is called directly by
# tests/internal code that never goes through the MCP schema layer).
MAX_PATTERN_LENGTH = 500

# Best-effort, NOT exhaustive: flags the single most common catastrophic-
# backtracking shape, a quantified group whose own body is itself quantified
# (e.g. (a+)+, (.*)*, ([a-zA-Z]+)*). Deliberately not relied on as the real
# defense -- pattern blocklists are notoriously incomplete (alternation-based
# ambiguity like (a|aa)+ slips right past this). The actual backstop is the
# per-file execution timeout in persistent_index.py's _process_file_regex,
# which bounds every pattern regardless of shape or whether this heuristic
# recognizes it. This one just fails fast, with a clearer message, for the
# obvious cases.
_NESTED_QUANTIFIER_RE = re.compile(r"\([^()]*[+*][^()]*\)[+*?]")


def _validate_regex_pattern(pattern: str) -> None:
    """Reject a pattern that is too long, an obvious catastrophic-
    backtracking shape, or simply doesn't compile. Raises ValueError;
    returns nothing on success.

    Split out of search_by_regex (Goal on C901): these three checks were
    the biggest contributor to that function's complexity (16) alongside
    the flag parsing and result formatting below -- each is an independent,
    self-contained guard clause with no shared state.
    """
    if len(pattern) > MAX_PATTERN_LENGTH:
        raise ValueError(
            f"Regex pattern too long: {len(pattern)} characters "
            f"(max: {MAX_PATTERN_LENGTH}). Note: pathological patterns are "
            "typically short, so this alone doesn't guarantee safety -- it "
            "just rejects unreasonably large input early."
        )
    if _NESTED_QUANTIFIER_RE.search(pattern):
        raise ValueError(
            "Regex pattern rejected: it contains a quantified group nested "
            "inside another quantifier (e.g. '(a+)+', '(.*)*'), a shape "
            "prone to catastrophic backtracking. Rewrite it to avoid nested "
            "unbounded quantifiers (e.g. a more specific character class, "
            "or restructure so only one level is unbounded)."
        )
    try:
        re.compile(pattern)
    except re.error as e:
        raise ValueError(f"Invalid regular expression pattern: {e}")


def _parse_regex_flags(flags: Sequence[str] | None) -> int:
    """Translate string flag names (e.g. "ignorecase"/"i") into an `re`
    flags bitmask. Raises ValueError on an unrecognized flag."""
    if not flags:
        return 0
    flag_map = {
        "ignorecase": re.IGNORECASE,
        "i": re.IGNORECASE,
        "multiline": re.MULTILINE,
        "m": re.MULTILINE,
        "dotall": re.DOTALL,
        "s": re.DOTALL,
    }
    regex_flags = 0
    for flag in flags:
        if flag.lower() in flag_map:
            regex_flags |= flag_map[flag.lower()]
        else:
            raise ValueError(
                f"Unknown regex flag: {flag}. Supported flags: ignorecase/i, multiline/m, dotall/s"
            )
    return regex_flags


def _format_regex_result(result: dict) -> dict:
    """Format one vault.search_by_regex() match (one note) into the tool's
    output shape, keeping capture groups only when present."""
    formatted_result = {
        "path": result["path"],
        "match_count": result["match_count"],
        "matches": [],
    }
    for match in result["matches"]:
        match_info = {
            "match": match["match"],
            "line": match["line"],
            "context": match["context"],
        }
        if match["groups"]:
            match_info["groups"] = match["groups"]
        formatted_result["matches"].append(match_info)
    return formatted_result


async def search_by_regex(
    pattern: str,
    flags: Sequence[str] | None = None,
    context_length: int = 20,
    max_results: int = 50,
    mode: str | None = None,
    ctx=None,
) -> dict:
    """
    Search for notes using regular expressions for advanced pattern matching.

    Use this tool instead of search_notes when you need to find:
    - Code patterns (function definitions, imports, specific syntax)
    - Structured data with specific formats
    - Complex patterns that simple text search can't handle
    - Text with wildcards or variable parts

    When to use regex search vs regular search:
    - Use search_notes for: Simple text, note titles (with path:), tags
    - Use search_by_regex for: Code patterns, formatted data, complex matching

    Args:
        pattern: Regular expression pattern to search for
        flags: List of regex flags to apply (optional). Supported flags:
               - "ignorecase" or "i": Case-insensitive matching
               - "multiline" or "m": ^ and $ match line boundaries
               - "dotall" or "s": . matches newlines
        context_length: Number of characters to show around matches (default: 20)
        max_results: Maximum number of results to return (default: 50)
        ctx: MCP context for progress reporting

    Returns:
        Dictionary containing search results with matched patterns, line numbers, and context

    Common Use Cases:
        # Find Python imports of a specific module
        pattern: r"(import|from)\\s+fastmcp"

        # Find function definitions
        pattern: r"def\\s+\\w+\\s*\\([^)]*\\):"

        # Find TODO/FIXME comments with context
        pattern: r"(TODO|FIXME)\\s*:?\\s*(.+)"

        # Find URLs in notes
        pattern: r"https?://[^\\s)>]+"

        # Find code blocks of specific language
        pattern: r"```python([^`]+)```"

    Example:
        >>> # Find Python function definitions with 'search' in the name
        >>> await search_by_regex(r"def\\s+\\w*search\\w*\\s*\\([^)]*\\):", flags=["ignorecase"])
        {
            "pattern": "def\\s+\\w*search\\w*\\s*\\([^)]*\\):",
            "count": 3,
            "results": [
                {
                    "path": "code/search_utils.py",
                    "match_count": 2,
                    "matches": [
                        {
                            "match": "def search_notes(query, limit):",
                            "line": 15,
                            "context": "...async def search_notes(query, limit):\\n    '''Search through all notes'''...",
                            "groups": null
                        }
                    ]
                }
            ]
        }
    """
    _validate_regex_pattern(pattern)

    # Validate context_length
    is_valid, error = validate_context_length(context_length)
    if not is_valid:
        raise ValueError(error)

    regex_flags = _parse_regex_flags(flags)

    if ctx:
        await ctx.info(f"Searching with regex pattern: {pattern}")

    vault = get_vault()

    try:
        # Perform regex search
        results = await vault.search_by_regex(
            pattern, regex_flags, context_length, max_results
        )

        # Format results for output
        formatted_results = [_format_regex_result(result) for result in results]

        # Search result mode (spec section 10.4). There's no natural
        # "score" field for a regex match, so match_count doubles as one —
        # more matches in a note is a reasonable proxy for relevance.
        effective_mode = _resolve_search_mode(vault, mode, len(formatted_results))
        if effective_mode == "index":
            formatted_results = await _to_index_items(
                vault,
                formatted_results,
                default_match_type="regex",
                score_key="match_count",
            )

        # Return standardized search results structure
        return {
            "results": formatted_results,
            "count": len(formatted_results),
            "query": {
                "pattern": pattern,
                "flags": flags or [],
                "context_length": context_length,
                "max_results": max_results,
                "mode": effective_mode,
            },
            "truncated": len(results) == max_results,  # True if we hit the limit
        }

    except ValueError:
        # Re-raise validation errors
        raise
    except Exception as e:
        if ctx:
            await ctx.info(f"Regex search failed: {e!s}")
        # Return standardized error structure
        return {
            "results": [],
            "count": 0,
            "query": {
                "pattern": pattern,
                "flags": flags or [],
                "context_length": context_length,
                "max_results": max_results,
            },
            "truncated": False,
            "error": f"Regex search failed: {e!s}",
        }
