"""Find orphaned notes in the vault."""

import logging
import re
from typing import List, Dict, Any, Optional, Literal
from pathlib import Path
from datetime import datetime, timedelta
from ..utils.filesystem import get_vault
from ..utils import is_markdown_file
from .link_management import get_backlinks, get_outgoing_links

logger = logging.getLogger(__name__)


async def find_orphaned_notes(
    orphan_type: str = "no_backlinks",
    exclude_folders: Optional[List[str]] = None,
    min_age_days: Optional[int] = None,
    ctx=None
) -> Dict[str, Any]:
    """
    Find orphaned notes based on specified criteria.
    
    Args:
        orphan_type: Type of orphaned notes to find
        exclude_folders: Folders to exclude from search
        min_age_days: Only include notes older than X days
        ctx: MCP context for progress reporting
        
    Returns:
        Dictionary containing orphaned notes and statistics
    """
    vault = get_vault()
    
    # Default exclusions if not specified
    if exclude_folders is None:
        exclude_folders = ["Templates", "Archive", "Daily"]
    
    if ctx:
        ctx.info(f"Finding orphaned notes of type: {orphan_type}")
    
    # Get all notes
    all_notes = await vault.list_notes(recursive=True)
    orphaned_notes = []
    
    # Calculate date threshold if min_age_days is specified
    date_threshold = None
    if min_age_days is not None:
        date_threshold = datetime.now() - timedelta(days=min_age_days)
    
    # Process each note
    total_notes = len(all_notes)
    for i, note_info in enumerate(all_notes):
        path = note_info["path"]
        
        # Skip excluded folders
        if any(path.startswith(folder + "/") or path.startswith(folder + "\\") 
               for folder in exclude_folders):
            continue
        
        # Skip if file is in root and we're excluding root level files
        if "/" not in path and "\\" not in path:
            # Root level file - you might want to handle these differently
            pass
        
        try:
            # Read note to get full information
            note = await vault.read_note(path)
            
            # Check age if threshold is set
            if date_threshold and note.metadata.modified:
                # Handle both timezone-aware and naive datetime strings
                mod_str = note.metadata.modified
                if mod_str.endswith('Z'):
                    mod_str = mod_str[:-1] + '+00:00'
                try:
                    # Try to parse with timezone
                    mod_time = datetime.fromisoformat(mod_str)
                except ValueError:
                    # If that fails, parse as naive and assume local timezone
                    mod_time = datetime.fromisoformat(mod_str.split('+')[0].split('.')[0])
                
                # Make date_threshold timezone-naive for comparison
                if mod_time.tzinfo is not None:
                    mod_time = mod_time.replace(tzinfo=None)
                
                if mod_time > date_threshold:
                    continue  # Skip recent notes
            
            # Check orphan criteria
            is_orphaned = False
            orphan_reason = ""
            
            if orphan_type == "no_backlinks":
                # Get backlinks for this note
                backlinks_result = await get_backlinks(path)
                backlinks = backlinks_result.get("backlinks", [])
                if not backlinks or len(backlinks) == 0:
                    is_orphaned = True
                    orphan_reason = "No incoming links"
                    
            elif orphan_type == "no_links":
                # Check both incoming and outgoing links
                backlinks_result = await get_backlinks(path)
                backlinks = backlinks_result.get("backlinks", [])
                outgoing_result = await get_outgoing_links(path)
                outgoing = outgoing_result.get("outgoing_links", [])
                if (not backlinks or len(backlinks) == 0) and (not outgoing or len(outgoing) == 0):
                    is_orphaned = True
                    orphan_reason = "No incoming or outgoing links"
                    
            elif orphan_type == "no_tags":
                # Check if note has any tags
                if not note.metadata.tags or len(note.metadata.tags) == 0:
                    is_orphaned = True
                    orphan_reason = "No tags"
                    
            elif orphan_type == "no_metadata":
                # Check if note has any frontmatter properties (beyond basic ones)
                frontmatter = note.metadata.frontmatter
                # Remove system properties
                user_properties = {k: v for k, v in frontmatter.items() 
                                 if k not in ["tags", "aliases", "cssclass"]}
                if not user_properties:
                    is_orphaned = True
                    orphan_reason = "No metadata properties"
                    
            elif orphan_type == "isolated":
                # Multiple criteria: no links AND no tags
                backlinks_result = await get_backlinks(path)
                backlinks = backlinks_result.get("backlinks", [])
                outgoing_result = await get_outgoing_links(path)
                outgoing = outgoing_result.get("outgoing_links", [])
                has_no_links = (not backlinks or len(backlinks) == 0) and (not outgoing or len(outgoing) == 0)
                has_no_tags = not note.metadata.tags or len(note.metadata.tags) == 0
                
                if has_no_links and has_no_tags:
                    is_orphaned = True
                    orphan_reason = "No links and no tags"
            
            if is_orphaned:
                # Calculate size and word count
                content = note.content
                size_bytes = len(content.encode('utf-8'))
                word_count = len(content.split())
                
                orphaned_notes.append({
                    "path": path,
                    "reason": orphan_reason,
                    "modified": note.metadata.modified.isoformat() if note.metadata.modified else None,
                    "size": size_bytes,
                    "word_count": word_count
                })
                
        except Exception as e:
            logger.warning(f"Error processing note {path}: {e}")
            continue
        
        # Progress reporting
        if ctx and (i + 1) % 50 == 0:
            ctx.info(f"Processed {i + 1}/{total_notes} notes...")
    
    # Sort by modified date (oldest first)
    orphaned_notes.sort(key=lambda x: x.get("modified", ""), reverse=False)
    
    # Prepare summary statistics
    stats = {
        "total_notes_scanned": total_notes,
        "excluded_folders": exclude_folders,
        "orphan_type": orphan_type,
        "min_age_days": min_age_days
    }
    
    if min_age_days:
        stats["date_threshold"] = date_threshold.isoformat()
    
    return {
        "orphaned_notes": orphaned_notes,
        "count": len(orphaned_notes),
        "stats": stats
    }