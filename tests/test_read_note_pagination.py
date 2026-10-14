"""read_note paging (offset/max_chars), outline, and section reads, plus the
OBSIDIAN_READ_MAX_CHARS knob that sets the default page size.

A long note used to come back whole. A 63,544-character note made Claude Code
save the result to a file. See
docs/superpowers/specs/2026-10-14-read-note-pagination-design.md.
"""

import re

import pydantic_core
import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError
from mcp.types import TextContent

from obsidian_mcp.constants import ERROR_MESSAGES
from obsidian_mcp.server import mcp, read_note_tool
from obsidian_mcp.tools.note_read import read_note
from obsidian_mcp.utils.filesystem import init_vault

TEN_LINES = "\n".join("y" * 299 for _ in range(10))  # 2,999 chars, "\n" every 300
DOC = (
    "---\ntags: [a]\n---\n"
    "# Doc\n\nIntro.\n\n"
    "## Tasks\n\n- one\n\n### Sub\n\nsub body\n\n"
    "## Notes\n\nnotes body\n"
)


@pytest.fixture
def vault(tmp_path):
    return init_vault(str(tmp_path))


def _write(vault, relpath: str, text: str) -> None:
    (vault.vault_path / relpath).write_text(text, encoding="utf-8")


def _error(key: str, **values: object) -> str:
    """Pattern that matches ERROR_MESSAGES[key], filled in, exactly."""
    return re.escape(ERROR_MESSAGES[key].format(**values))


async def _read_all(path: str, max_chars: int) -> list[str]:
    """Follow next_offset from offset 0 and return the content of each page."""
    pages: list[str] = []
    offset: int | None = 0
    while offset is not None:
        details = (await read_note(path, offset=offset, max_chars=max_chars))["details"]
        pages.append(details["content"])
        offset = details.get("next_offset")
    return pages


class TestReadMaxCharsEnv:
    def test_defaults_are_40000(self, tmp_path, monkeypatch):
        monkeypatch.delenv("OBSIDIAN_READ_MAX_CHARS", raising=False)
        monkeypatch.delenv("OBSIDIAN_MAX_NOTE_CHARS", raising=False)

        vault = init_vault(str(tmp_path))

        assert vault.read_max_chars == 40000
        assert vault.max_note_chars == 40000

    @pytest.mark.parametrize("raw", ["1000", "5000", "1000000"])
    def test_value_in_range_is_used(self, tmp_path, monkeypatch, raw):
        monkeypatch.setenv("OBSIDIAN_READ_MAX_CHARS", raw)

        assert init_vault(str(tmp_path)).read_max_chars == int(raw)

    @pytest.mark.parametrize("raw", ["999", "1000001"])
    def test_value_out_of_range_falls_back_to_default(self, tmp_path, monkeypatch, raw):
        monkeypatch.setenv("OBSIDIAN_READ_MAX_CHARS", raw)

        assert init_vault(str(tmp_path)).read_max_chars == 40000


