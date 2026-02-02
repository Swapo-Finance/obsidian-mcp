"""Organization tools for Obsidian MCP server."""

import re
from typing import List, Dict, Any, Optional
from fastmcp import Context
from ..utils.filesystem import get_vault
from ..utils import validate_note_path, sanitize_path, is_markdown_file
from ..utils.validation import validate_tags
from ..models import Note, NoteMetadata, Tag
from ..constants import ERROR_MESSAGES


async def move_note(
    source_path: str,
    destination_path: str,
    update_links: bool = True,
    ctx: Context = None
) -> dict:
    """
    Move a note to a new location, optionally with a new name.
    
    Use this tool to reorganize your vault by moving notes to different
    folders. If the filename changes during the move, all wiki-style links
    will be automatically updated throughout your vault.
    
    Args:
        source_path: Current path of the note
        destination_path: New path for the note (can include a new filename)
        update_links: Whether to update links when filename changes (default: true)
        ctx: MCP context for progress reporting
        
    Returns:
        Dictionary containing move status and link update information
        
    Examples:
        >>> # Move without renaming (no link updates needed)
        >>> await move_note("Inbox/Note.md", "Projects/Note.md")
        {
            "source": "Inbox/Note.md",
            "destination": "Projects/Note.md",
            "moved": true,
            "renamed": false,
            "links_updated": 0
        }
        
        >>> # Move with renaming (links will be updated)
        >>> await move_note("Inbox/Quick Note.md", "Projects/Project Plan.md")
        {
            "source": "Inbox/Quick Note.md",
            "destination": "Projects/Project Plan.md",
            "moved": true,
            "renamed": true,
            "links_updated": 5,
            "notes_updated": 3
        }
    """
    # Validate paths
    for path, name in [(source_path, "source"), (destination_path, "destination")]:
        is_valid, error_msg = validate_note_path(path)
        if not is_valid:
            raise ValueError(f"Invalid {name} path: {error_msg}")
    
    # Import link management functions
    from ..tools.link_management import get_backlinks
    
    # Sanitize paths
    source_path = sanitize_path(source_path)
    destination_path = sanitize_path(destination_path)
    
    if source_path == destination_path:
        raise ValueError("Source and destination paths are the same")
    
    vault = get_vault()
    
    # Check if source exists
    try:
        source_note = await vault.read_note(source_path)
    except FileNotFoundError:
        # If exact path not found, try to find the note by filename
        if ctx:
            ctx.info(f"Note not found at {source_path}, searching for filename...")
        
        # Import search function
        from ..tools.search_discovery import search_notes
        
        # Search for notes with this filename
        source_filename = source_path.split('/')[-1]
        search_result = await search_notes(f"path:{source_filename}", max_results=10, ctx=None)
        
        if search_result["count"] == 0:
            raise FileNotFoundError(ERROR_MESSAGES["note_not_found"].format(path=source_path))
        elif search_result["count"] == 1:
            # Exactly one match - use it
            found_path = search_result["results"][0]["path"]
            if ctx:
                ctx.info(f"Found unique match at: {found_path}")
            
            # Update source_path to the found path
            source_path = found_path
            
            # Now try to read the note again
            source_note = await vault.read_note(source_path)
        else:
            # Multiple matches - show them to the user
            matches = [result["path"] for result in search_result["results"]]
            matches_str = "\n  - ".join(matches)
            raise ValueError(
                f"Multiple notes found with name '{source_filename}'. Please specify the full path:\n  - {matches_str}"
            )
    
    # Extract filenames to check if name is changing
    source_filename = source_path.split('/')[-1]
    dest_filename = destination_path.split('/')[-1]
    source_name = source_filename[:-3] if source_filename.endswith('.md') else source_filename
    dest_name = dest_filename[:-3] if dest_filename.endswith('.md') else dest_filename
    
    name_changed = source_name != dest_name
    
    if ctx:
        ctx.info(f"Moving note from {source_path} to {destination_path}")
        if name_changed:
            ctx.info(f"Filename is changing from '{source_filename}' to '{dest_filename}'")
    
    # Check if destination already exists
    try:
        await vault.read_note(destination_path)
        raise FileExistsError(f"Note already exists at destination: {destination_path}")
    except FileNotFoundError:
        # Good, destination doesn't exist
        pass
    
    # If name is changing and update_links is True, update all backlinks before moving
    links_updated = 0
    notes_updated = 0
    link_update_details = []
    
    if name_changed and update_links:
        if ctx:
            ctx.info(f"Filename changed - updating all links from '{source_name}' to '{dest_name}'")
        
        # Get all backlinks to the old note
        backlinks_result = await get_backlinks(source_path, include_context=False, ctx=None)
        backlinks = backlinks_result['findings']
        
        if ctx:
            ctx.info(f"Found {len(backlinks)} backlinks to update")
        
        # Group backlinks by source note for efficient updating
        updates_by_note = {}
        for backlink in backlinks:
            source = backlink['source_path']
            if source not in updates_by_note:
                updates_by_note[source] = []
            updates_by_note[source].append(backlink)
        
        # Update each note that contains backlinks
        for note_path, note_backlinks in updates_by_note.items():
            try:
                # Read the note
                note = await vault.read_note(note_path)
                content = note.content
                original_content = content
                updates_in_note = 0
                
                # Replace all wiki-style links to the old note
                patterns_to_replace = [
                    (re.compile(rf'\[\[{re.escape(source_name)}\]\]'), f'[[{dest_name}]]'),
                    (re.compile(rf'\[\[{re.escape(source_name)}\.md\]\]'), f'[[{dest_name}.md]]'),
                    (re.compile(rf'\[\[{re.escape(source_filename)}\]\]'), f'[[{dest_filename}]]'),
                    # With aliases
                    (re.compile(rf'\[\[{re.escape(source_name)}\|([^\]]+)\]\]'), rf'[[{dest_name}|\1]]'),
                    (re.compile(rf'\[\[{re.escape(source_name)}\.md\|([^\]]+)\]\]'), rf'[[{dest_name}.md|\1]]'),
                    (re.compile(rf'\[\[{re.escape(source_filename)}\|([^\]]+)\]\]'), rf'[[{dest_filename}|\1]]'),
                ]
                
                # Apply all replacements
                for pattern, replacement in patterns_to_replace:
                    content, count = pattern.subn(replacement, content)
                    updates_in_note += count
                
                # If content changed, write it back
                if content != original_content:
                    await vault.write_note(note_path, content, overwrite=True)
                    links_updated += updates_in_note
                    notes_updated += 1
                    link_update_details.append({
                        "note": note_path,
                        "updates": updates_in_note
                    })
                    
                    if ctx:
                        ctx.info(f"Updated {updates_in_note} links in {note_path}")
                
            except Exception as e:
                if ctx:
                    ctx.info(f"Error updating links in {note_path}: {str(e)}")
    
    # Create note at new location
    await vault.write_note(destination_path, source_note.content, overwrite=False)
    
    # Delete original note
    await vault.delete_note(source_path)
    
    if ctx:
        if name_changed and update_links:
            ctx.info(f"Successfully moved and renamed note, updated {links_updated} links")
        else:
            ctx.info(f"Successfully moved note")
    
    # Return standardized move operation structure
    return {
        "success": True,
        "source": source_path,
        "destination": destination_path,
        "type": "note",
        "renamed": name_changed,
        "details": {
            "items_moved": 1,
            "links_updated": links_updated,
            "notes_updated": notes_updated,
            "link_update_details": link_update_details[:10] if name_changed else []
        }
    }


