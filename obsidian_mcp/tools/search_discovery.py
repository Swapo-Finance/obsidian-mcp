"""Search and discovery tools for Obsidian MCP server.

This is the facade for the whole search/discovery tool surface. list_notes,
list_folders, search_notes, and the public search_by_property are defined
directly below; search_by_date, search_by_regex, and the property-search /
parsing / result-mode internals live in sibling modules (search_metadata.py,
search_regex.py, search_property_engine.py, search_result_modes.py,
search_text.py) and are re-exported here so `obsidian_mcp.tools.search_discovery`
stays the stable import path callers and tests already depend on.

search_notes and the public search_by_property stay defined here rather than
moving out like their sibling functions did, because tests/test_property_search.py
(6 places) and tests/test_enhanced_search.py (1 place) monkeypatch
`obsidian_mcp.tools.search_discovery.get_vault` directly and then call these
two functions expecting their internal `get_vault()` lookup to resolve
against *this* module's namespace. A function's `__globals__` dict is fixed
to its defining module at `def` time — re-exporting the name here afterward
would not help, only keeping the definition here does.
"""

from pathlib import Path

from ..utils.filesystem import get_vault
from ..utils.validation import (
    validate_context_length,
    validate_directory_path,
    validate_search_query,
)
from ..utils.vault_config import normalize_vault_relative_path
from .search_metadata import search_by_date
from .search_property_engine import (
    _build_property_query,
    _parse_property_query,
    _search_by_property,
)
from .search_regex import search_by_regex
from .search_result_modes import (
    _enrich_with_note_meta,
    _resolve_search_mode,
    _to_index_items,
)
from .search_text import _search_by_path, _search_by_tag

__all__ = [
    "_enrich_with_note_meta",
    "_parse_property_query",
    "_search_by_property",
    "list_folders",
    "list_notes",
    "search_by_date",
    "search_by_property",
    "search_by_regex",
    "search_notes",
]


def _merge_filename_and_content_results(
    path_results: list[dict], content_results: list[dict], max_results: int
) -> list[dict]:
    """Merge filename/path matches (boosted, higher priority) with content
    matches, deduplicating by path and capping at max_results.

    Split out of search_notes's default-search branch (Goal on C901): pure
    list merging, no get_vault() call, so none of the module-attribute
    patch-target restriction on search_notes itself applies to this helper.
    """
    seen_paths = set()
    merged_results = []

    # Add path matches first (higher priority)
    for result in path_results:
        if result["path"] not in seen_paths:
            # Boost score for filename matches
            result["score"] = result.get("score", 1.0) * 2.0
            result["match_type"] = "filename"
            merged_results.append(result)
            seen_paths.add(result["path"])

    # Add content matches that aren't already in results
    for result in content_results:
        if result["path"] not in seen_paths:
            result["match_type"] = "content"
            merged_results.append(result)
            seen_paths.add(result["path"])

    # Sort by score (descending) and limit results
    merged_results.sort(key=lambda x: x.get("score", 0), reverse=True)
    return merged_results[:max_results]


