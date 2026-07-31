"""Property-query parsing and the private property-search engine.

_search_by_property and _parse_property_query are both re-exported from
search_discovery.py: tests/test_property_search.py and
tests/test_property_search_simple.py import the private names directly from
that module.
"""

import logging
import operator as op
from collections.abc import Callable
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

# Finding 1 (code review, C901 46): the >,<,>=,<= dispatch used to repeat a
# 4-way if/elif chain at four separate sites (list-length compare, date
# compare, numeric compare, string-fallback compare). Every site just calls
# the operator directly with no type coercion of its own, so one stdlib
# lookup table replaces all four identically.
_COMPARISONS: dict[str, Callable[[Any, Any], bool]] = {
    ">": op.gt,
    "<": op.lt,
    ">=": op.ge,
    "<=": op.le,
}


def _parse_property_query(query: str) -> dict[str, Any]:
    """
    Parse a property query string into components.

    Supports formats:
    - property:name:value (exact match)
    - property:name:>value (comparison)
    - property:name:*value* (contains)
    - property:name:* (exists)

    Returns:
        Dict with 'name', 'operator', and 'value'
    """
    # Remove 'property:' prefix
    prop_query = query[9:]  # len('property:') = 9

    # Split by first colon to separate name from value/operator
    parts = prop_query.split(":", 1)
    if len(parts) != 2:
        raise ValueError(f"Invalid property query format: {query}")

    name = parts[0]
    value_part = parts[1]

    # Check for operators
    if value_part == "*":
        return {"name": name, "operator": "exists", "value": None}
    elif value_part.startswith(">="):
        return {"name": name, "operator": ">=", "value": value_part[2:]}
    elif value_part.startswith("<="):
        return {"name": name, "operator": "<=", "value": value_part[2:]}
    elif value_part.startswith("!="):
        return {"name": name, "operator": "!=", "value": value_part[2:]}
    elif value_part.startswith(">"):
        return {"name": name, "operator": ">", "value": value_part[1:]}
    elif value_part.startswith("<"):
        return {"name": name, "operator": "<", "value": value_part[1:]}
    elif value_part.startswith("*") and value_part.endswith("*"):
        return {"name": name, "operator": "contains", "value": value_part[1:-1]}
    else:
        return {"name": name, "operator": "=", "value": value_part}


def _build_property_query(
    property_name: str, operator: str, value: str | None
) -> tuple[str, str]:
    """Normalize a search_by_property operator and build the internal
    'property:name:...' query string _search_by_property expects.

    Extracted out of search_discovery.search_by_property (Finding 5 / Goal
    on C901): that function must stay physically defined in
    search_discovery.py (get_vault() is patched module-qualified by tests
    there), but this pure operator/query-string logic carries no such
    restriction. Moving it here dropped search_by_property's own C901 from
    12 to well under 10.

    Returns:
        (normalized_operator, query_string)
    """
    if operator == "equals":
        operator = "="

    if operator == "exists":
        query = f"property:{property_name}:*"
    elif operator == "contains":
        query = f"property:{property_name}:*{value}*"
    elif operator in [">", "<", ">=", "<=", "!="]:
        query = f"property:{property_name}:{operator}{value}"
    else:  # = operator
        query = f"property:{property_name}:{value}"

    return operator, query


def _property_matches(prop_value: Any, operator: str, value: str | None) -> bool:
    """Return whether one note's frontmatter property value satisfies a
    parsed query (operator + value).

    Split out of _search_by_property's per-note loop (Finding 1 / Goal on
    C901). The four equality-flavored operators are simple, direct
    value/list comparisons and stay here; >,<,>=,<= need a materially
    heavier type-coercion cascade (list-length, then date, then numeric,
    then string fallback) and are delegated to _compare_ordered_property so
    each function is a single readable seam of the overall rule rather than
    one function trying to hold the whole thing.
    """
    if operator == "exists":
        return True

    if operator == "=":
        if isinstance(prop_value, list):
            return any(str(item).lower() == str(value).lower() for item in prop_value)
        return str(prop_value).lower() == str(value).lower()

    if operator == "!=":
        if isinstance(prop_value, list):
            return not any(
                str(item).lower() == str(value).lower() for item in prop_value
            )
        return str(prop_value).lower() != str(value).lower()

    if operator == "contains":
        if isinstance(prop_value, list):
            return any(str(value).lower() in str(item).lower() for item in prop_value)
        return str(value).lower() in str(prop_value).lower()

    if operator in _COMPARISONS:
        return _compare_ordered_property(prop_value, operator, value)

    return False