async def rename_note(
    old_path: str,
    new_path: str,
    update_links: bool = True,
    ctx: Context = None
) -> dict:
    """
    Rename a note and optionally update all references to it.
    
    Use this tool to change a note's filename while automatically updating
    all wiki-style links ([[note name]]) that reference it throughout your
    vault. This maintains link integrity when reorganizing notes.
    
    Args:
        old_path: Current path of the note
        new_path: New path for the note (must be in same directory)
        update_links: Whether to update links in other notes (default: true)
        ctx: MCP context for progress reporting
        
    Returns:
        Dictionary containing rename status and updated links information
        
    Example:
        >>> await rename_note("Projects/AI Research.md", "Projects/Machine Learning Study.md", ctx=ctx)
        {
            "success": true,
            "old_path": "Projects/AI Research.md",
            "new_path": "Projects/Machine Learning Study.md",
            "operation": "renamed",
            "details": {
                "links_updated": 12,
                "notes_updated": 8,
                "link_update_details": [
                    {
                        "note": "Daily/2024-01-15.md",
                        "updates": 2
                    }
                ]
            }
        }
    """
    # Import link management functions
    from ..tools.link_management import get_backlinks, WIKI_LINK_PATTERN
    
    # Validate paths
    for path, name in [(old_path, "old"), (new_path, "new")]:
        is_valid, error_msg = validate_note_path(path)
        if not is_valid:
            raise ValueError(f"Invalid {name} path: {error_msg}")
    
    # Sanitize paths
    old_path = sanitize_path(old_path)
    new_path = sanitize_path(new_path)
    
    if old_path == new_path:
        raise ValueError("Old and new paths are the same")
    
    vault = get_vault()
    
    # Check if source exists
    try:
        source_note = await vault.read_note(old_path)
    except FileNotFoundError:
        # If exact path not found, try to find the note by filename
        if ctx:
            ctx.info(f"Note not found at {old_path}, searching for filename...")
        
        # Import search function
        from ..tools.search_discovery import search_notes
        
        # Search for notes with this filename
        old_filename = old_path.split('/')[-1]
        search_result = await search_notes(f"path:{old_filename}", max_results=10, ctx=None)
        
        if search_result["count"] == 0:
            raise FileNotFoundError(ERROR_MESSAGES["note_not_found"].format(path=old_path))
        elif search_result["count"] == 1:
            # Exactly one match - use it
            found_path = search_result["results"][0]["path"]
            if ctx:
                ctx.info(f"Found unique match at: {found_path}")
            
            # Update old_path to the found path
            old_path = found_path
            
            # Also update new_path to use the same directory
            found_dir = '/'.join(found_path.split('/')[:-1]) if '/' in found_path else ''
            new_filename = new_path.split('/')[-1]
            new_path = f"{found_dir}/{new_filename}" if found_dir else new_filename
            
            # Now try to read the note again
            source_note = await vault.read_note(old_path)
        else:
            # Multiple matches - show them to the user
            matches = [result["path"] for result in search_result["results"]]
            matches_str = "\n  - ".join(matches)
            raise ValueError(
                f"Multiple notes found with name '{old_filename}'. Please specify the full path:\n  - {matches_str}"
            )
    
    # Now that we have the actual paths, extract directory and filename
    old_dir = '/'.join(old_path.split('/')[:-1]) if '/' in old_path else ''
    new_dir = '/'.join(new_path.split('/')[:-1]) if '/' in new_path else ''
    
    if old_dir != new_dir:
        raise ValueError("Rename can only change the filename, not the directory. Use move_note to change directories.")
    
    # Extract filenames with and without .md extension
    old_filename = old_path.split('/')[-1]
    new_filename = new_path.split('/')[-1]
    old_name = old_filename[:-3] if old_filename.endswith('.md') else old_filename
    new_name = new_filename[:-3] if new_filename.endswith('.md') else new_filename
    
    if ctx:
        ctx.info(f"Renaming note from {old_path} to {new_path}")
    
    # Check if destination already exists
    try:
        await vault.read_note(new_path)
        raise FileExistsError(f"Note already exists at destination: {new_path}")
    except FileNotFoundError:
        # Good, destination doesn't exist
        pass
    
    # If update_links is True, find and update all backlinks before renaming
    links_updated = 0
    notes_updated = 0
    link_update_details = []
    
    if update_links:
        if ctx:
            ctx.info(f"Finding all notes that link to {old_name}")
        
        # Get all backlinks to the old note
        backlinks_result = await get_backlinks(old_path, include_context=False, ctx=None)
        backlinks = backlinks_result['findings']
        
        if ctx:
            ctx.info(f"Found {len(backlinks)} backlinks to update")
        
        # Group backlinks by source note for efficient updating
        updates_by_note = {}
        for backlink in backlinks:
            source = backlink['source_path']
            if source not in updates_by_note:
                updates_by_note[source] = []
            updates_by_note[source].append(backlink)
        
        # Update each note that contains backlinks
        for note_path, note_backlinks in updates_by_note.items():
            try:
                # Read the note
                note = await vault.read_note(note_path)
                content = note.content
                original_content = content
                updates_in_note = 0
                
                # Replace all wiki-style links to the old note
                # We need to handle various formats:
                # [[old_name]], [[old_name.md]], [[old_name|alias]]
                
                # Pattern to match wiki links to our old note
                patterns_to_replace = [
                    (re.compile(rf'\[\[{re.escape(old_name)}\]\]'), f'[[{new_name}]]'),
                    (re.compile(rf'\[\[{re.escape(old_name)}\.md\]\]'), f'[[{new_name}.md]]'),
                    (re.compile(rf'\[\[{re.escape(old_filename)}\]\]'), f'[[{new_filename}]]'),
                    # With aliases
                    (re.compile(rf'\[\[{re.escape(old_name)}\|([^\]]+)\]\]'), rf'[[{new_name}|\1]]'),
                    (re.compile(rf'\[\[{re.escape(old_name)}\.md\|([^\]]+)\]\]'), rf'[[{new_name}.md|\1]]'),
                    (re.compile(rf'\[\[{re.escape(old_filename)}\|([^\]]+)\]\]'), rf'[[{new_filename}|\1]]'),
                ]
                
                # Apply all replacements
                for pattern, replacement in patterns_to_replace:
                    content, count = pattern.subn(replacement, content)
                    updates_in_note += count
                
                # If content changed, write it back
                if content != original_content:
                    await vault.write_note(note_path, content, overwrite=True)
                    links_updated += updates_in_note
                    notes_updated += 1
                    link_update_details.append({
                        "note": note_path,
                        "updates": updates_in_note
                    })
                    
                    if ctx:
                        ctx.info(f"Updated {updates_in_note} links in {note_path}")
                
            except Exception as e:
                if ctx:
                    ctx.info(f"Error updating links in {note_path}: {str(e)}")
    
    # Now rename the note itself
    await vault.write_note(new_path, source_note.content, overwrite=False)
    await vault.delete_note(old_path)
    
    if ctx:
        ctx.info(f"Successfully renamed note and updated {links_updated} links")
    
    # Return standardized CRUD success structure
    return {
        "success": True,
        "old_path": old_path,
        "new_path": new_path,
        "operation": "renamed",
        "details": {
            "links_updated": links_updated,
            "notes_updated": notes_updated,
            "link_update_details": link_update_details[:10]  # Limit details to first 10 notes
        }
    }


