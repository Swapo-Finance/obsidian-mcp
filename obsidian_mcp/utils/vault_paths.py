"""Cross-platform path normalization for vault-relative paths and paths
allowed to live outside the vault (spec section 2).

Split out of vault_config.py; re-exported from there for backward
compatibility.
"""

import re
from pathlib import Path, PurePosixPath

# Windows absolute paths: drive-letter ("C:\..." / "C:/...") or UNC
# ("\\server\share\..."). pathlib.Path.is_absolute() only recognizes a
# leading "/" (or "~"), so on a POSIX host these silently fall through to
# the vault-relative branch below instead of being rejected as
# absolute-and-outside-the-vault. Must be matched against the raw,
# pre-"\\"->"/"-normalization text: UNC's leading "\\\\" disappears after
# that replace, becoming indistinguishable from it.
_WINDOWS_ABS_RE = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\)")


def _is_windows_absolute_path(raw_text: str) -> bool:
    return bool(_WINDOWS_ABS_RE.match(raw_text))


def normalize_vault_relative_path(raw: str | None, vault_path: Path) -> str | None:
    """Accept vault-relative, vault-basename-prefixed, or absolute/`~` paths
    and return the canonical POSIX path relative to the vault root.

    Returns None if the resolved path falls outside the vault — callers that
    require an in-vault path (folders, daily dir) treat None as invalid;
    callers that allow out-of-vault paths (templates) use
    resolve_path_maybe_outside_vault instead.
    """
    if raw is None:
        return None
    text = raw.replace("\\", "/").strip()
    if text in ("", "."):
        return ""

    # Absoluteness (and "~") must be checked on the un-stripped text: a
    # leading "/" is what makes a POSIX path absolute in the first place
    # (e.g. "/Users/x/vaults/v/01-projects", straight from spec section 2's
    # own example). Stripping slashes first — as this used to do — silently
    # turns a real absolute path into a bogus vault-relative one instead of
    # resolving it and checking vault membership.
    candidate = Path(text)
    if (
        candidate.is_absolute()
        or text.startswith("~")
        or _is_windows_absolute_path(raw.strip())
    ):
        resolved = Path(text).expanduser().resolve()
    else:
        text = text.strip("/")
        if text == "":
            return ""
        # Detect and strip a leading "<vault-basename>/" prefix, e.g.
        # "brain-swapo/01-projects" when the vault itself is ".../brain-swapo".
        parts = PurePosixPath(text).parts
        if parts and parts[0] == vault_path.name:
            text = "/".join(parts[1:])
        resolved = (vault_path / text).resolve() if text else vault_path.resolve()

    try:
        rel = resolved.relative_to(vault_path.resolve())
    except ValueError:
        return None
    rel_str = str(rel).replace("\\", "/")
    return "" if rel_str == "." else rel_str


def resolve_path_maybe_outside_vault(raw: str, vault_path: Path) -> Path:
    """Like normalize_vault_relative_path, but for paths allowed to live
    outside the vault (templates can be shared across projects). Returns the
    resolved absolute Path without checking vault membership.

    Raises ValueError on an empty path.
    """
    text = (raw or "").replace("\\", "/").strip()
    if not text:
        raise ValueError("Empty path")

    candidate = Path(text)
    if (
        candidate.is_absolute()
        or text.startswith("~")
        or _is_windows_absolute_path((raw or "").strip())
    ):
        return Path(text).expanduser().resolve()

    stripped = text.strip("/")
    parts = PurePosixPath(stripped).parts
    if parts and parts[0] == vault_path.name:
        stripped = "/".join(parts[1:])
    return (vault_path / stripped).resolve() if stripped else vault_path.resolve()
