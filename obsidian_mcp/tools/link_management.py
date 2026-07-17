"""Link management tools for Obsidian MCP server."""

import re
import asyncio
from pathlib import Path
from typing import List, Optional, Dict, Tuple
from ..utils.filesystem import get_vault
from ..utils.validation import validate_note_path
from ..utils.vault_config import slugify_kebab

# Re-exported from utils/links.py (moved there so utils/vault_cache.py can
# reuse the same extraction without utils/ importing from tools/). The one
# pre-existing external import — `from ..tools.link_management import
# get_backlinks, WIKI_LINK_PATTERN` in organization.py — keeps working
# unchanged.
from ..utils.links import WIKI_LINK_PATTERN, MARKDOWN_LINK_PATTERN, extract_links_from_content


async def build_vault_notes_index(vault, force_refresh: bool = False) -> Dict[str, str]:
    """
    Build an index of all notes in the vault.
    Maps note names to their full paths.

    Backed by the vault's VaultCache (auto-updated on every MCP mutation,
    and via TTL-gated stat-diff for changes made outside the MCP server —
    see utils/vault_cache.py) instead of a flat, blindly-300s-TTL,
    re-scan-the-whole-vault-from-scratch cache.
    """
    return await vault.cache.get_notes_index(force_refresh=force_refresh)


async def find_notes_by_names(vault, note_names: List[str]) -> Dict[str, Optional[str]]:
    """
    Find multiple notes by their names efficiently.
    
    Returns a dict mapping requested names to their full paths (or None if not found).
    """
    # Build or get cached index
    notes_index = await build_vault_notes_index(vault)
    
    results = {}
    for name in note_names:
        # Ensure .md extension for lookup
        lookup_name = name if name.endswith('.md') else name + '.md'
        
        # First check if it's already a full path that exists
        if lookup_name in notes_index.values():
            results[name] = lookup_name
        else:
            # Look up by filename
            results[name] = notes_index.get(lookup_name) or notes_index.get(name)
    
    return results


async def check_links_validity_batch(vault, links: List[Dict[str, str]]) -> List[Dict[str, any]]:
    """
    Check validity of multiple links in batch for performance.
    """
    # Get unique paths to check
    unique_paths = list(set(link['path'] for link in links))
    
    # Find all notes in one go
    found_paths = await find_notes_by_names(vault, unique_paths)
    
    # Update links with validity info
    results = []
    for link in links:
        link_copy = link.copy()
        found_path = found_paths.get(link['path'])
        link_copy['exists'] = found_path is not None
        if found_path and found_path != link['path']:
            link_copy['actual_path'] = found_path
        results.append(link_copy)
    
    return results


def get_link_context(content: str, match, context_length: int = 100) -> str:
    """
    Extract context around a link match.
    
    Args:
        content: The full content
        match: The regex match object
        context_length: Characters to include before and after
        
    Returns:
        Context string with the link highlighted
    """
    start = max(0, match.start() - context_length)
    end = min(len(content), match.end() + context_length)
    
    # Extract context
    context = content[start:end]
    
    # Add ellipsis if truncated
    if start > 0:
        context = "..." + context
    if end < len(content):
        context = context + "..."
    
    return context.strip()


