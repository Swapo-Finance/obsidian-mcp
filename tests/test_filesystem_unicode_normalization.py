#!/usr/bin/env python3
"""Regression coverage for the NFC/NFD Unicode-normalization risk flagged as
*unverifiable on macOS* while fixing wikilink resolution (see
tools/wikilink_validation.py's _resolve_normalization_fallback_target): the
same class of byte-exact mismatch may exist in ObsidianVault's own path
boundary (_ensure_safe_path / _get_absolute_path, this module) -- a note
written under one Unicode normalization form (NFC, precomposed, e.g. a
single "é") could become unreadable/undeletable when a caller supplies
a path in the OTHER form (NFD, decomposed, e.g. "e" + a combining acute
accent), even though both forms display as the identical text.

Why this needs a real Linux run: APFS (macOS) reconciles NFC/NFD
transparently at the filesystem layer regardless of what the Python code
does, so a broken lookup here still "works" on a Mac even with zero fix.
Linux (ext4, CI's ubuntu-latest) stores exact bytes and does not paper over
the mismatch. These tests are written to fail on Linux if the boundary is
unfixed -- CI (or a local Linux container) is the verifier macOS cannot be.

Every NFC/NFD pair below is built with unicodedata.normalize and sanity
-checked (both `!=` and `is_normalized`) to guarantee two genuinely
different byte sequences before using them -- a test that accidentally
compares a string to itself would prove nothing.
"""

import shutil
import tempfile
import unicodedata

import pytest

from obsidian_mcp.utils.filesystem import init_vault

ACCENTED_NAME = "Café Especial"


def _distinct_forms(name: str) -> tuple[str, str]:
    """Return (nfc, nfd) forms of name, proving they are genuinely
    different byte sequences before a test relies on that difference."""
    nfc = unicodedata.normalize("NFC", name)
    nfd = unicodedata.normalize("NFD", name)
    assert nfc != nfd, "test fixture name has no composable accent -- pick another"
    assert unicodedata.is_normalized("NFC", nfc)
    assert unicodedata.is_normalized("NFD", nfd)
    return nfc, nfd


@pytest.fixture
def vault():
    """Bare ObsidianVault pointed at a fresh temp dir. Tests call vault.*
    methods directly (bypassing tools/note_management's policy chain) to
    isolate the filesystem boundary itself, matching how the prior
    investigation demonstrated/discussed this risk (vault.write_note /
    vault.read_note directly)."""
    temp_dir = tempfile.mkdtemp(prefix="obsidian_unicode_norm_")
    v = init_vault(temp_dir)
    yield v
    shutil.rmtree(temp_dir, ignore_errors=True)


class TestWriteThenReadAcrossNormalizationForms:
    """Intended behavior: a note is findable regardless of which Unicode
    normalization form the caller's path string happens to be in -- the
    same standard the wikilink fix already established. Expected to FAIL
    on Linux today (the discovery this file exists to make) if
    _get_absolute_path has no normalization-aware fallback."""

    @pytest.mark.asyncio
    async def test_write_nfc_read_nfd(self, vault):
        nfc, nfd = _distinct_forms(ACCENTED_NAME)
        await vault.write_note(f"{nfc}.md", "# Content\n")

        note = await vault.read_note(f"{nfd}.md")

        assert note.content == "# Content\n"

    @pytest.mark.asyncio
    async def test_write_nfd_read_nfc(self, vault):
        nfc, nfd = _distinct_forms(ACCENTED_NAME)
        await vault.write_note(f"{nfd}.md", "# Content\n")

        note = await vault.read_note(f"{nfc}.md")

        assert note.content == "# Content\n"