# search_notes is also the target of ~7 patch() calls in
# tests/test_auto_search.py, which work only because
# obsidian_mcp/tools/organization.py reaches search_notes via a
# function-local deferred import off this module, read at call time
# (`from ..tools.search_discovery import search_notes` inside the function
# body, not at module load time). Do not "clean up" that deferred import to
# point at search_text.py — it would silently break every one of those
# patches. organization.py also deferred-imports list_notes from here, which
# is why list_notes stays physically in this module too.
async def search_notes(
    query: str,
    context_length: int = 20,
    max_results: int = 50,
    mode: str | None = None,
    ctx=None,
) -> dict:
    """
    Search for notes containing specific text or matching search criteria.

    Use this tool to find notes by content, title, metadata, or properties. Supports
    multiple search modes with special prefixes:

    Search Syntax:
    - Content search (default): Just type your query to search within note content
      Example: "machine learning" finds notes containing this text
    - Path/Title search: Use "path:" prefix to search by filename or folder
      Example: "path:Daily/" finds all notes in Daily folder
      Example: "path:Note with Images" finds notes with this in their filename
    - Tag search: Use "tag:" prefix to search by tags
      Example: "tag:project" or "tag:#project" finds notes with the project tag
    - Property search: Use "property:" prefix to search by frontmatter properties
      Example: "property:status:active" finds notes where status = active
      Example: "property:priority:>2" finds notes where priority > 2
      Example: "property:assignee:*john*" finds notes where assignee contains "john"
      Example: "property:deadline:*" finds notes that have a deadline property
    - Combined searches are supported but limited to one mode at a time

    Property Operators:
    - ":" or "=" for exact match (property:name:value)
    - ">" for greater than (property:priority:>3)
    - "<" for less than (property:age:<30)
    - ">=" for greater or equal (property:score:>=80)
    - "<=" for less or equal (property:rating:<=5)
    - "!=" for not equal (property:status:!=completed)
    - "*value*" for contains (property:title:*project*)
    - "*" for exists (property:tags:*)

    Args:
        query: Search query with optional prefix (path:, tag:, property:, or plain text)
        context_length: Number of characters to show around matches (default: 20)
        ctx: MCP context for progress reporting

    Returns:
        Dictionary containing search results with matched notes and context

    Examples:
        >>> # Search by content
        >>> await search_notes("machine learning algorithms", ctx=ctx)

        >>> # Search by filename/path
        >>> await search_notes("path:Project Notes", ctx=ctx)

        >>> # Search by tag
        >>> await search_notes("tag:important", ctx=ctx)

        >>> # Search by property
        >>> await search_notes("property:status:active", ctx=ctx)
        >>> await search_notes("property:priority:>2", ctx=ctx)
    """
    # Validate parameters
    is_valid, error = validate_search_query(query)
    if not is_valid:
        raise ValueError(error)

    is_valid, error = validate_context_length(context_length)
    if not is_valid:
        raise ValueError(error)

    if ctx:
        await ctx.info(f"Searching notes with query: {query}")

    vault = get_vault()

    # Reassigned as the very first statement in every branch below, before
    # anything that could raise. This default is only for pyright -- it
    # can't prove the try body raises no earlier than that -- and only
    # matters to the except block's error dict, never to a real search.
    query_type = "content"

    try:
        # Handle special search syntax. query_type is assigned first in
        # every branch (before any call that could raise) so the except
        # block below can reuse it directly instead of re-deriving it from
        # the query prefix a second time.
        if query.startswith("tag:"):
            # Tag search
            query_type = "tag"
            tag = query[4:].lstrip("#")
            results = await _search_by_tag(vault, tag, context_length)
        elif query.startswith("path:"):
            # Path search
            query_type = "path"
            path_pattern = query[5:]
            results = await _search_by_path(vault, path_pattern, context_length)
        elif query.startswith("property:"):
            # Property search
            query_type = "property"
            results = await _search_by_property(vault, query, context_length)
        else:
            query_type = "content"
            # Enhanced default search: search both content AND filenames
            # First, get content search results
            content_results = await vault.search_notes(
                query, context_length, max_results * 2
            )  # Get more to allow merging

            # Also search by path/filename
            path_results = await _search_by_path(vault, query, context_length)

            results = _merge_filename_and_content_results(
                path_results, content_results, max_results
            )

        # Get search metadata if available (for content searches). query_type
        # already tells us this -- it's "content" exactly when none of the
        # three prefixes matched -- so reuse it instead of re-checking the
        # query string a second time.
        metadata = vault.get_last_search_metadata() if query_type == "content" else None

        # total_count is the true number of matches found by the vault. The tool
        # over-fetches (max_results * 2) for merging and then caps the displayed
        # results to max_results, so the vault's own "truncated" flag reflects the
        # doubled limit, not what we actually return. Recompute truncated from the
        # displayed count vs. the true total so it is accurate.
        total_count = (
            metadata.get("total_count", len(results)) if metadata else len(results)
        )

        # Search result mode (spec section 10.4): trim each result down to
        # {path, name, description, score, match_type} once resolved to
        # "index" — computed on the count about to be returned, same as
        # every other search tool below.
        effective_mode = _resolve_search_mode(vault, mode, len(results))
        if effective_mode == "index":
            results = await _to_index_items(
                vault, results, default_match_type=query_type
            )

        # Return standardized search results structure
        response = {
            "results": results,
            "count": len(results),
            "query": {
                "text": query,
                "context_length": context_length,
                "type": query_type,
                "mode": effective_mode,
            },
            "truncated": total_count > len(results),
            "total_count": total_count,
        }

        # Add message if results are truncated. total_count > count is
        # exactly what "truncated" above already means (total_count >
        # len(results) either way) -- no need to check it twice.
        if response["truncated"]:
            response["message"] = (
                f"Showing {response['count']} of {response['total_count']} results. Use max_results parameter to see more."
            )

        return response
    except Exception as e:
        if ctx:
            await ctx.info(f"Search failed: {e!s}")
        # Return standardized error structure
        return {
            "results": [],
            "count": 0,
            "query": {
                "text": query,
                "context_length": context_length,
                "type": query_type,
            },
            "truncated": False,
            "error": f"Search failed: {e!s}",
        }


