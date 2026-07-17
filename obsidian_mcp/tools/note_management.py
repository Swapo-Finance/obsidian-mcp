"""Note management tools for Obsidian MCP server."""

import asyncio
import functools
import re
import unicodedata
from typing import Optional, List, Dict, Any, Tuple
from fastmcp import Context
from ..utils.filesystem import get_vault
from ..utils import validate_note_path, sanitize_path
from ..utils.validation import validate_content
from ..utils.vault_config import (
    check_note_size_policy,
    check_template_conformance,
    count_lines,
    slugify_kebab,
)
from ..models import Note
from ..constants import ERROR_MESSAGES
from .link_management import validate_wikilinks_for_write


def apply_slug_style_to_path(vault, path: str) -> str:
    """If OBSIDIAN_SLUG_STYLE=kebab, kebab-slugify the note's filename (not
    its folder path — folder names are left as the user/template chose)
    so filenames/links stay portable ASCII (spec section 1, item a).
    Raises ValueError if the filename has nothing alphanumeric to slugify.
    """
    if vault.slug_style != "kebab":
        return path
    directory, _, filename = path.rpartition("/")
    stem = filename[:-3] if filename.endswith(".md") else filename
    slug = slugify_kebab(stem)
    if slug is None:
        raise ValueError(
            f"Filename '{stem}' cannot be normalized to kebab-case (OBSIDIAN_SLUG_STYLE=kebab). "
            "It must contain at least one letter or digit."
        )
    new_filename = f"{slug}.md"
    return f"{directory}/{new_filename}" if directory else new_filename


def apply_slug_style_to_frontmatter_name(vault, content: str) -> str:
    """If OBSIDIAN_SLUG_STYLE=kebab and content has a frontmatter `name:`
    field, kebab-slugify its value in place (spec section 1, item b).
    No-op if slug_style is off, there's no frontmatter, or no `name` key.
    """
    if vault.slug_style != "kebab" or not content.startswith("---\n"):
        return content
    frontmatter, _ = vault._parse_frontmatter(content)
    name = frontmatter.get("name")
    if not name or not isinstance(name, str):
        return content
    slug = slugify_kebab(name)
    if slug is None:
        raise ValueError(
            f"Frontmatter 'name: {name}' cannot be normalized to kebab-case "
            "(OBSIDIAN_SLUG_STYLE=kebab)."
        )
    if slug == name:
        return content

    end_index = content.find("\n---\n", 4)
    if end_index == -1:
        return content
    fm_text = content[4:end_index]
    new_fm_lines = [
        f"name: {slug}" if re.match(r'^name\s*:', line) else line
        for line in fm_text.split("\n")
    ]
    return f"---\n{chr(10).join(new_fm_lines)}\n---\n{content[end_index + 5:]}"


def normalize_frontmatter_tags_for_kebab(vault, content: str) -> str:
    """If OBSIDIAN_TAG_STYLE=kebab and content has frontmatter tags,
    kebab-normalize them in place (spec section 1: "nas tags de frontmatter
    em create quando enforcement ativo"). No-op otherwise — this never
    injects a tags block where none existed.
    """
    if vault.tag_style != "kebab" or not content.startswith("---\n"):
        return content
    frontmatter, _ = vault._parse_frontmatter(content)
    raw_tags = frontmatter.get("tags", frontmatter.get("tag"))
    if not raw_tags:
        return content
    if isinstance(raw_tags, str):
        raw_tags = [raw_tags]

    # Imported lazily: organization.py imports from note_management in the
    # other direction (rename_note et al already cross-import sibling tools
    # modules this way — see link_management import at the top of
    # organization.py), so importing at call time avoids a circular import
    # at module load.
    from ..utils.vault_config import normalize_tag_kebab
    from .organization import _update_frontmatter_tags

    normalized = []
    for tag in raw_tags:
        slug = normalize_tag_kebab(str(tag).lstrip("#").strip())
        if slug is None:
            raise ValueError(
                f"Frontmatter tag '{tag}' cannot be normalized to kebab-case "
                "(OBSIDIAN_TAG_STYLE=kebab). Each '/'-separated segment must contain "
                "at least one letter or digit."
            )
        normalized.append(slug)
    return _update_frontmatter_tags(content, normalized, "replace")