class TestWriteThenDeleteAcrossNormalizationForms:
    """Same standard applied to delete_note: a caller must be able to
    delete a note using either normalization form of its name."""

    @pytest.mark.asyncio
    async def test_write_nfc_delete_nfd(self, vault):
        nfc, nfd = _distinct_forms(ACCENTED_NAME)
        await vault.write_note(f"{nfc}.md", "# Content\n")

        assert await vault.delete_note(f"{nfd}.md") is True

        with pytest.raises(FileNotFoundError):
            await vault.read_note(f"{nfc}.md")

    @pytest.mark.asyncio
    async def test_write_nfd_delete_nfc(self, vault):
        nfc, nfd = _distinct_forms(ACCENTED_NAME)
        await vault.write_note(f"{nfd}.md", "# Content\n")

        assert await vault.delete_note(f"{nfc}.md") is True

        with pytest.raises(FileNotFoundError):
            await vault.read_note(f"{nfd}.md")


class TestListNotesRoundTripIsFormAgnostic:
    """Control case, expected to pass on BOTH platforms regardless of any
    fix: list_notes() never transforms a filename, it reports back whatever
    bytes are actually on disk. Reading via the *exact* string list_notes
    just returned is therefore always a literal byte-for-byte match, on any
    filesystem. This isolates 'does list_notes/read_note stay internally
    consistent' (always true) from the genuine cross-form lookup risk
    tested elsewhere in this file (only true once fixed, on Linux)."""

    @pytest.mark.asyncio
    async def test_nfc_written_note_listed_and_readable(self, vault):
        nfc, _ = _distinct_forms(ACCENTED_NAME)
        await vault.write_note(f"{nfc}.md", "# Content\n")

        notes = await vault.list_notes()
        matching = [n for n in notes if n["path"] == f"{nfc}.md"]
        assert len(matching) == 1

        note = await vault.read_note(matching[0]["path"])
        assert note.content == "# Content\n"

    @pytest.mark.asyncio
    async def test_nfd_written_note_listed_and_readable(self, vault):
        _, nfd = _distinct_forms(ACCENTED_NAME)
        await vault.write_note(f"{nfd}.md", "# Content\n")

        notes = await vault.list_notes()
        matching = [n for n in notes if n["path"] == f"{nfd}.md"]
        assert len(matching) == 1

        note = await vault.read_note(matching[0]["path"])
        assert note.content == "# Content\n"


class TestListNotesDirectoryArgumentAcrossNormalizationForms:
    """Additional boundary found while mapping the surface (not in the
    original checklist): list_notes(directory=...) routes `directory`
    through the exact same _get_absolute_path as read_note's `path`. A
    vault with an accented subfolder is exposed to the identical
    mismatch -- and the failure mode is worse than a 404: _get_absolute_path
    misses are swallowed by `if not search_path.exists(): return []`, so
    the caller silently sees an EMPTY listing instead of an error, hiding
    every note in that folder with no indication anything went wrong.

    Assert on count/name/readability, NOT on the exact string of the
    returned `path` -- confirmed by direct repro that Path.glob() echoes
    back whatever byte-form the caller's `directory` argument used for the
    parent segment (it does not re-read that segment's true on-disk name
    from the directory entry), so the returned string legitimately differs
    from the on-disk bytes without that being a bug. The bug this class
    exists to catch is the note going missing entirely, not which
    normalization form its reported path happens to use.
    """

    @pytest.mark.asyncio
    async def test_nfc_directory_listed_via_nfd_argument(self, vault):
        nfc, nfd = _distinct_forms(ACCENTED_NAME)
        await vault.write_note(f"{nfc}/note.md", "# Content\n")

        notes = await vault.list_notes(directory=nfd)

        assert len(notes) == 1
        assert notes[0]["name"] == "note.md"
        note = await vault.read_note(notes[0]["path"])
        assert note.content == "# Content\n"

    @pytest.mark.asyncio
    async def test_nfd_directory_listed_via_nfc_argument(self, vault):
        nfc, nfd = _distinct_forms(ACCENTED_NAME)
        await vault.write_note(f"{nfd}/note.md", "# Content\n")

        notes = await vault.list_notes(directory=nfc)

        assert len(notes) == 1
        assert notes[0]["name"] == "note.md"
        note = await vault.read_note(notes[0]["path"])
        assert note.content == "# Content\n"


