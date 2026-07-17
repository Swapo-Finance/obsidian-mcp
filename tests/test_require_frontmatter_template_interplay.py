#!/usr/bin/env python3
"""OBSIDIAN_REQUIRE_FRONTMATTER x OBSIDIAN_FOLDER_TEMPLATES interplay (spec
section 10.3's "template-aware, sem duplicar" bullet). The two prior sanity
files each cover one side in isolation:

- test_onda1_sanity.py's TestTemplateConformance: template heading/
  frontmatter-key checks with REQUIRE_FRONTMATTER effectively off (its
  fixture doesn't set descriptions, so it predates/ignores that concern).
- test_onda2_sanity.py's TestRequireFrontmatterDefaultOn: name/description
  enforcement with no template configured at all.

Neither exercises what happens when BOTH apply to the same write — whether
a template that already declares `name`/`description` as required keys
causes name/description to be asked for twice, in conflicting ways, or
whether check_template_conformance (presence-only) and
apply_frontmatter_requirements (value-quality) genuinely compose without
double-prompting the caller.
"""

import os
import shutil
import tempfile
from pathlib import Path

import pytest
import pytest_asyncio

from obsidian_mcp.tools.note_management import create_note, read_note
from obsidian_mcp.utils.filesystem import init_vault
from obsidian_mcp.utils.vault_config import build_template_info


@pytest_asyncio.fixture
async def vault_template_with_name_description():
    """Template folder rule whose own template file already declares
    `name`/`description` (plus an unrelated `status` key) as required
    frontmatter keys."""
    temp_dir = tempfile.mkdtemp(prefix="obsidian_reqfm_tmpl_")
    templates_dir = Path(temp_dir) / "templates"
    templates_dir.mkdir()
    (templates_dir / "projeto.md").write_text(
        "---\nname: \ndescription: \nstatus: \n---\n\n## Objetivo\n"
    )
    (Path(temp_dir) / "01-projects").mkdir()

    os.environ["OBSIDIAN_REQUIRE_FRONTMATTER"] = "true"
    os.environ["OBSIDIAN_FOLDER_TEMPLATES"] = (
        '[{"folder":"01-projects","template":"templates/projeto.md"}]'
    )
    vault = init_vault(temp_dir)
    yield vault
    os.environ.pop("OBSIDIAN_REQUIRE_FRONTMATTER", None)
    os.environ.pop("OBSIDIAN_FOLDER_TEMPLATES", None)
    shutil.rmtree(temp_dir)


@pytest_asyncio.fixture
async def vault_template_without_name_description():
    """Template folder rule that only requires an unrelated `status` key —
    name/description are entirely the require_frontmatter layer's concern
    here."""
    temp_dir = tempfile.mkdtemp(prefix="obsidian_reqfm_tmpl2_")
    templates_dir = Path(temp_dir) / "templates"
    templates_dir.mkdir()
    (templates_dir / "projeto.md").write_text("---\nstatus: \n---\n\n## Objetivo\n")
    (Path(temp_dir) / "01-projects").mkdir()

    os.environ["OBSIDIAN_REQUIRE_FRONTMATTER"] = "true"
    os.environ["OBSIDIAN_FOLDER_TEMPLATES"] = (
        '[{"folder":"01-projects","template":"templates/projeto.md"}]'
    )
    vault = init_vault(temp_dir)
    yield vault
    os.environ.pop("OBSIDIAN_REQUIRE_FRONTMATTER", None)
    os.environ.pop("OBSIDIAN_FOLDER_TEMPLATES", None)
    shutil.rmtree(temp_dir)


class TestTemplateKeysAlreadyIncludingNameDescription:
    @pytest.mark.asyncio
    async def test_missing_name_key_entirely_is_a_template_error_not_a_frontmatter_one(
        self, vault_template_with_name_description
    ):
        # description present, status present, but the `name` KEY itself is
        # absent from frontmatter -> this must fail at the template-
        # conformance layer (missing key), never reach the require_frontmatter
        # layer at all.
        with pytest.raises(ValueError, match="Missing frontmatter keys"):
            await create_note(
                "01-projects/Bad.md",
                "---\ndescription: x\nstatus: active\n---\n\n## Objetivo\n",
            )

    @pytest.mark.asyncio
    async def test_all_keys_present_but_empty_description_is_a_require_frontmatter_error(
        self, vault_template_with_name_description
    ):
        # All 3 keys are PRESENT (satisfies template conformance, which only
        # checks key presence) but description's VALUE is empty -> template
        # check passes silently; require_frontmatter's own value-quality
        # check is what actually raises. Only one error surfaces, not two
        # conflicting requests.
        with pytest.raises(ValueError, match="description") as excinfo:
            await create_note(
                "01-projects/Bad.md",
                '---\nname: whatever\ndescription: ""\nstatus: active\n---\n\n## Objetivo\n',
            )
        assert "Missing frontmatter keys" not in str(excinfo.value)

    @pytest.mark.asyncio
    async def test_fully_conformant_succeeds_and_name_is_forced_to_filename(
        self, vault_template_with_name_description
    ):
        result = await create_note(
            "01-projects/My Note.md",
            "---\nname: some-arbitrary-value\ndescription: Real summary.\nstatus: active\n---\n\n"
            "## Objetivo\n\nGoal text.\n",
        )
        assert result["success"] is True

        note = await read_note("01-projects/My Note.md")
        frontmatter = note["details"]["metadata"]["frontmatter"]
        # Template conformance only cared that `name` was present with SOME
        # value; require_frontmatter still overwrites it to match the
        # filename, same as with no template at all.
        assert frontmatter["name"] == "My Note"
        assert frontmatter["description"] == "Real summary."
        assert frontmatter["status"] == "active"


