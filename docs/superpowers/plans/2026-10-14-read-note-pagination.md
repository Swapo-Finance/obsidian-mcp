# Read Note Pagination Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Return a long note from `read_note_tool` in pages (by offset or by section) under the 50,000-character client limit, and make the write policy flag notes that are long in characters.

**Architecture:** `tools/note_read.read_note` selects the text: the whole note, or one section found with the parser of `edit_note_section`. When the text fits from offset 0, it returns today's response. Else it returns one page, cut after the last newline in the window, plus `truncated`, `next_offset`, `total_chars`, a heading `outline`, and a continuation `message`. `OBSIDIAN_READ_MAX_CHARS` sets the default page size. `check_note_size_policy` gets a character ceiling, `OBSIDIAN_MAX_NOTE_CHARS`, next to the line ceiling.

**Tech Stack:** Python ≥3.10, FastMCP 2.12.0, pydantic v2, pytest + pytest-asyncio (strict mode: `@pytest.mark.asyncio`), ruff 0.16 (default rules plus the project ignores), pyright (standard), uv.

**Spec:** `docs/superpowers/specs/2026-10-14-read-note-pagination-design.md`

## Global Constraints

- Defaults: `OBSIDIAN_READ_MAX_CHARS=40000` (valid from `1000` to `1000000`; another value logs a warning and uses `40000`) and `OBSIDIAN_MAX_NOTE_CHARS=40000` (no range check). All other env defaults stay the same.
- A read with `offset == 0` whose text fits in the page size returns today's response, byte for byte: `{success, path, operation, details: {content, metadata}}`, same key order, no new keys.
- Add no dependency and no tool (the count stays 31). Do not bump the version.
- Write identifiers, code, comments, docstrings, and tool descriptions in English (repo convention). Write prose in Simplified Technical English: short sentences, active voice, imperative instructions.
- Keep `obsidian_mcp/utils/` free of MCP concepts (no `Context`, no `ToolError`).
- A `*_tool` wrapper holds only the `Annotated[..., Field(...)]` schema, the docstring, and the try/except that re-raises `ToolError`.
- Put new error text in `constants.ERROR_MESSAGES`, in the "To fix: 1) … 2) …" form. Validators return `(ok, error)` and do not raise.
- Keep each touched module under 350 lines. `utils/filesystem.py` is the named exception. Two modules are close to the limit: `mcp_notes.py` (317 lines now, target 348) and `tools/vault_meta.py` (333 now, target 349). Check them with `wc -l`.
- Keep `await ctx.info(f"Reading note: {path}")` as it is. `tests/test_ctx_notifications_e2e.py` pins that text.
- Gates, on the whole tree:
  - `uv run ruff check obsidian_mcp tests` → `All checks passed!`
  - `uv run pyright` → `0 errors, 0 warnings, 0 informations`
  - `OBSIDIAN_VAULT_PATH=$(mktemp -d) uv run pytest -q` → all pass. Baseline before Task 1: `559 passed`.
- Implementers never run `git commit` (`.claude/rules/08-parallel-subagent-driven-development.md`). The controller commits per task.
- Wave 2 runs two implementers in one working tree. If a test fails in a file outside your `Files` list, do not edit that file. Run it again once. If it still fails, report it in your status.

## Review Focus

1. An agent reads page 1, then replaces the note with `update_note`. Then the rest of the note is lost. Expected: the paged response says that the page is partial and names `update_note`; the wrapper docstring says to read all pages first. Test: Task 3 `test_page_ends_after_the_last_newline_in_the_window` (asserts `"update_note"` in `message`).
2. Multibyte text (accents, emoji). Expected: offsets count characters, each page has at most `max_chars` characters, and the pages join to the exact text. Test: Task 3 `test_offsets_count_characters_not_bytes`.
3. Two equal headings. Expected: `section` reads the first one; `outline` lists both, so `offset` reaches the second one. Test: Task 3 `test_duplicate_heading_reads_the_first_match`.
4. A note without headings. Expected: `outline` is `[]` and the key is present. Test: Task 3 `test_note_without_headings_has_an_empty_outline`.
5. A note already over the char limit (edited outside the server). Expected: every checked write that leaves it over the limit is flagged; a write that brings it under the limit passes. Test: Task 2 `test_note_already_over_the_limit_is_flagged_until_a_write_brings_it_under`.

## File Structure

| File | Task | Responsibility after the change |
| --- | --- | --- |
| `obsidian_mcp/constants.py` | 1 | Paging bounds, continuation message, 5 error keys, `read_page` response shape |
| `obsidian_mcp/utils/filesystem.py` | 1 | Reads `OBSIDIAN_MAX_NOTE_CHARS` and `OBSIDIAN_READ_MAX_CHARS` |
| `tests/test_read_note_pagination.py` | 1 (create), 3 (extend) | Env, paging, outline, section, and wrapper tests |
| `obsidian_mcp/utils/vault_config.py` | 2 | Line and char ceilings in `check_note_size_policy` |
| `obsidian_mcp/tools/write_policy.py` | 2 | Passes the char count to the policy |
| `tests/test_note_size_policy_writes.py` | 2 | Char-ceiling tests |
| `obsidian_mcp/tools/section_editing.py` | 3 | Shared heading parser helpers |
| `obsidian_mcp/utils/validation.py` | 3 | `validate_read_window` |
| `obsidian_mcp/tools/note_read.py` | 3 | Section select, cut rule, outline, paged response |
| `obsidian_mcp/mcp_notes.py` | 3 | `read_note_tool` schema and docstring |
| `obsidian_mcp/tools/vault_meta.py` | 4 | Help catalog rows for the 2 knobs |
| `tests/test_server_tool_wrappers.py` | 4 | Help catalog names |
| `README.md`, `CLAUDE.md`, `obsidian_mcp/utils/CLAUDE.md` | 4 | Docs |

## Execution waves

| Wave | Tasks | Why grouped |
| --- | --- | --- |
| 1 | Task 1 | Owns the two shared files (`constants.py`, `utils/filesystem.py`) that Tasks 2 and 3 consume |
| 2 | Task 2, Task 3 | Both depend only on Task 1, and their `Files` are disjoint |
| 3 | Task 4 | Documents the final behavior of Tasks 2 and 3, with real counts |

---

### Task 1: Config knobs and message constants

**Agent:** `backend-specialist`

**Files:**

- Modify: `obsidian_mcp/constants.py` (new constants after `MARKDOWN_EXTENSIONS` at line 17; 5 keys at the end of `ERROR_MESSAGES`, after `"vault_unavailable"` at lines 110-115; `"read_page"` in `RESPONSE_STRUCTURES`, after `"index_sync"` at lines 169-179)
- Modify: `obsidian_mcp/utils/filesystem.py` (import at line 14; `__init__` after `append_headroom_lines` at lines 115-117; `is_daily_note_path` docstring at lines 180-181)
- Create: `tests/test_read_note_pagination.py`