async def _apply_write_checks(vault, path: str, content: str, enforce_template: bool) -> Tuple[str, List[str]]:
    """Shared write-time checks for create_note and update_note(replace):
    template conformance (only when enforce_template — create/replace, never
    edit_note_section/append), wikilink validation, and kebab tag/name
    normalization. Returns (possibly-rewritten content, warnings).
    Raises ValueError for any hard violation (strict policy, malformed
    wikilink, non-normalizable tag/name) — caller writes nothing in that case.
    """
    if enforce_template:
        check_template_conformance(vault, path, content)

    content, warnings = await validate_wikilinks_for_write(vault, content)
    content = normalize_frontmatter_tags_for_kebab(vault, content)
    content = apply_slug_style_to_frontmatter_name(vault, content)
    return content, warnings


def _size_policy_warning(vault, path: str, content: str, is_incremental: bool) -> List[str]:
    """Run check_note_size_policy and wrap a warn-level result as a
    single-item list (empty if ok/off/daily-exempt/strict-already-raised)."""
    warning = check_note_size_policy(vault, path, count_lines(content), is_incremental)
    return [warning] if warning else []


def _serialize_note_writes(func):
    """Hold the vault-wide write lock for the whole call so concurrent
    read-modify-write operations on the same note cannot lose an update
    (e.g. two edit_note_section calls dispatched together against one note).

    functools.wraps sets __wrapped__, so inspect.signature (and therefore the
    FastMCP tool schema) still reflects the original parameters."""
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        vault = get_vault()
        async with vault.write_lock:
            return await func(*args, **kwargs)
    return wrapper


async def read_note(
    path: str, 
    ctx: Optional[Context] = None
) -> dict:
    """
    Read the content and metadata of a specific note.
    
    Use this tool when you need to retrieve the full content of a note
    from the Obsidian vault. The path should be relative to the vault root.
    
    To view images embedded in a note, use the view_note_images tool.
    
    Args:
        path: Path to the note relative to vault root (e.g., "Daily/2024-01-15.md")
        ctx: MCP context for progress reporting
        
    Returns:
        Dictionary containing the note content and metadata
        
    Example:
        >>> await read_note("Projects/My Project.md", ctx=ctx)
        {
            "path": "Projects/My Project.md",
            "content": "# My Project\n\n![diagram](attachments/diagram.png)\n\nProject details...",
            "metadata": {
                "tags": ["project", "active"],
                "created": "2024-01-15T10:00:00Z",
                "modified": "2024-01-15T14:30:00Z"
            }
        }
    """
    # Validate path
    is_valid, error_msg = validate_note_path(path)
    if not is_valid:
        raise ValueError(f"Invalid path: {error_msg}")
    
    # Sanitize path
    path = sanitize_path(path)
    
    if ctx:
        ctx.info(f"Reading note: {path}")
    
    vault = get_vault()
    try:
        note = await vault.read_note(path)
    except FileNotFoundError:
        raise FileNotFoundError(ERROR_MESSAGES["note_not_found"].format(path=path))
    
    # Return standardized CRUD success structure
    return {
        "success": True,
        "path": note.path,
        "operation": "read",
        "details": {
            "content": note.content,
            "metadata": note.metadata.model_dump(exclude_none=True)
        }
    }