async def get_backlinks(
    path: str,
    include_context: bool = True,
    context_length: int = 100,
    ctx=None
) -> dict:
    """
    Get all notes that link to the specified note (optimized version).
    
    This tool finds all backlinks (incoming links) to a specific note,
    helping understand how notes are connected and referenced.
    
    Args:
        path: Path to the target note
        include_context: Whether to include surrounding text context
        context_length: Characters of context to include (default 100)
        ctx: MCP context for progress reporting
        
    Returns:
        Dictionary containing:
        - target_note: The note being linked to
        - backlink_count: Number of backlinks found
        - backlinks: List of backlink information including:
          - source_path: Note containing the link
          - link_text: The display text of the link
          - link_type: 'wiki' or 'markdown'
          - context: Surrounding text (if requested)
          
    Example:
        {
            "target_note": "Projects/My Project.md",
            "backlink_count": 3,
            "backlinks": [
                {
                    "source_path": "Daily/2024-01-15.md",
                    "link_text": "My Project",
                    "link_type": "wiki",
                    "context": "...working on [[My Project]] today..."
                }
            ]
        }
    """
    # Validate the note path
    is_valid, error = validate_note_path(path)
    if not is_valid:
        raise ValueError(error)
    
    if ctx:
        ctx.info(f"Finding backlinks to: {path}")
    
    vault = get_vault()
    
    # Verify the target note exists
    try:
        note = await vault.read_note(path)
    except FileNotFoundError:
        raise FileNotFoundError(f"Note not found: {path}")
    
    # Create variations of the target path to match against
    target_names = [path]
    if path.endswith('.md'):
        target_names.append(path[:-3])

    filename = path.split('/')[-1]
    if filename not in target_names:
        target_names.append(filename)
    if filename.endswith('.md'):
        filename_no_ext = filename[:-3]
        if filename_no_ext not in target_names:
            target_names.append(filename_no_ext)

    # Narrow the scan to notes whose extracted (already-parsed, in-memory —
    # no disk I/O) links plausibly resolve to our target, instead of
    # re-reading and regex-scanning every note in the vault. The actual
    # match/context extraction below is untouched: same regex, same note
    # content, same output shape — just run over a smaller candidate set.
    all_forward_links = await vault.cache.get_all_forward_links()
    candidate_note_paths = [
        source_path
        for source_path, links in all_forward_links.items()
        if source_path != note.path and any(link['path'] in target_names for link in links)
    ]

    if ctx:
        ctx.info(f"Will match against variations: {target_names}")
        ctx.info(f"Scanning {len(candidate_note_paths)} candidate notes (of {len(all_forward_links)} total)...")

    # Process notes in parallel batches
    backlinks = []
    batch_size = 10  # Process 10 notes at a time
    
    async def check_note_for_backlinks(note_path: str) -> List[dict]:
        """Check a single note for backlinks."""
        if note_path == path:
            return []
        
        try:
            note = await vault.read_note(note_path)
            
            content = note.content
            note_backlinks = []
            
            # Check for wiki-style links
            for match in WIKI_LINK_PATTERN.finditer(content):
                linked_path = match.group(1).strip()
                
                # Check if this link matches our target
                is_match = False
                if linked_path in target_names:
                    is_match = True
                elif linked_path + '.md' in target_names:
                    is_match = True
                
                if is_match:
                    alias = match.group(3)
                    link_text = alias.strip() if alias else match.group(1).strip()
                    
                    backlink_info = {
                        'source_path': note_path,
                        'link_text': link_text,
                        'link_type': 'wiki'
                    }
                    
                    if include_context:
                        backlink_info['context'] = get_link_context(content, match, context_length)
                    
                    note_backlinks.append(backlink_info)
            
            # Check for markdown-style links
            for match in MARKDOWN_LINK_PATTERN.finditer(content):
                link_path = match.group(2).strip()
                if link_path in target_names:
                    backlink_info = {
                        'source_path': note_path,
                        'link_text': match.group(1).strip(),
                        'link_type': 'markdown'
                    }
                    
                    if include_context:
                        backlink_info['context'] = get_link_context(content, match, context_length)
                    
                    note_backlinks.append(backlink_info)
            
            return note_backlinks
            
        except Exception:
            return []
    
    # Process in batches
    for i in range(0, len(candidate_note_paths), batch_size):
        batch = candidate_note_paths[i:i + batch_size]
        batch_results = await asyncio.gather(*[check_note_for_backlinks(np) for np in batch])
        
        for note_backlinks in batch_results:
            backlinks.extend(note_backlinks)
    
    if ctx:
        ctx.info(f"Found {len(backlinks)} backlinks")

    # Light enrichment (spec section 10.4's closing sentence): add the
    # linking note's cached name/description to each finding, from the
    # VaultCache — no extra disk reads.
    if backlinks:
        all_meta = await vault.cache.get_all_note_meta()
        for backlink in backlinks:
            meta = all_meta.get(backlink['source_path'], {})
            backlink['name'] = meta.get('name', '')
            backlink['description'] = meta.get('description', '')

    # Return standardized analysis results structure
    return {
        'findings': backlinks,
        'summary': {
            'backlink_count': len(backlinks),
            'sources': len(set(bl['source_path'] for bl in backlinks))  # Unique source notes
        },
        'target': path,
        'scope': {
            'include_context': include_context,
            'context_length': context_length
        }
    }


