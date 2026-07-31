"""Test find_orphaned_notes functionality."""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.asyncio
async def test_find_orphaned_notes_no_backlinks(monkeypatch):
    """Test finding notes with no backlinks."""
    # Create mock vault
    mock_vault = MagicMock()

    # Mock list_notes to return test notes
    mock_vault.list_notes = AsyncMock(
        return_value=[
            {"path": "Note1.md"},
            {"path": "Note2.md"},
            {"path": "Templates/Template.md"},  # Should be excluded
            {"path": "Note3.md"},
        ]
    )

    # Mock read_note (NoteMetadata.modified is a datetime)
    async def mock_read_note(path):
        mock_note = MagicMock()
        mock_note.path = path
        mock_note.metadata.modified = datetime.now()
        mock_note.metadata.size = 100
        mock_note.metadata.word_count = 50
        mock_note.metadata.tags = ["test"]
        mock_note.metadata.frontmatter = {"title": "Test"}
        return mock_note

    mock_vault.read_note = AsyncMock(side_effect=mock_read_note)

    # Mock the module-level get_backlinks - Note2 has backlinks, others don't.
    # get_backlinks returns {"findings": [...]}, which is what the tool reads.
    async def mock_get_backlinks(path, *args, **kwargs):
        if path == "Note2.md":
            return {
                "findings": [
                    {
                        "source_path": "Note1.md",
                        "link_text": "Note2",
                        "link_type": "wiki",
                    }
                ]
            }
        return {"findings": []}

    # find_orphaned_notes did `from ..utils.filesystem import get_vault` and
    # `from .link_management import get_backlinks`, so patch both names in the
    # tool module's own namespace.
    import sys

    from obsidian_mcp.tools.find_orphaned_notes import find_orphaned_notes

    # tools/__init__ re-exports the `find_orphaned_notes` function, which shadows
    # the submodule of the same name in the package namespace. Grab the real
    # module object from sys.modules so we can patch its module-level names.
    fon_module = sys.modules["obsidian_mcp.tools.find_orphaned_notes"]
    monkeypatch.setattr(fon_module, "get_vault", lambda: mock_vault)
    monkeypatch.setattr(
        fon_module, "get_backlinks", AsyncMock(side_effect=mock_get_backlinks)
    )

    # Test finding orphaned notes
    result = await find_orphaned_notes(
        orphan_type="no_backlinks", exclude_folders=["Templates"]
    )

    # Verify results
    assert (
        result["count"] == 2
    )  # Note1 and Note3 (Note2 has backlinks, Template excluded)
    assert len(result["orphaned_notes"]) == 2

    paths = [note["path"] for note in result["orphaned_notes"]]
    assert "Note1.md" in paths
    assert "Note3.md" in paths
    assert "Note2.md" not in paths  # Has backlinks
    assert "Templates/Template.md" not in paths  # Excluded

    # Check note details
    for note in result["orphaned_notes"]:
        assert note["reason"] == "No incoming links"
        assert "modified" in note
        assert "size" in note
        assert "word_count" in note

    print("✓ Find orphaned notes (no backlinks) test passed!")


@pytest.mark.asyncio
async def test_find_orphaned_notes_with_age_filter(monkeypatch):
    """Test finding orphaned notes with age filter."""
    mock_vault = MagicMock()

    # Create notes with different ages (NoteMetadata.modified is a datetime)
    old_date = datetime(2023, 1, 1, 10, 0, 0)  # Definitely older than 30 days
    recent_date = datetime.now()  # Current time

    mock_vault.list_notes = AsyncMock(
        return_value=[{"path": "OldNote.md"}, {"path": "RecentNote.md"}]
    )

    async def mock_read_note(path):
        mock_note = MagicMock()
        mock_note.path = path
        mock_note.metadata.modified = old_date if path == "OldNote.md" else recent_date
        mock_note.metadata.size = 100
        mock_note.metadata.word_count = 50
        mock_note.metadata.tags = []
        mock_note.metadata.frontmatter = {}
        return mock_note

    mock_vault.read_note = AsyncMock(side_effect=mock_read_note)

    # Patch get_vault and get_backlinks in the tool module's namespace.
    # get_backlinks returns {"findings": [...]}; empty findings => all orphaned.
    import sys

    from obsidian_mcp.tools.find_orphaned_notes import find_orphaned_notes

    # tools/__init__ re-exports the `find_orphaned_notes` function, which shadows
    # the submodule of the same name in the package namespace. Grab the real
    # module object from sys.modules so we can patch its module-level names.
    fon_module = sys.modules["obsidian_mcp.tools.find_orphaned_notes"]
    monkeypatch.setattr(fon_module, "get_vault", lambda: mock_vault)
    monkeypatch.setattr(
        fon_module, "get_backlinks", AsyncMock(return_value={"findings": []})
    )

    # Test with 30 day age filter
    result = await find_orphaned_notes(orphan_type="no_backlinks", min_age_days=30)

    # Should only find the old note
    assert result["count"] == 1
    assert result["orphaned_notes"][0]["path"] == "OldNote.md"

    print("✓ Find orphaned notes with age filter test passed!")


