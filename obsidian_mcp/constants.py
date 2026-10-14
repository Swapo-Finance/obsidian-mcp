"""Constants for Obsidian MCP server."""

# Obsidian REST API configuration
DEFAULT_TIMEOUT = 10  # seconds - reduced for local API
DEFAULT_SEARCH_CONTEXT_LENGTH = 100
DEFAULT_LIST_RECURSIVE = True

# API Endpoints
ENDPOINTS = {
    "vault": "/vault/",
    "vault_path": "/vault/{path}",  # No trailing slash for individual files
    "search": "/search/",
    "search_simple": "/search/simple/",
}

# File extensions
MARKDOWN_EXTENSIONS = {".md", ".markdown"}

# read_note paging: the bounds of the max_chars parameter and of
# OBSIDIAN_READ_MAX_CHARS, the default page size.
READ_MAX_CHARS_DEFAULT = 40_000
READ_MAX_CHARS_MIN = 1_000
READ_MAX_CHARS_MAX = 1_000_000

# Added to a paged read_note response when text remains after the page. Same
# role as the "message" of a truncated search result.
READ_TRUNCATED_MESSAGE = (
    "Showing characters {start}-{next_offset} of {total_chars}. This page is only "
    "part of the text. To read the next page, call read_note_tool again with "
    "offset={next_offset} and the same section, if you passed one. To read one "
    "section, pass section with a heading from outline. Read all pages before "
    "you replace the note with update_note."
)

# Error messages - Actionable and specific
ERROR_MESSAGES = {
    "note_not_found": (
        "Note not found at path: '{path}'. "
        "To fix: 1) Check if the path is correct (case-sensitive), 2) Ensure the note exists in your vault, "
        "3) Use search_notes('path:filename') to find notes by name, "
        "4) Use list_notes to see available notes in the directory"
    ),
    "invalid_path": (
        "Invalid note path: '{path}'. "
        "Valid paths must: 1) End with .md or .markdown, 2) Use forward slashes (e.g., 'folder/note.md'), "
        "3) Not contain '..' or start with '/', 4) Not exceed 255 characters. "
        "Example: 'Daily/2024-01-15.md' or 'Projects/My Project.md'"
    ),
    "overwrite_protection": (
        "Note already exists at '{path}'. "
        "To proceed: 1) Set overwrite=true to replace the existing note, "
        "2) Use update_note to modify the existing note, "
        "3) Choose a different path for the new note"
    ),
    "empty_search_query": (
        "Search query cannot be empty. "
        "Valid queries: 1) Keywords: 'machine learning', "
        "2) Tags: 'tag:#project', 3) Paths: 'path:Daily/', "
        "4) Combined: 'tag:#urgent TODO'"
    ),
    "invalid_date_type": (
        "Invalid date_type: '{date_type}'. "
        "Must be either 'created' or 'modified'. "
        "Use 'created' to find notes by creation date, 'modified' for last edit date"
    ),
    "invalid_operator": (
        "Invalid operator: '{operator}'. "
        "Must be either 'within' or 'exactly'. "
        "Use 'within' for date ranges (e.g., last 7 days), 'exactly' for specific days ago"
    ),
    "negative_days": (
        "Invalid days_ago: {days}. "
        "Must be a positive number (0 or greater). "
        "Use 0 for today, 1 for yesterday, 7 for last week, etc."
    ),
    "invalid_tags": (
        "Invalid tags provided. "
        "Tags must be non-empty strings without the # prefix. "
        "Example: ['project', 'urgent', 'review'] not ['#project', '', ' ']"
    ),
    "path_too_long": (
        "Path too long: {length} characters (max: 255). "
        "To fix: Use shorter folder/file names or reduce nesting depth"
    ),
    "invalid_context_length": (
        "Invalid context_length: {length}. "
        "Must be between 10 and 500 characters. "
        "Use smaller values for brief previews, larger for more context"
    ),
    "invalid_sort_by": (
        "Invalid sort_by parameter: '{value}'. "
        "Must be either 'name' (alphabetical) or 'count' (by usage). "
        "Default is 'name' for alphabetical sorting"
    ),
    "tag_collection_failed": (
        "Failed to collect tags from vault: {error}. "
        "This may happen if: 1) The Obsidian API is not responding, "
        "2) There are permission issues with some notes, "
        "3) The vault is very large (try again or use search_notes with specific tags)"
    ),
    "folder_listing_failed": (
        "Failed to list folders in '{directory}': {error}. "
        "To fix: 1) Verify the directory path is correct, "
        "2) Check if the directory exists using list_notes, "
        "3) Ensure you have permission to access this directory"
    ),
    "folder_move_failed": (
        "Failed to move folder '{source}' to '{destination}': {error}. "
        "To fix: 1) Verify both folder paths are correct, "
        "2) Ensure the source folder exists and contains notes, "
        "3) Check that destination is not a subfolder of source, "
        "4) Verify you have permission to create/delete notes"
    ),
    "path_outside_vault": (
        "Path '{path}' resolves outside the vault ({vault_path}). "
        "Accepted forms: 1) vault-relative (e.g. '01-projects'), "
        "2) vault-name-prefixed (e.g. '{vault_name}/01-projects'), "
        "3) absolute or '~'-expanded, pointing inside the vault. "
        "If this came from OBSIDIAN_FOLDER_TEMPLATES/OBSIDIAN_DAILY_DIR in the .mcp.json config, "
        "it's likely anchored at the project root instead of the vault — review that value."
    ),
    "invalid_daily_date": (
        "Invalid date: '{date}'. "
        "Must be ISO format YYYY-MM-DD (e.g. '2025-01-15'), or omit it to use today's date"
    ),
    "vault_unavailable": (
        "Vault folder is not reachable: '{path}'. The search index was left untouched. "
        "To fix: 1) Check that the drive or network share holding the vault is mounted, "
        "2) Check that OBSIDIAN_VAULT_PATH points at the vault folder, "
        "3) Retry once the folder is back"
    ),
    "invalid_read_offset": (
        "Invalid offset: {offset}. The offset must be 0 or more. "
        "To fix: 1) Use offset=0 to read from the start of the note, "
        "2) Use the next_offset value of the previous page"
    ),
    "invalid_read_max_chars": (
        "Invalid max_chars: {max_chars}. The value must be from {minimum} to {maximum}. "
        "To fix: 1) Use a value in that range, "
        "2) Omit max_chars to use OBSIDIAN_READ_MAX_CHARS"
    ),
    "read_offset_out_of_range": (
        "Offset {offset} is at or after the end of the text ({total_chars} characters) "
        "in '{path}'. To fix: 1) Use the next_offset value of the previous page. "
        "A null next_offset means that you already read the last page, "
        "2) Use offset=0 to read from the start, "
        "3) If the note changed after the previous page, read it again from offset=0"
    ),
    "section_not_found": (
        "No section matches '{section}' in '{path}'. "
        "To fix: 1) Give a markdown heading with its level, e.g. '## Tasks'. "
        "Case does not matter, but the number of # marks must match, "
        "2) Read the note without section to see its headings, "
        "or use the outline of a paged read"
    ),
    "note_over_char_limit": (
        "Note '{path}' would have {chars} characters, over "
        "OBSIDIAN_MAX_NOTE_CHARS={limit}. "
        "To fix: 1) Split the content into smaller notes and link them with [[wikilinks]], "
        "2) Or raise OBSIDIAN_MAX_NOTE_CHARS"
    ),
}

