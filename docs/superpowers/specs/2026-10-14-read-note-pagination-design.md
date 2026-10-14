# Read note pagination — design

Date: 2026-10-14 · Status: approved; plan written, not implemented · Branch: `feat/read-note-pagination`

## Problem

Claude Code saves an MCP text result over 50,000 characters to a file. The agent must then
read that file in chunks. `read_note_tool` returns the whole note in one result. A note of
63,544 characters in 499 lines gave a text result of 64,250 characters (measured with
FastMCP's in-memory `Client`). The write policy counts lines only, so this note passed the
500-line limit.

## Findings (current behavior, verified in code)

1. `read_note_tool(path)` (`mcp_notes.py:20`) calls `tools/note_read.read_note(path, ctx)`.
   It returns `{success, path, operation: "read", details: {content, metadata}}`. `content`
   is the whole file, frontmatter included.
2. The only size cap on a read is a 10 MB reject in `ObsidianVault.read_note`
   (`utils/filesystem.py:342`).
3. FastMCP 2.12 sends a dict result twice: a compact JSON text block
   (`pydantic_core.to_json`, non-ASCII kept as is) and `structuredContent`. The text block is
   the content plus JSON escapes and the other keys.
4. `ObsidianVault.read_note` reads in text mode with universal newlines, so `\r\n` reads as
   `\n`.
5. `edit_note_section` finds a section with the private `_find_section_bounds`
   (`tools/section_editing.py:16`):
   - a heading line matches `^(#{1,6})\s+(.+)$`;
   - the level must match; the text match is case-insensitive and NFC-normalized; the first
     match wins;
   - the section ends at the next line that matches `^(#{1,6})\s+` with the same or a higher
     level, or at the end of the note.

   `edit_note_section` parses its `section_identifier` with the same regex, inline
   (`section_editing.py:167`). The parser does not know code fences or frontmatter.
6. `check_note_size_policy` (`utils/vault_config.py:71`) takes a line count. Its one caller
   is `_size_policy_warning` (`tools/write_policy.py:135`). `create_note`, `update_note`
   (append and replace), and `edit_note_section` call that helper. The check uses the size
   after the write, so every checked write that leaves a note over the limit is flagged.
   `strict` raises `ValueError`, `warn` returns one message, and `off` and daily notes skip
   the check.
7. Search tools mark a cut result with `truncated` plus a `message`
   (`tools/search_discovery.py:240-260`).
8. `read_int_env` (`utils/env.py`) has no range check. A value that is not an integer falls
   back to the default with a warning.

## Goals

- A default read of any note gives a text result under the 50,000-character client limit.
- An agent can reach every part of a long note, by page or by section.
- A read without `section` that fits in one page from offset 0 returns today's response,
  byte for byte.
- The write policy flags a note that is long in characters, also when its line count is low.

## Non-goals

- A heading parser that knows code fences or frontmatter. A and B use the parser of
  `edit_note_section` as it is.
- Paging in other tools (`search_*`, `get_note_info`, `view_note_images`).
- A cursor that pins one version of a note across pages.
- Changes to `edit_note_section` behavior or error texts. Its wrapper schema shares the
  heading pattern of `section` (see B).
- An append headroom for characters.
- A version bump.

## Design

### Terms

- **text**: what one call pages. It is the whole note, or the selected section when
  `section` is set.
- **page**: the part of the text that one call returns.
- **offset**: a character position in the text. It is a Python `str` index, so one emoji is
  one character.

### A. Paged read

New parameters on `read_note_tool` and on `tools/note_read.read_note`: `offset: int = 0`,
`max_chars: int | None = None`, and `section: str | None = None` (part B). `ctx` stays last.

Steps:

1. Validate `path` as today.
2. Validate `offset >= 0` and, when set, `1000 <= max_chars <= 1_000_000` with
   `validate_read_window`. On failure, raise `ValueError` with the `ERROR_MESSAGES` text.
3. Read the note. Select the text (part B when `section` is set).
4. Set the page size to `max_chars`, or to `vault.read_max_chars` (`OBSIDIAN_READ_MAX_CHARS`)
   when `max_chars` is `None`.
5. If `offset == 0` and `len(text) <= page size`, return today's response with
   `content = text`. Add no keys, except `section`: when `section` is set, `details` is
   `{section, content, metadata}`, with `section` first and equal to the argument as passed.
   A read without `section` that fits from offset 0 keeps today's response byte for byte.
6. If `offset >= len(text)`, raise `ValueError(ERROR_MESSAGES["read_offset_out_of_range"])`.
   Step 5 already returned for `offset == 0`, so an empty note at offset 0 is not an error.
7. Else cut the page with the cut rule and return the paged response.

**Thread.** The section selection of step 3 and steps 5 to 7 (page cut, outline) are
CPU-bound. On a crafted 10 MiB note they take seconds. They live in one pure helper,
`_page_details(text, offset, page_size, metadata, section, path)`, which builds `details`;
`read_note` runs it with `await asyncio.to_thread(...)` and builds the response envelope
once. A thread is enough here: every regex match runs on one short line, so the thread
lets go of the GIL often. One long match would be a different case (see
`.claude/memory/architecture-regex-gil-process-isolation.md`).

**Cut rule.** Let `window_end = offset + page size`.

- If `window_end >= len(text)`, the page runs to the end of the text.
- Else the page ends just after the last `\n` in `text[offset:window_end]`. The page keeps
  that `\n`.
- If that window has no `\n`, the page ends at `window_end` (a hard cut).

A page has at most `page size` characters and at least one character. The pages, in order,
join to the exact text.

**Paged response.** The top level is the same as today: `success`, `path`,
`operation: "read"`, and `details`. `details` holds these keys, in this order:

| Key | Value |
| --- | --- |
| `section` | Only when `section` is set: the argument as passed |
| `truncated` | `true` when text remains after the page |
| `next_offset` | The offset of the next page, or `null` on the last page |
| `total_chars` | `len(text)` |
| `message` | Only when `truncated` is `true`: `READ_TRUNCATED_MESSAGE`, filled in |
| `content` | The page text |
| `metadata` | The note metadata, as today (also for a section read), on every page |
| `outline` | A list of `{heading, level, offset, chars}` for the whole text |

The flags and the message come before the long content, and `section` comes first, so a
page or a section reply shows that it holds only part of the note. `message` mirrors the
search pattern. It gives the next offset and names `section` and `outline`. It also tells
the agent to read all pages before it replaces the note with `update_note`, and ends with
"If total_chars or metadata.modified changes between pages, the note changed: read again
from offset 0." `RESPONSE_STRUCTURES["read_page"]` documents this shape, in this order.

The write tools say the same at the write side: the `update_note_tool` docstring, its
`content` description, and the `overwrite` description of `create_note_tool` tell the agent
that after a read with `truncated=true`, `offset`, or `section` it holds only part of the
note, must not replace the note with it, and must read every page first or use
`merge_strategy='append'` or `edit_note_section_tool`.

**Outline.**

- Add one entry for each line that the parser of `edit_note_section` accepts as a heading.
- `heading` is the heading text without the `#` marks, stripped, and cut at
  `SECTION_MAX_CHARS` (200) characters: a longer heading could not be passed back as
  `section`. `level` is the number of `#` marks.
- `offset` is the offset of the heading line start in the text.
- `chars` runs from the heading line start to the start of the next heading of the same or
  a higher level, or to the end of the text. This is the parser's own section end. So
  `chars` equals the length of a section read of that heading (first match).
- One pass computes every section end (`scan_headings`), O(lines). A stack holds the open
  headings, levels rising. Each line that matches the closer regex `^(#{1,6})\s+` ends
  every open heading of its level or deeper; then the line is pushed if it parses as a
  heading. The closer regex is looser than the heading regex: a bare `"## "` line closes
  sections but is not a heading, so it is never pushed. This gives the same ends as one
  `section_end` scan per heading, which cost O(lines × open headings): a paged read of six
  nested headings over 5.2 million plain lines took 23 s, all of it on the event loop.
  With the one pass and the thread it took about 4 s, and the longest loop stall was
  about 0.7 s, from the note read that runs before the thread (see Known limits).
- Cap the outline at 50 entries. While more than 50 entries remain, remove the deepest
  level. If one level remains with more than 50 entries, keep the first 50. A `# ponytail:`
  comment marks this ceiling. The cap runs before the entries are built.

### B. Section read

- `section` accepts the format of `section_identifier`. Both wrapper schemas use
  `pattern=SECTION_HEADING_PATTERN` (`^#{1,6}\s+\S.*$`) and
  `max_length=SECTION_MAX_CHARS` (200), from `constants.py`. The MCP SDK runs `jsonschema`
  with Python `re` on the whole argument before `maxLength` applies. The first pattern,
  `^#{1,6}\s+.+$`, backtracked quadratically: `"#" + " " * 16000 + "\nx\ny"` took over a
  second to reject. With `\S` right after the spaces the check is linear, and a blank
  heading text such as `"##   "` is rejected. The new pattern applies to
  `edit_note_section_tool.section_identifier` too.
- The tools layer rejects a `section` longer than `SECTION_MAX_CHARS` before it parses it,
  with `ValueError(ERROR_MESSAGES["section_not_found"])`. A direct call then never runs the
  heading regex on a long value.
- Matching follows `edit_note_section`. The level must match, the text match is
  case-insensitive and NFC-normalized, and the first match wins.
- The text starts at the heading line and runs to the parser's section end. It includes the
  sub-sections and the `\n` before the next heading.
- Part A applies to that text. `offset`, `next_offset`, `total_chars`, and the outline
  offsets count from the heading line.
- A section that fits from offset 0 returns today's shape with `section` first:
  `{section, content, metadata}`. `content` then holds only the section. A paged section
  read starts `details` with `section` too.
- No match raises `ValueError(ERROR_MESSAGES["section_not_found"])`. A value that is not a
  heading also gets this error. `edit_note_section` has no error key to reuse, and it keeps
  its inline texts.

**Parser reuse.** The heading grammar lives in `utils/markdown_sections.py`, pure and free
of MCP concepts, with no behavior change for `edit_note_section`:

- `parse_heading(line) -> tuple[int, str] | None` holds the heading regex. It returns the
  level and the stripped text.
- `section_end(lines, start, level) -> int` holds the section-end loop. It uses a looser
  closer regex than `parse_heading`.
- `find_section_bounds(lines, level, text)` finds the first matching heading and its end.
- `scan_headings(lines) -> list[Heading]` gives every heading with its section end in one
  pass (see Outline).

`section_editing.py` and `note_read.py` import these functions. `tests/test_edit_note_section.py`
guards the move.

### C. Character limit in the write policy

- Add the knob `OBSIDIAN_MAX_NOTE_CHARS`, default `40000`. Read it in `ObsidianVault.__init__`
  with `_read_int_env(..., minimum=1)`: a value below 1 logs a warning and uses `40000`.
  `OBSIDIAN_MAX_NOTE_LINES` keeps no range check.
- Give `check_note_size_policy` a fifth argument, `resulting_char_count`.
  `_size_policy_warning` passes `len(content)`.
- Run the char check next to the line check, after the `off` and daily-note exits. The
  modes and the exemptions are the same.
- Check full and incremental writes against `OBSIDIAN_MAX_NOTE_CHARS` directly. There is no
  headroom.
- Keep the line-limit semantics for a note that is already over the limit: the check uses
  the size after the write.
- Take the text from `ERROR_MESSAGES["note_over_char_limit"]`. It tells the agent to split
  the note into smaller notes linked with `[[wikilinks]]`.
- A write over both limits gets one message: the line text and the char text, joined by a
  space. `strict` raises it and `warn` returns it.

### Configuration

| Variable | Default | Valid values | Read into |
| --- | --- | --- | --- |
| `OBSIDIAN_READ_MAX_CHARS` | `40000` | integer from `1000` to `1000000`; another value logs a warning and uses `40000` | `vault.read_max_chars` |
| `OBSIDIAN_MAX_NOTE_CHARS` | `40000` | integer from `1`; another value logs a warning and uses `40000` | `vault.max_note_chars` |

`READ_MAX_CHARS_DEFAULT`, `READ_MAX_CHARS_MIN`, and `READ_MAX_CHARS_MAX` live in
`constants.py`. The env check, `validate_read_window`, and the wrapper `Field(ge=, le=)`
share them.

Both range checks run in `read_int_env` (`utils/env.py`), which takes keyword-only
`minimum` and `maximum` (each optional, inclusive). A value out of range logs a warning
that names the variable and returns the default. The `_read_int_env` delegator in
`utils/filesystem.py` takes the same parameters.

Why 40,000: a simulation used a 63,544-character, 499-line note with headings, accents,
and quotes. Its first page had 39,867 characters, and the JSON text block of that page had
42,799 characters, outline included. That leaves room under 50,000 for metadata and a full
50-entry outline. A plain-text test note of the same size and line count gives a first
page of 39,878 characters.

### Components

| File | Change |
| --- | --- |
| `constants.py` | 3 paging constants, `SECTION_HEADING_PATTERN`, `SECTION_MAX_CHARS`, `READ_TRUNCATED_MESSAGE`, 5 `ERROR_MESSAGES` keys, `RESPONSE_STRUCTURES["read_page"]` |
| `utils/env.py` | `minimum` and `maximum` on `read_int_env` |
| `utils/filesystem.py` | Read `OBSIDIAN_MAX_NOTE_CHARS` (minimum 1) and `OBSIDIAN_READ_MAX_CHARS` (1000 to 1000000) through `_read_int_env` |
| `utils/vault_config.py` | Char check in `check_note_size_policy` |
| `tools/write_policy.py` | `_size_policy_warning` passes `len(content)` |
| `utils/validation.py` | `validate_read_window(offset, max_chars)` |
| `utils/markdown_sections.py` | The heading grammar: `parse_heading`, `section_end`, `find_section_bounds`, `scan_headings` |
| `tools/section_editing.py` | Imports the heading grammar from `utils/markdown_sections.py` |
| `tools/note_read.py` | Section select, cut rule, outline, paged response, in `_page_details`, run in a thread |
| `mcp_notes.py` | 3 new parameters, a "Long notes" block in the docstring, the shared heading pattern, and the partial-read warning on the write tools |
| `tools/vault_meta.py` | 2 rows in the `help_tool` env catalog |
| Docs | `README.md`, root `CLAUDE.md`, `obsidian_mcp/utils/CLAUDE.md` |

`configure.py` and the `help_tool` docstring list no read parameters and no size knobs, so
they do not change.

The 5 new `ERROR_MESSAGES` keys are `invalid_read_offset`, `invalid_read_max_chars`,
`read_offset_out_of_range`, `section_not_found`, and `note_over_char_limit`.

### Error handling

| Condition | Result |
| --- | --- |
| `offset < 0` | `ValueError(invalid_read_offset)`, then `ToolError`. On an MCP call the schema rejects it first. |
| `max_chars` out of range | `ValueError(invalid_read_max_chars)`, then `ToolError`. On an MCP call the schema rejects it first. |
| `offset > 0` and `offset >= len(text)` | `ValueError(read_offset_out_of_range)`, then `ToolError` |
| `section` matches no heading | `ValueError(section_not_found)`, then `ToolError` |
| `section` longer than 200 characters | `ValueError(section_not_found)`, before any parse. On an MCP call the schema rejects it first. |
| `section` with a blank heading text, or over several lines | The schema rejects it (`ToolError`) |
| The note does not exist | `FileNotFoundError(note_not_found)`, as today |
| `OBSIDIAN_READ_MAX_CHARS` out of range | A warning in the log and the default `40000`. The boot continues. |
| `OBSIDIAN_MAX_NOTE_CHARS` below 1 | A warning in the log and the default `40000`. The boot continues. |

## Testing

TDD. New file `tests/test_read_note_pagination.py`:

- env: the default, valid values, and the out-of-range fallback;
- paged read: a note that fits returns today's response byte for byte (compared with
  `pydantic_core.to_json`, the serializer FastMCP uses); the cut after the last `\n`; pages
  that join to the exact text; the hard cut; the last page; the env page size; the offset
  errors; the window validation; an offset inside a line; multibyte text;
