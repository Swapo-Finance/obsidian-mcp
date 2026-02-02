"""Test validation allows quotes in filenames."""

import pytest
from obsidian_mcp.utils.validation import validate_note_path


def test_quotes_in_filenames():
    """Test that quotes are allowed in note paths."""
    # Valid paths with quotes
    assert validate_note_path("Note with \"quotes\".md")[0] is True
    assert validate_note_path("'Single quotes' note.md")[0] is True
    assert validate_note_path("Meeting \"Project X\" Notes.md")[0] is True
    assert validate_note_path("Book Review - \"1984\".md")[0] is True
    assert validate_note_path("Ideas/'Best Practices'.md")[0] is True
    
    # Still reject other invalid characters
    assert validate_note_path("note<with>bad.md")[0] is False
    assert validate_note_path("note:with:colon.md")[0] is False
    assert validate_note_path("note|pipe.md")[0] is False
    assert validate_note_path("note?question.md")[0] is False
    assert validate_note_path("note*star.md")[0] is False
    
    print("✓ All quote validation tests passed!")


if __name__ == "__main__":
    test_quotes_in_filenames()