class TestTemplateWithoutNameDescriptionKeys:
    @pytest.mark.asyncio
    async def test_require_frontmatter_still_enforces_description_on_top(
        self, vault_template_without_name_description
    ):
        # Template conformance only requires `status` — passes. But
        # REQUIRE_FRONTMATTER is a vault-wide policy independent of any
        # template, so description is still required "on top" (spec
        # section 10.3: "se não [prevê], exige por cima").
        with pytest.raises(ValueError, match="description"):
            await create_note(
                "01-projects/Bad.md", "---\nstatus: active\n---\n\n## Objetivo\n"
            )

    @pytest.mark.asyncio
    async def test_conformant_with_both_layers_satisfied_succeeds(
        self, vault_template_without_name_description
    ):
        result = await create_note(
            "01-projects/Good.md",
            "---\nstatus: active\ndescription: A real summary.\n---\n\n## Objetivo\n",
        )
        assert result["success"] is True
        note = await read_note("01-projects/Good.md")
        frontmatter = note["details"]["metadata"]["frontmatter"]
        assert frontmatter["name"] == "Good"
        assert frontmatter["status"] == "active"


class TestRequireFrontmatterOffTemplateStillEnforced:
    @pytest.mark.asyncio
    async def test_template_conformance_independent_of_require_frontmatter_off(self):
        temp_dir = tempfile.mkdtemp(prefix="obsidian_reqfm_off_tmpl_")
        try:
            templates_dir = Path(temp_dir) / "templates"
            templates_dir.mkdir()
            (templates_dir / "projeto.md").write_text("---\nstatus: \n---\n\n## Objetivo\n")
            (Path(temp_dir) / "01-projects").mkdir()

            os.environ["OBSIDIAN_REQUIRE_FRONTMATTER"] = "false"
            os.environ["OBSIDIAN_FOLDER_TEMPLATES"] = (
                '[{"folder":"01-projects","template":"templates/projeto.md"}]'
            )
            init_vault(temp_dir)

            # Missing the template's `status` key -> still rejected, even
            # though REQUIRE_FRONTMATTER is off (the two configs are
            # independent knobs).
            with pytest.raises(ValueError, match="Missing frontmatter keys"):
                await create_note("01-projects/Bad.md", "## Objetivo\n")

            # No description required this time (REQUIRE_FRONTMATTER off) —
            # only the template's own key matters.
            result = await create_note(
                "01-projects/Good.md", "---\nstatus: active\n---\n\n## Objetivo\n"
            )
            assert result["success"] is True
        finally:
            os.environ.pop("OBSIDIAN_REQUIRE_FRONTMATTER", None)
            os.environ.pop("OBSIDIAN_FOLDER_TEMPLATES", None)
            shutil.rmtree(temp_dir, ignore_errors=True)


class TestRequiredFrontmatterKeysAlwaysListDescription:
    """build_template_info's required_frontmatter_keys must surface
    'description' whenever OBSIDIAN_REQUIRE_FRONTMATTER is on, even when no
    folder template declares it (or no folder template applies at all) —
    otherwise a caller that trusts this list for a single-pass compliant
    write clears template conformance only to hit a second, avoidable error
    from apply_frontmatter_requirements."""

    @pytest.mark.asyncio
    async def test_template_without_description_still_lists_it(
        self, vault_template_without_name_description
    ):
        info = build_template_info(vault_template_without_name_description, "01-projects")
        assert info["enforced"] is True
        assert "description" in info["required_frontmatter_keys"]
        assert "status" in info["required_frontmatter_keys"]

    @pytest.mark.asyncio
    async def test_template_already_declaring_description_not_duplicated(
        self, vault_template_with_name_description
    ):
        info = build_template_info(vault_template_with_name_description, "01-projects")
        assert info["required_frontmatter_keys"].count("description") == 1

    @pytest.mark.asyncio
    async def test_no_template_rule_at_all_still_lists_description(self):
        temp_dir = tempfile.mkdtemp(prefix="obsidian_reqfm_notmpl_")
        try:
            os.environ["OBSIDIAN_REQUIRE_FRONTMATTER"] = "true"
            os.environ.pop("OBSIDIAN_FOLDER_TEMPLATES", None)
            vault = init_vault(temp_dir)
            info = build_template_info(vault, "02-areas")
            assert info["enforced"] is False
            assert info["required_frontmatter_keys"] == ["description"]
        finally:
            os.environ.pop("OBSIDIAN_REQUIRE_FRONTMATTER", None)
            shutil.rmtree(temp_dir, ignore_errors=True)

    @pytest.mark.asyncio
    async def test_require_frontmatter_off_never_adds_description(self):
        temp_dir = tempfile.mkdtemp(prefix="obsidian_reqfm_off_notmpl_")
        try:
            templates_dir = Path(temp_dir) / "templates"
            templates_dir.mkdir()
            (templates_dir / "projeto.md").write_text("---\nstatus: \n---\n\n## Objetivo\n")
            (Path(temp_dir) / "01-projects").mkdir()

            os.environ["OBSIDIAN_REQUIRE_FRONTMATTER"] = "false"
            os.environ["OBSIDIAN_FOLDER_TEMPLATES"] = (
                '[{"folder":"01-projects","template":"templates/projeto.md"}]'
            )
            vault = init_vault(temp_dir)
            info = build_template_info(vault, "01-projects")
            assert info["required_frontmatter_keys"] == ["status"]
        finally:
            os.environ.pop("OBSIDIAN_REQUIRE_FRONTMATTER", None)
            os.environ.pop("OBSIDIAN_FOLDER_TEMPLATES", None)
            shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