async def _search_and_load_image(
    image_ref: str,
    vault,
    ctx: Optional[Context] = None
) -> Optional[Dict[str, Any]]:
    """
    Search for and load a single image.
    
    Args:
        image_ref: Image reference (path or filename)
        vault: ObsidianVault instance
        ctx: Optional context for logging
        
    Returns:
        Image data dict or None if not found
    """
    try:
        if ctx:
            ctx.info(f"Loading embedded image: {image_ref}")
        
        # Try to read the image directly (with resizing for embedded images)
        try:
            image_data = await vault.read_image(image_ref, max_width=800)
        except FileNotFoundError:
            # If not found at direct path, search for it
            if ctx:
                ctx.info(f"Image not found at direct path, searching for: {image_ref}")
            
            # Extract just the filename
            filename = image_ref.split('/')[-1]
            
            # Use vault's find_image method
            found_path = await vault.find_image(filename)
            if found_path:
                if ctx:
                    ctx.info(f"Found image at: {found_path}")
                image_data = await vault.read_image(found_path, max_width=800)
            else:
                image_data = None
        
        if image_data:
            return {
                "path": image_data["path"],
                "content": image_data["content"],
                "mime_type": image_data["mime_type"]
            }
        elif ctx:
            ctx.info(f"Could not find image anywhere: {image_ref}")
            
    except Exception as e:
        # Log error but return None
        if ctx:
            ctx.info(f"Failed to load image {image_ref}: {str(e)}")
    
    return None


async def _extract_and_load_images(
    content: str, 
    vault,
    ctx: Optional[Context] = None
) -> List[Dict[str, Any]]:
    """
    Extract image references from markdown content and load them concurrently.
    
    Supports both Obsidian wiki-style (![[image.png]]) and standard markdown (![alt](image.png)) formats.
    """
    # Pattern for wiki-style embeds: ![[image.png]]
    wiki_pattern = r'!\[\[([^]]+\.(?:png|jpg|jpeg|gif|webp|svg|bmp|ico))\]\]'
    # Pattern for standard markdown: ![alt text](image.png)
    markdown_pattern = r'!\[[^\]]*\]\(([^)]+\.(?:png|jpg|jpeg|gif|webp|svg|bmp|ico))\)'
    
    # Find all image references
    image_paths = set()
    
    for match in re.finditer(wiki_pattern, content, re.IGNORECASE):
        image_paths.add(match.group(1))
    
    for match in re.finditer(markdown_pattern, content, re.IGNORECASE):
        image_paths.add(match.group(1))
    
    # Load all images concurrently for better performance
    if not image_paths:
        return []
    
    # Create tasks for all images
    tasks = [_search_and_load_image(image_ref, vault, ctx) for image_ref in image_paths]
    
    # Execute all tasks concurrently
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    # Filter out None results and exceptions
    images = []
    for result in results:
        if result and not isinstance(result, Exception):
            images.append(result)
    
    return images


@_serialize_note_writes
async def create_note(
    path: str,
    content: str,
    overwrite: bool = False,
    ctx: Optional[Context] = None
) -> dict:
    """
    Create a new note or update an existing one.
    
    Use this tool to create new notes in the Obsidian vault. By default,
    it will fail if a note already exists at the specified path unless
    overwrite is set to true.
    
    Args:
        path: Path where the note should be created (e.g., "Ideas/New Idea.md")
        content: Markdown content for the note
        overwrite: Whether to overwrite if the note already exists (default: false)
        ctx: MCP context for progress reporting
        
    Returns:
        Dictionary containing the created note information
        
    Example:
        >>> await create_note(
        ...     "Ideas/AI Integration.md",
        ...     "# AI Integration Ideas\n\n- Use LLMs for note summarization\n- Auto-tagging",
        ...     ctx=ctx
        ... )
        {
            "path": "Ideas/AI Integration.md",
            "created": true,
            "metadata": {"tags": [], "created": "2024-01-15T15:00:00Z"}
        }
    """
    # Validate path
    is_valid, error_msg = validate_note_path(path)
    if not is_valid:
        raise ValueError(f"Invalid path: {error_msg}")

    # Validate content
    is_valid, error_msg = validate_content(content)
    if not is_valid:
        raise ValueError(error_msg)

    # Sanitize path
    path = sanitize_path(path)

    vault = get_vault()
    path = apply_slug_style_to_path(vault, path)  # OBSIDIAN_SLUG_STYLE=kebab

    if ctx:
        ctx.info(f"Creating note: {path}")

    # Template conformance (folder rule, if any) + wikilink validation +
    # kebab tag/name normalization. Raises ValueError before anything is
    # written if a hard check fails (strict template/wikilink violation,
    # non-normalizable tag/name).
    content, wikilink_warnings = await _apply_write_checks(vault, path, content, enforce_template=True)
    warnings = wikilink_warnings + _size_policy_warning(vault, path, content, is_incremental=False)

    # Create the note
    try:
        note = await vault.write_note(path, content, overwrite=overwrite)
        created = True
    except FileExistsError:
        if not overwrite:
            raise FileExistsError(ERROR_MESSAGES["overwrite_protection"].format(path=path))
        # If we get here, overwrite is True but file exists - this shouldn't happen
        # with our write_note implementation, but handle it just in case
        note = await vault.write_note(path, content, overwrite=True)
        created = False

    # Return standardized CRUD success structure
    result = {
        "success": True,
        "path": note.path,
        "operation": "created" if created else "overwritten",
        "details": {
            "created": created,
            "overwritten": not created,
            "metadata": note.metadata.model_dump(exclude_none=True)
        }
    }
    if warnings:
        result["warnings"] = warnings
    return result


