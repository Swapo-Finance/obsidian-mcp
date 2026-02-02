"""Filesystem operations for Obsidian vault access."""

import os
import re
import json
import asyncio
import aiofiles
import yaml
import base64
import io
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any, List, Tuple
from PIL import Image
from ..models import Note, NoteMetadata
from .persistent_index import PersistentSearchIndex

logger = logging.getLogger(__name__)


class ObsidianVault:
    """Direct filesystem access to Obsidian vault."""
    
    def __init__(self, vault_path: Optional[str] = None):
        """
        Initialize vault access.
        
        Args:
            vault_path: Path to vault. If not provided, uses OBSIDIAN_VAULT_PATH env var.
        """
        self.vault_path = Path(vault_path or os.getenv("OBSIDIAN_VAULT_PATH", ""))
        
        if not self.vault_path:
            raise ValueError(
                "Vault path not provided. Set OBSIDIAN_VAULT_PATH environment variable "
                "or pass vault_path parameter."
            )
        
        if not self.vault_path.exists():
            raise ValueError(f"Vault path does not exist: {self.vault_path}")
        
        if not self.vault_path.is_dir():
            raise ValueError(f"Vault path is not a directory: {self.vault_path}")
        
        # Initialize SQLite search index
        self.persistent_index: Optional[PersistentSearchIndex] = None
        self._index_timestamp: Optional[float] = None
        self._index_lock = asyncio.Lock()
        
        # Track if persistent index has been initialized
        self._persistent_index_initialized = False
        
        # Store last search metadata for access by tools
        self._last_search_metadata: Optional[Dict[str, Any]] = None
        
        # Track if an index update is in progress
        self._index_update_in_progress = False
        self._index_update_task: Optional[asyncio.Task] = None
        
        # Configuration for index updates
        self._index_update_interval = int(os.getenv("OBSIDIAN_INDEX_UPDATE_INTERVAL", "300"))  # 5 minutes default
        self._index_batch_size = int(os.getenv("OBSIDIAN_INDEX_BATCH_SIZE", "50"))
        self._auto_index_update = os.getenv("OBSIDIAN_AUTO_INDEX_UPDATE", "true").lower() in ("true", "1", "yes", "on")
    
    def _ensure_safe_path(self, path: str) -> Path:
        """
        Ensure the path is safe and within the vault.
        
        Args:
            path: Relative path within vault
            
        Returns:
            Full path object
            
        Raises:
            ValueError: If path is unsafe
        """
        # Normalize path separators for cross-platform compatibility
        path = path.replace('\\', '/')
        
        # Remove any leading/trailing slashes
        path = path.strip("/")
        
        # Validate path components
        parts = path.split('/')
        for part in parts:
            if part in ('..', '.', '') or part.startswith('.'):
                raise ValueError(f"Invalid path component: {part}")
            # Check for invalid characters
            if any(char in part for char in '<>:"|?*'):
                raise ValueError(f"Invalid characters in path: {part}")
        
        # Convert to Path object
        full_path = self.vault_path / path
        
        # Resolve to absolute path and check it's within vault
        try:
            resolved = full_path.resolve()
            resolved.relative_to(self.vault_path.resolve())
        except (ValueError, RuntimeError):
            raise ValueError(f"Path escapes vault: {path}")
        
        return resolved
    
    def _get_absolute_path(self, path: str) -> Path:
        """
        Get absolute path for reading existing files (more lenient validation).
        Only checks for directory traversal, not character restrictions.
        
        Args:
            path: Relative path within vault
            
        Returns:
            Absolute Path object
            
        Raises:
            ValueError: If path escapes vault
        """
        # Normalize path separators
        normalized = path.replace('\\', '/')
        
        # Basic security check - no directory traversal
        parts = normalized.split('/')
        for part in parts:
            if part in ('..', '.', ''):
                raise ValueError(f"Invalid path component: {part}")
        
        # Convert to Path object
        full_path = self.vault_path / path
        
        # Resolve to absolute path and check it's within vault
        try:
            resolved = full_path.resolve()
            resolved.relative_to(self.vault_path.resolve())
        except (ValueError, RuntimeError):
            raise ValueError(f"Path escapes vault: {path}")
        
        return resolved
    
    def _parse_frontmatter(self, content: str) -> Tuple[Dict[str, Any], str]:
        """
        Parse YAML frontmatter from markdown content.
        
        Args:
            content: Full markdown content
            
        Returns:
            Tuple of (frontmatter dict, content without frontmatter)
        """
        frontmatter = {}
        clean_content = content
        
        # Check if content starts with frontmatter
        if content.startswith("---\n"):
            try:
                # Find the closing ---
                end_index = content.find("\n---\n", 4)
                if end_index != -1:
                    # Extract frontmatter text
                    fm_text = content[4:end_index]
                    
                    # Parse YAML properly
                    try:
                        frontmatter = yaml.safe_load(fm_text) or {}
                        # Ensure it's a dict
                        if not isinstance(frontmatter, dict):
                            frontmatter = {}
                    except yaml.YAMLError:
                        # Fall back to simple parsing for invalid YAML
                        frontmatter = {}
                        for line in fm_text.split('\n'):
                            if ':' in line and not line.strip().startswith('#'):
                                key, value = line.split(':', 1)
                                key = key.strip()
                                value = value.strip()
                                if value:
                                    frontmatter[key] = value
                    
                    # Remove frontmatter from content
                    clean_content = content[end_index + 4:].lstrip()
            except Exception as e:
                # If parsing fails, just return original content
                # Log the error for debugging
                pass
        
        return frontmatter, clean_content
    
    def _normalize_frontmatter(self, frontmatter: Dict[str, Any]) -> Dict[str, Any]:
        """
        Normalize frontmatter to handle legacy property names.
        
        Migrations:
        - tag -> tags (as list)
        - alias -> aliases (as list)
        - cssClass -> cssclasses (as list)
        
        Args:
            frontmatter: Raw frontmatter dict
            
        Returns:
            Normalized frontmatter dict
        """
        normalized = frontmatter.copy()
        
        # Handle tag/tags migration
        if "tag" in normalized and "tags" not in normalized:
            tag_value = normalized.pop("tag")
            if isinstance(tag_value, str):
                normalized["tags"] = [tag_value]
            elif isinstance(tag_value, list):
                normalized["tags"] = tag_value
        
        # Handle alias/aliases migration
        if "alias" in normalized and "aliases" not in normalized:
            alias_value = normalized.pop("alias")
            if isinstance(alias_value, str):
                normalized["aliases"] = [alias_value]
            elif isinstance(alias_value, list):
                normalized["aliases"] = alias_value
        
        # Handle cssClass/cssclasses migration
        if "cssClass" in normalized and "cssclasses" not in normalized:
            css_value = normalized.pop("cssClass")
            if isinstance(css_value, str):
                normalized["cssclasses"] = [css_value]
            elif isinstance(css_value, list):
                normalized["cssclasses"] = css_value
        
        # Ensure plural properties are lists
        for key in ["tags", "aliases", "cssclasses"]:
            if key in normalized:
                value = normalized[key]
                if isinstance(value, str):
                    normalized[key] = [value]
                elif not isinstance(value, list):
                    normalized[key] = []
        
        return normalized
    
    def _extract_tags(self, content: str, frontmatter: Dict[str, Any]) -> List[str]:
        """
        Extract all tags from content and frontmatter.
        
        Args:
            content: Markdown content
            frontmatter: Parsed frontmatter
            
        Returns:
            List of unique tags (without # prefix)
        """
        tags = set()
        
        # Get tags from frontmatter (supports both "tag" and "tags")
        fm_tags = frontmatter.get("tags", frontmatter.get("tag", []))
        if isinstance(fm_tags, str):
            fm_tags = [fm_tags]
        elif not isinstance(fm_tags, list):
            fm_tags = []
        
        for tag in fm_tags:
            if isinstance(tag, str):
                tags.add(tag.lstrip('#'))
        
        # Remove code blocks from content before extracting tags
        # Remove fenced code blocks
        clean_content = re.sub(r'```[\s\S]*?```', '', content)
        # Remove inline code
        clean_content = re.sub(r'`[^`]+`', '', clean_content)
        
        # Find inline tags in cleaned content
        # More strict pattern: tag must be preceded by whitespace or start of line
        # Support hierarchical tags with forward slashes (e.g., #parent/child/grandchild)
        inline_tags = re.findall(r'(?:^|[\s\n])#([a-zA-Z0-9_\-]+(?:/[a-zA-Z0-9_\-]+)*)(?=\s|$)', clean_content, re.MULTILINE)
        tags.update(inline_tags)
        
        return sorted(list(tags))
    
    async def read_note(self, path: str) -> Note:
        """
        Read a note from the vault.
        
        Args:
            path: Path to note relative to vault root
            
        Returns:
            Note object with content and metadata
        """
        # Ensure .md extension
        if not path.endswith('.md'):
            path += '.md'
        
        # Use lenient path validation for reading existing files
        full_path = self._get_absolute_path(path)
        
        if not full_path.exists():
            raise FileNotFoundError(f"Note not found: {path}")
        
        # Check file size to prevent memory issues
        stat = full_path.stat()
        max_size = 10 * 1024 * 1024  # 10MB limit
        if stat.st_size > max_size:
            raise ValueError(f"File too large: {stat.st_size} bytes (max: {max_size} bytes)")
        
        # Read file content asynchronously
        async with aiofiles.open(full_path, 'r', encoding='utf-8') as f:
            content = await f.read()
        
        # Parse frontmatter
        frontmatter, clean_content = self._parse_frontmatter(content)
        
        # Normalize frontmatter for legacy property names
        normalized_frontmatter = self._normalize_frontmatter(frontmatter)
        
        # Extract tags
        tags = self._extract_tags(clean_content, normalized_frontmatter)
        
        # Get file stats
        stat = full_path.stat()
        
        # Create metadata
        metadata = NoteMetadata(
            tags=tags,
            aliases=normalized_frontmatter.get("aliases", []),
            created=datetime.fromtimestamp(stat.st_ctime),
            modified=datetime.fromtimestamp(stat.st_mtime),
            frontmatter=normalized_frontmatter
        )
        
        return Note(
            path=path,
            content=content,
            metadata=metadata
        )
    
    async def write_note(self, path: str, content: str, overwrite: bool = False) -> Note:
        """
        Write a note to the vault.
        
        Args:
            path: Path to note relative to vault root
            content: Markdown content
            overwrite: Whether to overwrite existing file
            
        Returns:
            Created/updated Note object
        """
        # Ensure .md extension
        if not path.endswith('.md'):
            path += '.md'
        
        full_path = self._ensure_safe_path(path)
        
        # Check if exists
        if full_path.exists() and not overwrite:
            raise FileExistsError(f"Note already exists: {path}")
        
        # Create parent directories if needed
        full_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Write content asynchronously
        async with aiofiles.open(full_path, 'w', encoding='utf-8') as f:
            await f.write(content)
        
        # Return the newly created note
        return await self.read_note(path)
    
    async def delete_note(self, path: str) -> bool:
        """
        Delete a note from the vault.
        
        Args:
            path: Path to note relative to vault root
            
        Returns:
            True if deleted successfully
        """
        # Ensure .md extension
        if not path.endswith('.md'):
            path += '.md'
        
        full_path = self._ensure_safe_path(path)
        
        if not full_path.exists():
            raise FileNotFoundError(f"Note not found: {path}")
        
        # Delete the file
        full_path.unlink()
        return True
    
    async def _initialize_persistent_index(self) -> None:
        """Initialize the persistent search index if not already done."""
        if not self._persistent_index_initialized:
            try:
                self.persistent_index = PersistentSearchIndex(self.vault_path)
                await self.persistent_index.initialize()
                self._persistent_index_initialized = True
                logger.info("Persistent search index initialized")
            except PermissionError as e:
                raise RuntimeError(
                    f"Permission denied when accessing search index. "
                    f"To fix: Ensure '{self.vault_path}/.obsidian' is writable: "
                    f"chmod +w '{self.vault_path}/.obsidian'. "
                    f"Original error: {e}"
                )
            except OSError as e:
                if "read-only" in str(e).lower():
                    raise RuntimeError(
                        f"Cannot create search index: vault appears to be read-only. "
                        f"Please ensure '{self.vault_path}' is writable."
                    )
                else:
                    raise RuntimeError(f"File system error: {e}")
            except Exception as e:
                raise RuntimeError(
                    f"Failed to initialize search index: {type(e).__name__}: {e}. "
                    f"This may be due to: 1) Missing aiosqlite package, "
                    f"2) Corrupted database file, 3) Incompatible Python version"
                )
    

    def _start_background_index_update(self) -> None:
        """Start a background task to update the search index."""
        if self._index_update_in_progress:
            logger.warning("Index update already in progress, skipping")
            return
        
        # Cancel any existing update task
        if self._index_update_task and not self._index_update_task.done():
            self._index_update_task.cancel()
        
        # Start new background update
        self._index_update_task = asyncio.create_task(self._update_search_index_async())
        logger.info("Started background index update task")
    
    async def _update_search_index_async(self) -> None:
        """Async wrapper for index update with error handling."""
        try:
            self._index_update_in_progress = True
            await self._update_search_index()
        except Exception as e:
            logger.error(f"Background index update failed: {e}")
        finally:
            self._index_update_in_progress = False
            logger.info("Background index update completed")
    
    async def _update_search_index(self) -> None:
        """Update the search index with current vault content."""
        import time
        
        # Initialize persistent index if needed
        if not self._persistent_index_initialized:
            await self._initialize_persistent_index()
        
        async with self._index_lock:
            # Use persistent index with incremental updates
            await self._update_persistent_index()
            self._index_timestamp = time.time()
    
    
    async def _update_persistent_index(self) -> None:
        """Update the persistent search index with incremental updates."""
        existing_files = set()
        files_to_process = []
        
        # First, collect all markdown files
        logger.info("Scanning vault for markdown files...")
        try:
            all_files = list(self.vault_path.rglob("*.md"))
            logger.info(f"Found {len(all_files)} markdown files in vault")
        except Exception as e:
            logger.error(f"Failed to scan vault: {e}")
            return
        
        # Check which files need updating
        for md_file in all_files:
            try:
                stat = md_file.stat()
                rel_path = str(md_file.relative_to(self.vault_path))
                existing_files.add(rel_path)
                
                # Check if file needs updating
                if await self.persistent_index.needs_update(rel_path, stat.st_mtime, stat.st_size):
                    files_to_process.append((md_file, rel_path, stat))
            except Exception as e:
                logger.error(f"Failed to check file {md_file}: {e}")
                continue
        
        logger.info(f"{len(files_to_process)} files need indexing")
        
        # Process files in batches
        for i in range(0, len(files_to_process), self._index_batch_size):
            batch = files_to_process[i:i + self._index_batch_size]
            batch_end = min(i + self._index_batch_size, len(files_to_process))
            logger.info(f"Processing batch {i+1}-{batch_end} of {len(files_to_process)} files")
            
            for md_file, rel_path, stat in batch:
                try:
                    # Read content
                    async with aiofiles.open(md_file, 'r', encoding='utf-8') as f:
                        content = await f.read()
                    
                    # Extract metadata
                    metadata = self._extract_file_metadata(content)
                    
                    # Index the file
                    await self.persistent_index.index_file(
                        rel_path, content, stat.st_mtime, stat.st_size, metadata
                    )
                    
                    logger.debug(f"Indexed: {rel_path}")
                except Exception as e:
                    logger.error(f"Failed to index {md_file}: {e}")
                    continue
            
            # Yield control periodically to prevent blocking
            await asyncio.sleep(0.1)
        
        # Remove orphaned entries
        logger.info("Cleaning up orphaned index entries...")
        await self.persistent_index.clear_orphaned_entries(existing_files)
        logger.info("Index update completed")
    
    def _extract_file_metadata(self, content: str) -> Dict[str, Any]:
        """Extract metadata from file content (tags, frontmatter, etc.)."""
        metadata = {}
        
        # Extract frontmatter
        if content.startswith('---\n'):
            try:
                end_index = content.find('\n---\n', 4)
                if end_index > 0:
                    frontmatter_text = content[4:end_index]
                    frontmatter = yaml.safe_load(frontmatter_text) or {}
                    # Convert dates and other non-serializable objects to strings
                    metadata['frontmatter'] = self._serialize_metadata(frontmatter)
            except:
                pass
        
        # Extract tags
        tags = set()
        # Frontmatter tags
        if 'frontmatter' in metadata and 'tags' in metadata['frontmatter']:
            fm_tags = metadata['frontmatter']['tags']
            if isinstance(fm_tags, list):
                tags.update(fm_tags)
            elif isinstance(fm_tags, str):
                tags.add(fm_tags)
        
        # Inline tags
        tag_pattern = r'#([a-zA-Z0-9_\-/]+)'
        for match in re.finditer(tag_pattern, content):
            tags.add(match.group(1))
        
        metadata['tags'] = list(tags)
        
        return metadata
    
    def _serialize_metadata(self, obj: Any) -> Any:
        """Convert non-serializable objects to JSON-serializable format."""
        if isinstance(obj, (datetime, type(datetime.now().date()))):
            return obj.isoformat()
        elif isinstance(obj, dict):
            return {k: self._serialize_metadata(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [self._serialize_metadata(v) for v in obj]
        else:
            return obj
    
    async def search_notes(self, query: str, context_length: int = 100, max_results: int = 50) -> List[Dict[str, Any]]:
        """
        Search for notes containing query text using indexed search.
        
        Args:
            query: Search query
            context_length: Characters to show around match
            max_results: Maximum number of results to return
            
        Returns:
            List of search results
            
        Note: Search metadata (total_count, truncated) is stored in self._last_search_metadata
        """
        import time
        
        # Initialize persistent index if needed (but not initialized)
        if not self._persistent_index_initialized:
            await self._initialize_persistent_index()
        
        # Check if we should update the index
        should_update = False
        if self._auto_index_update and not self._index_update_in_progress:
            if self._index_timestamp is None or (time.time() - self._index_timestamp) > self._index_update_interval:
                should_update = True
                logger.info(f"Index is stale (last updated: {self._index_timestamp}), scheduling update")
        
        # Start background index update if needed (non-blocking)
        if should_update:
            self._start_background_index_update()
        elif self._index_update_in_progress:
            logger.info("Index update already in progress, using current index")
        
        # Use persistent index
        return await self._search_with_persistent_index(query, context_length, max_results)
    
    def get_last_search_metadata(self) -> Optional[Dict[str, Any]]:
        """
        Get metadata from the last search operation.
        
        Returns:
            Dictionary with total_count, truncated, and limit, or None if no search has been performed
        """
        return self._last_search_metadata
    
    async def _search_with_persistent_index(self, query: str, context_length: int, max_results: int) -> List[Dict[str, Any]]:
        """Search using the persistent SQLite index."""
        # Use simple search for now (FTS5 search can be added later)
        search_data = await self.persistent_index.search_simple(query, max_results)
        search_results = search_data['results']
        total_count = search_data['total_count']
        truncated = search_data['truncated']
        
        results = []
        query_lower = query.lower()
        
        for file_info in search_results:
            content = file_info['content']
            content_lower = content.lower()
            
            # Find all matches
            matches = []
            start_pos = 0
            while True:
                match_pos = content_lower.find(query_lower, start_pos)
                if match_pos == -1:
                    break
                matches.append(match_pos)
                start_pos = match_pos + 1
            
            # Extract context for first match
            if matches:
                first_match = matches[0]
                
                # Calculate context bounds
                start = max(0, first_match - context_length // 2)
                end = min(len(content), first_match + len(query) + context_length // 2)
                context = content[start:end].strip()
                
                # Add ellipsis if truncated
                if start > 0:
                    context = "..." + context
                if end < len(content):
                    context = context + "..."
                
                # Calculate simple relevance score based on match count
                score = min(len(matches) / 10.0 + 1.0, 5.0)  # Score between 1 and 5
                
                results.append({
                    "path": file_info['filepath'],
                    "score": score,
                    "matches": [query],
                    "match_count": len(matches),
                    "context": context
                })
        
        # Sort by score (descending)
        results.sort(key=lambda x: x["score"], reverse=True)
        
        # Store search metadata
        self._last_search_metadata = {
            "total_count": total_count,
            "truncated": truncated,
            "limit": max_results
        }
        
        return results
    
    
    async def search_by_regex(self, pattern: str, flags: int = 0, context_length: int = 100, max_results: int = 50) -> List[Dict[str, Any]]:
        """
        Search for notes matching a regular expression pattern.
        
        Args:
            pattern: Regular expression pattern
            flags: Regex flags (e.g., re.IGNORECASE)
            context_length: Characters to show around match
            max_results: Maximum number of results to return
            
        Returns:
            List of search results with matches and context
        """
        import time
        
        # Initialize persistent index if needed
        if not self._persistent_index_initialized:
            await self._initialize_persistent_index()
        
        # Update index if it's stale
        if self._index_timestamp is None or (time.time() - self._index_timestamp) > 60:
            await self._update_search_index()
        
        # Use persistent index for efficient regex search
        results = await self.persistent_index.search_regex(pattern, flags, max_results, context_length)
        # Convert filepath to path for consistency
        for result in results:
            result["path"] = result.pop("filepath")
        return results
    
    
    async def list_notes(self, directory: Optional[str] = None, recursive: bool = True) -> List[Dict[str, str]]:
        """
        List all notes in vault or specific directory.
        
        Args:
            directory: Specific directory to list (optional)
            recursive: Whether to include subdirectories
            
        Returns:
            List of note paths and names
        """
        notes = []
        
        # Determine search path
        if directory:
            # Use lenient validation for reading existing directories
            search_path = self._get_absolute_path(directory)
            if not search_path.exists() or not search_path.is_dir():
                return []
        else:
            search_path = self.vault_path
        
        # Find markdown files
        pattern = "**/*.md" if recursive else "*.md"
        for md_file in search_path.glob(pattern):
            rel_path = md_file.relative_to(self.vault_path)
            notes.append({
                "path": str(rel_path),
                "name": md_file.name
            })
        
        # Sort by path
        notes.sort(key=lambda x: x["path"])
        
        return notes
    
    async def find_image(self, filename: str) -> Optional[str]:
        """
        Find an image file anywhere in the vault.
        
        Args:
            filename: Image filename to search for
            
        Returns:
            Relative path to image if found, None otherwise
        """
        # Common image extensions
        image_extensions = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg', '.bmp', '.ico'}
        
        # Check if filename has valid extension
        file_ext = Path(filename).suffix.lower()
        if file_ext not in image_extensions:
            return None
        
        # Search for the image file
        for image_file in self.vault_path.rglob(filename):
            if image_file.is_file():
                return str(image_file.relative_to(self.vault_path))
        
        return None
    
    async def read_image(self, path: str, max_width: int = 1600) -> Dict[str, Any]:
        """
        Read an image file from the vault with automatic resizing.
        
        Args:
            path: Path to image relative to vault root
            max_width: Maximum width for resizing (default: 800px)
            
        Returns:
            Dictionary with image data and metadata
        """
        # Use lenient validation for reading existing image files
        full_path = self._get_absolute_path(path)
        
        if not full_path.exists():
            raise FileNotFoundError(f"Image not found: {path}")
        
        # Check file size to prevent memory issues
        stat = full_path.stat()
        max_size = 50 * 1024 * 1024  # 50MB limit for images
        if stat.st_size > max_size:
            raise ValueError(f"Image too large: {stat.st_size} bytes (max: {max_size} bytes)")
        
        # Read binary content asynchronously
        async with aiofiles.open(full_path, 'rb') as f:
            content = await f.read()
        
        # Determine MIME type
        ext = full_path.suffix.lower()
        mime_types = {
            '.png': 'image/png',
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg',
            '.gif': 'image/gif',
            '.webp': 'image/webp',
            '.svg': 'image/svg+xml',
            '.bmp': 'image/bmp',
            '.ico': 'image/x-icon'
        }
        mime_type = mime_types.get(ext, 'application/octet-stream')
        
        # Skip resizing for SVG images (vector graphics)
        if ext == '.svg':
            base64_content = base64.b64encode(content).decode('utf-8')
            return {
                "path": path,
                "content": base64_content,
                "mime_type": mime_type,
                "size": len(content),
                "original_size": len(content)
            }
        
        # Resize image if needed
        try:
            # Open image with PIL
            img = Image.open(io.BytesIO(content))
            original_width, original_height = img.size
            
            # Only resize if image is larger than max_width
            if original_width > max_width:
                # Calculate new height maintaining aspect ratio
                aspect_ratio = original_height / original_width
                new_height = int(max_width * aspect_ratio)
                
                # Resize image
                img = img.resize((max_width, new_height), Image.Resampling.LANCZOS)
                
                # Save to bytes
                output = io.BytesIO()
                # Use appropriate format based on original
                if ext in ['.jpg', '.jpeg']:
                    img.save(output, format='JPEG', quality=85, optimize=True)
                elif ext == '.png':
                    img.save(output, format='PNG', optimize=True)
                elif ext == '.webp':
                    img.save(output, format='WEBP', quality=85)
                else:
                    # For other formats, convert to PNG
                    img.save(output, format='PNG', optimize=True)
                    mime_type = 'image/png'
                
                resized_content = output.getvalue()
                base64_content = base64.b64encode(resized_content).decode('utf-8')
                
                return {
                    "path": path,
                    "content": base64_content,
                    "mime_type": mime_type,
                    "size": len(resized_content),
                    "original_size": len(content),
                    "resized": True,
                    "dimensions": {
                        "original": {"width": original_width, "height": original_height},
                        "resized": {"width": max_width, "height": new_height}
                    }
                }
            else:
                # Image is already small enough, return as-is
                base64_content = base64.b64encode(content).decode('utf-8')
                return {
                    "path": path,
                    "content": base64_content,
                    "mime_type": mime_type,
                    "size": len(content),
                    "original_size": len(content),
                    "resized": False,
                    "dimensions": {
                        "original": {"width": original_width, "height": original_height}
                    }
                }
        except Exception as e:
            # If image processing fails, return original (but this might be too large)
            # Log the error for debugging
            print(f"Warning: Failed to process image {path}: {e}")
            base64_content = base64.b64encode(content).decode('utf-8')
            return {
                "path": path,
                "content": base64_content,
                "mime_type": mime_type,
                "size": len(content),
                "original_size": len(content),
                "error": str(e)
            }
    

# Global vault instance (will be initialized in server.py)
vault: Optional[ObsidianVault] = None


def get_vault() -> ObsidianVault:
    """Get the global vault instance."""
    if vault is None:
        raise RuntimeError("Vault not initialized. Call init_vault() first.")
    return vault


def init_vault(vault_path: Optional[str] = None) -> ObsidianVault:
    """
    Initialize the global vault instance.
    
    Args:
        vault_path: Path to vault (uses OBSIDIAN_VAULT_PATH env var if not provided)
    """
    global vault
    
    vault = ObsidianVault(vault_path)
    return vault