async def create_folder(
    folder_path: str,
    create_placeholder: bool = True,
    ctx: Context = None
) -> dict:
    """
    Create a new folder in the vault, including all parent folders.
    
    Since Obsidian doesn't have explicit folders (they're created automatically
    when notes are added), this tool creates a folder by adding a placeholder
    file. It will create all necessary parent folders in the path.
    
    Args:
        folder_path: Path of the folder to create (e.g., "Research/Studies/2024")
        create_placeholder: Whether to create a placeholder file (default: true)
        ctx: MCP context for progress reporting
        
    Returns:
        Dictionary containing creation status
        
    Example:
        >>> await create_folder("Research/Studies/2024", ctx=ctx)
        {
            "folder": "Research/Studies/2024",
            "created": true,
            "placeholder_file": "Research/Studies/2024/.gitkeep",
            "folders_created": ["Research", "Research/Studies", "Research/Studies/2024"]
        }
    """
    # Validate folder path
    if folder_path.endswith('.md') or folder_path.endswith('.markdown'):
        raise ValueError(f"Invalid folder path: '{folder_path}'. Folder paths should not end with .md")
    if '..' in folder_path or folder_path.startswith('/'):
        raise ValueError(f"Invalid folder path: '{folder_path}'. Paths must be relative and cannot contain '..'")
    if not folder_path or folder_path.isspace():
        raise ValueError("Folder path cannot be empty")
    
    # Sanitize path
    folder_path = folder_path.strip('/').replace('\\', '/')
    
    if ctx:
        ctx.info(f"Creating folder: {folder_path}")
    
    vault = get_vault()
    
    # Split the path to check each level
    path_parts = folder_path.split('/')
    folders_to_check = []
    folders_created = []
    
    # Build list of all folders to check/create
    for i in range(len(path_parts)):
        partial_path = '/'.join(path_parts[:i+1])
        folders_to_check.append(partial_path)
    
    # Check each folder level
    from ..tools.search_discovery import list_notes
    for folder in folders_to_check:
        try:
            existing_notes = await list_notes(folder, recursive=False, ctx=None)
            # Folder exists if we can list it (even with 0 notes)
            if ctx:
                ctx.info(f"Folder already exists: {folder}")
        except Exception:
            # Folder doesn't exist, mark it for creation
            folders_created.append(folder)
            if ctx:
                ctx.info(f"Will create folder: {folder}")
    
    if not folders_created and not create_placeholder:
        # All folders already exist
        # Return standardized CRUD success structure
        return {
            "success": True,
            "path": folder_path,
            "operation": "exists",
            "details": {
                "created": False,
                "message": "All folders in path already exist",
                "folders_created": []
            }
        }
    
    if not create_placeholder:
        # Return standardized CRUD success structure
        return {
            "success": True,
            "path": folder_path,
            "operation": "created",
            "details": {
                "created": True,
                "message": "Folders will be created when first note is added",
                "placeholder_file": None,
                "folders_created": folders_created
            }
        }
    
    # Create a placeholder file in the deepest folder to establish the entire path
    placeholder_path = f"{folder_path}/.gitkeep"
    placeholder_content = f"# Folder: {folder_path}\n\nThis file ensures the folder exists in the vault structure.\n"
    
    try:
        await vault.write_note(placeholder_path, placeholder_content, overwrite=False)
        # Return standardized CRUD success structure
        return {
            "success": True,
            "path": folder_path,
            "operation": "created",
            "details": {
                "created": True,
                "placeholder_file": placeholder_path,
                "folders_created": folders_created if folders_created else ["(all already existed)"]
            }
        }
    except Exception as e:
        # Try with README.md if .gitkeep fails
        try:
            readme_path = f"{folder_path}/README.md"
            readme_content = f"# {folder_path.split('/')[-1]}\n\nThis folder contains notes related to {folder_path.replace('/', ' > ')}.\n"
            await vault.write_note(readme_path, readme_content, overwrite=False)
            # Return standardized CRUD success structure
            return {
                "success": True,
                "path": folder_path,
                "operation": "created",
                "details": {
                    "created": True,
                    "placeholder_file": readme_path,
                    "folders_created": folders_created if folders_created else ["(all already existed)"]
                }
            }
        except Exception as e2:
            raise ValueError(f"Failed to create folder: {str(e2)}")