async def get_outgoing_links(
    path: str,
    check_validity: bool = False,
    ctx=None
) -> dict:
    """
    Get all links from a specific note (optimized version).
    
    This tool extracts all outgoing links from a note, helping understand
    what other notes and resources it references.
    
    Args:
        path: Path to the source note
        check_validity: Whether to check if linked notes exist
        ctx: MCP context for progress reporting
        
    Returns:
        Dictionary containing:
        - source_note: The note containing the links
        - link_count: Number of links found
        - links: List of link information including:
          - path: The linked note path
          - display_text: The display text of the link
          - type: 'wiki' or 'markdown'
          - exists: Whether the linked note exists (if check_validity=True)
          - actual_path: The actual path if different from link path
          
    Example:
        {
            "source_note": "Daily/2024-01-15.md",
            "link_count": 5,
            "links": [
                {
                    "path": "Projects/My Project.md",
                    "display_text": "My Project",
                    "type": "wiki",
                    "exists": true
                }
            ]
        }
    """
    # Validate the note path
    is_valid, error = validate_note_path(path)
    if not is_valid:
        raise ValueError(error)
    
    if ctx:
        ctx.info(f"Extracting links from: {path}")
    
    vault = get_vault()
    
    # Read the note content
    try:
        note = await vault.read_note(path)
    except FileNotFoundError:
        raise FileNotFoundError(f"Note not found: {path}")
    
    content = note.content
    
    # Extract all links
    links = extract_links_from_content(content)
    
    # Check validity if requested - in batch!
    if check_validity:
        if ctx:
            ctx.info(f"Checking validity of {len(links)} links...")
        links = await check_links_validity_batch(vault, links)
    
    if ctx:
        ctx.info(f"Found {len(links)} outgoing links")
    
    # Return standardized analysis results structure
    return {
        'findings': links,
        'summary': {
            'link_count': len(links),
            'checked_validity': check_validity,
            'broken_count': len([l for l in links if check_validity and not l.get('exists', True)])
        },
        'target': path,
        'scope': {
            'check_validity': check_validity
        }
    }


