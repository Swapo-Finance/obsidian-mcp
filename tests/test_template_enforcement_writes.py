#!/usr/bin/env python3
"""Template conformance (spec section 3) wired into the actual write path:
create_note, update_note(replace), update_note(create_if_not_exists) enforce
it; update_note(append) and edit_note_section are exempt (incremental edits
don't re-validate the whole note). The sanity pass only exercised
check_template_conformance() directly against a couple of scenarios — this
covers every write entry point plus out-of-order/missing-frontmatter through
the real tools.
"""

import os
import shutil
import tempfile
from pathlib import Path

import pytest
import pytest_asyncio

from obsidian_mcp.tools.note_management import create_note, edit_note_section, update_note
from obsidian_mcp.utils.filesystem import init_vault


@pytest_asyncio.fixture
async def templated_vault():
    temp_dir = tempfile.mkdtemp(prefix="obsidian_tmpl_writes_")
    templates_dir = Path(temp_dir) / "templates"
    templates_dir.mkdir()
    (templates_dir / "projeto.md").write_text(
        "---\nstatus: \n---\n\n## Objetivo\n\n## Status\n"
    )
    (Path(temp_dir) / "01-projects").mkdir()

    os.environ["OBSIDIAN_FOLDER_TEMPLATES"] = (
        '[{"folder":"01-projects","template":"templates/projeto.md"}]'
    )
    vault = init_vault(temp_dir)
    yield vault
    os.environ.pop("OBSIDIAN_FOLDER_TEMPLATES", None)
    shutil.rmtree(temp_dir)


CONFORMING = (
    "---\nstatus: active\ndescription: Sample.\n---\n\n"
    "## Objetivo\n\nGoal\n\n## Status\n\nOK\n"
)


class TestCreateNoteEnforcesTemplate:
    @pytest.mark.asyncio
    async def test_missing_heading_raises(self, templated_vault):
        with pytest.raises(ValueError, match="Missing headings"):
            await create_note(
                "01-projects/Bad.md",
                "---\ndescription: x\nstatus: a\n---\n\n## Objetivo\n",
            )

    @pytest.mark.asyncio
    async def test_out_of_order_headings_raises(self, templated_vault):
        with pytest.raises(ValueError, match="out of order"):
            await create_note(
                "01-projects/Bad.md",
                "---\ndescription: x\nstatus: a\n---\n\n## Status\n\n## Objetivo\n",
            )

    @pytest.mark.asyncio
    async def test_missing_frontmatter_key_raises(self, templated_vault):
        with pytest.raises(ValueError, match="frontmatter"):
            await create_note(
                "01-projects/Bad.md", "---\ndescription: x\n---\n\n## Objetivo\n\n## Status\n"
            )

    @pytest.mark.asyncio
    async def test_error_message_includes_skeleton_and_retry_instruction(self, templated_vault):
        with pytest.raises(ValueError) as excinfo:
            await create_note("01-projects/Bad.md", "## Objetivo\n")
        message = str(excinfo.value)
        assert "01-projects" in message
        assert "templates/projeto.md" in message
        assert "Resend the FULL content" in message
        assert "## Objetivo" in message  # skeleton echoed back

    @pytest.mark.asyncio
    async def test_conforming_content_succeeds(self, templated_vault):
        result = await create_note("01-projects/Good.md", CONFORMING)
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_extra_headings_anywhere_allowed(self, templated_vault):
        content = (
            "---\nstatus: active\ndescription: x\n---\n\n"
            "## Intro\n\n## Objetivo\n\n## Extra\n\n## Status\n"
        )
        result = await create_note("01-projects/Good2.md", content)
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_folder_without_rule_is_free_form(self, templated_vault):
        result = await create_note(
            "elsewhere/Anything.md", "---\ndescription: x\n---\n\nwhatever, no headings needed\n"
        )
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_nested_subfolder_under_enforced_folder_also_enforced(self, templated_vault):
        with pytest.raises(ValueError, match="Missing headings"):
            await create_note(
                "01-projects/sub/Bad.md", "---\ndescription: x\nstatus: a\n---\n\n## Objetivo\n"
            )


class TestUpdateNoteReplaceEnforcesTemplate:
    @pytest.mark.asyncio
    async def test_replace_missing_heading_raises(self, templated_vault):
        await create_note("01-projects/Existing.md", CONFORMING)
        with pytest.raises(ValueError, match="Missing headings"):
            await update_note(
                "01-projects/Existing.md",
                "---\ndescription: x\nstatus: a\n---\n\n## Objetivo\n",
                merge_strategy="replace",
            )

    @pytest.mark.asyncio
    async def test_replace_conforming_content_succeeds(self, templated_vault):
        await create_note("01-projects/Existing.md", CONFORMING)
        result = await update_note(
            "01-projects/Existing.md",
            "---\nstatus: done\ndescription: Updated.\n---\n\n## Objetivo\n\nNew goal\n\n## Status\n\nDone\n",
            merge_strategy="replace",
        )
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_create_if_not_exists_enforces_template_on_first_write(self, templated_vault):
        with pytest.raises(ValueError, match="Missing headings"):
            await update_note(
                "01-projects/Fresh.md",
                "---\ndescription: x\nstatus: a\n---\n\n## Objetivo\n",
                create_if_not_exists=True,
            )

    @pytest.mark.asyncio
    async def test_create_if_not_exists_conforming_succeeds(self, templated_vault):
        result = await update_note(
            "01-projects/Fresh.md", CONFORMING, create_if_not_exists=True
        )
        assert result["success"] is True
        assert result["details"]["created"] is True


class TestIncrementalEditsExemptFromTemplate:
    """spec section 3: edit_note_section and update_note(append) never
    re-validate the whole note against the template."""

    @pytest.mark.asyncio
    async def test_update_append_does_not_enforce_template(self, templated_vault):
        await create_note("01-projects/Existing.md", CONFORMING)
        # Appending arbitrary prose would break conformance if re-checked —
        # this must succeed regardless.
        result = await update_note(
            "01-projects/Existing.md", "Some unstructured append.", merge_strategy="append"
        )
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_edit_note_section_does_not_enforce_template(self, templated_vault):
        await create_note("01-projects/Existing.md", CONFORMING)
        result = await edit_note_section(
            "01-projects/Existing.md",
            "## Status",
            "Edited in place.",
            operation="append_to_section",
        )
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_edit_note_section_create_missing_section_does_not_enforce_template(
        self, templated_vault
    ):
        await create_note("01-projects/Existing.md", CONFORMING)
        result = await edit_note_section(
            "01-projects/Existing.md",
            "## Not In Template",
            "New section content.",
            operation="insert_after",
            create_if_missing=True,
        )
        assert result["success"] is True
        assert result["section_created"] is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