async def move_folder(
    source_folder: str,
    destination_folder: str,
    update_links: bool = True,
    ctx: Context = None
) -> dict:
    """
    Move an entire folder and all its contents to a new location.
    
    Use this tool to reorganize your vault structure by moving entire
    folders with all their notes and subfolders.
    
    Args:
        source_folder: Current folder path (e.g., "Projects/Old")
        destination_folder: New folder path (e.g., "Archive/Projects/Old")
        update_links: Whether to update links in other notes (default: true)
        ctx: MCP context for progress reporting
        
    Returns:
        Dictionary containing move status and statistics
        
    Example:
        >>> await move_folder("Projects/Completed", "Archive/2024/Projects", ctx=ctx)
        {
            "source": "Projects/Completed",
            "destination": "Archive/2024/Projects",
            "moved": true,
            "notes_moved": 15,
            "folders_moved": 3,
            "links_updated": 0
        }
    """
    # Validate folder paths (no .md extension)
    for folder, name in [(source_folder, "source"), (destination_folder, "destination")]:
        if folder.endswith('.md') or folder.endswith('.markdown'):
            raise ValueError(f"Invalid {name} folder path: '{folder}'. Folder paths should not end with .md")
        if '..' in folder or folder.startswith('/'):
            raise ValueError(f"Invalid {name} folder path: '{folder}'. Paths must be relative and cannot contain '..'")
    
    # Sanitize paths
    source_folder = source_folder.strip('/').replace('\\', '/')
    destination_folder = destination_folder.strip('/').replace('\\', '/')
    
    if source_folder == destination_folder:
        raise ValueError("Source and destination folders are the same")
    
    # Check if destination is a subfolder of source (would create circular reference)
    if destination_folder.startswith(source_folder + '/'):
        raise ValueError("Cannot move a folder into its own subfolder")
    
    if ctx:
        ctx.info(f"Moving folder from {source_folder} to {destination_folder}")
    
    vault = get_vault()
    
    # Get all notes in the source folder recursively
    from ..tools.search_discovery import list_notes
    folder_contents = await list_notes(source_folder, recursive=True, ctx=None)
    
    if folder_contents["count"] == 0:
        raise ValueError(f"No notes found in folder: {source_folder}")
    
    notes_moved = 0
    folders_moved = set()  # Track unique folders
    links_updated = 0
    errors = []
    
    # Move each note
    for note_info in folder_contents["notes"]:
        old_path = note_info["path"]
        # Calculate new path by replacing the source folder prefix
        relative_path = old_path[len(source_folder):].lstrip('/')
        new_path = f"{destination_folder}/{relative_path}" if destination_folder else relative_path
        
        # Track folders
        folder_parts = relative_path.split('/')[:-1]  # Exclude filename
        for i in range(len(folder_parts)):
            folder_path = '/'.join(folder_parts[:i+1])
            folders_moved.add(folder_path)
        
        try:
            # Read the note
            note = await vault.read_note(old_path)
            # Create at new location
            await vault.write_note(new_path, note.content, overwrite=False)
            # Delete from old location
            await vault.delete_note(old_path)
            notes_moved += 1
            
            if ctx:
                ctx.info(f"Moved: {old_path} → {new_path}")
        except Exception as e:
            errors.append(f"Failed to move {old_path}: {str(e)}")
            if ctx:
                ctx.info(f"Error moving {old_path}: {str(e)}")
    
    # Update links if requested
    if update_links:
        # This would require searching for all notes that link to notes in the source folder
        # and updating them. For now, we'll mark this as a future enhancement.
        pass
    
    # Return standardized move operation structure
    result = {
        "success": True,
        "source": source_folder,
        "destination": destination_folder,
        "type": "folder",
        "details": {
            "items_moved": notes_moved,
            "links_updated": links_updated,
            "notes_moved": notes_moved,
            "folders_moved": len(folders_moved)
        }
    }
    
    if errors:
        result["details"]["errors"] = errors[:5]  # Limit to first 5 errors
        result["details"]["total_errors"] = len(errors)
    
    return result