**Depends-on:** none

**Interfaces:**

- Consumes: nothing new.
- Produces:
  - `ObsidianVault.read_max_chars: int` from `OBSIDIAN_READ_MAX_CHARS`. Always from `1000` to `1000000`. Default `40000`.
  - `ObsidianVault.max_note_chars: int` from `OBSIDIAN_MAX_NOTE_CHARS`. Default `40000`.
  - `constants.READ_MAX_CHARS_DEFAULT = 40_000`, `READ_MAX_CHARS_MIN = 1_000`, `READ_MAX_CHARS_MAX = 1_000_000`.
  - `constants.READ_TRUNCATED_MESSAGE`, with the `str.format` placeholders `{start}`, `{next_offset}`, `{total_chars}`.
  - `ERROR_MESSAGES` keys and their placeholders:
    - `invalid_read_offset`: `{offset}`
    - `invalid_read_max_chars`: `{max_chars}`, `{minimum}`, `{maximum}`
    - `read_offset_out_of_range`: `{offset}`, `{total_chars}`, `{path}`
    - `section_not_found`: `{section}`, `{path}`
    - `note_over_char_limit`: `{path}`, `{chars}`, `{limit}`
  - `RESPONSE_STRUCTURES["read_page"]` (documentation only).
  - `tests/test_read_note_pagination.py` with the class `TestReadMaxCharsEnv`. Task 3 extends this file.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_read_note_pagination.py`:

```python
"""read_note paging (offset/max_chars), outline, and section reads, plus the
OBSIDIAN_READ_MAX_CHARS knob that sets the default page size.

A long note used to come back whole. A 63,544-character note made Claude Code
save the result to a file. See
docs/superpowers/specs/2026-10-14-read-note-pagination-design.md.
"""

import pytest

from obsidian_mcp.utils.filesystem import init_vault


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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `OBSIDIAN_VAULT_PATH=$(mktemp -d) uv run pytest tests/test_read_note_pagination.py -v`
Expected: 6 tests FAIL with `AttributeError: 'ObsidianVault' object has no attribute 'read_max_chars'`.

- [ ] **Step 3: Add the constants**

In `obsidian_mcp/constants.py`, add this block right after `MARKDOWN_EXTENSIONS = {".md", ".markdown"}`:

```python
# read_note paging: the bounds of the max_chars parameter and of
# OBSIDIAN_READ_MAX_CHARS, the default page size.
READ_MAX_CHARS_DEFAULT = 40_000
READ_MAX_CHARS_MIN = 1_000
READ_MAX_CHARS_MAX = 1_000_000

# Added to a paged read_note response when text remains after the page. Same
# role as the "message" of a truncated search result.
READ_TRUNCATED_MESSAGE = (
    "Showing characters {start}-{next_offset} of {total_chars}. This page is only "
    "part of the text. To read the next page, call read_note_tool again with "
    "offset={next_offset} and the same section, if you passed one. To read one "
    "section, pass section with a heading from outline. Read all pages before "
    "you replace the note with update_note. If total_chars or metadata.modified "
    "changes between pages, the note changed: read again from offset 0."
)
```

Add these keys at the end of `ERROR_MESSAGES`, after the `"vault_unavailable"` entry:

```python
    "invalid_read_offset": (
        "Invalid offset: {offset}. The offset must be 0 or more. "
        "To fix: 1) Use offset=0 to read from the start of the note, "
        "2) Use the next_offset value of the previous page"
    ),
    "invalid_read_max_chars": (
        "Invalid max_chars: {max_chars}. The value must be from {minimum} to {maximum}. "
        "To fix: 1) Use a value in that range, "
        "2) Omit max_chars to use OBSIDIAN_READ_MAX_CHARS"
    ),
    "read_offset_out_of_range": (
        "Offset {offset} is at or after the end of the text ({total_chars} characters) "
        "in '{path}'. To fix: 1) Use the next_offset value of the previous page. "
        "A null next_offset means that you already read the last page, "
        "2) Use offset=0 to read from the start, "
        "3) If the note changed after the previous page, read it again from offset=0"
    ),
    "section_not_found": (
        "No section matches '{section}' in '{path}'. "
        "To fix: 1) Give a markdown heading with its level, e.g. '## Tasks'. "
        "Case does not matter, but the number of # marks must match, "
        "2) Read the note without section to see its headings, "
        "or use the outline of a paged read"
    ),
    "note_over_char_limit": (
        "Note '{path}' would have {chars} characters, over "
        "OBSIDIAN_MAX_NOTE_CHARS={limit}. "
        "To fix: 1) Split the content into smaller notes and link them with [[wikilinks]], "
        "2) Or raise OBSIDIAN_MAX_NOTE_CHARS"
    ),
```

Add this entry to `RESPONSE_STRUCTURES`, right after the `"index_sync"` entry:

```python
    # Paged read (read_note_tool): offset > 0, or the text does not fit in one
    # page. A read that fits from offset 0 keeps crud_success with details
    # {content, metadata}.
    "read_page": {
        "success": True,
        "path": str,  # The note path
        "operation": str,  # "read"
        "details": {
            "content": str,  # This page of the note (or section) text
            "metadata": dict,  # Same as an unpaged read
            "truncated": bool,  # True when text remains after this page
            "next_offset": int,  # Start of the next page; None on the last page
            "total_chars": int,  # Length of the whole note (or section) text
            "outline": list,  # [{heading, level, offset, chars}], 50 entries max
            "message": str,  # Only when truncated: how to read the next page
        },
    },
```

- [ ] **Step 4: Read the two knobs**

In `obsidian_mcp/utils/filesystem.py`, replace the import `from ..constants import ERROR_MESSAGES` with:

```python
from ..constants import (
    ERROR_MESSAGES,
    READ_MAX_CHARS_DEFAULT,
    READ_MAX_CHARS_MAX,
    READ_MAX_CHARS_MIN,
)
```

In `__init__`, add this block right after the `self.append_headroom_lines = self._read_int_env(...)` statement:

```python
        self.max_note_chars = self._read_int_env("OBSIDIAN_MAX_NOTE_CHARS", 40000)
        # Default page size of read_note. Like any other invalid knob, a value
        # out of range falls back to the default: config never crashes the boot.
        read_max_chars = self._read_int_env(
            "OBSIDIAN_READ_MAX_CHARS", READ_MAX_CHARS_DEFAULT
        )
        if not READ_MAX_CHARS_MIN <= read_max_chars <= READ_MAX_CHARS_MAX:
            logger.warning(
                "Invalid OBSIDIAN_READ_MAX_CHARS=%d; must be from %d to %d. "
                "Falling back to %d.",
                read_max_chars,
                READ_MAX_CHARS_MIN,
                READ_MAX_CHARS_MAX,
                READ_MAX_CHARS_DEFAULT,
            )
            read_max_chars = READ_MAX_CHARS_DEFAULT
        self.read_max_chars = read_max_chars
```

