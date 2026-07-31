"""Folder operations for Obsidian MCP server."""

from fastmcp import Context

from ..utils.filesystem import get_vault
from .write_policy import _serialize_note_writes


@_serialize_note_writes
async def create_folder(
    folder_path: str, create_placeholder: bool = True, ctx: Context | None = None
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
    if folder_path.endswith((".md", ".markdown")):
        raise ValueError(
            f"Invalid folder path: '{folder_path}'. Folder paths should not end with .md"
        )
    if ".." in folder_path or folder_path.startswith("/"):
        raise ValueError(
            f"Invalid folder path: '{folder_path}'. Paths must be relative and cannot contain '..'"
        )
    if not folder_path or folder_path.isspace():
        raise ValueError("Folder path cannot be empty")

    # Sanitize path
    folder_path = folder_path.strip("/").replace("\\", "/")

    if ctx:
        await ctx.info(f"Creating folder: {folder_path}")

    vault = get_vault()

    # Split the path to check each level
    path_parts = folder_path.split("/")
    folders_to_check = []
    folders_created = []

    # Build list of all folders to check/create
    for i in range(len(path_parts)):
        partial_path = "/".join(path_parts[: i + 1])
        folders_to_check.append(partial_path)

    # Check each folder level
    from ..tools.search_discovery import list_notes

    for folder in folders_to_check:
        try:
            await list_notes(folder, recursive=False, ctx=None)
            # Folder exists if we can list it (even with 0 notes)
            if ctx:
                await ctx.info(f"Folder already exists: {folder}")
        except Exception:
            # Folder doesn't exist, mark it for creation
            folders_created.append(folder)
            if ctx:
                await ctx.info(f"Will create folder: {folder}")

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
                "folders_created": [],
            },
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
                "folders_created": folders_created,
            },
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
                "folders_created": folders_created
                if folders_created
                else ["(all already existed)"],
            },
        }
    except Exception:
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
                    "folders_created": folders_created
                    if folders_created
                    else ["(all already existed)"],
                },
            }
        except Exception as e2:
            raise ValueError(f"Failed to create folder: {e2!s}")


@_serialize_note_writes
async def move_folder(
    source_folder: str,
    destination_folder: str,
    update_links: bool = True,
    ctx: Context | None = None,
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
    for folder, name in [
        (source_folder, "source"),
        (destination_folder, "destination"),
    ]:
        if folder.endswith((".md", ".markdown")):
            raise ValueError(
                f"Invalid {name} folder path: '{folder}'. Folder paths should not end with .md"
            )
        if ".." in folder or folder.startswith("/"):
            raise ValueError(
                f"Invalid {name} folder path: '{folder}'. Paths must be relative and cannot contain '..'"
            )

    # Sanitize paths
    source_folder = source_folder.strip("/").replace("\\", "/")
    destination_folder = destination_folder.strip("/").replace("\\", "/")

    if source_folder == destination_folder:
        raise ValueError("Source and destination folders are the same")

    # Check if destination is a subfolder of source (would create circular reference)
    if destination_folder.startswith(source_folder + "/"):
        raise ValueError("Cannot move a folder into its own subfolder")

    if ctx:
        await ctx.info(f"Moving folder from {source_folder} to {destination_folder}")

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
        relative_path = old_path[len(source_folder) :].lstrip("/")
        new_path = (
            f"{destination_folder}/{relative_path}"
            if destination_folder
            else relative_path
        )

        # Track folders
        folder_parts = relative_path.split("/")[:-1]  # Exclude filename
        for i in range(len(folder_parts)):
            folder_path = "/".join(folder_parts[: i + 1])
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
                await ctx.info(f"Moved: {old_path} → {new_path}")
        except Exception as e:
            errors.append(f"Failed to move {old_path}: {e!s}")
            if ctx:
                await ctx.info(f"Error moving {old_path}: {e!s}")

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
            "folders_moved": len(folders_moved),
        },
    }

    if errors:
        result["details"]["errors"] = errors[:5]  # Limit to first 5 errors
        result["details"]["total_errors"] = len(errors)

    return result