@_serialize_note_writes
async def update_note(
    path: str,
    content: str,
    create_if_not_exists: bool = False,
    merge_strategy: str = "replace",
    ctx: Optional[Context] = None
) -> dict:
    """
    Update the content of an existing note.
    
    Use this tool to modify the content of an existing note while preserving
    its metadata and location. Optionally create the note if it doesn't exist.
    
    IMPORTANT: This tool REPLACES the entire note content by default. Always
    read the note first with read_note_tool if you want to preserve existing content.
    
    Args:
        path: Path to the note to update
        content: New markdown content for the note (REPLACES existing content)
        create_if_not_exists: Create the note if it doesn't exist (default: false)
        merge_strategy: How to handle updates - "replace" (default) or "append"
        ctx: MCP context for progress reporting
        
    Returns:
        Dictionary containing update status
        
    Example:
        >>> await update_note(
        ...     "Projects/My Project.md",
        ...     "# My Project\\n\\n## Updated Status\\nProject is now complete!",
        ...     ctx=ctx
        ... )
        {
            "path": "Projects/My Project.md",
            "updated": true,
            "created": false,
            "metadata": {"tags": ["project", "completed"], "modified": "2024-01-15T16:00:00Z"}
        }
    """
    # Validate path
    is_valid, error_msg = validate_note_path(path)
    if not is_valid:
        raise ValueError(f"Invalid path: {error_msg}")
    
    # Sanitize path
    path = sanitize_path(path)
    
    if ctx:
        ctx.info(f"Updating note: {path}")
    
    vault = get_vault()
    
    # Try to read existing note
    try:
        existing_note = await vault.read_note(path)
        note_exists = True
    except FileNotFoundError:
        note_exists = False
        existing_note = None
    
    if not note_exists:
        if create_if_not_exists:
            # A first-time write is a full-content write, same as
            # create_note: template conformance + wikilink validation +
            # kebab tag/name normalization all apply.
            content, wikilink_warnings = await _apply_write_checks(vault, path, content, enforce_template=True)
            warnings = wikilink_warnings + _size_policy_warning(vault, path, content, is_incremental=False)

            note = await vault.write_note(path, content, overwrite=False)
            # Return standardized CRUD success structure
            result = {
                "success": True,
                "path": note.path,
                "operation": "created",
                "details": {
                    "updated": False,
                    "created": True,
                    "metadata": note.metadata.model_dump(exclude_none=True)
                }
            }
            if warnings:
                result["warnings"] = warnings
            return result
        else:
            raise FileNotFoundError(ERROR_MESSAGES["note_not_found"].format(path=path))

    # Handle merge strategies
    if merge_strategy == "append":
        # Incremental edit — exempt from template conformance (spec section
        # 3). Wikilink validation and the size check run against just the
        # appended fragment / resulting total, matching edit_note_section.
        content, wikilink_warnings = await validate_wikilinks_for_write(vault, content)
        final_content = existing_note.content.rstrip() + "\n\n" + content
        warnings = wikilink_warnings + _size_policy_warning(vault, path, final_content, is_incremental=True)
    elif merge_strategy == "replace":
        content, wikilink_warnings = await _apply_write_checks(vault, path, content, enforce_template=True)
        final_content = content
        warnings = wikilink_warnings + _size_policy_warning(vault, path, final_content, is_incremental=False)
    else:
        raise ValueError(f"Invalid merge_strategy: {merge_strategy}. Must be 'replace' or 'append'")

    # Update existing note
    note = await vault.write_note(path, final_content, overwrite=True)

    # Return standardized CRUD success structure
    result = {
        "success": True,
        "path": note.path,
        "operation": "updated",
        "details": {
            "updated": True,
            "created": False,
            "merge_strategy": merge_strategy,
            "metadata": note.metadata.model_dump(exclude_none=True)
        }
    }
    if warnings:
        result["warnings"] = warnings
    return result


