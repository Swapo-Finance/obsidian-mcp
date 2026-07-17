#!/usr/bin/env python3
"""Tests for edit_note_section functionality."""

import pytest
import pytest_asyncio
import unicodedata
from pathlib import Path
import tempfile
import shutil

from obsidian_mcp.tools.note_management import edit_note_section
from obsidian_mcp.utils.filesystem import init_vault


class TestEditNoteSection:
    """Test suite for edit_note_section function."""
    
    @pytest_asyncio.fixture
    async def test_vault(self):
        """Create a test vault with notes."""
        temp_dir = tempfile.mkdtemp(prefix="obsidian_test_")
        
        # Initialize vault
        init_vault(temp_dir)
        
        # Create test notes
        notes_dir = Path(temp_dir)
        
        # Note with multiple sections
        (notes_dir / "structured.md").write_text("""# Main Document

## Introduction

This is the introduction section.

## Tasks

- [x] Task 1
- [ ] Task 2

### Subtasks

Some subtasks here.

## Status Updates

### 2024-01-01

Initial status.

## Conclusion

Final thoughts.
""")
        
        # Note with single section
        (notes_dir / "simple.md").write_text("""# Simple Note

Just a simple note with one section.
""")
        
        # Empty note
        (notes_dir / "empty.md").write_text("")
        
        yield temp_dir
        
        # Cleanup
        shutil.rmtree(temp_dir)
    
    @pytest.mark.asyncio
    async def test_insert_after_section(self, test_vault):
        """Test inserting content after a section heading."""
        result = await edit_note_section(
            path="structured.md",
            section_identifier="## Tasks",
            content="- [ ] Task 3\n- [ ] Task 4",
            operation="insert_after"
        )
        
        assert result["success"] is True
        assert result["section_found"] is True
        assert result["section_created"] is False
        assert result["edit_type"] == "insert_after"
        
        # Verify content
        content = Path(test_vault, "structured.md").read_text()
        assert "## Tasks\n\n- [ ] Task 3\n- [ ] Task 4" in content
        assert "- [x] Task 1" in content  # Original content preserved
    
    @pytest.mark.asyncio
    async def test_insert_before_section(self, test_vault):
        """Test inserting content before a section heading."""
        result = await edit_note_section(
            path="structured.md",
            section_identifier="## Status Updates",
            content="*Last updated: 2024-01-15*",
            operation="insert_before"
        )
        
        assert result["success"] is True
        assert result["section_found"] is True
        
        # Verify content
        content = Path(test_vault, "structured.md").read_text()
        assert "*Last updated: 2024-01-15*\n\n## Status Updates" in content
    
    @pytest.mark.asyncio
    async def test_replace_section(self, test_vault):
        """Test replacing an entire section."""
        result = await edit_note_section(
            path="structured.md",
            section_identifier="## Introduction",
            content="## Introduction\n\nThis is the new introduction with updated content.",
            operation="replace"
        )
        
        assert result["success"] is True
        assert result["section_found"] is True
        
        # Verify content
        content = Path(test_vault, "structured.md").read_text()
        assert "This is the new introduction with updated content." in content
        assert "This is the introduction section." not in content  # Old content gone
        assert "## Tasks" in content  # Other sections preserved
    
    @pytest.mark.asyncio
    async def test_append_to_section(self, test_vault):
        """Test appending to the end of a section."""
        result = await edit_note_section(
            path="structured.md",
            section_identifier="### 2024-01-01",
            content="Additional notes for this date.",
            operation="append_to_section"
        )
        
        assert result["success"] is True
        assert result["section_found"] is True
        
        # Verify content
        content = Path(test_vault, "structured.md").read_text()
        # Content should be added before the next section
        assert "Initial status.\n\nAdditional notes for this date.\n\n## Conclusion" in content
    
    @pytest.mark.asyncio
    async def test_create_missing_section(self, test_vault):
        """Test creating a section when it doesn't exist."""
        result = await edit_note_section(
            path="simple.md",
            section_identifier="## New Section",
            content="This is new content.",
            operation="insert_after",
            create_if_missing=True
        )
        
        assert result["success"] is True
        assert result["section_found"] is False
        assert result["section_created"] is True
        
        # Verify content
        content = Path(test_vault, "simple.md").read_text()
        assert "## New Section\n\nThis is new content." in content
    
    @pytest.mark.asyncio
    async def test_missing_section_error(self, test_vault):
        """Test error when section is missing and create_if_missing is False."""
        with pytest.raises(ValueError) as exc_info:
            await edit_note_section(
                path="simple.md",
                section_identifier="## Nonexistent",
                content="Content",
                operation="insert_after",
                create_if_missing=False
            )
        
        assert "not found" in str(exc_info.value)
    
    @pytest.mark.asyncio
    async def test_invalid_section_identifier(self, test_vault):
        """Test error with invalid section identifier."""
        with pytest.raises(ValueError) as exc_info:
            await edit_note_section(
                path="simple.md",
                section_identifier="Not a heading",
                content="Content",
                operation="insert_after"
            )
        
        assert "Invalid section identifier" in str(exc_info.value)
    
    @pytest.mark.asyncio
    async def test_invalid_operation(self, test_vault):
        """Test error with invalid operation."""
        with pytest.raises(ValueError) as exc_info:
            await edit_note_section(
                path="simple.md",
                section_identifier="# Simple Note",
                content="Content",
                operation="invalid_op"
            )
        
        assert "Invalid operation" in str(exc_info.value)
    
    @pytest.mark.asyncio
    async def test_case_insensitive_matching(self, test_vault):
        """Test that section matching is case-insensitive."""
        result = await edit_note_section(
            path="structured.md",
            section_identifier="## TASKS",  # Different case
            content="Case insensitive test",
            operation="insert_after"
        )
        
        assert result["success"] is True
        assert result["section_found"] is True
    
    @pytest.mark.asyncio
    async def test_nested_sections(self, test_vault):
        """Test editing nested sections respects hierarchy."""
        result = await edit_note_section(
            path="structured.md",
            section_identifier="### Subtasks",
            content="- Subtask A\n- Subtask B",
            operation="replace"
        )
        
        assert result["success"] is True
        
        # Verify content
        content = Path(test_vault, "structured.md").read_text()
        assert "- Subtask A\n- Subtask B" in content
        assert "Some subtasks here." not in content
        assert "## Status Updates" in content  # Next section preserved
    
    @pytest.mark.asyncio
    async def test_empty_note_section_creation(self, test_vault):
        """Test adding a section to an empty note."""
        result = await edit_note_section(
            path="empty.md",
            section_identifier="# New Title",
            content="Content for empty note.",
            operation="insert_after",
            create_if_missing=True
        )
        
        assert result["success"] is True
        assert result["section_created"] is True
        
        # Verify content
        content = Path(test_vault, "empty.md").read_text()
        assert "# New Title\n\nContent for empty note." in content
    
    @pytest.mark.asyncio
    async def test_section_at_end_of_file(self, test_vault):
        """Test editing the last section in a file."""
        result = await edit_note_section(
            path="structured.md",
            section_identifier="## Conclusion",
            content="More final thoughts.",
            operation="append_to_section"
        )
        
        assert result["success"] is True
        
        # Verify content
        content = Path(test_vault, "structured.md").read_text()
        assert content.endswith("Final thoughts.\n\nMore final thoughts.\n")

    @pytest_asyncio.fixture
    async def accented_vault_factory(self):
        """Factory fixture: build a temp vault with an accented-heading note
        written in a caller-chosen Unicode normalization form (NFC or NFD).
        Mirrors the real-world scenario where a note saved by an app that
        writes NFD-decomposed text (common on macOS) is edited by a caller
        that types/generates section identifiers in NFC (the common form for
        JSON/tool-call arguments). Returns the temp vault dir path."""
        temp_dir = tempfile.mkdtemp(prefix="obsidian_test_accents_")

        init_vault(temp_dir)

        def write(form: str) -> str:
            raw = (
                "# Nota\n\n"
                "## Decisões\n\n"
                "Conteúdo inicial de decisões.\n\n"
                "## Problemas\n\n"
                "Conteúdo inicial de problemas.\n\n"
                "## Próximos passos\n\n"
                "Conteúdo inicial de próximos passos.\n\n"
                "### Ação imediata\n\n"
                "Conteúdo inicial de ação imediata.\n"
            )
            normalized = unicodedata.normalize(form, raw)
            note_path = Path(temp_dir, "accented.md")
            note_path.write_text(normalized, encoding="utf-8")
            # Sanity: confirm the file actually landed in the requested form,
            # otherwise the test would silently stop testing what it claims to.
            on_disk = note_path.read_text(encoding="utf-8")
            assert on_disk == normalized
            return temp_dir

        yield write

        shutil.rmtree(temp_dir)

    @pytest.mark.asyncio
    async def test_accented_heading_nfd_file_nfc_identifier(self, accented_vault_factory):
        """File on disk is NFD (decomposed, common on macOS); the
        section_identifier argument is NFC (the typical typed/JSON form).
        Regression test: previously this raised 'Section not found' because
        the raw comparison did not reconcile the two normalization forms."""
        temp_dir = accented_vault_factory("NFD")

        nfc_identifier = unicodedata.normalize("NFC", "## Decisões")
        result = await edit_note_section(
            path="accented.md",
            section_identifier=nfc_identifier,
            content="Marcador NFC contra arquivo NFD.",
            operation="append_to_section"
        )

        assert result["success"] is True
        assert result["section_found"] is True

        content = Path(temp_dir, "accented.md").read_text(encoding="utf-8")
        assert "Marcador NFC contra arquivo NFD." in content

    @pytest.mark.asyncio
    async def test_accented_heading_nfc_file_nfd_identifier(self, accented_vault_factory):
        """Symmetric case: file on disk is NFC, section_identifier argument
        is NFD. Must also match -- the fix normalizes both sides."""
        temp_dir = accented_vault_factory("NFC")

        nfd_identifier = unicodedata.normalize("NFD", "## Próximos passos")
        result = await edit_note_section(
            path="accented.md",
            section_identifier=nfd_identifier,
            content="Marcador NFD contra arquivo NFC.",
            operation="append_to_section"
        )

        assert result["success"] is True
        assert result["section_found"] is True

        content = Path(temp_dir, "accented.md").read_text(encoding="utf-8")
        assert "Marcador NFD contra arquivo NFC." in content

    @pytest.mark.asyncio
    async def test_accented_nested_heading_nfd_file(self, accented_vault_factory):
        """Hierarchical case: an H3 accented heading nested under an H2,
        file saved as NFD, identifier passed as NFC. Confirms the fix also
        covers nested/hierarchical sections, not just top-level ones."""
        temp_dir = accented_vault_factory("NFD")

        nfc_identifier = unicodedata.normalize("NFC", "### Ação imediata")
        result = await edit_note_section(
            path="accented.md",
            section_identifier=nfc_identifier,
            content="Marcador NFC contra H3 aninhado em arquivo NFD.",
            operation="append_to_section"
        )

        assert result["success"] is True
        assert result["section_found"] is True

        content = Path(temp_dir, "accented.md").read_text(encoding="utf-8")
        assert "Marcador NFC contra H3 aninhado em arquivo NFD." in content

    @pytest.mark.asyncio
    async def test_unaccented_heading_still_matches_nfd_file(self, accented_vault_factory):
        """Control case: a heading with no accented characters must keep
        matching regardless of the file's normalization form -- proves the
        fix is scoped to Unicode composition and doesn't change behavior
        for plain ASCII headings."""
        temp_dir = accented_vault_factory("NFD")

        result = await edit_note_section(
            path="accented.md",
            section_identifier="## Problemas",
            content="Marcador de controle sem acento.",
            operation="append_to_section"
        )

        assert result["success"] is True
        assert result["section_found"] is True

        content = Path(temp_dir, "accented.md").read_text(encoding="utf-8")
        assert "Marcador de controle sem acento." in content