async def add_tags(
    path: str,
    tags: List[str],
    ctx: Context = None
) -> dict:
    """
    Add tags to a note's frontmatter.
    
    Use this tool to add organizational tags to notes. Tags are added
    to the YAML frontmatter and do not modify the note's content.
    
    Args:
        path: Path to the note
        tags: List of tags to add (without # prefix)
        ctx: MCP context for progress reporting
        
    Returns:
        Dictionary containing updated tag list
        
    Example:
        >>> await add_tags("Projects/AI.md", ["machine-learning", "research"], ctx=ctx)
        {
            "path": "Projects/AI.md",
            "tags_added": ["machine-learning", "research"],
            "all_tags": ["ai", "project", "machine-learning", "research"]
        }
    """
    # Validate path
    is_valid, error_msg = validate_note_path(path)
    if not is_valid:
        raise ValueError(f"Invalid path: {error_msg}")
    
    path = sanitize_path(path)
    
    # Validate tags
    is_valid, error = validate_tags(tags)
    if not is_valid:
        raise ValueError(error)
    
    # Clean tags (remove # prefix if present) - validation already does this
    tags = [tag.lstrip("#").strip() for tag in tags if tag.strip()]
    
    if ctx:
        ctx.info(f"Adding tags to {path}: {tags}")
    
    vault = get_vault()
    
    try:
        note = await vault.read_note(path)
    except FileNotFoundError:
        raise FileNotFoundError(ERROR_MESSAGES["note_not_found"].format(path=path))
    
    # Parse frontmatter and update tags
    content = note.content
    updated_content = _update_frontmatter_tags(content, tags, "add")
    
    # Update the note
    await vault.write_note(path, updated_content, overwrite=True)
    
    # Get updated note to return current tags
    updated_note = await vault.read_note(path)
    
    # Return standardized tag operation structure
    return {
        "success": True,
        "path": path,
        "operation": "added",
        "tags": {
            "before": note.metadata.tags if note.metadata.tags else [],
            "after": updated_note.metadata.tags,
            "changes": {
                "added": tags,
                "removed": []
            }
        }
    }


async def update_tags(
    path: str,
    tags: List[str],
    merge: bool = False,
    ctx: Context = None
) -> dict:
    """
    Update tags on a note - either replace all tags or merge with existing.
    
    Use this tool when you want to set a note's tags based on its content
    or purpose. Perfect for AI-driven tag suggestions after analyzing a note.
    
    Args:
        path: Path to the note
        tags: New tags to set (without # prefix)
        merge: If True, adds to existing tags. If False, replaces all tags (default: False)
        ctx: MCP context for progress reporting
        
    Returns:
        Dictionary containing previous and new tag lists
        
    Example:
        >>> # After analyzing a note about machine learning project
        >>> await update_tags("Projects/ML Research.md", ["ai", "research", "neural-networks"], ctx=ctx)
        {
            "path": "Projects/ML Research.md",
            "previous_tags": ["project", "todo"],
            "new_tags": ["ai", "research", "neural-networks"],
            "operation": "replaced"
        }
    """
    # Validate path
    is_valid, error_msg = validate_note_path(path)
    if not is_valid:
        raise ValueError(f"Invalid path: {error_msg}")
    
    path = sanitize_path(path)
    
    # Validate tags
    is_valid, error = validate_tags(tags)
    if not is_valid:
        raise ValueError(error)
    
    # Clean tags (remove # prefix if present)
    tags = [tag.lstrip("#").strip() for tag in tags if tag.strip()]
    
    if ctx:
        ctx.info(f"Updating tags for {path}: {tags} (merge={merge})")
    
    vault = get_vault()
    
    try:
        note = await vault.read_note(path)
    except FileNotFoundError:
        raise FileNotFoundError(ERROR_MESSAGES["note_not_found"].format(path=path))
    
    # Store previous tags
    previous_tags = note.metadata.tags.copy() if note.metadata.tags else []
    
    # Determine final tags based on merge setting
    if merge:
        # Merge with existing tags (like add_tags but more explicit)
        final_tags = list(set(previous_tags + tags))
        operation = "merged"
    else:
        # Replace all tags
        final_tags = tags
        operation = "replaced"
    
    # Update the note's frontmatter
    content = note.content
    updated_content = _update_frontmatter_tags(content, final_tags, "replace")
    
    # Update the note
    await vault.write_note(path, updated_content, overwrite=True)
    
    # Return standardized tag operation structure
    added_tags = list(set(final_tags) - set(previous_tags)) if merge else final_tags
    removed_tags = list(set(previous_tags) - set(final_tags)) if not merge else []
    
    return {
        "success": True,
        "path": path,
        "operation": "updated",
        "tags": {
            "before": previous_tags,
            "after": final_tags,
            "changes": {
                "added": added_tags,
                "removed": removed_tags,
                "merge_mode": merge,
                "operation_type": operation
            }
        }
    }


async def remove_tags(
    path: str,
    tags: List[str],
    ctx: Context = None
) -> dict:
    """
    Remove tags from a note's frontmatter.
    
    Use this tool to remove organizational tags from notes.
    
    Args:
        path: Path to the note
        tags: List of tags to remove (without # prefix)
        ctx: MCP context for progress reporting
        
    Returns:
        Dictionary containing updated tag list
        
    Example:
        >>> await remove_tags("Projects/AI.md", ["outdated"], ctx=ctx)
        {
            "path": "Projects/AI.md",
            "tags_removed": ["outdated"],
            "remaining_tags": ["ai", "project", "machine-learning"]
        }
    """
    # Validate path
    is_valid, error_msg = validate_note_path(path)
    if not is_valid:
        raise ValueError(f"Invalid path: {error_msg}")
    
    path = sanitize_path(path)
    
    # Validate tags
    is_valid, error = validate_tags(tags)
    if not is_valid:
        raise ValueError(error)
    
    # Clean tags (remove # prefix if present) - validation already does this
    tags = [tag.lstrip("#").strip() for tag in tags if tag.strip()]
    
    if ctx:
        ctx.info(f"Removing tags from {path}: {tags}")
    
    vault = get_vault()
    
    try:
        note = await vault.read_note(path)
    except FileNotFoundError:
        raise FileNotFoundError(ERROR_MESSAGES["note_not_found"].format(path=path))
    
    # Parse frontmatter and update tags
    content = note.content
    updated_content = _update_frontmatter_tags(content, tags, "remove")
    
    # Update the note
    await vault.write_note(path, updated_content, overwrite=True)
    
    # Get updated note to return current tags
    updated_note = await vault.read_note(path)
    
    # Return standardized tag operation structure
    return {
        "success": True,
        "path": path,
        "operation": "removed",
        "tags": {
            "before": note.metadata.tags if note.metadata.tags else [],
            "after": updated_note.metadata.tags if updated_note.metadata.tags else [],
            "changes": {
                "added": [],
                "removed": tags
            }
        }
    }