def _compare_ordered_property(
    prop_value: Any, operator: str, value: str | None
) -> bool:
    """Handle _property_matches' >,<,>=,<= branch: list-length compare,
    then date compare (trying a few common formats), then numeric, then
    string fallback -- in that order, matching the original fallback chain
    exactly. operator is always a key of _COMPARISONS here.
    """
    if value is None:
        # _parse_property_query only produces value=None for "exists",
        # which never reaches here -- guard kept so the float()/str()
        # conversions below are well-typed for whatever calls this.
        return False

    # For arrays, compare the length.
    if isinstance(prop_value, list):
        try:
            return _COMPARISONS[operator](len(prop_value), float(value))
        except (ValueError, TypeError):
            return False

    # Try date/datetime comparison first.
    try:
        date_prop = None
        date_val = None
        # Try common date formats, ISO first (YYYY-MM-DD or
        # YYYY-MM-DDTHH:MM:SS).
        for fmt in ["%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"]:
            try:
                date_prop = datetime.strptime(str(prop_value), fmt)
                date_val = datetime.strptime(value, fmt)
                break
            except ValueError:
                continue

        if date_prop and date_val:
            return _COMPARISONS[operator](date_prop, date_val)
        raise ValueError("Not a date")
    except ValueError:
        # Fall back to numeric comparison.
        try:
            return _COMPARISONS[operator](float(prop_value), float(value))
        except (ValueError, TypeError):
            # Fall back to string comparison.
            return _COMPARISONS[operator](str(prop_value), value)


def _with_content_preview(
    context: str, content: str | None, context_length: int
) -> str:
    """Append a truncated content preview to a property-match context
    string, if there's any content to preview.

    Split out of _search_by_property (Goal on C901): this exact
    truncate-and-append block was duplicated between the persistent-index
    path and the manual-fallback loop, identical apart from which "content"
    field each reads from -- the one thing that genuinely differs between
    them (how the header itself is formatted for list vs. scalar property
    values) is deliberately left at each call site, not merged here.
    """
    if not content:
        return context
    preview = content[:context_length].strip()
    if len(content) > context_length:
        preview += "..."
    return f"{context}\n\n{preview}"


async def _search_by_property(
    vault, property_query: str, context_length: int
) -> list[dict[str, Any]]:
    """Search for notes by property values."""
    # Parse the property query
    parsed = _parse_property_query(property_query)

    prop_name = parsed["name"]
    operator = parsed["operator"]
    value = parsed["value"]

    # Check if we can use the persistent index for property search
    if hasattr(vault, "persistent_index") and vault.persistent_index:
        try:
            # Use persistent index for efficient property search
            results_from_index = await vault.persistent_index.search_by_property(
                prop_name,
                operator,
                value,
                200,  # Get more results to filter
            )

            results = []
            for file_info in results_from_index:
                filepath = file_info["filepath"]
                content = file_info["content"]
                prop_value = file_info["property_value"]

                # Create context showing the property
                context = _with_content_preview(
                    f"{prop_name}: {prop_value}", content, context_length
                )

                results.append(
                    {
                        "path": filepath,
                        "score": 1.0,
                        "matches": [
                            f"{prop_name} {operator} {value if value else 'exists'}"
                        ],
                        "context": context,
                        "property_value": prop_value,
                    }
                )

            return results
        except Exception as e:
            # Fall back to manual search if index fails
            logger.warning(
                f"Property search via index failed: {e}, falling back to manual search"
            )

    # Fall back to manual search (original implementation)
    results = []
    all_notes = await vault.list_notes(recursive=True)

    for note_info in all_notes:
        try:
            # Read note to get metadata
            note = await vault.read_note(note_info["path"])

            # Get the property value from frontmatter
            frontmatter = note.metadata.frontmatter
            if prop_name not in frontmatter:
                continue  # Property not present on this note

            prop_value = frontmatter[prop_name]
            if not _property_matches(prop_value, operator, value):
                continue

            # Create context showing the property
            if isinstance(prop_value, list):
                # Format list values nicely
                header = f"{prop_name}: [{', '.join(str(v) for v in prop_value)}]"
            else:
                header = f"{prop_name}: {prop_value}"
            context = _with_content_preview(header, note.content, context_length)

            results.append(
                {
                    "path": note.path,
                    "score": 1.0,
                    "matches": [
                        f"{prop_name} {operator} {value if value else 'exists'}"
                    ],
                    "context": context,
                    "property_value": prop_value,
                }
            )
        except Exception:
            # Skip notes we can't read
            continue

    return results
