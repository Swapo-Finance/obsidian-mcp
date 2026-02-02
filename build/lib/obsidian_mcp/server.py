"""Main entry point for Obsidian MCP server."""

import os
import logging
from typing import Annotated, Optional, List, Literal, Union
from pydantic import Field
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from .utils.filesystem import init_vault, get_vault

# Configure logging
logging.basicConfig(
    level=os.getenv("OBSIDIAN_LOG_LEVEL", "INFO"),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# Import all tools
from .tools import (
    read_note,
    create_note,
    update_note,
    edit_note_section,
    delete_note,
    search_notes,
    search_by_date,
    search_by_regex,
    search_by_property,
    list_notes,
    list_folders,
    move_note,
    rename_note,
    create_folder,
    move_folder,
    add_tags,
    update_tags,
    remove_tags,
    get_note_info,
    list_tags,
    batch_update_properties,
    get_backlinks,
    get_outgoing_links,
    find_broken_links,
    find_orphaned_notes,
    read_image,
    view_note_images,
)

# Check for vault path
if not os.getenv("OBSIDIAN_VAULT_PATH"):
    raise ValueError("OBSIDIAN_VAULT_PATH environment variable must be set")

# Initialize vault
init_vault()

# Create FastMCP server instance
mcp = FastMCP(
    "obsidian-mcp",
    instructions="MCP server for direct filesystem access to Obsidian vaults"
)

# Register tools with proper error handling
@mcp.tool()
async def read_note_tool(
    path: Annotated[str, Field(
        description="Note location within your vault (e.g., 'Projects/AI Research.md'). Use forward slashes for folders.",
        pattern=r"^[^/].*\.md$",
        min_length=1,
        max_length=255,
        examples=["Daily/2024-01-15.md", "Projects/AI Research.md", "Ideas/Quick Note.md"]
    )],
    ctx=None
):
    """
    Read the content and metadata of a specific note.
    
    When to use:
    - Displaying note contents to the user
    - Analyzing or processing existing note data
    - ALWAYS before updating a note to preserve existing content
    - Verifying a note exists before making changes
    
    When NOT to use:
    - Searching multiple notes (use search_notes instead)
    - Getting only metadata (use get_note_info for efficiency)
    - Viewing images in a note (use view_note_images instead)
    
    Returns:
        Note content and metadata including tags, aliases, and frontmatter.
        Image references (![alt](path)) are preserved in the content but images are not loaded.
        
    IMPORTANT: If the note contains image references, proactively offer to analyze them:
    "I can see this note contains [N] images. Would you like me to analyze/examine them for you?"
    Then use view_note_images to load and analyze the images if requested.
    """
    try:
        return await read_note(path, ctx)
    except (ValueError, FileNotFoundError) as e:
        raise ToolError(str(e))
    except Exception as e:
        raise ToolError(f"Failed to read note: {str(e)}")

@mcp.tool()
async def create_note_tool(
    path: Annotated[str, Field(
        description="Where to create the new note in your vault. Folders will be created automatically if needed.",
        pattern=r"^[^/].*\.md$",
        min_length=1,
        max_length=255,
        examples=["Ideas/New Idea.md", "Daily/2024-01-15.md", "Projects/Project Plan.md"]
    )],
    content: Annotated[str, Field(
        description="The markdown content for your note. Can include headings (#), tags (#tag), links ([[other note]]), and frontmatter.",
        min_length=0,
        max_length=1000000,
        examples=[
            "# Meeting Notes\n#meeting #project-alpha\n\nDiscussed timeline and deliverables...",
            "---\ntags: [daily, planning]\n---\n\n# Daily Note\n\nToday's tasks..."
        ]
    )],
    overwrite: Annotated[bool, Field(
        description="Set to true to replace an existing note at this location. Use carefully as this deletes the original content.",
        default=False
    )] = False,
    ctx=None
):
    """
    Create a new note or overwrite an existing one.
    
    When to use:
    - Creating new notes with specific content
    - Setting up templates or structured notes
    - Programmatically generating documentation
    
    When NOT to use:
    - Updating existing notes (use update_note unless you want to replace entirely)
    - Appending content (use update_note with merge_strategy="append")
        
    Returns:
        Created note information with path and metadata
    """
    try:
        return await create_note(path, content, overwrite, ctx)
    except (ValueError, FileExistsError) as e:
        raise ToolError(str(e))
    except Exception as e:
        raise ToolError(f"Failed to create note: {str(e)}")

@mcp.tool()
async def update_note_tool(
    path: Annotated[str, Field(
        description="Which note to update in your vault",
        pattern=r"^[^/].*\.md$",
        min_length=1,
        max_length=255,
        examples=["Daily/2024-01-15.md", "Projects/Project.md"]
    )],
    content: Annotated[str, Field(
        description="New content for the note. By default this REPLACES all existing content. Use merge_strategy='append' to add to the end instead.",
        min_length=0,
        max_length=1000000
    )],
    create_if_not_exists: Annotated[bool, Field(
        description="Automatically create the note if it doesn't exist yet",
        default=False
    )] = False,
    merge_strategy: Annotated[Literal["replace", "append"], Field(
        description="How to handle existing content. 'replace' = overwrite everything (default), 'append' = add new content to the end",
        default="replace"
    )] = "replace",
    ctx=None
):
    """
    Update the content of an existing note.
    
    ⚠️ IMPORTANT: By default, this REPLACES the entire note content.
    Always read the note first if you need to preserve existing content.
    
    When to use:
    - Updating a note with completely new content (replace)
    - Adding content to the end of a note (append)
    - Programmatically modifying notes
    
    When NOT to use:
    - Making small edits (read first, then update with full content)
    - Creating new notes (use create_note instead)
    
    Returns:
        Update status with path, metadata, and operation performed
    """
    try:
        return await update_note(path, content, create_if_not_exists, merge_strategy, ctx)
    except (ValueError, FileNotFoundError) as e:
        raise ToolError(str(e))
    except Exception as e:
        raise ToolError(f"Failed to update note: {str(e)}")

@mcp.tool()
async def edit_note_section_tool(
    path: Annotated[str, Field(
        description="Path to the note to edit",
        pattern=r"^[^/].*\.md$",
        min_length=1,
        max_length=255,
        examples=["Daily/2024-01-15.md", "Projects/Project.md"]
    )],
    section_identifier: Annotated[str, Field(
        description="Markdown heading that identifies the section (e.g., '## Tasks', '### Status')",
        pattern=r"^#{1,6}\s+.+$",
        min_length=3,
        max_length=200,
        examples=["## Tasks", "### Status Updates", "# Overview", "#### Implementation Details"]
    )],
    content: Annotated[str, Field(
        description="Content to insert, replace, or append to the section",
        min_length=1,
        max_length=100000
    )],
    operation: Annotated[Literal["insert_after", "insert_before", "replace", "append_to_section"], Field(
        description="How to edit the section. 'insert_after' = add content after heading, 'insert_before' = add before heading, 'replace' = replace entire section, 'append_to_section' = add to end of section",
        default="insert_after"
    )] = "insert_after",
    create_if_missing: Annotated[bool, Field(
        description="Create the section at the end of the note if it doesn't exist",
        default=False
    )] = False,
    ctx=None
):
    """
    Edit a specific section of a note identified by a markdown heading.
    
    When to use:
    - Adding content to a specific section without rewriting the whole note
    - Updating a particular section (like status updates, task lists)
    - Inserting content at precise locations in structured notes
    - Building up notes incrementally by section
    
    When NOT to use:
    - Simple append to end of note (use update_note with merge_strategy='append')
    - Replacing entire note content (use update_note)
    - Creating a new note (use create_note)
    
    Section identification:
    - Sections are identified by markdown headings (# ## ### etc.)
    - Match is case-insensitive
    - First matching heading is used if duplicates exist
    - Section includes content until next heading of same/higher level
    
    Operations:
    - insert_after: Add content immediately after the section heading
    - insert_before: Add content immediately before the section heading
    - replace: Replace entire section including the heading
    - append_to_section: Add content at the end of the section
    
    Returns:
        Edit status including whether section was found or created
    """
    try:
        return await edit_note_section(path, section_identifier, content, operation, create_if_missing, ctx)
    except (ValueError, FileNotFoundError) as e:
        raise ToolError(str(e))
    except Exception as e:
        raise ToolError(f"Failed to edit section: {str(e)}")

@mcp.tool()
async def delete_note_tool(
    path: Annotated[str, Field(
        description="Path to the note to delete from your vault",
        pattern=r"^[^/].*\.md$",
        min_length=1,
        max_length=255,
        examples=["Archive/Old Note.md", "Temp/Draft.md"]
    )],
    ctx=None
):
    """
    Delete a note from the vault permanently.
    
    When to use:
    - Removing outdated or duplicate notes
    - Cleaning up temporary drafts
    - Part of a move operation (delete after successful copy)
    
    When NOT to use:
    - Archiving (use move_note to Archive folder instead)
    - Temporary removal (no undo available)
    
    ⚠️ WARNING: This operation cannot be undone. The note will be permanently deleted.
    
    Returns:
        Deletion confirmation with the path of the deleted note
    """
    try:
        return await delete_note(path, ctx)
    except (ValueError, FileNotFoundError) as e:
        raise ToolError(str(e))
    except Exception as e:
        raise ToolError(f"Failed to delete note: {str(e)}")

@mcp.tool()
async def search_notes_tool(
    query: Annotated[str, Field(
        description="Search query that matches BOTH filenames and content by default. Just type a note name to find it! Use prefixes for specific search types: 'tag:' for tags, 'path:' for ONLY filenames, 'property:' for metadata.",
        min_length=1,
        max_length=500,
        examples=[
            "Meeting Notes",
            "Obsidian Tag Refactor",
            "machine learning",
            "tag:project",
            "path:Daily/",
            "property:status:active"
        ]
    )],
    context_length: Annotated[int, Field(
        description="How much text to show around each match for context. Higher values show more surrounding content.",
        ge=10,
        le=500,
        default=100
    )] = 100,
    max_results: Annotated[int, Field(
        description="Maximum number of results to return. Use smaller values for faster responses and larger values for comprehensive searches.",
        ge=1,
        le=500,
        default=50
    )] = 50,
    ctx=None
):
    """
    Search for notes by filename or content, with smart ranking.
    
    DEFAULT BEHAVIOR (NEW): Searches BOTH note filenames AND content automatically.
    Filename matches are ranked higher than content matches for better discovery.
    
    When to use:
    - Finding a note when you know part of its name (just type the name)
    - Finding notes containing specific content
    - Locating notes with specific tags
    - Searching within specific folders
    - Finding notes by frontmatter properties
    
    Search modes:
    - Default: searches BOTH filenames and content (filename matches ranked higher)
      Example: "tag refactor" finds "Obsidian Tag Refactor.md" AND notes mentioning "tag refactor"
    - "path:text" - searches ONLY in filenames/paths
    - "tag:tagname" - searches by tag (supports hierarchical tags)
    - "property:name:value" - searches by frontmatter properties
    
    Examples:
    - Find a note by name: "Project Tracker" (will find "Project Tracker.md" first)
    - Search content only: Use explicit path: prefix to exclude: "path:Project"
    - Find by tag: "tag:important" or "tag:project/web"
    - Find by property: "property:status:active"
    
    Tag search supports hierarchical tags:
    - "tag:project" finds all project-related tags including project/web, project/mobile
    - "tag:web" finds any tag ending with "web" like project/web, design/web
    
    When NOT to use:
    - Searching by date (use search_by_date instead)
    - Listing all notes (use list_notes for better performance)
    - Reading a specific note when you know the exact path (use read_note directly)
    
    Returns:
        Search results with matched notes, relevance scores, and context.
        Filename matches have higher scores than content matches.
        Response includes match_type field: "filename" or "content".
    """
    try:
        return await search_notes(query, context_length, max_results, ctx)
    except ValueError as e:
        raise ToolError(str(e))
    except Exception as e:
        raise ToolError(f"Search failed: {str(e)}")

@mcp.tool()
async def search_by_date_tool(
    date_type: Annotated[Literal["created", "modified"], Field(
        description="Which date to search by: when the note was first created or last modified",
        default="modified"
    )] = "modified",
    days_ago: Annotated[int, Field(
        description="How many days back to search from today. 0 = today, 1 = yesterday, 7 = last week",
        ge=0,
        le=365,
        default=7,
        examples=[0, 1, 7, 30]
    )] = 7,
    operator: Annotated[Literal["within", "exactly"], Field(
        description="'within' = all notes in the last N days, 'exactly' = only notes from exactly N days ago",
        default="within"
    )] = "within",
    ctx=None
):
    """
    Search for notes by creation or modification date.
    
    When to use:
    - Finding recently modified notes
    - Locating notes created in a specific time period
    - Reviewing activity from specific dates
    
    When NOT to use:
    - Content-based search (use search_notes)
    - Finding notes by tags or path (use search_notes)
    
    Returns:
        Notes matching the date criteria with paths and timestamps
    """
    try:
        return await search_by_date(date_type, days_ago, operator, ctx)
    except ValueError as e:
        raise ToolError(str(e))
    except Exception as e:
        raise ToolError(f"Date search failed: {str(e)}")

@mcp.tool()
async def search_by_regex_tool(
    pattern: Annotated[str, Field(
        description="Regular expression pattern for advanced searches. Use for finding URLs, code patterns, TODO items, etc.",
        min_length=1,
        examples=[r"TODO\s*:.*", r"https?://[^\s]+", r"def\s+\w+\("]
    )],
    flags: Annotated[Optional[List[Literal["ignorecase", "multiline", "dotall"]]], Field(
        description="Options for regex matching: 'ignorecase' = case-insensitive, 'multiline' = ^ and $ match line boundaries, 'dotall' = . matches newlines",
        default=None
    )] = None,
    context_length: Annotated[int, Field(
        description="Characters to show around matches",
        default=100,
        ge=10,
        le=500
    )] = 100,
    max_results: Annotated[int, Field(
        description="Maximum number of notes to return. Use smaller values for faster responses.",
        default=50,
        ge=1,
        le=200
    )] = 50,
    ctx=None
):
    """
    Search for notes using regular expressions for advanced pattern matching.
    
    When to use:
    - Finding complex patterns (URLs, code syntax, structured data)
    - Searching with wildcards and special characters
    - Case-sensitive or multi-line pattern matching
    - Finding TODO/FIXME comments with context
    
    When NOT to use:
    - Simple text search (use search_notes instead)
    - Searching by tags or properties (use dedicated tools)
    
    Common patterns:
    - URLs: r"https?://[^\\s]+"
    - Email: r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\\.[a-zA-Z]{2,}"
    - TODO comments: r"(TODO|FIXME)\\s*:.*"
    - Markdown headers: r"^#{1,6}\\s+.*"
    - Code blocks: r"```\\w*\\n[\\s\\S]*?```"
    
    Returns:
        Notes containing regex matches with match details and context
    """
    try:
        return await search_by_regex(pattern, flags, context_length, max_results, ctx)
    except ValueError as e:
        raise ToolError(str(e))
    except Exception as e:
        raise ToolError(f"Regex search failed: {str(e)}")

@mcp.tool()
async def search_by_property_tool(
    property_name: Annotated[str, Field(
        description="The frontmatter property to search for (e.g., 'status', 'priority'). These are metadata fields at the top of notes.",
        min_length=1,
        examples=["status", "priority", "author", "tags", "deadline"]
    )],
    value: Annotated[Optional[str], Field(
        description="The value to match against. Not needed when using 'exists' to just check if property is present.",
        default=None,
        examples=["active", "high", "2024-01-15", "John Doe"]
    )] = None,
    operator: Annotated[Literal["=", "!=", ">", "<", ">=", "<=", "contains", "exists"], Field(
        description="How to compare: '=' exact match, '!=' not equal, '>/</>=/<=' for numbers/dates, 'contains' partial match, 'exists' just checks presence",
        default="="
    )] = "=",
    context_length: Annotated[int, Field(
        description="Characters of note content to include",
        default=100,
        ge=0,
        le=500
    )] = 100,
    ctx=None
):
    """
    Search for notes by their frontmatter property values.
    
    When to use:
    - Finding notes with specific metadata (status, priority, etc.)
    - Filtering by numeric properties (rating > 4, priority <= 2)
    - Filtering by date properties (deadline < "2024-12-31")
    - Searching within array/list properties (tags, aliases, categories)
    - Checking which notes have certain properties defined
    - Building database-like queries on your notes
    
    Property types supported:
    - Text/String: Exact match or contains
    - Numbers: Comparison operators work numerically
    - Dates: ISO format (YYYY-MM-DD) with intelligent comparison
    - Arrays/Lists: Searches within list items, comparisons use list length
    - Legacy properties: Automatically handles tag→tags, alias→aliases migrations
    
    When NOT to use:
    - Content search (use search_notes instead)
    - Tag search (use search_notes with tag: prefix)
    - Path/filename search (use search_notes with path: prefix)
    
    Examples:
    - Find active projects: property_name="status", value="active"
    - Find high priority: property_name="priority", operator=">", value="2"
    - Find notes with deadlines: property_name="deadline", operator="exists"
    - Find notes by author: property_name="author", operator="contains", value="john"
    - Find notes with tag in list: property_name="tags", value="project"
    - Find past deadlines: property_name="due_date", operator="<", value="2024-01-01"
    
    Returns:
        Notes matching the property criteria with values displayed
    """
    try:
        return await search_by_property(property_name, value, operator, context_length, ctx)
    except ValueError as e:
        raise ToolError(str(e))
    except Exception as e:
        raise ToolError(f"Property search failed: {str(e)}")

@mcp.tool()
async def list_notes_tool(
    directory: Annotated[Optional[str], Field(
        description="Specific folder to list notes from. Leave empty to list entire vault.",
        default=None,
        examples=[None, "Projects", "Daily", "Archive/2024"]
    )] = None,
    recursive: Annotated[bool, Field(
        description="Include notes from all subfolders. Set to false for only immediate children.",
        default=True
    )] = True,
    ctx=None
):
    """
    List notes in the vault or a specific directory.
    
    When to use:
    - Getting an overview of vault structure
    - Finding notes in a specific folder
    - Checking what notes exist before bulk operations
    - Understanding vault organization
    
    When NOT to use:
    - Searching for specific content (use search_notes)
    - Finding notes by properties (use search_by_property)
    - Just counting notes (this loads full paths)
    
    Performance notes:
    - Fast for directories with <100 notes
    - May be slower for large vaults (1000+ notes) with recursive=True
    
    Returns:
        Hierarchical structure of notes with paths and folder organization
    """
    try:
        return await list_notes(directory, recursive, ctx)
    except Exception as e:
        raise ToolError(f"Failed to list notes: {str(e)}")

@mcp.tool()
async def list_folders_tool(
    directory: Annotated[Optional[str], Field(
        description="Specific directory to list folders from (optional, defaults to root)",
        default=None,
        examples=[None, "Projects", "Daily", "Archive/2024"]
    )] = None,
    recursive: Annotated[bool, Field(
        description="Whether to include all nested subfolders",
        default=True
    )] = True,
    ctx=None
):
    """
    List folders in the vault or a specific directory.
    
    When to use:
    - Exploring vault organization structure
    - Verifying folder names before creating notes
    - Checking if a specific folder exists
    - Understanding the hierarchy of the vault
    
    When NOT to use:
    - Listing notes (use list_notes instead)
    - Searching for content (use search_notes)
    
    Returns:
        Folder structure with paths and names
    """
    try:
        return await list_folders(directory, recursive, ctx)
    except ValueError as e:
        raise ToolError(str(e))
    except Exception as e:
        raise ToolError(f"Failed to list folders: {str(e)}")

@mcp.tool()
async def move_note_tool(
    source_path: Annotated[str, Field(
        description="Current location of the note to move",
        pattern=r"^[^/].*\.md$",
        min_length=1,
        max_length=255,
        examples=["Inbox/Quick Note.md", "Projects/Old Project.md"]
    )],
    destination_path: Annotated[str, Field(
        description="New location for the note. Folders will be created if needed.",
        pattern=r"^[^/].*\.md$",
        min_length=1,
        max_length=255,
        examples=["Projects/Active/Quick Note.md", "Archive/2024/Old Project.md"]
    )],
    update_links: Annotated[bool, Field(
        description="Automatically update all [[wiki links]] if the filename changes during move",
        default=True
    )] = True,
    ctx=None
):
    """
    Move a note to a new location, optionally with a new name.
    
    When to use:
    - Reorganizing notes into different folders
    - Moving AND renaming in one operation
    - Archiving completed projects
    - Consolidating scattered notes
    
    When NOT to use:
    - Just renaming within same folder (use rename_note for clarity)
    - Copying notes (use read_note + create_note instead)
    - Moving entire folders (use move_folder)
    
    Link updating:
    - Automatically detects if filename changes during move
    - Updates all [[wiki-style links]] only when name changes
    - Preserves link aliases and formatting
    - No updates needed for simple folder moves (links work by name)
    
    Returns:
        Move confirmation with path changes and link update details
    """
    try:
        return await move_note(source_path, destination_path, update_links, ctx)
    except (ValueError, FileNotFoundError, FileExistsError) as e:
        raise ToolError(str(e))
    except Exception as e:
        raise ToolError(f"Failed to move note: {str(e)}")

@mcp.tool()
async def rename_note_tool(
    old_path: Annotated[str, Field(
        description="Current path of the note to rename",
        pattern=r"^[^/].*\.md$",
        min_length=1,
        max_length=255,
        examples=["Projects/Old Name.md", "Ideas/Temporary Title.md"]
    )],
    new_path: Annotated[str, Field(
        description="New path for the note (must be in same directory)",
        pattern=r"^[^/].*\.md$",
        min_length=1,
        max_length=255,
        examples=["Projects/New Name.md", "Ideas/Final Title.md"]
    )],
    update_links: Annotated[bool, Field(
        description="Automatically update all [[wiki links]] to this note across the vault",
        default=True
    )] = True,
    ctx=None
):
    """
    Rename a note and automatically update all references to it.
    
    When to use:
    - Changing a note's title to better reflect its content
    - Fixing typos in note names
    - Standardizing naming conventions
    - Updating temporary names to permanent ones
    
    When NOT to use:
    - Moving notes to different folders (use move_note)
    - Creating a copy with new name (use read_note + create_note)
    
    Important:
    - Can only rename within the same directory
    - Automatically updates all [[wiki-style links]] throughout vault
    - Preserves link aliases like [[old name|display text]]
    - Shows which notes were updated for transparency
    
    Returns:
        Rename confirmation with link update details
    """
    try:
        return await rename_note(old_path, new_path, update_links, ctx)
    except (ValueError, FileNotFoundError, FileExistsError) as e:
        raise ToolError(str(e))
    except Exception as e:
        raise ToolError(f"Failed to rename note: {str(e)}")

@mcp.tool()
async def create_folder_tool(
    folder_path: Annotated[str, Field(
        description="Path of the folder to create",
        min_length=1,
        max_length=255,
        examples=["Projects/2025", "Archive/Q1", "Daily/January"]
    )],
    create_placeholder: Annotated[bool, Field(
        description="Whether to create a placeholder file (.gitkeep or README.md)",
        default=True
    )] = True,
    ctx=None
):
    """
    Create a new folder in the vault, including all parent folders in the path.
    
    When to use:
    - Setting up project structure in advance
    - Creating deep folder hierarchies (e.g., "Research/Studies/2024")
    - Creating archive folders before moving notes
    - Establishing organizational hierarchy
    - Preparing folders for future content
    
    When NOT to use:
    - If you're about to create a note in that path (folders are created automatically)
    - For temporary organization (just create notes directly)
    
    Note: Will create all necessary parent folders. For example, "Research/Studies/2024"
    will create Research, Research/Studies, and Research/Studies/2024 if they don't exist.
    
    Returns:
        Creation status with list of folders created and placeholder file path
    """
    try:
        return await create_folder(folder_path, create_placeholder, ctx)
    except ValueError as e:
        raise ToolError(str(e))
    except Exception as e:
        raise ToolError(f"Failed to create folder: {str(e)}")

@mcp.tool()
async def move_folder_tool(
    source_folder: Annotated[str, Field(
        description="Current folder path to move",
        min_length=1,
        max_length=255,
        examples=["Projects/Old", "Archive/2023", "Inbox/Unsorted"]
    )],
    destination_folder: Annotated[str, Field(
        description="New location for the folder",
        min_length=1,
        max_length=255,
        examples=["Archive/Projects/Old", "Completed/2023", "Projects/Sorted"]
    )],
    update_links: Annotated[bool, Field(
        description="Whether to update links in other notes (future enhancement)",
        default=True
    )] = True,
    ctx=None
):
    """
    Move an entire folder and all its contents to a new location.
    
    When to use:
    - Reorganizing vault structure
    - Archiving completed projects
    - Consolidating related notes
    - Seasonal organization (e.g., moving to year-based archives)
    
    When NOT to use:
    - Moving individual notes (use move_note instead)
    - Moving to a subfolder of the source (creates circular reference)
    
    Returns:
        Move status with count of notes and folders moved
    """
    try:
        return await move_folder(source_folder, destination_folder, update_links, ctx)
    except (ValueError, FileNotFoundError) as e:
        raise ToolError(str(e))
    except Exception as e:
        raise ToolError(f"Failed to move folder: {str(e)}")

@mcp.tool()
async def add_tags_tool(
    path: Annotated[str, Field(
        description="Path to the note",
        pattern=r"^[^/].*\.md$",
        min_length=1,
        max_length=255
    )],
    tags: Annotated[List[str], Field(
        description="List of tags to add to the note. Don't include the # symbol - it will be added automatically. Supports hierarchical tags with forward slashes.",
        min_length=1,
        max_length=50,
        examples=[["project", "urgent"], ["project/web", "project/mobile"], ["work/meetings/standup", "work/meetings/planning"]]
    )],
    ctx=None
):
    """
    Add tags to a note's frontmatter.
    
    When to use:
    - Organizing notes with tags
    - Creating hierarchical tag structures (e.g., project/web, work/meetings/standup)
    - Bulk tagging operations
    - Adding metadata for search
    
    Tag format:
    - Simple tags: "project", "urgent"
    - Hierarchical tags: "project/web", "work/meetings/standup"
    - Tags are automatically added without duplicates
    
    When NOT to use:
    - Adding tags in note content (use update_note)
    - Replacing all tags (use update_tags with merge=False)
    
    Returns:
        Updated tag list for the note
    """
    try:
        return await add_tags(path, tags, ctx)
    except (ValueError, FileNotFoundError) as e:
        raise ToolError(str(e))
    except Exception as e:
        raise ToolError(f"Failed to add tags: {str(e)}")

@mcp.tool()
async def update_tags_tool(
    path: Annotated[str, Field(
        description="Path to the note",
        pattern=r"^[^/].*\.md$",
        min_length=1,
        max_length=255
    )],
    tags: Annotated[List[str], Field(
        description="New tags for the note. Empty list removes all tags. Don't include # symbols. Supports hierarchical tags with forward slashes.",
        min_length=0,
        max_length=50,
        examples=[["meeting", "important", "q1-2025"], ["project/ai", "research/neural-networks", "status/active"]]
    )],
    merge: Annotated[bool, Field(
        description="True = add these tags to existing ones, False = replace all tags with this new list",
        default=False
    )] = False,
    ctx=None
):
    """
    Update tags on a note - either replace all tags or merge with existing.
    
    When to use:
    - After analyzing a note's content to suggest relevant tags
    - Reorganizing tags across your vault
    - Setting consistent tags based on note types or projects
    - AI-driven tag suggestions ("What is this note about? Add appropriate tags")
    
    When NOT to use:
    - Just adding a few tags (use add_tags)
    - Just removing specific tags (use remove_tags)
    
    Returns:
        Previous tags, new tags, and operation performed
    """
    try:
        return await update_tags(path, tags, merge, ctx)
    except (ValueError, FileNotFoundError) as e:
        raise ToolError(str(e))
    except Exception as e:
        raise ToolError(f"Failed to update tags: {str(e)}")

@mcp.tool()
async def remove_tags_tool(
    path: Annotated[str, Field(
        description="Path to the note",
        pattern=r"^[^/].*\.md$",
        min_length=1,
        max_length=255
    )],
    tags: Annotated[List[str], Field(
        description="Tags to remove from the note (without # prefix). Removes exact matches only.",
        min_length=1,
        max_length=50,
        examples=[["outdated", "draft"], ["project/completed", "priority/high"]]
    )],
    ctx=None
):
    """
    Remove specific tags from a note's frontmatter.
    
    When to use:
    - Cleaning up outdated tags
    - Removing temporary tags (like 'draft' or 'review')
    - Tag maintenance and reorganization
    - After completing tagged tasks
    
    When NOT to use:
    - Removing all tags (use update_tags with empty list)
    - Replacing tags (use update_tags with merge=False)
    
    Note: Only removes exact matches. To remove all subtags of a hierarchical tag,
    list them explicitly or use update_tags.
    
    Returns:
        Updated tag list after removal, with count of removed tags
    """
    try:
        return await remove_tags(path, tags, ctx)
    except (ValueError, FileNotFoundError) as e:
        raise ToolError(str(e))
    except Exception as e:
        raise ToolError(f"Failed to remove tags: {str(e)}")

@mcp.tool()
async def get_note_info_tool(
    path: Annotated[str, Field(
        description="Path to the note to analyze",
        pattern=r"^[^/].*\.md$",
        min_length=1,
        max_length=255,
        examples=["Projects/Overview.md", "Daily/2024-01-15.md"]
    )],
    ctx=None
):
    """
    Get metadata and statistics about a note without reading its content.
    
    When to use:
    - Checking note properties quickly (tags, dates, size)
    - Getting frontmatter without loading content
    - Gathering statistics (word count, link count)
    - Verifying note exists and getting basic info
    - Batch processing note metadata
    
    When NOT to use:
    - Reading note content (use read_note)
    - Searching for notes (use search tools)
    - Modifying metadata (use specific update tools)
    
    Returns:
        Note metadata including path, existence, dates, size, frontmatter properties,
        and statistics (word count, link count, tag count, image presence)
    """
    try:
        return await get_note_info(path, ctx)
    except ValueError as e:
        raise ToolError(str(e))
    except Exception as e:
        raise ToolError(f"Failed to get note info: {str(e)}")

@mcp.tool()
async def get_backlinks_tool(
    path: Annotated[str, Field(
        description="Path to the note to find backlinks for",
        pattern=r"^[^/].*\.md$",
        min_length=1,
        max_length=255,
        examples=["Daily/2024-01-15.md", "Projects/AI Research.md"]
    )],
    include_context: Annotated[bool, Field(
        description="Include the text surrounding each link to understand why the link was made",
        default=True
    )] = True,
    context_length: Annotated[int, Field(
        description="How much surrounding text to show for each link (in characters)",
        ge=50,
        le=500,
        default=100
    )] = 100,
    ctx=None
):
    """
    Find all notes that link to a specific note (backlinks).
    
    When to use:
    - Understanding which notes reference a concept or topic
    - Discovering relationships between notes
    - Finding notes that depend on the current note
    - Building a mental map of note connections
    
    When NOT to use:
    - Finding links FROM a note (use get_outgoing_links)
    - Searching for broken links (use find_broken_links)
    
    Performance note:
    - Fast for small vaults (<100 notes)
    - May take several seconds for large vaults (1000+ notes)
    - Consider using search_notes for specific link queries
    
    Returns:
        All notes linking to the target with optional context
    """
    try:
        return await get_backlinks(path, include_context, context_length, ctx)
    except (ValueError, FileNotFoundError) as e:
        raise ToolError(str(e))
    except Exception as e:
        raise ToolError(f"Failed to get backlinks: {str(e)}")

@mcp.tool()
async def get_outgoing_links_tool(
    path: Annotated[str, Field(
        description="Path to the note to extract links from",
        pattern=r"^[^/].*\.md$",
        min_length=1,
        max_length=255,
        examples=["Projects/Overview.md", "Index.md"]
    )],
    check_validity: Annotated[bool, Field(
        description="Also check if each linked note actually exists in your vault",
        default=False
    )] = False,
    ctx=None
):
    """
    List all links from a specific note (outgoing links).
    
    When to use:
    - Understanding what a note references
    - Checking note dependencies before moving/deleting
    - Exploring the structure of index or hub notes
    - Validating links after changes
    
    When NOT to use:
    - Finding notes that link TO this note (use get_backlinks)
    - Searching across multiple notes (use find_broken_links)
    
    Returns:
        All outgoing links with their types and optional validity status
    """
    try:
        return await get_outgoing_links(path, check_validity, ctx)
    except (ValueError, FileNotFoundError) as e:
        raise ToolError(str(e))
    except Exception as e:
        raise ToolError(f"Failed to get outgoing links: {str(e)}")

@mcp.tool()
async def find_broken_links_tool(
    directory: Annotated[Optional[str], Field(
        description="Check only this folder and its subfolders. Leave empty to check entire vault.",
        default=None,
        examples=[None, "Projects", "Archive/2023"]
    )] = None,
    single_note: Annotated[Optional[str], Field(
        description="Check links in just this one note instead of the whole vault or directory",
        default=None,
        examples=["Daily/2025-01-09.md", "Projects/Overview.md"]
    )] = None,
    ctx=None
):
    """
    Find all broken links in the vault, a specific directory, or a single note.
    
    When to use:
    - After renaming or deleting notes
    - Regular vault maintenance
    - Before reorganizing folder structure
    - Cleaning up after imports
    - Checking links in a specific note
    
    When NOT to use:
    - Just getting outgoing links without needing broken status (use get_outgoing_links)
    - Finding backlinks (use get_backlinks)
    
    Returns:
        All broken links found in the specified scope
    """
    try:
        return await find_broken_links(directory, single_note, ctx)
    except ValueError as e:
        raise ToolError(str(e))
    except Exception as e:
        raise ToolError(f"Failed to find broken links: {str(e)}")

@mcp.tool()
async def find_orphaned_notes_tool(
    orphan_type: Annotated[Literal["no_backlinks", "no_links", "no_tags", "no_metadata", "isolated"], Field(
        description="What makes a note 'orphaned'. Choose the criteria that best fits your organization needs.",
        default="no_backlinks"
    )] = "no_backlinks",
    exclude_folders: Annotated[Optional[Union[List[str], str]], Field(
        description="Folders to exclude from search. Useful for skipping templates, archives, or daily notes. Can be a list or JSON string.",
        default=None,
        examples=[["Templates", "Archive"], '["Daily", "Templates", "Inbox"]']
    )] = None,
    min_age_days: Annotated[Optional[int], Field(
        description="Only include notes older than this many days. Helps exclude recent work-in-progress notes.",
        default=None,
        ge=0,
        le=365,
        examples=[7, 30, 90]
    )] = None,
    ctx=None
):
    """
    Find orphaned notes that may need organization or cleanup.
    
    When to use:
    - Regular vault maintenance and cleanup
    - Finding forgotten or disconnected notes
    - Identifying notes that need better organization
    - Preparing for vault reorganization
    - Finding candidates for archival or deletion
    
    Orphan types explained:
    - **no_backlinks**: Notes with no incoming links (most common definition)
    - **no_links**: Notes with no incoming OR outgoing links (completely isolated)
    - **no_tags**: Notes without any tags (untagged content)
    - **no_metadata**: Notes with minimal/no frontmatter properties
    - **isolated**: Notes with no links AND no tags (truly disconnected)
    
    Default exclusions:
    - Templates folder (usually contains reference notes)
    - Archive folder (already organized)
    - Daily folder (daily notes often standalone)
    
    When NOT to use:
    - Finding specific notes (use search_notes)
    - Getting all notes in a folder (use list_notes)
    - Finding notes by content (use search tools)
    
    Performance note:
    - Scans entire vault and checks links/metadata for each note
    - For vaults >1000 notes, this may take 10-30 seconds
    
    Returns:
        List of orphaned notes with paths, reasons, and metadata.
        Results are sorted by modification date (oldest first).
        
    Example response:
    {
        "count": 23,
        "orphaned_notes": [
            {
                "path": "Random Thoughts/Old Idea.md",
                "reason": "No incoming links",
                "modified": "2023-06-15T10:30:00Z",
                "size": 245,
                "word_count": 42
            }
        ],
        "stats": {
            "total_notes_scanned": 500,
            "excluded_folders": ["Templates", "Archive", "Daily"],
            "orphan_type": "no_backlinks"
        }
    }
    """
    try:
        # Parse exclude_folders if it's a JSON string
        if isinstance(exclude_folders, str):
            try:
                import json
                exclude_folders = json.loads(exclude_folders)
                if not isinstance(exclude_folders, list):
                    raise ValueError("exclude_folders must be a list")
            except json.JSONDecodeError:
                raise ToolError("Invalid JSON format for exclude_folders. Expected a JSON array like: [\"Daily\", \"Templates\"]")
        
        return await find_orphaned_notes(orphan_type, exclude_folders, min_age_days, ctx)
    except ValueError as e:
        raise ToolError(str(e))
    except Exception as e:
        raise ToolError(f"Failed to find orphaned notes: {str(e)}")

@mcp.tool()
async def list_tags_tool(
    include_counts: Annotated[bool, Field(
        description="Show how many times each tag is used across your vault",
        default=True
    )] = True,
    sort_by: Annotated[Literal["name", "count"], Field(
        description="Sort tags alphabetically by 'name' or by popularity with 'count'",
        default="name"
    )] = "name",
    include_files: Annotated[bool, Field(
        description="Include the list of file paths that contain each tag",
        default=False
    )] = False,
    ctx=None
):
    """
    List all unique tags used across the vault with usage statistics.
    
    When to use:
    - Before adding tags to maintain consistency
    - Getting an overview of your tagging taxonomy
    - Finding underused or overused tags
    - Discovering tag variations (e.g., 'project' vs 'projects')
    - Understanding hierarchical tag structures in your vault
    - Finding all files that use a specific tag (with include_files=true)
    
    Hierarchical tags:
    - Lists both parent and full hierarchical paths (e.g., both "project" and "project/web")
    - Shows how nested tags are organized in your vault
    - Helps identify opportunities for better tag organization
    
    File paths (with include_files=true):
    - Returns a list of all file paths that contain each tag
    - Useful for bulk operations on files with specific tags
    - Paths are relative to vault root
    
    When NOT to use:
    - Getting tags for a specific note (use get_note_info)
    - Searching notes by tag (use search_notes with tag: prefix)
    
    Performance note:
    - For vaults with <1000 notes: Fast (1-3 seconds)
    - For vaults with 1000-5000 notes: Moderate (3-10 seconds)
    - For vaults with >5000 notes: May be slow (10+ seconds)
    - Uses batched concurrent requests to optimize performance
    - include_files=true adds minimal overhead
    
    Returns:
        All unique tags with optional usage counts and file paths
    """
    try:
        return await list_tags(include_counts, sort_by, include_files, ctx)
    except ValueError as e:
        raise ToolError(str(e))
    except Exception as e:
        raise ToolError(f"Failed to list tags: {str(e)}")

@mcp.tool()
async def batch_update_properties_tool(
    search_criteria: Annotated[dict, Field(
        description="How to find notes to update. Must include one of: 'query' (search string), 'folder' (folder path), or 'files' (list of paths). Use 'query' for complex searches, 'folder' for directory operations, 'files' for specific notes.",
        examples=[
            {"query": "tag:project status:active"},
            {"folder": "Projects", "recursive": True},
            {"files": ["Projects/A.md", "Projects/B.md"]}
        ]
    )],
    property_updates: Annotated[Optional[dict], Field(
        description="Properties to add or update in frontmatter. Set value to null to remove.",
        default=None,
        examples=[
            {"status": "completed", "priority": 1},
            {"year": 2024, "archived": True}
        ]
    )] = None,
    properties_to_remove: Annotated[Optional[List[str]], Field(
        description="List of property names to remove from frontmatter",
        default=None,
        examples=[["draft", "temp"], ["old_status", "deprecated_field"]]
    )] = None,
    add_tags: Annotated[Optional[Union[List[str], str]], Field(
        description="Tags to add to notes (additive, won't remove existing tags). Can be a list or JSON string.",
        default=None,
        examples=[["archived", "2024"], ["reviewed", "approved"], '["archived", "2024"]']
    )] = None,
    remove_tags: Annotated[Optional[Union[List[str], str]], Field(
        description="Tags to remove from notes. Can be a list or JSON string.",
        default=None,
        examples=[["draft", "todo"], ["urgent", "needs-review"], '["draft", "todo"]']
    )] = None,
    remove_inline_tags: Annotated[bool, Field(
        description="Also remove tags from note body (inline #tags). Only applies when remove_tags is specified.",
        default=False
    )] = False,
    ctx=None
):
    """
    Batch update properties across multiple notes.
    
    When to use:
    - Updating metadata across many notes (status, priority, etc.)
    - Bulk tag operations (add/remove tags from multiple notes)
    - Archiving projects (set archived=true, add year property)
    - Cleaning up properties (remove outdated fields)
    - Normalizing metadata across your vault
    
    Search criteria options:
    - query: Use search syntax (tag:project, folder:Archive, property:status:active)
    - folder: Process all notes in a folder (with optional recursive flag)
    - files: Explicit list of file paths
    
    Property operations:
    - Add/update any frontmatter property
    - Remove properties by name
    - Special handling for tags (add/remove with deduplication)
    - Remove inline #tags from note body (optional)
    
    Examples:
    - Archive completed projects: query="tag:project status:completed", property_updates={"archived": true, "year": 2024}
    - Clean up draft tags: query="tag:draft", remove_tags=["draft"], remove_inline_tags=true
    - Update all notes in folder: folder="Projects/2023", property_updates={"year": 2023}
    
    When NOT to use:
    - Single note updates (use update_note, add_tags, etc.)
    - Complex content changes (this only updates frontmatter)
    
    Returns:
        {
            "total_notes": 10,         # Total notes found matching criteria
            "updated": 8,              # Successfully updated notes
            "failed": 2,               # Failed updates
            "details": [...],          # List of changes per note
            "errors": [...]            # List of errors with paths and reasons
        }
    """
    try:
        import json
        
        # Parse string inputs if needed
        if isinstance(add_tags, str):
            try:
                add_tags = json.loads(add_tags)
                if not isinstance(add_tags, list):
                    raise ValueError("add_tags must be a list when parsed from JSON string")
            except json.JSONDecodeError as e:
                raise ToolError(
                    f"Invalid JSON in add_tags parameter: {str(e)}. "
                    "Expected format: '[\"tag1\", \"tag2\"]' or use a list directly."
                )
        
        if isinstance(remove_tags, str):
            try:
                remove_tags = json.loads(remove_tags)
                if not isinstance(remove_tags, list):
                    raise ValueError("remove_tags must be a list when parsed from JSON string")
            except json.JSONDecodeError as e:
                raise ToolError(
                    f"Invalid JSON in remove_tags parameter: {str(e)}. "
                    "Expected format: '[\"tag1\", \"tag2\"]' or use a list directly."
                )
        
        return await batch_update_properties(
            search_criteria,
            property_updates,
            properties_to_remove,
            add_tags,
            remove_tags,
            remove_inline_tags,
            ctx
        )
    except ValueError as e:
        raise ToolError(str(e))
    except Exception as e:
        raise ToolError(f"Failed to batch update properties: {str(e)}")

@mcp.tool()
async def read_image_tool(
    path: Annotated[str, Field(
        description="Path to the image file relative to vault root",
        pattern=r"^[^/].*\.(png|jpg|jpeg|gif|webp|svg|bmp|ico)$",
        min_length=1,
        max_length=255,
        examples=["attachments/screenshot.png", "images/diagram.jpg", "media/logo.svg"]
    )],
    include_metadata: Annotated[bool, Field(
        description="Include file size and other metadata about the image",
        default=False
    )] = False,
    ctx=None
):
    """
    Read an image file from the Obsidian vault for analysis.
    
    When to use:
    - Analyzing specific image files from the vault
    - Examining standalone images (not embedded in notes)
    - Processing images for detailed analysis
    
    When NOT to use:
    - Getting images embedded in notes (use view_note_images instead)
    - Searching for images (use list_notes with appropriate filters)
    
    Returns:
        Image object that Claude can analyze and describe
    """
    try:
        return await read_image(path, include_metadata, ctx)
    except (ValueError, FileNotFoundError) as e:
        raise ToolError(str(e))
    except Exception as e:
        raise ToolError(f"Failed to read image: {str(e)}")

@mcp.tool()
async def view_note_images_tool(
    path: Annotated[str, Field(
        description="Path to the note containing images",
        pattern=r"^[^/].*\.md$",
        min_length=1,
        max_length=255,
        examples=["Projects/Design.md", "Daily/2024-01-15.md", "Ideas/Mockups.md"]
    )],
    image_index: Annotated[Optional[int], Field(
        description="Get only the Nth image from the note (0 = first image). Leave empty to get all images.",
        default=None,
        ge=0
    )] = None,
    max_width: Annotated[int, Field(
        description="Resize images wider than this to save memory. Images smaller than this are unchanged.",
        default=1600,
        gt=0,
        le=4096
    )] = 800,
    ctx=None
):
    """
    Extract and analyze images embedded in a note.
    
    When to use:
    - Analyzing images referenced in a note's markdown content
    - Examining visual content within notes (screenshots, diagrams, etc.)
    - Extracting specific images from notes for analysis
    
    When NOT to use:
    - Reading standalone image files (use read_image instead)
    - Getting note content without images (use read_note instead)
    
    Returns:
        List of Image objects that Claude can analyze and describe
    """
    try:
        return await view_note_images(path, image_index, max_width, ctx)
    except (ValueError, FileNotFoundError) as e:
        raise ToolError(str(e))
    except Exception as e:
        raise ToolError(f"Failed to view note images: {str(e)}")



def main():
    """Entry point for packaged distribution."""
    mcp.run()


if __name__ == "__main__":
    main()