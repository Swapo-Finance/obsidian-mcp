"""Tool for viewing images embedded in notes."""

import base64
import re

from fastmcp import Context, Image

from ..constants import ERROR_MESSAGES
from ..utils import sanitize_path, validate_note_path
from ..utils.filesystem import get_vault

_MIME_TO_FORMAT = {
    "image/png": "png",
    "image/jpeg": "jpeg",
    "image/jpg": "jpeg",
    "image/gif": "gif",
    "image/webp": "webp",
    "image/svg+xml": "svg",
    "image/bmp": "bmp",
    "image/x-icon": "ico",
}


async def _resolve_and_read_image(
    vault, image_ref: str, max_width: int, ctx: Context | None
) -> dict | None:
    """Read one embedded image, falling back to a filename search if the
    literal reference doesn't resolve directly.

    Returns None (after logging via ctx) when the image can't be found
    anywhere in the vault. Any other exception propagates so the caller's
    per-image try/except can log it and skip just that image without
    failing the whole call.
    """
    try:
        return await vault.read_image(image_ref, max_width=max_width)
    except FileNotFoundError:
        pass

    filename = image_ref.split("/")[-1]
    found_path = await vault.find_image(filename)
    if not found_path:
        if ctx:
            await ctx.info(f"Could not find image: {image_ref}")
        return None

    if ctx:
        await ctx.info(f"Found image at: {found_path}")
    return await vault.read_image(found_path, max_width=max_width)


async def view_note_images(
    path: str,
    image_index: int | None = None,
    max_width: int = 1600,
    ctx: Context | None = None,
) -> list[Image]:
    """
    Extract and display images embedded in a note.

    Use this tool to view images that are referenced within a note's content.
    This is separate from read_note to ensure proper image display in MCP clients.

    Args:
        path: Path to the note containing images
        image_index: Optional specific image index to display (0-based)
        max_width: Maximum width for automatic resizing in pixels (default: 1600)
        ctx: MCP context for progress reporting

    Returns:
        List of Image objects from the note (or single image if index specified)

    Example:
        >>> # View all images in a note
        >>> await view_note_images("Projects/Design.md")
        [<Image>, <Image>, ...]

        >>> # View specific image by index
        >>> await view_note_images("Projects/Design.md", image_index=0)
        [<Image>]
    """
    # Validate path
    is_valid, error_msg = validate_note_path(path)
    if not is_valid:
        raise ValueError(f"Invalid path: {error_msg}")

    path = sanitize_path(path)

    if ctx:
        await ctx.info(f"Extracting images from note: {path}")

    vault = get_vault()

    # Read the note to get its content
    try:
        note = await vault.read_note(path)
    except FileNotFoundError:
        raise FileNotFoundError(ERROR_MESSAGES["note_not_found"].format(path=path))

    # Extract image references
    wiki_pattern = r"!\[\[([^]]+\.(?:png|jpg|jpeg|gif|webp|svg|bmp|ico))\]\]"
    markdown_pattern = r"!\[[^\]]*\]\(([^)]+\.(?:png|jpg|jpeg|gif|webp|svg|bmp|ico))\)"

    image_paths = []

    # Find wiki-style embeds
    for match in re.finditer(wiki_pattern, note.content, re.IGNORECASE):
        image_paths.append(match.group(1))

    # Find markdown-style embeds
    for match in re.finditer(markdown_pattern, note.content, re.IGNORECASE):
        image_paths.append(match.group(1))

    if not image_paths:
        if ctx:
            await ctx.info("No images found in this note")
        return []

    if ctx:
        await ctx.info(f"Found {len(image_paths)} image(s) in note")

    # If specific index requested, validate it
    if image_index is not None:
        if image_index < 0 or image_index >= len(image_paths):
            raise ValueError(
                f"Invalid image index {image_index}. Note contains {len(image_paths)} images (0-{len(image_paths) - 1})"
            )
        image_paths = [image_paths[image_index]]

    # Load and convert images to Image objects
    images = []

    for i, image_ref in enumerate(image_paths):
        try:
            if ctx:
                await ctx.info(f"Loading image {i + 1}/{len(image_paths)}: {image_ref}")

            image_data = await _resolve_and_read_image(vault, image_ref, max_width, ctx)
            if image_data is None:
                continue

            # Convert to Image object
            image_bytes = base64.b64decode(image_data["content"])
            format_type = _MIME_TO_FORMAT.get(image_data["mime_type"], "png")

            images.append(Image(data=image_bytes, format=format_type))

        except Exception as e:
            if ctx:
                await ctx.info(f"Error loading image {image_ref}: {e!s}")
            continue

    return images