async def get_note_info(
    path: str,
    ctx: Context = None
) -> dict:
    """
    Get metadata and information about a note without retrieving its full content.
    
    Use this tool when you need to check a note's metadata, tags, or other
    properties without loading the entire content.
    
    Args:
        path: Path to the note
        ctx: MCP context for progress reporting
        
    Returns:
        Dictionary containing note metadata and statistics
        
    Example:
        >>> await get_note_info("Projects/AI Research.md", ctx=ctx)
        {
            "path": "Projects/AI Research.md",
            "exists": true,
            "metadata": {
                "tags": ["ai", "research", "active"],
                "created": "2024-01-10T10:00:00Z",
                "modified": "2024-01-15T14:30:00Z",
                "aliases": ["AI Study", "ML Research"]
            },
            "stats": {
                "size_bytes": 4523,
                "word_count": 823,
                "link_count": 12
            }
        }
    """
    # Validate path
    is_valid, error_msg = validate_note_path(path)
    if not is_valid:
        raise ValueError(f"Invalid path: {error_msg}")
    
    path = sanitize_path(path)
    
    if ctx:
        ctx.info(f"Getting info for: {path}")
    
    vault = get_vault()
    
    try:
        note = await vault.read_note(path)
    except FileNotFoundError:
        # Return standardized CRUD structure for non-existent note
        return {
            "success": False,
            "path": path,
            "operation": "read",
            "details": {
                "exists": False,
                "error": "Note not found"
            }
        }
    
    # Calculate statistics
    content = note.content
    word_count = len(content.split())
    
    # Count links (both [[wikilinks]] and [markdown](links))
    wikilink_count = len(re.findall(r'\[\[([^\]]+)\]\]', content))
    markdown_link_count = len(re.findall(r'\[([^\]]+)\]\(([^)]+)\)', content))
    link_count = wikilink_count + markdown_link_count
    
    # Return standardized CRUD success structure
    return {
        "success": True,
        "path": path,
        "operation": "read",
        "details": {
            "exists": True,
            "metadata": note.metadata.model_dump(exclude_none=True),
            "stats": {
                "size_bytes": len(content.encode('utf-8')),
                "word_count": word_count,
                "link_count": link_count
            }
        }
    }


def _update_frontmatter_tags(content: str, tags: List[str], operation: str) -> str:
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
        frontmatter = content[4:end_index-5]
        rest_of_content = content[end_index:]
    except ValueError:
        # Invalid frontmatter
        return content
    
    # Parse YAML manually (simple approach for tags)
    lines = frontmatter.split('\n')
    new_lines = []
    tags_found = False
    in_tags_list = False
    existing_tags = []
    
    i = 0
    while i < len(lines):
        line = lines[i]
        
        if line.startswith('tags:'):
            tags_found = True
            # Check if tags are on the same line
            if '[' in line:
                # Array format: tags: [tag1, tag2]
                match = re.search(r'\[(.*?)\]', line)
                if match:
                    existing_tags = [t.strip().strip('"').strip("'") for t in match.group(1).split(',') if t.strip()]
            elif line.strip() != 'tags:':
                # Inline format: tags: tag1 tag2
                existing_tags = line.split(':', 1)[1].strip().split()
            else:
                # Bullet list format on next lines
                in_tags_list = True
                i += 1
                # Parse all bullet list items
                while i < len(lines) and lines[i].strip().startswith('- '):
                    tag = lines[i].strip()[2:].strip()
                    if tag:
                        existing_tags.append(tag)
                    i += 1
                i -= 1  # Back up one since the outer loop will increment
            
            # Update tags based on operation
            if operation == "add":
                # Add new tags, avoid duplicates
                for tag in tags:
                    if tag not in existing_tags:
                        existing_tags.append(tag)
            elif operation == "replace":
                # Replace all tags
                existing_tags = tags
            else:  # remove
                existing_tags = [t for t in existing_tags if t not in tags]
            
            # Format updated tags
            if existing_tags:
                new_lines.append(f"tags: [{', '.join(existing_tags)}]")
            # Skip line if no tags remain
            
        elif in_tags_list and line.strip().startswith('- '):
            # Skip bullet list items - they've already been processed
            pass
        else:
            new_lines.append(line)
            in_tags_list = False
        
        i += 1
    
    # If no tags were found and we're adding or replacing, add them
    if not tags_found and operation in ["add", "replace"]:
        new_lines.insert(0, f"tags: [{', '.join(tags)}]")
    
    # Reconstruct content
    new_frontmatter = '\n'.join(new_lines)
    return f"---\n{new_frontmatter}\n---\n{rest_of_content}"