# Standardized response structures for reasoning-friendly consistency
RESPONSE_STRUCTURES = {
    # CRUD operations (Create, Read, Update, Delete)
    "crud_success": {
        "success": True,
        "path": str,  # The note/folder path
        "operation": str,  # "created", "read", "updated", "deleted"
        "details": dict,  # Operation-specific details
    },
    # Search operations
    "search_results": {
        "results": list,  # List of search result items
        "count": int,  # Total number of results
        "query": dict,  # The search parameters used
        "truncated": bool,  # Whether results were limited
    },
    # Listing operations
    "list_results": {
        "items": list,  # List of items (notes, folders, tags, etc.)
        "total": int,  # Total count
        "scope": dict,  # Parameters like directory, recursive, etc.
    },
    # Analysis operations (backlinks, broken links, etc.)
    "analysis_results": {
        "findings": list,  # List of findings
        "summary": dict,  # Statistical summary
        "target": str,  # What was analyzed
        "scope": dict,  # Analysis parameters
    },
    # Tag operations
    "tag_operation": {
        "success": True,
        "path": str,  # Note path
        "operation": str,  # "added", "updated", "removed"
        "tags": {
            "before": list,  # Tags before operation
            "after": list,  # Tags after operation
            "changes": dict,  # What changed
        },
    },
    # Move operations
    "move_operation": {
        "success": True,
        "source": str,  # Original path
        "destination": str,  # New path
        "type": str,  # "note" or "folder"
        "details": {
            "items_moved": int,  # Number of items moved
            "links_updated": int,  # Number of links updated
        },
    },
    # Index sync (sync_vault_index_tool)
    "index_sync": {
        "success": True,
        "scanned": int,  # Notes found on disk
        "added": int,  # Notes indexed for the first time
        "updated": int,  # Notes re-indexed (changed, or all with full=True)
        "removed": int,  # Index entries dropped because the note is gone
        "failed": int,  # Notes that could not be read or indexed
        "full": bool,  # Whether every note was re-indexed
        "duration_ms": int,  # Wall time of the pass
    },
    # Paged read (read_note_tool): offset > 0, or the text does not fit in one
    # page. A read that fits from offset 0 keeps crud_success with details
    # {content, metadata}.
    "read_page": {
        "success": True,
        "path": str,  # The note path
        "operation": str,  # "read"
        "details": {
            "content": str,  # This page of the note (or section) text
            "metadata": dict,  # Same as an unpaged read
            "truncated": bool,  # True when text remains after this page
            "next_offset": int,  # Start of the next page; None on the last page
            "total_chars": int,  # Length of the whole note (or section) text
            "outline": list,  # [{heading, level, offset, chars}], 50 entries max
            "message": str,  # Only when truncated: how to read the next page
        },
    },
    # Error response
    "error": {
        "success": False,
        "error": str,  # Error type/code
        "message": str,  # Human-readable message
        "suggestions": list,  # Actionable suggestions
    },
}