class TestEnsureSafePathContainmentIsFormAgnostic:
    """_ensure_safe_path's vault-containment check (resolve() +
    relative_to()) is purely lexical -- it must not misclassify an
    NFD-encoded path as escaping the vault just because its bytes differ
    from the NFC form of the same visible name. This is a containment
    *safety* check, not an existence *lookup*: expected to already hold
    today, on both platforms, with no fix needed -- included as a
    permanent regression guard and to document that this particular
    hypothesis (the safety check itself misfiring on NFD input) is ruled
    out, narrowing where the real risk can live."""

    def test_nfd_relative_path_resolves_inside_vault(self, vault):
        _, nfd = _distinct_forms(ACCENTED_NAME)

        resolved = vault._ensure_safe_path(f"{nfd}.md")

        assert resolved.is_relative_to(vault.vault_path)
        assert resolved.name == f"{nfd}.md"

    def test_nfc_relative_path_resolves_inside_vault(self, vault):
        nfc, _ = _distinct_forms(ACCENTED_NAME)

        resolved = vault._ensure_safe_path(f"{nfc}.md")

        assert resolved.is_relative_to(vault.vault_path)
        assert resolved.name == f"{nfc}.md"


class TestWriteNoteOverwriteSemanticsAcrossNormalizationForms:
    """Judgment call (see report): write_note's overwrite check must treat
    an existing note under the OTHER normalization form as THE SAME note --
    matching the precedent already set for wikilink resolution
    (_resolve_normalization_fallback_target: "this isn't a style choice,
    the two strings are the same text"). Two consequences, both verified:

    1. overwrite=False must still refuse (raise FileExistsError) instead of
       silently creating a second, byte-different file that would render
       identically in Obsidian's UI -- indistinguishable duplicates are a
       worse outcome than a loud rejection.
    2. overwrite=True must update the existing file IN PLACE, not create a
       second file next to it -- the "additive lookup, don't rewrite what's
       already on disk" shape applies to the write target too: retargeting
       to the note that's already there is what avoids clobbering/orphaning
       data, not what causes it.
    """

    @pytest.mark.asyncio
    async def test_overwrite_false_raises_when_other_form_exists(self, vault):
        nfc, nfd = _distinct_forms(ACCENTED_NAME)
        await vault.write_note(f"{nfc}.md", "# Original\n")

        with pytest.raises(FileExistsError):
            await vault.write_note(f"{nfd}.md", "# New\n", overwrite=False)

    @pytest.mark.asyncio
    async def test_overwrite_true_updates_existing_file_in_place(self, vault):
        nfc, nfd = _distinct_forms(ACCENTED_NAME)
        await vault.write_note(f"{nfc}.md", "# Original\n")

        await vault.write_note(f"{nfd}.md", "# Updated\n", overwrite=True)

        md_files = list(vault.vault_path.glob("*.md"))
        assert len(md_files) == 1, (
            f"expected exactly one file on disk, found {[f.name for f in md_files]}"
        )
        assert md_files[0].read_text(encoding="utf-8") == "# Updated\n"


class TestNoFalsePositiveWhenNoAliasExists:
    """The alias fallback must not fire when there is nothing to find --
    confirms the new code path degrades to exactly today's behavior for a
    genuinely new or genuinely missing note (branch coverage for the
    fallback's "nothing matched" path, not just its "found a match" path)."""

    @pytest.mark.asyncio
    async def test_read_genuinely_missing_note_still_raises(self, vault):
        with pytest.raises(FileNotFoundError):
            await vault.read_note("Does Not Exist.md")

    @pytest.mark.asyncio
    async def test_write_brand_new_accented_note_succeeds(self, vault):
        nfc, _ = _distinct_forms(ACCENTED_NAME)

        note = await vault.write_note(f"{nfc}.md", "# Content\n")

        assert note.content == "# Content\n"