class TestPagedRead:
    @pytest.mark.asyncio
    async def test_note_that_fits_returns_todays_response_byte_for_byte(self, vault):
        text = "# Small\n\nBody.\n"
        _write(vault, "small.md", text)
        note = await vault.read_note("small.md")
        expected = {
            "success": True,
            "path": "small.md",
            "operation": "read",
            "details": {
                "content": text,
                "metadata": note.metadata.model_dump(exclude_none=True),
            },
        }

        result = await read_note("small.md")

        # pydantic_core.to_json is the serializer FastMCP uses for the result.
        assert pydantic_core.to_json(result) == pydantic_core.to_json(expected)

    @pytest.mark.asyncio
    async def test_page_ends_after_the_last_newline_in_the_window(self, vault):
        _write(vault, "long.md", TEN_LINES)

        details = (await read_note("long.md", max_chars=1000))["details"]

        assert details["content"] == TEN_LINES[:900]
        assert details["truncated"] is True
        assert details["next_offset"] == 900
        assert details["total_chars"] == 2999
        # The message tells how to go on and warns against a partial replace.
        assert "offset=900" in details["message"]
        assert "update_note" in details["message"]

    @pytest.mark.asyncio
    async def test_pages_join_back_to_the_exact_note(self, vault):
        _write(vault, "long.md", TEN_LINES)

        pages = await _read_all("long.md", max_chars=1000)

        assert [len(page) for page in pages] == [900, 900, 900, 299]
        assert "".join(pages) == TEN_LINES

    @pytest.mark.asyncio
    async def test_last_page_has_no_next_offset_and_no_message(self, vault):
        _write(vault, "long.md", TEN_LINES)

        details = (await read_note("long.md", offset=2700, max_chars=1000))["details"]

        assert details["content"] == TEN_LINES[2700:]
        assert details["truncated"] is False
        assert details["next_offset"] is None
        assert "message" not in details

    @pytest.mark.asyncio
    async def test_line_longer_than_the_window_is_cut_hard(self, vault):
        _write(vault, "wide.md", "z" * 2500)

        pages = await _read_all("wide.md", max_chars=1000)

        assert [len(page) for page in pages] == [1000, 1000, 500]

    @pytest.mark.asyncio
    async def test_offset_inside_a_line_starts_the_page_there(self, vault):
        _write(vault, "long.md", TEN_LINES)

        details = (await read_note("long.md", offset=150, max_chars=1000))["details"]

        assert details["content"] == TEN_LINES[150:900]

    @pytest.mark.asyncio
    async def test_offsets_count_characters_not_bytes(self, vault):
        line = "ação 🚀 " * 30  # 210 characters, more bytes in UTF-8
        text = "\n".join(line for _ in range(10))
        _write(vault, "utf.md", text)

        pages = await _read_all("utf.md", max_chars=1000)

        assert "".join(pages) == text
        assert all(len(page) <= 1000 for page in pages)

    @pytest.mark.asyncio
    async def test_page_size_defaults_to_obsidian_read_max_chars(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("OBSIDIAN_READ_MAX_CHARS", "1000")
        vault = init_vault(str(tmp_path))
        _write(vault, "long.md", TEN_LINES)

        details = (await read_note("long.md"))["details"]

        assert details["next_offset"] == 900

    @pytest.mark.asyncio
    async def test_offset_at_the_end_is_an_error(self, vault):
        _write(vault, "long.md", TEN_LINES)

        with pytest.raises(
            ValueError,
            match=_error(
                "read_offset_out_of_range",
                offset=2999,
                total_chars=2999,
                path="long.md",
            ),
        ):
            await read_note("long.md", offset=2999)

    @pytest.mark.asyncio
    async def test_empty_note_reads_at_offset_zero_only(self, vault):
        _write(vault, "empty.md", "")

        result = await read_note("empty.md")

        assert result["details"]["content"] == ""
        with pytest.raises(
            ValueError,
            match=_error(
                "read_offset_out_of_range", offset=1, total_chars=0, path="empty.md"
            ),
        ):
            await read_note("empty.md", offset=1)

    @pytest.mark.parametrize(
        ("offset", "max_chars", "start_of_error"),
        [
            (-1, None, "Invalid offset: -1."),
            (0, 999, "Invalid max_chars: 999."),
            (0, 1_000_001, "Invalid max_chars: 1000001."),
        ],
    )
    @pytest.mark.asyncio
    async def test_window_out_of_range_is_rejected(
        self, vault, offset, max_chars, start_of_error
    ):
        _write(vault, "small.md", "# Small\n")

        with pytest.raises(ValueError, match=re.escape(start_of_error)):
            await read_note("small.md", offset=offset, max_chars=max_chars)


class TestOutline:
    @pytest.mark.asyncio
    async def test_outline_gives_offset_and_size_of_each_heading(self, vault):
        _write(vault, "doc.md", DOC)
        doc, tasks, sub, notes = (
            DOC.index(heading)
            for heading in ("# Doc", "## Tasks", "### Sub", "## Notes")
        )

        # offset=1 asks for a paged response from a note that would fit.
        details = (await read_note("doc.md", offset=1, max_chars=1000))["details"]

        assert details["outline"] == [
            {"heading": "Doc", "level": 1, "offset": doc, "chars": len(DOC) - doc},
            {"heading": "Tasks", "level": 2, "offset": tasks, "chars": notes - tasks},
            {"heading": "Sub", "level": 3, "offset": sub, "chars": notes - sub},
            {
                "heading": "Notes",
                "level": 2,
                "offset": notes,
                "chars": len(DOC) - notes,
            },
        ]

    @pytest.mark.asyncio
    async def test_note_without_headings_has_an_empty_outline(self, vault):
        _write(vault, "plain.md", "just text\n" * 5)

        details = (await read_note("plain.md", offset=1, max_chars=1000))["details"]

        assert details["outline"] == []

    @pytest.mark.asyncio
    async def test_outline_drops_the_deepest_level_first(self, vault):
        # 3 H1 + 60 H2 = 63 headings, over the 50-entry cap: the H2 level goes.
        text = "".join(
            f"# H{i}\n" + "".join(f"## S{i}.{j}\n" for j in range(20)) for i in range(3)
        )
        _write(vault, "many.md", text)

        details = (await read_note("many.md", offset=1, max_chars=1000))["details"]

        assert [entry["heading"] for entry in details["outline"]] == ["H0", "H1", "H2"]

    @pytest.mark.asyncio
    async def test_outline_keeps_the_first_50_when_one_level_is_left(self, vault):
        _write(vault, "flat.md", "".join(f"# H{i}\n" for i in range(60)))

        details = (await read_note("flat.md", offset=1, max_chars=1000))["details"]

        assert len(details["outline"]) == 50
        assert details["outline"][-1]["heading"] == "H49"


class TestSectionRead:
    @pytest.mark.asyncio
    async def test_section_runs_from_its_heading_through_its_subsections(self, vault):
        _write(vault, "doc.md", DOC)

        result = await read_note("doc.md", section="## tasks")  # case-insensitive

        expected = DOC[DOC.index("## Tasks") : DOC.index("## Notes")]
        assert result["details"]["content"] == expected
        assert set(result["details"]) == {"content", "metadata"}  # fits: today's shape

    @pytest.mark.asyncio
    async def test_outline_sizes_match_the_section_reads(self, vault):
        _write(vault, "doc.md", DOC)
        outline = (await read_note("doc.md", offset=1, max_chars=1000))["details"][
            "outline"
        ]

        for entry in outline:
            heading = "#" * entry["level"] + " " + entry["heading"]
            content = (await read_note("doc.md", section=heading))["details"]["content"]
            assert content == DOC[entry["offset"] : entry["offset"] + entry["chars"]]

    @pytest.mark.asyncio
    async def test_long_section_is_paged_from_its_heading(self, vault):
        body = "\n".join("s" * 299 for _ in range(5))
        text = "# Intro\n\nhello\n\n## Big\n" + body + "\n## After\nend\n"
        _write(vault, "doc.md", text)
        section = text[text.index("## Big") : text.index("## After")]  # 1,507 chars

        first = (await read_note("doc.md", section="## Big", max_chars=1000))["details"]
        second = (
            await read_note(
                "doc.md", section="## Big", offset=first["next_offset"], max_chars=1000
            )
        )["details"]

        assert first["total_chars"] == len(section)
        assert first["next_offset"] == 907
        # The next call must repeat section, or it reads whole-note text.
        assert "same section" in first["message"]
        assert first["content"] + second["content"] == section
        assert second["next_offset"] is None

    @pytest.mark.asyncio
    async def test_duplicate_heading_reads_the_first_match(self, vault):
        _write(vault, "dup.md", "## Notes\nfirst\n## Notes\nsecond\n")

        result = await read_note("dup.md", section="## Notes")
        outline = (await read_note("dup.md", offset=1, max_chars=1000))["details"][
            "outline"
        ]

        assert result["details"]["content"] == "## Notes\nfirst\n"
        # The outline still lists the second one, so offset can reach it.
        assert [entry["offset"] for entry in outline] == [0, 15]

    @pytest.mark.asyncio
    async def test_section_that_matches_no_heading_is_an_error(self, vault):
        _write(vault, "doc.md", DOC)

        # Wrong level: '### Tasks' does not match '## Tasks'.
        with pytest.raises(
            ValueError,
            match=_error("section_not_found", section="### Tasks", path="doc.md"),
        ):
            await read_note("doc.md", section="### Tasks")
        # Not a heading at all. On an MCP call the schema rejects it first.
        with pytest.raises(
            ValueError,
            match=_error("section_not_found", section="Tasks", path="doc.md"),
        ):
            await read_note("doc.md", section="Tasks")


class TestReadNoteToolWrapper:
    @pytest.mark.asyncio
    async def test_wrapper_passes_offset_max_chars_and_section_through(self, vault):
        _write(vault, "long.md", "# Top\n" + TEN_LINES)
        _write(vault, "doc.md", DOC)

        page = await read_note_tool.fn(path="long.md", offset=6, max_chars=1000)
        section = await read_note_tool.fn(path="doc.md", section="## Notes")

        # Swapped offset/max_chars would fail validation (max_chars=6).
        assert page["details"]["content"] == TEN_LINES[:900]
        assert section["details"]["content"] == "## Notes\n\nnotes body\n"

    @pytest.mark.asyncio
    async def test_wrapper_turns_read_errors_into_tool_errors(self, vault):
        _write(vault, "doc.md", DOC)

        with pytest.raises(ToolError):
            await read_note_tool.fn(path="doc.md", offset=10_000)
        with pytest.raises(ToolError):
            await read_note_tool.fn(path="doc.md", section="## Missing")

    @pytest.mark.asyncio
    async def test_63544_char_note_result_stays_under_the_client_limit(self, vault):
        # The note that started this feature: 63,544 characters in 499 lines.
        text = "\n".join(["w" * 126] * 498 + ["w" * 298])
        _write(vault, "big.md", text)

        async with Client(mcp) as client:
            result = await client.call_tool("read_note_tool", {"path": "big.md"})

        [block] = result.content
        assert isinstance(block, TextContent)
        assert len(block.text) < 50_000  # Claude Code saves larger results to a file
        assert result.data["details"]["total_chars"] == 63_544
        assert result.data["details"]["truncated"] is True