@_serialize_note_writes
async def edit_note_section(
    path: str,
    section_identifier: str,
    content: str,
    operation: str = "insert_after",
    create_if_missing: bool = False,
    ctx: Optional[Context] = None
) -> dict:
    """
    Edit a specific section of a note.
    
    Use this tool to insert, replace, or append content at specific sections
    identified by markdown headings.
    
    Args:
        path: Path to the note to edit
        section_identifier: Markdown heading to identify the section (e.g., "## Tasks", "### Status")
        content: Content to insert/replace/append
        operation: One of "insert_after", "insert_before", "replace", "append_to_section"
        create_if_missing: Create the section at the end if it doesn't exist
        ctx: MCP context for progress reporting
        
    Returns:
        Dictionary containing edit status and details
        
    Example:
        >>> await edit_note_section(
        ...     "Projects/Project.md",
        ...     "## Status Updates",
        ...     "- 2024-01-15: Completed phase 1",
        ...     operation="append_to_section"
        ... )
        {
            "success": true,
            "path": "Projects/Project.md",
            "operation": "section_edit",
            "section": "## Status Updates",
            "edit_type": "append_to_section",
            "section_found": true,
            "section_created": false
        }
    """
    # Validate path
    is_valid, error_msg = validate_note_path(path)
    if not is_valid:
        raise ValueError(f"Invalid path: {error_msg}")
    
    # Validate operation
    valid_operations = ["insert_after", "insert_before", "replace", "append_to_section"]
    if operation not in valid_operations:
        raise ValueError(f"Invalid operation: {operation}. Must be one of {valid_operations}")
    
    # Sanitize path
    path = sanitize_path(path)
    
    if ctx:
        ctx.info(f"Editing section '{section_identifier}' in: {path}")
    
    vault = get_vault()
    
    # Read existing note
    try:
        existing_note = await vault.read_note(path)
        note_content = existing_note.content
    except FileNotFoundError:
        raise FileNotFoundError(ERROR_MESSAGES["note_not_found"].format(path=path))

    # Incremental edit — exempt from template conformance (spec section 3).
    # Wikilink validation runs against just the inserted fragment.
    content, warnings = await validate_wikilinks_for_write(vault, content)

    # Parse the section identifier to extract heading level and text
    heading_match = re.match(r'^(#{1,6})\s+(.+)$', section_identifier)
    if not heading_match:
        raise ValueError(f"Invalid section identifier: {section_identifier}. Must be a markdown heading (e.g., '## Section Name')")
    
    heading_level = len(heading_match.group(1))
    heading_text = heading_match.group(2).strip()
    
    # Find the section in the content
    lines = note_content.split('\n')
    section_start = None
    section_end = None
    
    # Find the section
    for i, line in enumerate(lines):
        line_match = re.match(r'^(#{1,6})\s+(.+)$', line)
        if line_match:
            line_level = len(line_match.group(1))
            line_text = line_match.group(2).strip()
            
            # Found our section
            if (
                unicodedata.normalize("NFC", line_text.lower())
                == unicodedata.normalize("NFC", heading_text.lower())
                and line_level == heading_level
            ):
                section_start = i
                
                # Find where this section ends (next heading of same or higher level, or end of file)
                for j in range(i + 1, len(lines)):
                    next_match = re.match(r'^(#{1,6})\s+', lines[j])
                    if next_match:
                        next_level = len(next_match.group(1))
                        if next_level <= heading_level:
                            section_end = j
                            break
                
                # If no end found, section goes to end of file
                if section_end is None:
                    section_end = len(lines)
                break
    
    # Handle missing section
    if section_start is None:
        if create_if_missing:
            # Add section at the end
            if not note_content.endswith('\n'):
                note_content += '\n'
            note_content += f"\n{section_identifier}\n\n{content}"

            warnings = warnings + _size_policy_warning(vault, path, note_content, is_incremental=True)

            # Save the updated note
            await vault.write_note(path, note_content, overwrite=True)

            result = {
                "success": True,
                "path": path,
                "operation": "section_edit",
                "section": section_identifier,
                "edit_type": operation,
                "section_found": False,
                "section_created": True
            }
            if warnings:
                result["warnings"] = warnings
            return result
        else:
            raise ValueError(f"Section '{section_identifier}' not found in {path}")
    
    # Perform the requested operation
    if operation == "insert_after":
        # Insert content right after the heading
        insert_pos = section_start + 1
        # Always add a blank line after the heading if one doesn't exist
        if insert_pos >= len(lines) or lines[insert_pos].strip():
            lines.insert(insert_pos, "")
        # Insert the content after the blank line
        lines.insert(insert_pos + 1, content)
        
    elif operation == "insert_before":
        # Insert content right before the heading
        insert_pos = section_start
        # Add a blank line before content if previous line has content
        if insert_pos > 0 and lines[insert_pos - 1].strip():
            lines.insert(insert_pos, "")
            insert_pos += 1
        lines.insert(insert_pos, content)
        # Always add a blank line after the content before the heading
        lines.insert(insert_pos + 1, "")
            
    elif operation == "replace":
        # Replace the entire section (including heading)
        del lines[section_start:section_end]
        lines.insert(section_start, content)
        
    elif operation == "append_to_section":
        # Append to the end of the section (before the next section or EOF)
        insert_pos = section_end
        # Move back to skip empty lines at the end of section
        while insert_pos > section_start + 1 and not lines[insert_pos - 1].strip():
            insert_pos -= 1
        
        # Add content with appropriate spacing
        if insert_pos > 0 and lines[insert_pos - 1].strip():
            lines.insert(insert_pos, "")
            insert_pos += 1
        lines.insert(insert_pos, content)
    
    # Reconstruct the content
    new_content = '\n'.join(lines)

    warnings = warnings + _size_policy_warning(vault, path, new_content, is_incremental=True)

    # Save the updated note
    await vault.write_note(path, new_content, overwrite=True)

    result = {
        "success": True,
        "path": path,
        "operation": "section_edit",
        "section": section_identifier,
        "edit_type": operation,
        "section_found": True,
        "section_created": False
    }
    if warnings:
        result["warnings"] = warnings
    return result


@_serialize_note_writes
async def delete_note(path: str, ctx: Optional[Context] = None) -> dict:
    """
    Delete a note from the vault.
    
    Use this tool to permanently remove a note from the Obsidian vault.
    This action cannot be undone.
    
    Args:
        path: Path to the note to delete
        ctx: MCP context for progress reporting
        
    Returns:
        Dictionary containing deletion status
        
    Example:
        >>> await delete_note("Temporary/Draft.md", ctx)
        {"path": "Temporary/Draft.md", "deleted": true}
    """
    # Validate path
    is_valid, error_msg = validate_note_path(path)
    if not is_valid:
        raise ValueError(f"Invalid path: {error_msg}")
    
    # Sanitize path
    path = sanitize_path(path)
    
    if ctx:
        ctx.info(f"Deleting note: {path}")
    
    vault = get_vault()
    
    try:
        await vault.delete_note(path)
        deleted = True
    except FileNotFoundError:
        raise FileNotFoundError(ERROR_MESSAGES["note_not_found"].format(path=path))
    
    # Return standardized CRUD success structure
    return {
        "success": True,
        "path": path,
        "operation": "deleted",
        "details": {
            "deleted": True
        }
    }