async def list_tags(
    include_counts: bool = True,
    sort_by: str = "name",
    include_files: bool = False,
    ctx=None
) -> dict:
    """
    List all unique tags used across the vault with usage statistics.
    
    Use this tool to discover existing tags before creating new ones. This helps
    maintain consistency in your tagging system and prevents duplicate tags with
    slight variations (e.g., 'project' vs 'projects').
    
    Args:
        include_counts: Whether to include usage count for each tag (default: true)
        sort_by: How to sort results - "name" (alphabetical) or "count" (by usage) (default: "name")
        include_files: Whether to include file paths for each tag (default: false)
        ctx: MCP context for progress reporting
        
    Returns:
        Dictionary containing all unique tags with optional usage counts
        
    Example:
        >>> await list_tags(include_counts=True, sort_by="count")
        {
            "items": [
                {"name": "project", "count": 42},
                {"name": "meeting", "count": 38},
                {"name": "idea", "count": 15}
            ],
            "total": 25,
            "scope": {"include_counts": true, "sort_by": "count", "include_files": false}
        }
        
        >>> await list_tags(include_files=True, include_counts=False)
        {
            "items": [
                {"name": "project", "files": ["Projects/Web App.md", "Projects/Mobile App.md"]},
                {"name": "meeting", "files": ["Meetings/2024-01-15.md", "Meetings/2024-01-22.md"]}
            ],
            "total": 2,
            "scope": {"include_counts": false, "sort_by": "name", "include_files": true}
        }
    """
    # Validate sort_by parameter
    if sort_by not in ["name", "count"]:
        raise ValueError(ERROR_MESSAGES["invalid_sort_by"].format(value=sort_by))
    
    if ctx:
        ctx.info("Collecting tags from vault...")
    
    vault = get_vault()
    
    # Dictionary to store tag counts and file paths
    tag_counts = {}
    tag_files = {} if include_files else None
    
    try:
        # Get all notes in the vault
        all_notes = await vault.list_notes(recursive=True)
        
        if ctx:
            ctx.info(f"Scanning {len(all_notes)} notes for tags...")
        
        # Process notes in batches for better performance
        import asyncio
        
        # Adjust batch size based on vault size
        batch_size = 50 if len(all_notes) > 1000 else 20
        max_concurrent = asyncio.Semaphore(batch_size)
        
        async def process_note(note_info):
            async with max_concurrent:
                try:
                    note = await vault.read_note(note_info["path"])
                    if note and note.metadata and note.metadata.tags:
                        return (note_info["path"], note.metadata.tags)
                except Exception:
                    return (note_info["path"], [])
                return (note_info["path"], [])
        
        # Process all notes concurrently with semaphore limiting
        tasks = [process_note(note_info) for note_info in all_notes]
        results = await asyncio.gather(*tasks)
        
        # Count tags and collect file paths
        for path, tags in results:
            for tag in tags:
                if tag:
                    tag_counts[tag] = tag_counts.get(tag, 0) + 1
                    if include_files:
                        if tag not in tag_files:
                            tag_files[tag] = []
                        tag_files[tag].append(path)
        
        # Format results
        if include_counts or include_files:
            tags = []
            for tag, count in tag_counts.items():
                tag_item = {"name": tag}
                if include_counts:
                    tag_item["count"] = count
                if include_files and tag in tag_files:
                    tag_item["files"] = sorted(tag_files[tag])
                tags.append(tag_item)
            
            # Sort based on preference
            if sort_by == "count":
                tags.sort(key=lambda x: x.get("count", 0), reverse=True)
            else:  # sort by name
                tags.sort(key=lambda x: x["name"].lower())
        else:
            # Just return tag names sorted
            tags = sorted(tag_counts.keys(), key=str.lower)
        
        # Return standardized list results structure
        return {
            "items": tags,
            "total": len(tag_counts),
            "scope": {
                "include_counts": include_counts,
                "sort_by": sort_by,
                "include_files": include_files
            }
        }
        
    except Exception as e:
        if ctx:
            ctx.info(f"Failed to list tags: {str(e)}")
        raise ValueError(ERROR_MESSAGES["tag_collection_failed"].format(error=str(e)))


def _update_frontmatter_properties(content: str, property_updates: dict, properties_to_remove: List[str] = None) -> str:
    """
    Update multiple frontmatter properties while preserving YAML structure.
    
    Args:
        content: Note content with frontmatter
        property_updates: Dict of properties to add/update {property: value}
        properties_to_remove: List of property names to remove
        
    Returns:
        Updated content with modified frontmatter
    """
    import yaml
    from io import StringIO
    
    properties_to_remove = properties_to_remove or []
    
    # Check if frontmatter exists
    if not content.startswith("---\n"):
        # Create frontmatter if properties to add
        if property_updates:
            # Use yaml.dump to ensure proper formatting
            yaml_content = yaml.dump(property_updates, default_flow_style=False, allow_unicode=True, sort_keys=False)
            return f"---\n{yaml_content}---\n\n{content}"
        return content
    
    # Parse existing frontmatter
    try:
        end_index = content.index("\n---\n", 4) + 5
        frontmatter_str = content[4:end_index-5]
        rest_of_content = content[end_index:]
    except ValueError:
        # Invalid frontmatter
        return content
    
    # Parse YAML
    try:
        frontmatter = yaml.safe_load(frontmatter_str) or {}
    except yaml.YAMLError:
        # If YAML parsing fails, return original
        return content
    
    # Update properties
    for key, value in property_updates.items():
        if value is None and key not in properties_to_remove:
            properties_to_remove.append(key)
        else:
            frontmatter[key] = value
    
    # Remove properties
    for prop in properties_to_remove:
        frontmatter.pop(prop, None)
    
    # Special handling for tags to ensure list format
    if 'tags' in frontmatter and isinstance(frontmatter['tags'], str):
        # Convert string tags to list
        frontmatter['tags'] = [frontmatter['tags']]
    
    # Dump back to YAML
    if frontmatter:
        yaml_content = yaml.dump(frontmatter, default_flow_style=False, allow_unicode=True, sort_keys=False)
        # Remove the trailing newline that yaml.dump adds
        yaml_content = yaml_content.rstrip('\n')
        return f"---\n{yaml_content}\n---\n{rest_of_content}"
    else:
        # No frontmatter left
        return rest_of_content.lstrip('\n')


def _remove_inline_tags(content: str, tags_to_remove: List[str]) -> tuple[str, int]:
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
        return f"__CODE_BLOCK_{len(code_blocks)-1}__"
    
    body_no_code = re.sub(r'```[\s\S]*?```', replace_code_block, body)
    
    # Remove inline code
    inline_code = []
    def replace_inline_code(match):
        inline_code.append(match.group(0))
        return f"__INLINE_CODE_{len(inline_code)-1}__"
    
    body_no_code = re.sub(r'`[^`]+`', replace_inline_code, body_no_code)
    
    # Remove tags
    tags_removed = 0
    for tag in tags_to_remove:
        # Escape special regex characters
        escaped_tag = re.escape(tag)
        # Match #tag at word boundaries (not part of URL or other text)
        pattern = rf'(^|\s)#{escaped_tag}(?=\s|$|\.|,|;|:|!|\?|\))'
        
        # Count matches before replacing
        matches = re.findall(pattern, body_no_code)
        tags_removed += len(matches)
        
        # Remove the tags (keep the whitespace before the tag)
        body_no_code = re.sub(pattern, r'\1', body_no_code)
    
    # Clean up multiple spaces left by removal
    body_no_code = re.sub(r'  +', ' ', body_no_code)
    
    # Restore code blocks
    for i, block in enumerate(code_blocks):
        body_no_code = body_no_code.replace(f"__CODE_BLOCK_{i}__", block)
    
    for i, code in enumerate(inline_code):
        body_no_code = body_no_code.replace(f"__INLINE_CODE_{i}__", code)
    
    return frontmatter + body_no_code, tags_removed