- outline: entries and sizes, both cap rules, and a note without headings;
- section: bounds with sub-sections, case-insensitive match, `chars` equal to the section
  length, a paged section, duplicate headings, and no match;
- wrapper: forwarding of `offset`, `max_chars`, and `section`; `ToolError`; and an in-memory
  `Client` call on a 63,544-character, 499-line note whose text result stays under 50,000
  characters;
- boundaries: the key order of each shape; a hard cut after an offset that follows a
  newline; texts of `max_chars` and `max_chars + 1` characters; a window that ends at the
  end of the text; exactly 50 headings; exact page lengths of multibyte text; a 5,000-char
  heading cut to 200; a deep-tail note (H1 to H4 nested, a long tail, a bare `"## "` line)
  whose outline sizes equal its section reads;
- the heading schema through the in-memory `Client` (a blank heading text, and the old
  ReDoS input at a small size, for both tools); a 201-character `section` on a direct call;
  the partial-read warning in the write tool descriptions; and the env warnings.

`tests/test_note_size_policy_writes.py` gets char-limit cases: the boundary, `strict`, the
`warn` text, `off`, the daily exemption, an append without headroom, a note already over
the limit, and the combined message. `tests/test_edit_note_section.py` guards the parser
carve-out without changes. `tests/test_server_tool_wrappers.py` checks the two new names in
the help catalog.

