#!/usr/bin/env python3
"""Regression guard for the tools/utils facade split done this session.

Ten names silently stopped being importable through their pre-split module
path once obsidian_mcp/tools/link_management.py and note_management.py were
split into sibling modules: any caller still doing e.g.
`from obsidian_mcp.tools.link_management import find_notes_by_names` would
have started raising ImportError with no other signal (the module still
imports fine, it just no longer has that attribute). That was found and
fixed by hand this session -- each facade now declares an explicit
`__all__` naming everything it re-exports. This test makes sure that
regression, and anything shaped like it in the other three facades that
went through the same kind of split, cannot recur silently.

Method: for each facade module, walk the *old* monolithic source (the git
blob at BASE_SHA, before the split) and collect every name it bound at
module level -- functions, classes, and plain top-level constants (not
names it merely imported from elsewhere; importing `typing.Optional`
doesn't mean a module "defines" Optional). A public (non-underscore)
function or class from that old source must still be an attribute of the
current module, full stop -- that is the entire point of a facade. A
private helper (leading underscore) or a plain constant is only part of
the contract if the *current* module's own `__all__` says so -- that list
is what each split explicitly committed to re-exporting, and is a more
reliable signal than "no leading underscore" alone (a stray
`logger = logging.getLogger(__name__)` is non-underscore too, but was
never meant to be imported cross-module, and indeed isn't in any of the
five current `__all__` lists).
"""

from __future__ import annotations

import ast
import importlib
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
BASE_SHA = "aeb0f1d9dfd9078405485643ef8a4104f8970e3e"

FACADE_MODULES = [
    "obsidian_mcp.tools.link_management",
    "obsidian_mcp.tools.note_management",
    "obsidian_mcp.tools.organization",
    "obsidian_mcp.tools.search_discovery",
    "obsidian_mcp.utils.vault_config",
]


def _old_source(module_path: str) -> str:
    """Return this module's source as it was at BASE_SHA, pre-split."""
    relpath = module_path.replace(".", "/") + ".py"
    result = subprocess.run(
        ["git", "show", f"{BASE_SHA}:{relpath}"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def _names_defined_at_module_level(source: str) -> tuple[set[str], set[str]]:
    """Split a module's own top-level bindings into (functions/classes,
    plain constants), ignoring `__all__` itself.
    """
    tree = ast.parse(source)
    callables_or_classes: set[str] = set()
    constants: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            callables_or_classes.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id != "__all__":
                    constants.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            constants.add(node.target.id)
    return callables_or_classes, constants


def _names_required_to_still_import(
    module_path: str, current_module: object
) -> set[str]:
    """The regression contract for one facade (see module docstring)."""
    callables_or_classes, constants = _names_defined_at_module_level(
        _old_source(module_path)
    )
    current_all = set(getattr(current_module, "__all__", None) or ())

    required = {
        name
        for name in callables_or_classes
        if not name.startswith("_") or name in current_all
    }
    required |= {name for name in constants if name in current_all}
    return required


@pytest.mark.parametrize("module_path", FACADE_MODULES)
def test_facade_still_exports_every_pre_split_name(module_path: str) -> None:
    """Every function/class a facade defined before the split, plus every
    name its own __all__ still claims, must resolve as an attribute of the
    current module.
    """
    current_module = importlib.import_module(module_path)
    required_names = _names_required_to_still_import(module_path, current_module)

    # A facade with nothing to check would make this test vacuously green
    # and hide a real regression -- guard the guard.
    assert required_names, (
        f"{module_path}: extracted zero names to verify from {BASE_SHA[:12]}"
    )

    missing = sorted(
        name for name in required_names if not hasattr(current_module, name)
    )
    assert not missing, (
        f"{module_path} no longer re-exports: {missing}. These names were defined "
        f"directly in this module at {BASE_SHA[:12]} (pre-split) or are listed in "
        f"the module's current __all__, and existing callers may still import them "
        f"from this exact path."
    )