async def find_broken_links(
    directory: Optional[str] = None,
    single_note: Optional[str] = None,
    ctx=None
) -> dict:
    """
    Find all broken links in the vault, a specific directory, or a single note (optimized version).
    
    This tool identifies links pointing to non-existent notes, helping maintain
    vault integrity. Broken links often occur after renaming or deleting notes.
    
    Args:
        directory: Specific directory to check (optional, defaults to entire vault)
        single_note: Check only this specific note (optional)
        ctx: MCP context for progress reporting
        
    Returns:
        Dictionary containing:
        - broken_link_count: Total number of broken links
        - affected_notes: Number of notes containing broken links
        - broken_links: List of broken link details including:
          - source_path: Note containing the broken link
          - broken_link: The path that doesn't exist
          - link_text: The display text of the link
          - link_type: 'wiki' or 'markdown'
          
    Example:
        {
            "broken_link_count": 3,
            "affected_notes": 2,
            "broken_links": [
                {
                    "source_path": "Daily/2024-01-15.md",
                    "broken_link": "Projects/Old Project.md",
                    "link_text": "Old Project",
                    "link_type": "wiki"
                }
            ]
        }
    """
    if ctx:
        if single_note:
            scope = f"note: {single_note}"
        elif directory:
            scope = f"directory: {directory}"
        else:
            scope = "entire vault"
        ctx.info(f"Checking for broken links in {scope}")
    
    vault = get_vault()
    
    # Get notes to check
    notes_to_check = []
    if single_note:
        notes_to_check = [single_note]
    else:
        # Build index to get all notes
        notes_index = await build_vault_notes_index(vault)
        all_notes = list(set(notes_index.values()))  # Get unique paths
        
        if directory:
            # Filter to directory
            notes_to_check = [n for n in all_notes if n.startswith(directory + '/') or n.startswith(directory)]
        else:
            notes_to_check = all_notes
    
    if ctx:
        ctx.info(f"Checking {len(notes_to_check)} notes...")
    
    # Collect all links from all notes. single_note reads directly (matches
    # the pre-existing behavior of working even when the path isn't already
    # vault-cache-known, e.g. missing a .md suffix vault.read_note fixes up).
    # Directory/vault-wide scans instead pull each note's already-parsed
    # links out of the cache (no disk I/O, no re-running the regex).
    all_links_by_note = {}
    if single_note:
        try:
            note = await vault.read_note(single_note)
            links = extract_links_from_content(note.content)
            if links:
                all_links_by_note[note.path] = links
        except Exception:
            pass
    else:
        all_forward_links = await vault.cache.get_all_forward_links()
        for note_path in notes_to_check:
            links = all_forward_links.get(note_path)
            if links:
                all_links_by_note[note_path] = links
    
    # Get all unique link paths
    all_link_paths = set()
    for links in all_links_by_note.values():
        for link in links:
            all_link_paths.add(link['path'])
    
    if ctx:
        ctx.info(f"Checking validity of {len(all_link_paths)} unique links...")
    
    # Check which links exist - in one batch!
    found_paths = await find_notes_by_names(vault, list(all_link_paths))
    
    # Find broken links
    broken_links = []
    affected_notes_set = set()
    
    for note_path, links in all_links_by_note.items():
        for link in links:
            if not found_paths.get(link['path']):
                broken_link_info = {
                    'source_path': note_path,
                    'broken_link': link['path'],
                    'link_text': link['display_text'],
                    'link_type': link['type']
                }
                broken_links.append(broken_link_info)
                affected_notes_set.add(note_path)
    
    if ctx:
        ctx.info(f"Found {len(broken_links)} broken links in {len(affected_notes_set)} notes")

    # Sort broken links by source path
    broken_links.sort(key=lambda x: x['source_path'])

    # Light enrichment (spec section 10.4's closing sentence): add the
    # linking note's cached name/description to each finding.
    if broken_links:
        all_meta = await vault.cache.get_all_note_meta()
        for broken_link in broken_links:
            meta = all_meta.get(broken_link['source_path'], {})
            broken_link['name'] = meta.get('name', '')
            broken_link['description'] = meta.get('description', '')

    # Return standardized analysis results structure
    return {
        'findings': broken_links,
        'summary': {
            'broken_link_count': len(broken_links),
            'affected_notes': len(affected_notes_set),
            'notes_checked': len(notes_to_check)
        },
        'target': single_note if single_note else directory or 'vault',
        'scope': {
            'type': 'single_note' if single_note else 'directory' if directory else 'vault',
            'path': single_note if single_note else directory if directory else '/'
        }
    }


# ---------------------------------------------------------------------------
# Write-time wikilink validation (spec section 4) — used by note_management's
# create_note/update_note/edit_note_section and by daily_notes.add_daily_note.
#
# This is intentionally a *separate* extractor from extract_links_from_content
# above: that one feeds get_backlinks/get_outgoing_links/find_broken_links and
# must keep matching everything it always has (including embeds and links
# inside code, for full backward compatibility). This one only looks at
# genuine, prose [[wikilinks]] the user is about to write.
# ---------------------------------------------------------------------------

_FENCED_CODE_RE = re.compile(r'```.*?```', re.DOTALL)
_INLINE_CODE_RE = re.compile(r'`[^`\n]+`')
_WIKI_EMBED_RE = re.compile(r'!\[\[[^\]]*\]\]')
_MARKDOWN_EMBED_RE = re.compile(r'!\[[^\]]*\]\([^)]*\)')
_VALIDATION_WIKI_LINK_RE = re.compile(r'(?<!!)\[\[([^\]]*)\]\]')


def _mask_ineligible_regions(content: str) -> str:
    """Blank out (same length, so match spans stay aligned with the
    original string) fenced code, inline code, and embeds, so the wikilink
    validator never matches a link that only appears inside one of those.
    """
    def _blank(match: "re.Match[str]") -> str:
        return " " * len(match.group(0))

    masked = _FENCED_CODE_RE.sub(_blank, content)
    masked = _INLINE_CODE_RE.sub(_blank, masked)
    masked = _WIKI_EMBED_RE.sub(_blank, masked)
    masked = _MARKDOWN_EMBED_RE.sub(_blank, masked)
    return masked