# search_by_property (public) also stays physically defined here — see the
# module docstring above. tests/test_property_search.py's
# TestSearchByPropertyTool (5 tests) patches search_discovery.get_vault and
# calls this function directly.
async def search_by_property(
    property_name: str,
    value: str | None = None,
    operator: str = "=",
    context_length: int = 20,
    mode: str | None = None,
    ctx=None,
) -> dict:
    """
    Search for notes by their frontmatter property values.

    This tool allows advanced filtering of notes based on YAML frontmatter properties,
    supporting various comparison operators and data types.

    Args:
        property_name: Name of the property to search for
        value: Value to compare against (optional for 'exists' operator)
        operator: Comparison operator (=, !=, >, <, >=, <=, contains, exists)
        context_length: Characters of note content to include in results
        ctx: MCP context for progress reporting

    Operators:
    - "=" or "equals": Exact match (case-insensitive)
    - "!=": Not equal
    - ">": Greater than (numeric/date comparison)
    - "<": Less than (numeric/date comparison)
    - ">=": Greater or equal
    - "<=": Less or equal
    - "contains": Property value contains the search value
    - "exists": Property exists (value parameter ignored)

    Returns:
        Dictionary with search results including property values

    Examples:
        >>> # Find all notes with status = "active"
        >>> await search_by_property("status", "active", "=")

        >>> # Find notes with priority > 2
        >>> await search_by_property("priority", "2", ">")

        >>> # Find notes that have a deadline property
        >>> await search_by_property("deadline", operator="exists")

        >>> # Find notes where title contains "project"
        >>> await search_by_property("title", "project", "contains")
    """
    if ctx:
        await ctx.info(f"Searching by property: {property_name} {operator} {value}")

    # Validate operator
    valid_operators = ["=", "equals", "!=", ">", "<", ">=", "<=", "contains", "exists"]
    if operator not in valid_operators:
        raise ValueError(
            f"Invalid operator: {operator}. Must be one of: {', '.join(valid_operators)}"
        )

    # Normalize the operator and build the query string for _search_by_property.
    operator, query = _build_property_query(property_name, operator, value)

    vault = get_vault()

    try:
        results = await _search_by_property(vault, query, context_length)

        # Sort results by property value if numeric
        if results and operator in [">", "<", ">=", "<="]:
            try:
                # Try to sort numerically
                results.sort(
                    key=lambda x: float(x.get("property_value", 0)),
                    reverse=(operator in [">", ">="]),
                )
            except (ValueError, TypeError):
                # Fall back to string sort
                results.sort(key=lambda x: str(x.get("property_value", "")))

        # Search result mode (spec section 10.4). property_value is kept
        # even in index mode — it's the whole point of a property search,
        # structured data rather than the prose snippet index mode strips.
        effective_mode = _resolve_search_mode(vault, mode, len(results))
        if effective_mode == "index":
            results = await _to_index_items(
                vault,
                results,
                default_match_type="property",
                extra_keys=("property_value",),
            )

        # Return standardized search results structure
        return {
            "results": results,
            "count": len(results),
            "query": {
                "property": property_name,
                "operator": operator,
                "value": value,
                "context_length": context_length,
                "mode": effective_mode,
            },
            "truncated": False,
        }
    except Exception as e:
        if ctx:
            await ctx.info(f"Property search failed: {e!s}")
        # Return standardized error structure
        return {
            "results": [],
            "count": 0,
            "query": {
                "property": property_name,
                "operator": operator,
                "value": value,
                "context_length": context_length,
            },
            "truncated": False,
            "error": f"Property search failed: {e!s}",
        }


async def list_notes(
    directory: str | None = None, recursive: bool = True, ctx=None
) -> dict:
    """
    List notes in the vault or a specific directory.

    Use this tool to browse the vault structure and discover notes. You can list
    all notes or focus on a specific directory. This is helpful when you know
    the general location but not the exact filename.

    Args:
        directory: Specific directory to list (optional, defaults to root)
        recursive: Whether to list all subdirectories recursively (default: true)
        ctx: MCP context for progress reporting

    Returns:
        Dictionary containing vault structure and note paths

    Example:
        >>> await list_notes("Projects", recursive=True, ctx=ctx)
        {
            "directory": "Projects",
            "recursive": true,
            "count": 12,
            "notes": [
                {"path": "Projects/Web App.md", "name": "Web App.md"},
                {"path": "Projects/Ideas/AI Assistant.md", "name": "AI Assistant.md"}
            ]
        }
    """
    # Validate directory parameter
    is_valid, error = validate_directory_path(directory)
    if not is_valid:
        raise ValueError(error)

    if ctx:
        if directory:
            await ctx.info(f"Listing notes in: {directory}")
        else:
            await ctx.info("Listing all notes in vault")

    vault = get_vault()

    try:
        notes = await vault.list_notes(directory, recursive)

        # Light enrichment (spec section 10.4's closing sentence): add each
        # note's cached description. Not `name` too — list_notes' own
        # "name" already means the filename (e.g. "Web App.md"); overloading
        # it with the frontmatter-derived name would be a breaking, confusing
        # collision, so only description (a genuinely new field) is added.
        if notes:
            all_meta = await vault.cache.get_all_note_meta()
            for note in notes:
                note["description"] = all_meta.get(note["path"], {}).get(
                    "description", ""
                )

        # Return standardized list results structure
        return {
            "items": notes,
            "total": len(notes),
            "scope": {"directory": directory or "vault root", "recursive": recursive},
        }
    except Exception as e:
        if ctx:
            await ctx.info(f"Failed to list notes: {e!s}")
        # Return standardized error structure
        return {
            "items": [],
            "total": 0,
            "scope": {"directory": directory or "vault root", "recursive": recursive},
            "error": f"Failed to list notes: {e!s}",
        }