## Compatibility

- A read that fits in 40,000 characters from offset 0 does not change. A longer note now
  returns its first page and the paged keys. This change is intended.
- `edit_note_section_tool` shares the new heading pattern, so its schema now rejects a
  blank heading text such as `"##   "`, which it used to accept. Direct calls of
  `edit_note_section` do not change.
- `OBSIDIAN_MAX_NOTE_CHARS` values below 1 now fall back to `40000`.
- `tools.note_read.read_note` moves `ctx` from position 2 to position 5. `mcp_notes.py` is
  the only positional caller, and the plan updates it. Other code must pass `ctx=` by name.
- Under the default `warn` policy, a write that leaves a note over 40,000 characters now
  returns a warning. Under `strict`, the write is rejected.
- `check_note_size_policy` takes a new required argument. Its only caller is
  `_size_policy_warning`.
- The tool count stays 31. There is no new dependency.

## Known limits

- The heading parser does not know code fences. A `# comment` line in a fenced code block,
  or a YAML comment line in frontmatter (`#`, a space, and text), counts as a heading. It shows in the outline and
  can end a section early. `edit_note_section` has the same limit today.
- Each page is a separate read. If the note changes between calls, the offsets shift.
  Compare `total_chars` and `metadata.modified`, and read again from offset 0 when either
  changed.
- `metadata` repeats on every page, since it carries `modified` for that check. A very
  large frontmatter repeats on every page.
- The thread covers the section select, the page cut, and the outline. The read of the
  note itself (`ObsidianVault.read_note`) still runs on the event loop: on a 10 MiB note
  it held the loop for about 1.2 s in one measurement.
- `section` reaches only the first of two equal headings. Use the `offset` of the later one
  from `outline`.
- No flag tells the agent that the outline was capped. The agent can still reach every part
  with `offset`.
- 40,000 is a character budget, not a byte or token budget. Many quotes, backslashes, or
  emoji make the JSON block larger (an emoji is 2 UTF-16 units). For such notes, lower
  `max_chars` or `OBSIDIAN_READ_MAX_CHARS`.
- The 200-character heading cap bounds each outline entry, not the outline. A full page
  plus 50 headings of about 200 characters gives a text block of about 53,500 characters.
  The 50,000-character goal holds when the outline headings average about 120 characters
  or less. For a note with many long headings, lower `max_chars`.
