#!/usr/bin/env python3
"""Tests for edit_note_section functionality."""

import pytest
import pytest_asyncio
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