def _resolve_list_folders_search_path(directory: str | None, vault) -> Path | None:
    """Resolve list_folders' directory argument to the Path to search.

    Returns None if the directory doesn't exist (caller returns an empty
    result for that, unchanged from before this was split out). Raises
    ValueError if it escapes the vault.

    Split out of list_folders (Goal on C901): pure path resolution, no
    get_vault() call and not itself a patch target, so it carries none of
    the restrictions that keep other functions in this file from moving.
    Route through the public normalize_vault_relative_path helper
    (utils/vault_config.py) rather than the private vault._ensure_safe_path
    -- that method is an ObsidianVault implementation detail; this tool only
    needs the same escape-checking guarantee, which the public helper
    already gives (validate_directory_path already rejected ".."/leading-"/"
    /length; this adds the resolve()+relative_to() symlink-escape check).
    """
    if not directory:
        return vault.vault_path
    normalized = normalize_vault_relative_path(directory, vault.vault_path)
    if normalized is None:
        raise ValueError(f"Path escapes vault: {directory}")
    search_path = vault.vault_path / normalized if normalized else vault.vault_path
    if not search_path.exists() or not search_path.is_dir():
        return None
    return search_path


async def list_folders(
    directory: str | None = None, recursive: bool = True, ctx=None
) -> dict:
    """
    List folders in the vault or a specific directory.

    Use this tool to explore the vault's folder structure. This is helpful for
    verifying folder names before creating notes, understanding the organizational
    hierarchy, or checking if a specific folder exists.

    Args:
        directory: Specific directory to list folders from (optional, defaults to root)
        recursive: Whether to include all nested subfolders (default: true)
        ctx: MCP context for progress reporting

    Returns:
        Dictionary containing folder structure with paths and folder counts

    Example:
        >>> await list_folders("Projects", recursive=True, ctx=ctx)
        {
            "directory": "Projects",
            "recursive": true,
            "count": 5,
            "folders": [
                {"path": "Projects/Active", "name": "Active"},
                {"path": "Projects/Archive", "name": "Archive"},
                {"path": "Projects/Ideas", "name": "Ideas"}
            ]
        }
    """
    # Validate directory parameter
    is_valid, error = validate_directory_path(directory)
    if not is_valid:
        raise ValueError(error)

    if ctx:
        if directory:
            await ctx.info(f"Listing folders in: {directory}")
        else:
            await ctx.info("Listing all folders in vault")

    vault = get_vault()

    try:
        # Determine search path.
        search_path = _resolve_list_folders_search_path(directory, vault)
        if search_path is None:
            return {
                "directory": directory,
                "recursive": recursive,
                "count": 0,
                "folders": [],
            }

        # Find all directories. rglob (recursive) and iterdir
        # (immediate-children-only) are the only difference between the
        # recursive and non-recursive cases; the hidden-directory check is
        # identical either way -- iterdir's rel_path always has exactly one
        # part, so checking every part of it is equivalent to checking just
        # the name, and also correctly checks every ancestor segment for the
        # rglob case.
        folders = []
        entries = search_path.rglob("*") if recursive else search_path.iterdir()
        for path in entries:
            if not path.is_dir():
                continue
            rel_path = path.relative_to(vault.vault_path)
            if any(part.startswith(".") for part in rel_path.parts):
                continue  # Skip hidden directories
            folders.append({"path": str(rel_path), "name": path.name})

        # Sort by path
        folders.sort(key=lambda x: x["path"])

        # Return standardized list results structure
        return {
            "items": folders,
            "total": len(folders),
            "scope": {"directory": directory or "vault root", "recursive": recursive},
        }
    except Exception as e:
        if ctx:
            await ctx.info(f"Failed to list folders: {e!s}")
        # Return standardized error structure
        return {
            "items": [],
            "total": 0,
            "scope": {"directory": directory or "vault root", "recursive": recursive},
            "error": f"Failed to list folders: {e!s}",
        }