class TestParseNoteModified:
    """Unit tests for the extracted _parse_note_modified helper: the
    date-normalization block that used to be inlined in find_orphaned_notes'
    age-filter check (isinstance check -> "Z"-suffix rewrite -> try/except
    ISO parse with fallback -> tzinfo strip).
    """

    def test_naive_datetime_passes_through_unchanged(self):
        from obsidian_mcp.tools.find_orphaned_notes import _parse_note_modified

        naive = datetime(2024, 1, 1, 12, 30, 0)
        assert _parse_note_modified(naive) == naive

    def test_offset_aware_datetime_has_tzinfo_stripped(self):
        from datetime import timezone

        from obsidian_mcp.tools.find_orphaned_notes import _parse_note_modified

        aware = datetime(2024, 1, 1, 12, 30, 0, tzinfo=timezone.utc)
        result = _parse_note_modified(aware)
        assert result == datetime(2024, 1, 1, 12, 30, 0)
        assert result.tzinfo is None

    def test_z_suffixed_string_is_parsed_then_made_naive(self):
        from obsidian_mcp.tools.find_orphaned_notes import _parse_note_modified

        result = _parse_note_modified("2024-01-01T12:30:00Z")
        assert result == datetime(2024, 1, 1, 12, 30, 0)
        assert result.tzinfo is None

    def test_offset_string_is_parsed_then_made_naive(self):
        from obsidian_mcp.tools.find_orphaned_notes import _parse_note_modified

        result = _parse_note_modified("2024-01-01T12:30:00+02:00")
        assert result == datetime(2024, 1, 1, 12, 30, 0)
        assert result.tzinfo is None

    def test_naive_string_is_parsed_directly(self):
        from obsidian_mcp.tools.find_orphaned_notes import _parse_note_modified

        assert _parse_note_modified("2024-01-01T12:30:00") == datetime(
            2024, 1, 1, 12, 30, 0
        )

    def test_unparseable_string_falls_back_to_stripped_prefix(self):
        # A trailing IANA zone-name suffix (as some datetime libraries emit,
        # e.g. "...+05:00[Asia/Karachi]") isn't valid for
        # datetime.fromisoformat, so the primary parse raises ValueError and
        # the fallback strips everything from "+"/"." onward before retrying.
        from obsidian_mcp.tools.find_orphaned_notes import _parse_note_modified

        result = _parse_note_modified("2024-01-01T12:30:00.123456+05:00[Asia/Karachi]")
        assert result == datetime(2024, 1, 1, 12, 30, 0)


class TestGetLinkState:
    """Unit tests for the extracted _get_link_state helper: the
    backlinks+outgoing-links fetch previously duplicated verbatim across
    the no_links and isolated branches of find_orphaned_notes'
    orphan_type dispatch.
    """

    @pytest.mark.asyncio
    async def test_returns_backlinks_and_outgoing_findings_as_tuple(self, monkeypatch):
        import sys

        from obsidian_mcp.tools.find_orphaned_notes import _get_link_state

        fon_module = sys.modules["obsidian_mcp.tools.find_orphaned_notes"]
        monkeypatch.setattr(
            fon_module,
            "get_backlinks",
            AsyncMock(return_value={"findings": [{"source_path": "A.md"}]}),
        )
        monkeypatch.setattr(
            fon_module,
            "get_outgoing_links",
            AsyncMock(return_value={"findings": [{"path": "B.md"}]}),
        )

        backlinks, outgoing = await _get_link_state("Note.md")

        assert backlinks == [{"source_path": "A.md"}]
        assert outgoing == [{"path": "B.md"}]

    @pytest.mark.asyncio
    async def test_defaults_to_empty_lists_when_findings_key_missing(self, monkeypatch):
        import sys

        from obsidian_mcp.tools.find_orphaned_notes import _get_link_state

        fon_module = sys.modules["obsidian_mcp.tools.find_orphaned_notes"]
        monkeypatch.setattr(fon_module, "get_backlinks", AsyncMock(return_value={}))
        monkeypatch.setattr(
            fon_module, "get_outgoing_links", AsyncMock(return_value={})
        )

        backlinks, outgoing = await _get_link_state("Note.md")

        assert backlinks == []
        assert outgoing == []


if __name__ == "__main__":
    import asyncio

    with pytest.MonkeyPatch.context() as mp:
        asyncio.run(test_find_orphaned_notes_no_backlinks(mp))
    # asyncio.run(test_find_orphaned_notes_with_age_filter())  # Skip for now due to date issues