async def batch_update_properties(
    search_criteria: dict,
    property_updates: dict = None,
    properties_to_remove: List[str] = None,
    add_tags: List[str] = None,
    remove_tags: List[str] = None,
    remove_inline_tags: bool = False,
    ctx=None
) -> dict:
    """
    Batch update properties across multiple notes.
    
    Args:
        search_criteria: How to find notes - dict with one of:
            - 'query': Search query string
            - 'folder': Folder path to process
            - 'files': Explicit list of file paths
        property_updates: Dict of properties to add/update
        properties_to_remove: List of property names to remove
        add_tags: List of tags to add (additive)
        remove_tags: List of tags to remove
        remove_inline_tags: Whether to also remove tags from note body
        ctx: MCP context for progress reporting
        
    Returns:
        Dict with operation results and affected files
    """
    vault = get_vault()
    
    # Validate search criteria
    if not search_criteria:
        raise ValueError(
            "search_criteria is required. Provide one of: "
            "1) {'query': 'tag:project'} for search-based selection, "
            "2) {'folder': 'Projects', 'recursive': true} for folder-based selection, "
            "3) {'files': ['Note1.md', 'Note2.md']} for specific files"
        )
    
    if not any(k in search_criteria for k in ['query', 'folder', 'files']):
        raise ValueError(
            "search_criteria must include exactly one selection method: "
            "'query' (search string), 'folder' (directory path), or 'files' (list of paths). "
            f"Received: {list(search_criteria.keys())}"
        )
    
    # Validate that at least one operation is specified
    if not any([property_updates, properties_to_remove, add_tags, remove_tags]):
        raise ValueError(
            "No operations specified. Provide at least one of: "
            "property_updates (to add/update properties), "
            "properties_to_remove (to delete properties), "
            "add_tags (to add tags), or "
            "remove_tags (to remove tags)"
        )
    
    # Find notes to update
    notes_to_update = []
    
    if 'files' in search_criteria:
        # Explicit file list
        for file_path in search_criteria['files']:
            try:
                note = await vault.read_note(file_path)
                if note:
                    notes_to_update.append(file_path)
            except Exception:
                pass  # Skip invalid files
    
    elif 'folder' in search_criteria:
        # All notes in folder
        folder = search_criteria['folder']
        all_notes = await vault.list_notes(directory=folder, recursive=search_criteria.get('recursive', True))
        notes_to_update = [note['path'] for note in all_notes]
    
    elif 'query' in search_criteria:
        # Search results
        from .search_discovery import search_notes
        results = await search_notes(
            query=search_criteria['query'],
            max_results=search_criteria.get('max_results', 500),
            ctx=ctx
        )
        notes_to_update = [r['path'] for r in results['results']]
    
    if ctx:
        ctx.info(f"Found {len(notes_to_update)} notes to update")
    
    # Process each note
    results = {
        'total_notes': len(notes_to_update),
        'updated': 0,
        'failed': 0,
        'details': [],
        'errors': []
    }
    
    for note_path in notes_to_update:
        try:
            # Read note
            note = await vault.read_note(note_path)
            if not note:
                results['errors'].append({
                    'path': note_path,
                    'error': 'Note not found'
                })
                results['failed'] += 1
                continue
            
            content = note.content
            original_content = content
            changes_made = []
            
            # Handle special tag operations first
            if add_tags or remove_tags:
                # Get current tags from frontmatter
                current_tags = note.metadata.frontmatter.get('tags', [])
                if isinstance(current_tags, str):
                    current_tags = [current_tags]
                elif not isinstance(current_tags, list):
                    current_tags = list(current_tags) if current_tags else []
                
                updated_tags = current_tags.copy()
                
                # Remove tags
                if remove_tags:
                    for tag in remove_tags:
                        if tag in updated_tags:
                            updated_tags.remove(tag)
                            changes_made.append(f"Removed tag '{tag}' from frontmatter")
                
                # Add tags
                if add_tags:
                    for tag in add_tags:
                        if tag not in updated_tags:
                            updated_tags.append(tag)
                            changes_made.append(f"Added tag '{tag}' to frontmatter")
                
                # Update frontmatter if tags changed
                if updated_tags != current_tags:
                    if property_updates is None:
                        property_updates = {}
                    property_updates['tags'] = updated_tags
            
            # Update frontmatter properties
            if property_updates or properties_to_remove:
                content = _update_frontmatter_properties(
                    content,
                    property_updates or {},
                    properties_to_remove
                )
                
                # Track changes
                if property_updates:
                    for prop, value in property_updates.items():
                        if prop != 'tags':  # Already tracked above
                            changes_made.append(f"Set {prop} = {value}")
                
                if properties_to_remove:
                    for prop in properties_to_remove:
                        changes_made.append(f"Removed property '{prop}'")
            
            # Remove inline tags if requested
            inline_tags_removed = 0
            if remove_inline_tags and remove_tags:
                content, inline_tags_removed = _remove_inline_tags(content, remove_tags)
                if inline_tags_removed > 0:
                    changes_made.append(f"Removed {inline_tags_removed} inline tags")
            
            # Update note if changed
            if content != original_content:
                await vault.write_note(note_path, content, overwrite=True)
                results['updated'] += 1
                results['details'].append({
                    'path': note_path,
                    'changes': changes_made
                })
            
            if ctx and results['updated'] % 10 == 0:
                ctx.info(f"Updated {results['updated']} notes...")
                
        except Exception as e:
            results['errors'].append({
                'path': note_path,
                'error': str(e)
            })
            results['failed'] += 1
    
    if ctx:
        ctx.info(f"Batch update complete: {results['updated']} updated, {results['failed']} failed")
    
    return results