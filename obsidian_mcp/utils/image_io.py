"""Pure image-reading/resizing helper (no vault dependency).

Extracted from ObsidianVault.read_image. Path-safety resolution
(_get_absolute_path) stays on ObsidianVault, which resolves the path and
delegates the rest here — kept vault-free so this is directly unit-testable
against a plain file on disk. ObsidianVault keeps a thin delegating
`read_image` method for existing callers — see filesystem.py.
"""

import base64
import io
import logging
from pathlib import Path
from typing import Any

import aiofiles
from PIL import Image

logger = logging.getLogger(__name__)


async def read_image(
    full_path: Path, path: str, max_width: int = 1600
) -> dict[str, Any]:
    """
    Read an image file with automatic resizing.

    Args:
        full_path: Resolved absolute path to the image file (path-safety
            validation already done by the caller)
        path: Vault-relative path, echoed back in the response
        max_width: Maximum width for resizing (default: 1600px)

    Returns:
        Dictionary with image data and metadata
    """
    if not full_path.exists():
        raise FileNotFoundError(f"Image not found: {path}")

    # Check file size to prevent memory issues
    stat = full_path.stat()
    max_size = 50 * 1024 * 1024  # 50MB limit for images
    if stat.st_size > max_size:
        raise ValueError(
            f"Image too large: {stat.st_size} bytes (max: {max_size} bytes)"
        )

    # Read binary content asynchronously
    async with aiofiles.open(full_path, "rb") as f:
        content = await f.read()

    # Determine MIME type
    ext = full_path.suffix.lower()
    mime_types = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".svg": "image/svg+xml",
        ".bmp": "image/bmp",
        ".ico": "image/x-icon",
    }
    mime_type = mime_types.get(ext, "application/octet-stream")

    # Skip resizing for SVG images (vector graphics)
    if ext == ".svg":
        base64_content = base64.b64encode(content).decode("utf-8")
        return {
            "path": path,
            "content": base64_content,
            "mime_type": mime_type,
            "size": len(content),
            "original_size": len(content),
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
            if ext in [".jpg", ".jpeg"]:
                img.save(output, format="JPEG", quality=85, optimize=True)
            elif ext == ".png":
                img.save(output, format="PNG", optimize=True)
            elif ext == ".webp":
                img.save(output, format="WEBP", quality=85)
            else:
                # For other formats, convert to PNG
                img.save(output, format="PNG", optimize=True)
                mime_type = "image/png"

            resized_content = output.getvalue()
            base64_content = base64.b64encode(resized_content).decode("utf-8")

            return {
                "path": path,
                "content": base64_content,
                "mime_type": mime_type,
                "size": len(resized_content),
                "original_size": len(content),
                "resized": True,
                "dimensions": {
                    "original": {
                        "width": original_width,
                        "height": original_height,
                    },
                    "resized": {"width": max_width, "height": new_height},
                },
            }
        else:
            # Image is already small enough, return as-is
            base64_content = base64.b64encode(content).decode("utf-8")
            return {
                "path": path,
                "content": base64_content,
                "mime_type": mime_type,
                "size": len(content),
                "original_size": len(content),
                "resized": False,
                "dimensions": {
                    "original": {"width": original_width, "height": original_height}
                },
            }
    except Exception as e:
        # If image processing fails, return original (but this might be too large)
        logger.warning(f"Failed to process image {path}: {e}")
        base64_content = base64.b64encode(content).decode("utf-8")
        return {
            "path": path,
            "content": base64_content,
            "mime_type": mime_type,
            "size": len(content),
            "original_size": len(content),
            "error": str(e),
        }