In `is_daily_note_path`, replace the docstring with:

```python
        """True if relpath lives inside OBSIDIAN_DAILY_DIR — daily notes are
        always exempt from the note-size policy (OBSIDIAN_MAX_NOTE_LINES,
        _APPEND_HEADROOM_LINES, OBSIDIAN_MAX_NOTE_CHARS)."""
```

- [ ] **Step 5: Run the tests, the gates, and the full suite**

Run: `OBSIDIAN_VAULT_PATH=$(mktemp -d) uv run pytest tests/test_read_note_pagination.py -v`
Expected: `6 passed`.
Run: `uv run ruff check obsidian_mcp tests && uv run pyright`
Expected: `All checks passed!` and `0 errors, 0 warnings, 0 informations`.
Run: `OBSIDIAN_VAULT_PATH=$(mktemp -d) uv run pytest -q`
Expected: `565 passed`.

- [ ] **Step 6: Controller commits**

```bash
git add obsidian_mcp/constants.py obsidian_mcp/utils/filesystem.py tests/test_read_note_pagination.py
git commit -m "feat(config): add OBSIDIAN_READ_MAX_CHARS and OBSIDIAN_MAX_NOTE_CHARS knobs"
```

---

### Task 2: Character ceiling in the write policy

**Agent:** `backend-specialist`

**Files:**

- Modify: `obsidian_mcp/utils/vault_config.py` (imports at lines 20-38; section comment at lines 60-62; `check_note_size_policy` at lines 71-115)
- Modify: `obsidian_mcp/tools/write_policy.py` (`_size_policy_warning` at lines 135-141)
- Test: `tests/test_note_size_policy_writes.py` (new helper after `_lines` at lines 28-30; `make_vault` fixture at lines 33-62; new class right above `if __name__ == "__main__":` at line 193)

**Depends-on:** Task 1

**Interfaces:**

- Consumes (Task 1): `vault.max_note_chars: int`; `ERROR_MESSAGES["note_over_char_limit"]` with `{path}`, `{chars}`, `{limit}`.
- Produces: `check_note_size_policy(vault, relpath: str, resulting_line_count: int, is_incremental: bool, resulting_char_count: int) -> str | None`. Its one caller, `_size_policy_warning(vault, path: str, content: str, is_incremental: bool) -> list[str]`, keeps its signature.

- [ ] **Step 1: Write the failing tests**

In `tests/test_note_size_policy_writes.py`, add this helper right below `_lines`:

```python
def _chars(n: int) -> str:
    """One line of exactly n characters: inside any line ceiling."""
    return "x" * n
```

In the `make_vault` fixture, add a `max_chars` argument. Change the inner signature to:

```python
    def _make(max_lines=None, headroom=None, policy=None, daily_dir=None, max_chars=None):
```

Add this right after the `daily_dir` branch:

```python
        if max_chars is not None:
            os.environ["OBSIDIAN_MAX_NOTE_CHARS"] = str(max_chars)
```

Add `"OBSIDIAN_MAX_NOTE_CHARS",` to the tuple of keys that the teardown pops.

Add this class right above `if __name__ == "__main__":`:

```python
class TestCharCeiling:
    """OBSIDIAN_MAX_NOTE_CHARS: checked next to the line ceiling, with the same
    modes and daily exemption, and no append headroom. The 63,544-character
    note that motivated it had only 499 lines."""

    @pytest.mark.asyncio
    async def test_exactly_at_the_char_limit_succeeds_under_strict(self, make_vault):
        make_vault(max_chars=100, policy="strict")
        result = await create_note("Note.md", _chars(100))
        assert result["success"] is True
        assert "warnings" not in result

    @pytest.mark.asyncio
    async def test_over_the_char_limit_within_the_line_limit_raises_under_strict(
        self, make_vault
    ):
        make_vault(max_chars=100, policy="strict")
        with pytest.raises(ValueError, match="OBSIDIAN_MAX_NOTE_CHARS=100"):
            await create_note("Note.md", _chars(101))
        with pytest.raises(FileNotFoundError):
            await read_note("Note.md")

    @pytest.mark.asyncio
    async def test_warning_tells_the_agent_to_split_into_linked_notes(self, make_vault):
        make_vault(max_chars=100, policy="warn")
        result = await create_note("Note.md", _chars(101))
        assert result["success"] is True
        [warning] = result["warnings"]
        assert "101 characters" in warning
        assert "smaller notes" in warning
        assert "[[wikilinks]]" in warning

    @pytest.mark.asyncio
    async def test_off_ignores_the_char_limit(self, make_vault):
        make_vault(max_chars=100, policy="off")
        result = await create_note("Note.md", _chars(101))
        assert "warnings" not in result

    @pytest.mark.asyncio
    async def test_daily_note_is_exempt(self, make_vault):
        make_vault(max_chars=100, policy="strict", daily_dir="daily")
        result = await create_note("daily/2020-01-01.md", _chars(500))
        assert "warnings" not in result

    @pytest.mark.asyncio
    async def test_append_has_no_char_headroom(self, make_vault):
        make_vault(max_chars=100, policy="strict")
        await create_note("Note.md", "Base")
        # base(4) + blank-line join(2) + fragment(94) = 100: exactly the limit.
        result = await update_note("Note.md", _chars(94), merge_strategy="append")
        assert result["success"] is True
        with pytest.raises(ValueError, match="OBSIDIAN_MAX_NOTE_CHARS"):
            await update_note("Note.md", "y", merge_strategy="append")

    @pytest.mark.asyncio
    async def test_note_already_over_the_limit_is_flagged_until_a_write_brings_it_under(
        self, make_vault
    ):
        vault = make_vault(max_chars=100, policy="strict")
        (vault.vault_path / "Big.md").write_text(_chars(150))  # edited outside
        # Same as the line ceiling: the check uses the size after the write.
        with pytest.raises(ValueError, match="OBSIDIAN_MAX_NOTE_CHARS"):
            await update_note("Big.md", _chars(120), merge_strategy="replace")
        result = await update_note("Big.md", _chars(50), merge_strategy="replace")
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_line_and_char_violations_share_one_message(self, make_vault):
        make_vault(max_lines=2, max_chars=10, policy="warn")
        result = await create_note("Note.md", "aaaa\nbbbb\ncccc")  # 3 lines, 14 chars
        [warning] = result["warnings"]
        assert "OBSIDIAN_MAX_NOTE_LINES=2" in warning
        assert "OBSIDIAN_MAX_NOTE_CHARS=10" in warning
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `OBSIDIAN_VAULT_PATH=$(mktemp -d) uv run pytest tests/test_note_size_policy_writes.py -v`
Expected: 5 new tests FAIL. The 3 strict tests that expect an error fail with `DID NOT RAISE`. `test_warning_tells_the_agent_to_split_into_linked_notes` fails with `KeyError: 'warnings'`. `test_line_and_char_violations_share_one_message` fails on the `OBSIDIAN_MAX_NOTE_CHARS=10` assert. The 3 guard tests (at the limit, `off`, daily) and the 13 old tests PASS.

- [ ] **Step 3: Add the char check**

In `obsidian_mcp/utils/vault_config.py`, add this import right above `from .frontmatter_requirements import (`:

```python
from ..constants import ERROR_MESSAGES
```

Replace the section comment above `count_lines` with:

```python
# ---------------------------------------------------------------------------
# Note-size policy (spec section 1: OBSIDIAN_MAX_NOTE_LINES /
# _APPEND_HEADROOM_LINES, plus OBSIDIAN_MAX_NOTE_CHARS)
# ---------------------------------------------------------------------------
```

Replace `check_note_size_policy` with:

```python
def check_note_size_policy(
    vault,
    relpath: str,
    resulting_line_count: int,
    is_incremental: bool,
    resulting_char_count: int,
) -> str | None:
    """Check the note's size after the write against OBSIDIAN_MAX_NOTE_LINES
    and OBSIDIAN_MAX_NOTE_CHARS.

    is_incremental=True (update append / edit_note_section) uses the lower,
    early-warning line ceiling MAX - APPEND_HEADROOM_LINES; False (create_note /
    update replace) uses MAX directly, since the whole note is being
    (re)written in one shot. The char ceiling has no headroom: every write is
    checked against OBSIDIAN_MAX_NOTE_CHARS directly.

    Returns None (ok / off / daily-exempt), a warning message (warn policy —
    caller still writes and surfaces the message), or raises ValueError
    (strict policy — caller must not write). A write over both ceilings gets
    one message that holds both texts.
    """
    if vault.note_size_policy == "off":
        return None
    if vault.is_daily_note_path(relpath):
        return None

    if is_incremental:
        ceiling = vault.max_note_lines - vault.append_headroom_lines
        line_message = (
            f"Note '{relpath}' would reach {resulting_line_count} lines, over the "
            f"{ceiling}-line append ceiling (OBSIDIAN_MAX_NOTE_LINES={vault.max_note_lines} - "
            f"OBSIDIAN_APPEND_HEADROOM_LINES={vault.append_headroom_lines}). Split the content "
            "into a new note, or raise OBSIDIAN_APPEND_HEADROOM_LINES/OBSIDIAN_MAX_NOTE_LINES."
        )
    else:
        ceiling = vault.max_note_lines
        line_message = (
            f"Note '{relpath}' would have {resulting_line_count} lines, over "
            f"OBSIDIAN_MAX_NOTE_LINES={vault.max_note_lines}. Split the content into multiple "
            "notes, or raise OBSIDIAN_MAX_NOTE_LINES."
        )

    messages: list[str] = []
    if resulting_line_count > ceiling:
        messages.append(line_message)
    if resulting_char_count > vault.max_note_chars:
        messages.append(
            ERROR_MESSAGES["note_over_char_limit"].format(
                path=relpath, chars=resulting_char_count, limit=vault.max_note_chars
            )
        )
    if not messages:
        return None

    message = " ".join(messages)
    if vault.note_size_policy == "strict":
        raise ValueError(message)
    return message  # warn
```

The two line-limit texts are the same as before. Only the variable name changes (`message` → `line_message`).

In `obsidian_mcp/tools/write_policy.py`, replace the call in `_size_policy_warning` with:

```python
    warning = check_note_size_policy(
        vault, path, count_lines(content), is_incremental, len(content)
    )
```

- [ ] **Step 4: Run the tests, the gates, and the full suite**

Run: `OBSIDIAN_VAULT_PATH=$(mktemp -d) uv run pytest tests/test_note_size_policy_writes.py -v`
Expected: `21 passed`.
Run: `uv run ruff check obsidian_mcp tests && uv run pyright`
Expected: `All checks passed!` and `0 errors, 0 warnings, 0 informations`.
Run: `OBSIDIAN_VAULT_PATH=$(mktemp -d) uv run pytest -q`
Expected: all pass (`573 passed` alone; `598 passed` when Task 3 is also in the tree).
Run: `wc -l obsidian_mcp/utils/vault_config.py obsidian_mcp/tools/write_policy.py`
Expected: both under 350.

- [ ] **Step 5: Controller commits**

```bash
git add obsidian_mcp/utils/vault_config.py obsidian_mcp/tools/write_policy.py tests/test_note_size_policy_writes.py
git commit -m "feat(policy): flag notes over OBSIDIAN_MAX_NOTE_CHARS characters"
```

---

### Task 3: Paged and section reads in read_note_tool

**Agent:** `backend-specialist`

**Files:**

- Modify: `obsidian_mcp/tools/section_editing.py` (`_find_section_bounds` at lines 16-46; the identifier parse in `edit_note_section` at lines 166-174)
- Modify: `obsidian_mcp/utils/validation.py` (import at line 5; new function right after `validate_context_length`, which ends at line 89)
- Modify: `obsidian_mcp/tools/note_read.py` (whole module)
- Modify: `obsidian_mcp/mcp_notes.py` (imports at lines 3-16; `read_note_tool` at lines 19-64)
- Test: `tests/test_read_note_pagination.py` (import block; new block between the imports and `TestReadMaxCharsEnv`; new classes at the end)

**Depends-on:** Task 1

**Interfaces:**

- Consumes (Task 1): `vault.read_max_chars: int`; `READ_MAX_CHARS_MIN`, `READ_MAX_CHARS_MAX`; `READ_TRUNCATED_MESSAGE` (`{start}`, `{next_offset}`, `{total_chars}`); the `ERROR_MESSAGES` keys `invalid_read_offset`, `invalid_read_max_chars`, `read_offset_out_of_range`, `section_not_found` (placeholders listed in Task 1).
- Produces:
  - `async read_note(path: str, offset: int = 0, max_chars: int | None = None, section: str | None = None, ctx: Context | None = None) -> dict` in `tools/note_read.py`. `ctx` moves from position 2 to position 5.
  - `read_note_tool(path, offset=0, max_chars=None, section=None, ctx=None)` in `mcp_notes.py`.
  - `validate_read_window(offset: int, max_chars: int | None) -> tuple[bool, str | None]` in `utils/validation.py`.
  - `_parse_heading(line: str) -> tuple[int, str] | None` and `_section_end(lines: list[str], start: int, level: int) -> int` in `tools/section_editing.py`.

- [ ] **Step 1: Carve the heading parser helpers out of `_find_section_bounds`**

This is a refactor with no behavior change. `tests/test_edit_note_section.py` (16 tests: identifiers, levels, case, NFC/NFD, nesting, end of file) guards it.

In `obsidian_mcp/tools/section_editing.py`, replace `_find_section_bounds` (lines 16-46) with:

```python
def _parse_heading(line: str) -> tuple[int, str] | None:
    """(level, stripped text) of a markdown heading line, or None when the line
    is not a heading. The one heading parser of edit_note_section and
    read_note."""
    match = re.match(r"^(#{1,6})\s+(.+)$", line)
    if not match:
        return None
    return len(match.group(1)), match.group(2).strip()


def _section_end(lines: list[str], start: int, level: int) -> int:
    """Index of the first line after `start` that is a heading of `level` or a
    higher level (fewer #), or len(lines) when the section runs to the end."""
    for j in range(start + 1, len(lines)):
        next_match = re.match(r"^(#{1,6})\s+", lines[j])
        if next_match and len(next_match.group(1)) <= level:
            return j
    return len(lines)


def _find_section_bounds(
    lines: list[str], heading_level: int, heading_text: str
) -> tuple[int, int] | None:
    """Locate the first heading (case-insensitive, NFC-normalized) matching
    heading_level/heading_text -- first match wins when a heading repeats.

    Returns (section_start, section_end): section_end is the index of the
    next heading of the same or higher level, or len(lines) if the section
    runs to end of file. Returns None if no heading matches.
    """
    normalized_target = unicodedata.normalize("NFC", heading_text.lower())
    for i, line in enumerate(lines):
        heading = _parse_heading(line)
        if heading is None or heading[0] != heading_level:
            continue
        if unicodedata.normalize("NFC", heading[1].lower()) != normalized_target:
            continue
        return i, _section_end(lines, i, heading_level)

    return None
```

In `edit_note_section`, replace the identifier parse (from `# Parse the section identifier` through `heading_text = heading_match.group(2).strip()`) with:

```python
    # Parse the section identifier to extract heading level and text
    heading = _parse_heading(section_identifier)
    if heading is None:
        raise ValueError(
            f"Invalid section identifier: {section_identifier}. Must be a markdown heading (e.g., '## Section Name')"
        )
    heading_level, heading_text = heading
```

- [ ] **Step 2: Verify the refactor**

Run: `OBSIDIAN_VAULT_PATH=$(mktemp -d) uv run pytest tests/test_edit_note_section.py -v`
Expected: `16 passed`.

- [ ] **Step 3: Write the failing read tests**

In `tests/test_read_note_pagination.py`, replace the import block with:

```python
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
```

Insert this block between the imports and `class TestReadMaxCharsEnv:`. Leave one blank line after the last import and two blank lines before the class (ruff I001 checks the first):

```python
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
```

Append these classes at the end of the file:

```python
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
                "read_offset_out_of_range", offset=2999, total_chars=2999, path="long.md"
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
            DOC.index(heading) for heading in ("# Doc", "## Tasks", "### Sub", "## Notes")
        )

        # offset=1 asks for a paged response from a note that would fit.
        details = (await read_note("doc.md", offset=1, max_chars=1000))["details"]

        assert details["outline"] == [
            {"heading": "Doc", "level": 1, "offset": doc, "chars": len(DOC) - doc},
            {"heading": "Tasks", "level": 2, "offset": tasks, "chars": notes - tasks},
            {"heading": "Sub", "level": 3, "offset": sub, "chars": notes - sub},
            {"heading": "Notes", "level": 2, "offset": notes, "chars": len(DOC) - notes},
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
            ValueError, match=_error("section_not_found", section="Tasks", path="doc.md")
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
```

- [ ] **Step 4: Run the tests to verify they fail**

Run: `OBSIDIAN_VAULT_PATH=$(mktemp -d) uv run pytest tests/test_read_note_pagination.py -v`
Expected: 24 new tests FAIL, most with `TypeError: read_note() got an unexpected keyword argument` (`offset`, `max_chars`, or `section`). `test_page_size_defaults_to_obsidian_read_max_chars` fails with `KeyError: 'next_offset'`. The client test fails on the `< 50_000` assert (the text is about 64,000 characters today). `test_note_that_fits_returns_todays_response_byte_for_byte` and the 6 `TestReadMaxCharsEnv` tests PASS.

- [ ] **Step 5: Add `validate_read_window`**

In `obsidian_mcp/utils/validation.py`, replace the import `from ..constants import ERROR_MESSAGES, MARKDOWN_EXTENSIONS` with:

```python
from ..constants import (
    ERROR_MESSAGES,
    MARKDOWN_EXTENSIONS,
    READ_MAX_CHARS_MAX,
    READ_MAX_CHARS_MIN,
)
```

Add this function right after `validate_context_length`:

```python
def validate_read_window(offset: int, max_chars: int | None) -> tuple[bool, str | None]:
    """
    Validate the paging parameters of read_note.

    Args:
        offset: Character offset where the page starts
        max_chars: Page size, or None to use OBSIDIAN_READ_MAX_CHARS

    Returns:
        Tuple of (is_valid, error_message)
    """
    if offset < 0:
        return False, ERROR_MESSAGES["invalid_read_offset"].format(offset=offset)
    if max_chars is not None and not (
        READ_MAX_CHARS_MIN <= max_chars <= READ_MAX_CHARS_MAX
    ):
        return False, ERROR_MESSAGES["invalid_read_max_chars"].format(
            max_chars=max_chars, minimum=READ_MAX_CHARS_MIN, maximum=READ_MAX_CHARS_MAX
        )
    return True, None
```

- [ ] **Step 6: Rewrite `tools/note_read.py`**

Replace the whole module with:

```python
"""Note reading: read_note, with pages and section reads for long notes."""

from typing import Any

from fastmcp import Context

from ..constants import ERROR_MESSAGES, READ_TRUNCATED_MESSAGE
from ..utils import sanitize_path, validate_note_path
from ..utils.filesystem import get_vault
from ..utils.validation import validate_read_window
from .section_editing import _find_section_bounds, _parse_heading, _section_end

# ponytail: fixed 50-entry outline ceiling. It drops the deepest heading level
# first, then keeps the first 50 entries. Add a parameter if agents need more.
OUTLINE_MAX_ENTRIES = 50


def _line_starts(lines: list[str], text_length: int) -> list[int]:
    """Offset where each line starts, plus text_length as the last entry, so
    starts[len(lines)] is the end of the text."""
    starts = [0]
    for line in lines:
        starts.append(starts[-1] + len(line) + 1)
    starts[-1] = text_length
    return starts


def _select_section(text: str, section: str, path: str) -> str:
    """Text of the first section that matches `section` under the rules of
    edit_note_section: from its heading line to the next heading of the same
    or a higher level, sub-sections included."""
    heading = _parse_heading(section)
    lines = text.split("\n")
    bounds = None if heading is None else _find_section_bounds(lines, *heading)
    if bounds is None:
        raise ValueError(
            ERROR_MESSAGES["section_not_found"].format(section=section, path=path)
        )
    starts = _line_starts(lines, len(text))
    return text[starts[bounds[0]] : starts[bounds[1]]]


def _cap_outline(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop the deepest heading level until at most OUTLINE_MAX_ENTRIES
    remain. When one level is left, keep its first OUTLINE_MAX_ENTRIES."""
    while len(entries) > OUTLINE_MAX_ENTRIES:
        deepest = max(entry["level"] for entry in entries)
        shallower = [entry for entry in entries if entry["level"] < deepest]
        if not shallower:
            return entries[:OUTLINE_MAX_ENTRIES]
        entries = shallower
    return entries


def _build_outline(text: str) -> list[dict[str, Any]]:
    """{heading, level, offset, chars} for each heading in text. offset is the
    start of the heading line; chars runs to the next heading of the same or a
    higher level, or to the end of text."""
    lines = text.split("\n")
    starts = _line_starts(lines, len(text))
    entries: list[dict[str, Any]] = []
    for index, line in enumerate(lines):
        heading = _parse_heading(line)
        if heading is None:
            continue
        level, title = heading
        end = _section_end(lines, index, level)
        entries.append(
            {
                "heading": title,
                "level": level,
                "offset": starts[index],
                "chars": starts[end] - starts[index],
            }
        )
    return _cap_outline(entries)


def _page_end(text: str, offset: int, max_chars: int) -> int:
    """End (exclusive) of the page that starts at offset: the end of text when
    the rest fits; else just after the last newline in the window; else a hard
    cut at offset + max_chars. Always greater than offset."""
    window_end = offset + max_chars
    if window_end >= len(text):
        return len(text)
    newline = text.rfind("\n", offset, window_end)
    return window_end if newline == -1 else newline + 1


async def read_note(
    path: str,
    offset: int = 0,
    max_chars: int | None = None,
    section: str | None = None,
    ctx: Context | None = None,
) -> dict:
    """
    Read the content and metadata of a specific note.

    Use this tool when you need to retrieve the content of a note from the
    Obsidian vault. The path should be relative to the vault root. A note
    longer than the page size comes back in pages.

    To view images embedded in a note, use the view_note_images tool.

    Args:
        path: Path to the note relative to vault root (e.g., "Daily/2024-01-15.md")
        offset: Character offset where the page starts (0 = start of the text)
        max_chars: Page size; None uses OBSIDIAN_READ_MAX_CHARS
        section: Markdown heading (e.g., "## Tasks"); read only that section,
            matched like edit_note_section
        ctx: MCP context for progress reporting

    Returns:
        {success, path, operation, details: {content, metadata}} when the text
        fits in one page from offset 0. Else details also holds truncated,
        next_offset, total_chars, outline, and (when text remains) message --
        see RESPONSE_STRUCTURES["read_page"].

    Example:
        >>> await read_note("Projects/My Project.md", ctx=ctx)
        {
            "path": "Projects/My Project.md",
            "content": "# My Project\n\n![diagram](attachments/diagram.png)\n\nProject details...",
            "metadata": {
                "tags": ["project", "active"],
                "created": "2024-01-15T10:00:00Z",
                "modified": "2024-01-15T14:30:00Z"
            }
        }
    """
    # Validate path
    is_valid, error_msg = validate_note_path(path)
    if not is_valid:
        raise ValueError(f"Invalid path: {error_msg}")

    is_valid, error_msg = validate_read_window(offset, max_chars)
    if not is_valid:
        raise ValueError(error_msg)

    # Sanitize path
    path = sanitize_path(path)

    if ctx:
        await ctx.info(f"Reading note: {path}")

    vault = get_vault()
    try:
        note = await vault.read_note(path)
    except FileNotFoundError:
        raise FileNotFoundError(ERROR_MESSAGES["note_not_found"].format(path=path))

    text = (
        note.content
        if section is None
        else _select_section(note.content, section, path)
    )
    page_size = vault.read_max_chars if max_chars is None else max_chars
    metadata = note.metadata.model_dump(exclude_none=True)

    # A text that fits from offset 0 keeps today's response, byte for byte.
    if offset == 0 and len(text) <= page_size:
        return {
            "success": True,
            "path": note.path,
            "operation": "read",
            "details": {"content": text, "metadata": metadata},
        }

    if offset >= len(text):
        raise ValueError(
            ERROR_MESSAGES["read_offset_out_of_range"].format(
                offset=offset, total_chars=len(text), path=path
            )
        )

    end = _page_end(text, offset, page_size)
    truncated = end < len(text)
    details: dict[str, Any] = {
        "content": text[offset:end],
        "metadata": metadata,
        "truncated": truncated,
        "next_offset": end if truncated else None,
        "total_chars": len(text),
        "outline": _build_outline(text),
    }
    if truncated:
        details["message"] = READ_TRUNCATED_MESSAGE.format(
            start=offset, next_offset=end, total_chars=len(text)
        )
    return {
        "success": True,
        "path": note.path,
        "operation": "read",
        "details": details,
    }
```

- [ ] **Step 7: Extend `read_note_tool`**

In `obsidian_mcp/mcp_notes.py`, add this import right after `from .app import mcp`:

```python
from .constants import READ_MAX_CHARS_MAX, READ_MAX_CHARS_MIN
```

Add these three parameters between the `path` parameter and `ctx: Context | None = None,`. Do not add `default=` or `examples=` inside these `Field(...)` calls: the signature default sets the schema default, and the module has no lines to spare.

```python
    offset: Annotated[
        int,
        Field(
            description="Character offset where the page starts. Use 0 first, then the next_offset value of the previous page.",
            ge=0,
        ),
    ] = 0,
    max_chars: Annotated[
        int | None,
        Field(
            description="Maximum characters in one page, from 1000 to 1000000. Omit it to use OBSIDIAN_READ_MAX_CHARS (default 40000). A page ends after the last line break that fits.",
            ge=READ_MAX_CHARS_MIN,
            le=READ_MAX_CHARS_MAX,
        ),
    ] = None,
    section: Annotated[
        str | None,
        Field(
            description="Markdown heading of one section to read (e.g., '## Tasks'). Matched like edit_note_section: case-insensitive, first match, sub-sections included. offset then counts from the heading line.",
            pattern=r"^#{1,6}\s+.+$",
            max_length=200,
        ),
    ] = None,
```

In the docstring, add this block between the "When NOT to use" list and `Returns:`:

```text
    Long notes:
    - A note longer than max_chars comes back in pages, with truncated,
      next_offset, total_chars, outline, and message. Call again with
      offset=next_offset until it is null, or read one part with section.
    - A page is only part of the note. Read all pages before you replace the
      note with update_note, or edit one section with edit_note_section.

```

Replace the call in the `try` block with:

```python
        return await read_note(path, offset, max_chars, section, ctx)
```

The positional call matches the other wrappers. `test_wrapper_passes_offset_max_chars_and_section_through` fails if a later edit swaps `offset` and `max_chars`.

- [ ] **Step 8: Run the tests to verify they pass**

Run: `OBSIDIAN_VAULT_PATH=$(mktemp -d) uv run pytest tests/test_read_note_pagination.py tests/test_edit_note_section.py tests/test_ctx_notifications_e2e.py -v`
Expected: `48 passed` (31 + 16 + 1).

- [ ] **Step 9: Run the line counts, the gates, and the full suite**

Run: `wc -l obsidian_mcp/mcp_notes.py obsidian_mcp/tools/note_read.py obsidian_mcp/tools/section_editing.py obsidian_mcp/utils/validation.py`
Expected: every count under 350; `mcp_notes.py` at 348. If `mcp_notes.py` has 350 lines or more, shorten the "Long notes" block. Keep the partial-page warning.
Run: `uv run ruff check obsidian_mcp tests && uv run pyright`
Expected: `All checks passed!` and `0 errors, 0 warnings, 0 informations`.
Run: `OBSIDIAN_VAULT_PATH=$(mktemp -d) uv run pytest -q`
Expected: all pass (`590 passed` alone; `598 passed` when Task 2 is also in the tree).

- [ ] **Step 10: Controller commits**

```bash
git add obsidian_mcp/tools/section_editing.py obsidian_mcp/utils/validation.py obsidian_mcp/tools/note_read.py obsidian_mcp/mcp_notes.py tests/test_read_note_pagination.py
git commit -m "feat(read): return long notes in pages, by offset or by section"
```

---

### Task 4: Documentation and help catalog

**Agent:** `documentation-writer`

**Files:**

- Modify: `obsidian_mcp/tools/vault_meta.py` (new `_env_row` after the `OBSIDIAN_APPEND_HEADROOM_LINES` row at lines 201-210; new `_env_row` after the `OBSIDIAN_SEARCH_INDEX_THRESHOLD` row at lines 274-282)
- Modify: `tests/test_server_tool_wrappers.py` (`expected` set in `test_returns_all_expected_env_vars` at lines 91-105)
- Modify: `README.md` (tool table row at line 186; `read_note` entry at lines 220-242; full example `env` block at lines 147-156; project tree at line 1233; note-size policy at lines 1253-1259; "Safe note editing" at lines 1322-1325; configuration table at lines 1388-1390; Testing paragraph at line 1529; changelog list from line 1576)
- Modify: `CLAUDE.md` (lines 107, 116, 130)
- Modify: `obsidian_mcp/utils/CLAUDE.md` (lines 16 and 32-35)

**Depends-on:** Task 2, Task 3

**Interfaces:**

- Consumes: the final behavior of Tasks 1-3; `vault.max_note_chars` and `vault.read_max_chars` (Task 1). The spec is the source of truth for the wording.
- Produces: docs and 2 help catalog rows. No code behavior changes.

- [ ] **Step 1: Write the failing test**

In `tests/test_server_tool_wrappers.py`, add two names to the `expected` set of `test_returns_all_expected_env_vars`: `"OBSIDIAN_MAX_NOTE_CHARS",` right after `"OBSIDIAN_APPEND_HEADROOM_LINES",`, and `"OBSIDIAN_READ_MAX_CHARS",` right after `"OBSIDIAN_SEARCH_INDEX_THRESHOLD",`.

- [ ] **Step 2: Run the test to verify it fails**

Run: `OBSIDIAN_VAULT_PATH=$(mktemp -d) uv run pytest tests/test_server_tool_wrappers.py -v -k test_returns_all_expected_env_vars`
Expected: FAIL on `assert expected <= names`.

- [ ] **Step 3: Add the two help catalog rows**

In `obsidian_mcp/tools/vault_meta.py`, add this row right after the `OBSIDIAN_APPEND_HEADROOM_LINES` row. Keep each description on one line: the module has room for exactly 16 new lines.

```python
        _env_row(
            "OBSIDIAN_MAX_NOTE_CHARS",
            "int",
            "40000",
            str(vault.max_note_chars),
            "Character ceiling per note, checked next to OBSIDIAN_MAX_NOTE_LINES on every write, with no append headroom. Daily notes are always exempt.",
            "40000",
        ),
```

Add this row right after the `OBSIDIAN_SEARCH_INDEX_THRESHOLD` row:

```python
        _env_row(
            "OBSIDIAN_READ_MAX_CHARS",
            "int (1000-1000000)",
            "40000",
            str(vault.read_max_chars),
            "Default page size of read_note_tool. A longer note comes back in pages (offset, next_offset). A value out of range falls back to 40000.",
            "40000",
        ),
```

- [ ] **Step 4: Run the test and the line count**

Run: `OBSIDIAN_VAULT_PATH=$(mktemp -d) uv run pytest tests/test_server_tool_wrappers.py -v`
Expected: `25 passed`.
Run: `wc -l obsidian_mcp/tools/vault_meta.py`
Expected: `349`.

- [ ] **Step 5: README — the `read_note` tool**

Replace the tool table row at line 186 with:

```markdown
| [`read_note`](#read_note) | Read a note's content and metadata, in pages for a long note, or one section by its heading |
```

Replace the `read_note` entry, from `<a id="read_note"></a>` to the `</details>` line right above `<a id="create_note"></a>`, with:

````markdown
<a id="read_note"></a>
<details>
<summary><b><code>read_note</code></b></summary>

Read the content and metadata of a note. A long note comes back in pages.

**Parameters:**
- `path`: Path to the note (e.g., "Daily/2024-01-15.md")
- `offset` (default: `0`): Character offset where the page starts. Use the `next_offset` value of the previous page.
- `max_chars` (default: `OBSIDIAN_READ_MAX_CHARS`, which is `40000`): Maximum characters in one page, from `1000` to `1000000`. A page ends after the last line break that fits. A line longer than the page is cut.
- `section` (optional): Markdown heading of one section, e.g. `"## Tasks"`. The match is the same as in `edit_note_section`: case-insensitive, first match, sub-sections included. `offset` then counts from the heading line.

**Returns, when the text fits in one page from offset 0:**
```json
{
  "success": true,
  "path": "Daily/2024-01-15.md",
  "operation": "read",
  "details": {
    "content": "# Daily Note\n\nContent here...",
    "metadata": {"tags": ["daily", "journal"], "aliases": [], "frontmatter": {}}
  }
}
```

**Returns, for a paged read:**
```json
{
  "success": true,
  "path": "Projects/Big.md",
  "operation": "read",
  "details": {
    "content": "# Big\n\n## Context\n...",
    "metadata": {"tags": [], "aliases": [], "frontmatter": {}},
    "truncated": true,
    "next_offset": 39878,
    "total_chars": 63544,
    "outline": [
      {"heading": "Big", "level": 1, "offset": 0, "chars": 63544},
      {"heading": "Context", "level": 2, "offset": 7, "chars": 21230}
    ],
    "message": "Showing characters 0-39878 of 63544. This page is only part of the text. ..."
  }
}
```

- Follow `next_offset` until it is `null` to read the whole note. The pages join to the exact text.
- `outline` lists up to 50 headings, each with its `offset` and its size in `chars`. When a note has more, the deepest heading level is left out first.
- Read all pages before you replace the note with `update_note`. A page is only part of the note.
- An `offset` at or after the end of the text returns an error. A `section` that matches no heading returns an error.

</details>
````

- [ ] **Step 6: README — configuration**

In the "Full example — all fork features enabled" block, add a comma after `"OBSIDIAN_SEARCH_RESULT_MODE": "auto"` and add these two lines after it, with the same indentation:

```json
           "OBSIDIAN_MAX_NOTE_CHARS": "40000",
           "OBSIDIAN_READ_MAX_CHARS": "40000"
```

In the configuration table, replace the `OBSIDIAN_NOTE_SIZE_POLICY` row with:

```markdown
| `OBSIDIAN_NOTE_SIZE_POLICY` | How exceeding the line or character ceiling is handled | `warn` | `strict\|warn\|off` |
```

Add these two rows right after the `OBSIDIAN_APPEND_HEADROOM_LINES` row:

```markdown
| `OBSIDIAN_MAX_NOTE_CHARS` | Character ceiling per note, checked next to the line ceiling (no append headroom) | `40000` | integer |
| `OBSIDIAN_READ_MAX_CHARS` | Default page size of `read_note`; a longer note comes back in pages | `40000` | integer from `1000` to `1000000` (other values use the default) |
```

- [ ] **Step 7: README — note-size policy and best practices**

Replace the "Note-size policy" section body (lines 1255-1259) with:

```markdown
`OBSIDIAN_NOTE_SIZE_POLICY` (`strict|warn|off`, default `warn`) checks each written note against two ceilings: `OBSIDIAN_MAX_NOTE_LINES` (default `500`) and `OBSIDIAN_MAX_NOTE_CHARS` (default `40000`):
- Full-content writes are checked against both ceilings
- Incremental writes (append, `edit_note_section`) are checked against a lower line ceiling (`OBSIDIAN_MAX_NOTE_LINES` minus `OBSIDIAN_APPEND_HEADROOM_LINES`, default headroom `100` — effectively 400), so an append gets flagged before a later one would blow past the hard limit. The character ceiling has no headroom
- A note within the line ceiling but over the character ceiling is flagged too. The warning tells the agent to split the note into smaller linked notes
- The check uses the size of the note after the write. A note that is already over a ceiling is flagged by every write that leaves it over
- `strict` rejects the write; `warn` writes anyway and returns a warning; `off` disables the check
- Daily notes are always exempt
```

Replace step 1 of "Safe note editing" (line 1323) with:

```markdown
1. Read the existing note first. If the response has `truncated: true`, read every page: follow `next_offset` until it is `null`
```

- [ ] **Step 8: README — changelog and counts**

Add these two bullets right above the `- 🐛 **Bug fixes**:` line of "Unreleased — Swapo-Finance fork additions":

```markdown
- 📄 **Paged note reads** - `read_note` returns a long note in pages. New parameters: `offset`, `max_chars`, and `section` (read one section by its heading). A paged response adds `truncated`, `next_offset`, `total_chars`, a heading `outline`, and a `message`. The default page size is `OBSIDIAN_READ_MAX_CHARS` (`40000`). This keeps a read under 50,000 characters, the size at which Claude Code saves a tool result to a file. A note that fits in one page keeps the old response
- 📏 **Character ceiling for notes** - The note-size policy also checks `OBSIDIAN_MAX_NOTE_CHARS` (default `40000`), so a long note with few lines is flagged too. Under the default `warn` policy, a write over the ceiling returns a warning that tells the agent to split the note into smaller linked notes
```

Get the real counts:

Run: `ls tests/test_*.py | wc -l` → the file count (44 expected).
Run: `OBSIDIAN_VAULT_PATH=$(mktemp -d) uv run pytest -q | tail -1` → the test count (`598 passed` expected).

Put both counts in the project tree line (line 1233, `# 42 files + conftest.py, flat, one per feature, 559 tests total`) and in the Testing paragraph (line 1529, `The suite is 42 files, ... 559 tests total`).

- [ ] **Step 9: CLAUDE.md files**

Root `CLAUDE.md`:

- Line 107: replace `(1041 lines)` with the count from `wc -l obsidian_mcp/utils/filesystem.py`.
- Line 116: replace `559 tests` with the test count from Step 8.
- Line 130: replace `(17 already: slug/tag style, template enforcement, note size policy, wikilink policy, index tuning)` with `(19 already: slug/tag style, template enforcement, note size policy, wikilink policy, index tuning, read page size)`.

`obsidian_mcp/utils/CLAUDE.md`:

- Line 16: replace `1041 lines` with the same `filesystem.py` count.
- Lines 32-35: replace `(owned directly by \`vault_config.py\`)` with `(owned directly by \`vault_config.py\`; it checks the line ceiling and the character ceiling)`.

- [ ] **Step 10: Verify**

Run: `grep -c 'id="read_note"' README.md`
Expected: `1`.
Run: `grep -n 'OBSIDIAN_READ_MAX_CHARS\|OBSIDIAN_MAX_NOTE_CHARS' README.md obsidian_mcp/tools/vault_meta.py`
Expected: hits in the full example, the configuration table, the note-size policy, the `read_note` entry, the changelog, and the 2 catalog rows.
Run: `uv run ruff check obsidian_mcp tests && uv run pyright`
Expected: `All checks passed!` and `0 errors, 0 warnings, 0 informations`.
Run: `OBSIDIAN_VAULT_PATH=$(mktemp -d) uv run pytest -q`
Expected: `598 passed`.

- [ ] **Step 11: Controller commits**

```bash
git add obsidian_mcp/tools/vault_meta.py tests/test_server_tool_wrappers.py README.md CLAUDE.md obsidian_mcp/utils/CLAUDE.md
git commit -m "docs: document paged note reads and the character ceiling"
```

---

## Final verification (controller, after Wave 3)

- [ ] Run `uv run ruff check obsidian_mcp tests` → `All checks passed!`
- [ ] Run `uv run pyright` → `0 errors, 0 warnings, 0 informations`
- [ ] Run `OBSIDIAN_VAULT_PATH=$(mktemp -d) uv run pytest -q` → `598 passed`
- [ ] Run `wc -l obsidian_mcp/mcp_notes.py obsidian_mcp/tools/vault_meta.py obsidian_mcp/tools/note_read.py obsidian_mcp/tools/section_editing.py obsidian_mcp/utils/validation.py obsidian_mcp/utils/vault_config.py obsidian_mcp/tools/write_policy.py obsidian_mcp/constants.py` → every count under 350
- [ ] Run `graphify update .` to keep the code graph current (`graphify-out/` is git-ignored)
- [ ] Run the whole-branch review that root `CLAUDE.md` requires: `code-reviewer`, `security-reviewer`, and `python-reviewer`, with the `uncle-bob-craft` criteria. Add `mcp-tool-contract-reviewer` for the `read_note_tool` change.
