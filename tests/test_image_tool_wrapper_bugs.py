#!/usr/bin/env python3
"""Regression tests for two server.py wrapper bugs in the image tools.

Bug 1 (read_image_tool): the wrapper calls `read_image(path, include_metadata,
ctx)` positionally, but read_image's real signature is `(path,
include_metadata, max_width, ctx)` - so `ctx` lands in the `max_width` slot
instead of `max_width` itself. Inside vault.read_image, the resize check
`original_width > max_width` then compares an int against whatever ctx is
(None in these tests, since we call with ctx=None), raising a TypeError. That
TypeError is silently caught by a broad `except Exception` in
filesystem.py's read_image, which returns the original, unresized image
(dropping the "resized"/"dimensions" keys entirely) instead of resizing it.

Bug 2 (view_note_images_tool): the wrapper's Annotated Field for max_width
declares default=1600 for the MCP JSON schema, while the actual Python
parameter default - the one used whenever a caller omits max_width - is 800
(the plain `= 800` after the Annotated[...] block). The real runtime
behavior was always 800; only the declared schema default was wrong. These
tests pin the real default (mirroring the inspect.signature idiom already
used by TestContextAnnotations in test_server_tool_wrappers.py) and the real
resize behavior, so a future fix cannot silently "fix" the mismatch by
flipping the real default to 1600 to match the old, wrong schema value
instead of correcting the schema to 800.

Per this repo's test conventions (see test_server_tool_wrappers.py): tests
are flat under tests/. tests/conftest.py exists (shared autouse fixtures for
env/vault isolation) but deliberately does not set OBSIDIAN_VAULT_PATH or
import obsidian_mcp.server itself (see its own module docstring), so this
file still sets OBSIDIAN_VAULT_PATH to an existing directory before
obsidian_mcp.server is imported below, since that import raises ValueError
at import time otherwise.
"""

import io
import os
import shutil
import tempfile

import pytest
from PIL import Image as PILImage

# ponytail: one throwaway bootstrap dir for the whole test session, never
# rmtree'd - satisfies server.py's import-time OBSIDIAN_VAULT_PATH check
# only; every test below repoints the vault to its own tmp dir anyway.
os.environ["OBSIDIAN_VAULT_PATH"] = tempfile.mkdtemp(
    prefix="obsidian_image_bugs_bootstrap_"
)

from obsidian_mcp.server import read_image_tool, view_note_images_tool
from obsidian_mcp.tools.note_management import create_note
from obsidian_mcp.utils.filesystem import init_vault


@pytest.fixture
def vault():
    temp_dir = tempfile.mkdtemp(prefix="obsidian_image_bugs_")
    os.environ["OBSIDIAN_REQUIRE_FRONTMATTER"] = "false"
    v = init_vault(temp_dir)
    yield v
    os.environ.pop("OBSIDIAN_REQUIRE_FRONTMATTER", None)
    shutil.rmtree(temp_dir, ignore_errors=True)


def _save_png(path, width, height):
    PILImage.new("RGB", (width, height), color=(200, 50, 50)).save(path, format="PNG")


class TestReadImageToolResize:
    """read_image_tool.fn must actually resize images wider than the internal
    1600px default, and leave narrower images untouched."""

    @pytest.mark.asyncio
    async def test_wide_image_is_resized_to_default_max_width(self, vault):
        _save_png(vault.vault_path / "wide.png", 2000, 1000)

        result = await read_image_tool.fn(
            path="wide.png", include_metadata=True, ctx=None
        )

        assert result["resized"] is True
        assert result["dimensions"]["original"] == {"width": 2000, "height": 1000}
        assert result["dimensions"]["resized"] == {"width": 1600, "height": 800}
        # The returned image bytes must reflect the resize too, not just the
        # metadata dict - real PIL decoding, no mocking of the resize path.
        actual = PILImage.open(io.BytesIO(result["image"].data))
        assert actual.size == (1600, 800)

    @pytest.mark.asyncio
    async def test_narrow_image_passes_through_unresized(self, vault):
        _save_png(vault.vault_path / "narrow.png", 400, 300)

        result = await read_image_tool.fn(
            path="narrow.png", include_metadata=True, ctx=None
        )

        assert result["resized"] is False
        assert result["dimensions"] == {"original": {"width": 400, "height": 300}}
        actual = PILImage.open(io.BytesIO(result["image"].data))
        assert actual.size == (400, 300)


class TestViewNoteImagesToolMaxWidthDefault:
    """view_note_images_tool's max_width Field declares default=1600 in the
    MCP schema, but the real Python default is 800. Pin the real default two
    ways: static introspection and actual resize behavior."""

    def test_actual_parameter_default_is_800(self):
        import inspect

        sig = inspect.signature(view_note_images_tool.fn)
        assert sig.parameters["max_width"].default == 800

    @pytest.mark.asyncio
    async def test_image_between_800_and_1600_is_resized_when_max_width_omitted(
        self, vault
    ):
        # 1000px wide: bigger than 800 (the real default) but smaller than
        # 1600 (the wrong schema value) - this width alone distinguishes
        # which default is actually in effect.
        _save_png(vault.vault_path / "medium.png", 1000, 500)
        await create_note("Notes/WithImage.md", "# Note\n\n![[medium.png]]\n")

        images = await view_note_images_tool.fn(path="Notes/WithImage.md")

        assert len(images) == 1
        actual = PILImage.open(io.BytesIO(images[0].data))
        assert actual.size == (800, 400)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