def _suggest_similar_notes(notes_index: Dict[str, str], targets: List[str], limit: int = 3) -> str:
    """Up to `limit` fuzzy (case-insensitive substring/prefix) suggestions
    per broken target, for a strict-policy error message."""
    candidates = sorted({name[:-3] if name.endswith('.md') else name for name in notes_index.keys()})
    parts = []
    for target in targets:
        target_lower = target.lower()
        matches = [c for c in candidates if target_lower in c.lower()]
        if matches:
            parts.append(f"'{target}' -> maybe: {', '.join(matches[:limit])}")
    return f" Suggestions: {'; '.join(parts)}." if parts else ""


async def validate_wikilinks_for_write(vault, content: str) -> Tuple[str, List[str]]:
    """
    Validate (and, for OBSIDIAN_SLUG_STYLE=kebab, transparently fix up)
    [[wikilinks]] in content that is about to be written.

    Format errors — an empty target ([[]]) or nested/unbalanced brackets —
    always raise ValueError, regardless of OBSIDIAN_WIKILINK_POLICY: the
    format is malformed independent of whether broken targets are tolerated.
    [[#Heading]] (no note name — a same-note heading reference) is valid and
    skipped, it isn't a link to another note.

    Broken *targets* (the note doesn't exist) are handled per
    OBSIDIAN_WIKILINK_POLICY: strict raises ValueError with fuzzy
    suggestions, warn returns the message in the warnings list (content is
    still written), off is silent.

    Returns (possibly-rewritten content, warnings). The content is rewritten
    only when OBSIDIAN_SLUG_STYLE=kebab and a link target doesn't resolve
    directly but its kebab-slug matches an existing note — the link is
    rewritten to point at the real filename (keeping the user's original
    text as the alias) so Obsidian can still resolve it.
    """
    masked = _mask_ineligible_regions(content)
    matches = list(_VALIDATION_WIKI_LINK_RE.finditer(masked))
    if not matches:
        return content, []

    notes_index = await build_vault_notes_index(vault)
    warnings: List[str] = []
    broken_targets: List[str] = []
    replacements: Dict[str, str] = {}  # raw "[[...]]" text -> replacement text

    for match in matches:
        raw_inner = match.group(1)
        if "[[" in raw_inner or "]]" in raw_inner:
            raise ValueError(
                f"Malformed wikilink {match.group(0)!r}: nested or unbalanced brackets. "
                "Fix or remove it before saving."
            )

        target_part, _, alias = raw_inner.partition("|")
        # Strip an optional "#Heading" suffix — only the note target is
        # validated, per spec (the heading itself isn't checked).
        note_ref = target_part.split("#", 1)[0].strip()

        if not note_ref:
            if target_part.strip().startswith("#"):
                continue  # [[#Heading]] — same-note reference, always valid
            raise ValueError(
                f"Malformed wikilink {match.group(0)!r}: empty target. "
                "Fix or remove it before saving."
            )

        lookup_name = note_ref if note_ref.endswith('.md') else note_ref + '.md'
        resolved_path = notes_index.get(lookup_name) or notes_index.get(note_ref)
        if not resolved_path and lookup_name in notes_index.values():
            resolved_path = lookup_name

        if not resolved_path and vault.slug_style == "kebab":
            target_slug = slugify_kebab(note_ref)
            if target_slug:
                for name, real_path in notes_index.items():
                    stem = name[:-3] if name.endswith('.md') else name
                    if slugify_kebab(stem) == target_slug:
                        resolved_path = real_path
                        display = alias.strip() if alias else target_part.strip()
                        real_stem = Path(real_path).stem
                        replacements[match.group(0)] = f"[[{real_stem}|{display}]]"
                        break

        if not resolved_path:
            broken_targets.append(note_ref)

    new_content = content
    for old, new in replacements.items():
        new_content = new_content.replace(old, new)

    if broken_targets:
        if vault.wikilink_policy == "strict":
            suggestions = _suggest_similar_notes(notes_index, broken_targets)
            broken_list = ", ".join(f"[[{t}]]" for t in broken_targets)
            raise ValueError(
                f"Broken wikilink target(s): {broken_list}.{suggestions} "
                "Create the target note first, fix the link text, or remove the link."
            )
        elif vault.wikilink_policy == "warn":
            for target in broken_targets:
                warnings.append(f"Wikilink target not found: [[{target}]]")
        # "off": no-op — content is written as-is.

    return new_content, warnings