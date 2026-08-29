"""Shared AST helpers for the tests that police what the core is allowed to import.

Two separate architectural claims are enforced by walking the same import graph -
"the core holds no model" (invariants 1 and 2) and "the core holds no game"
(invariant 4). They live in separate test modules so that a failure names the claim
that actually broke, and share the walker from here so the two cannot drift apart.

Not named ``test_*``, so pytest collects nothing from this file.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CORE = REPO_ROOT / "src" / "automa" / "core"


def core_modules() -> list[Path]:
    return sorted(CORE.rglob("*.py"))


def imported_paths(tree: ast.AST) -> set[str]:
    """Every module path this file imports, as written."""
    paths: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            paths.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            # node.level > 0 is a relative import, which cannot escape automa.core.
            if node.level == 0 and node.module:
                paths.add(node.module)
    return paths


def offenders(source: str, prefixes: tuple[str, ...], filename: str = "<test>") -> list[str]:
    """Imports in ``source`` matching any forbidden prefix, sorted."""
    tree = ast.parse(source, filename=filename)
    return sorted(
        path
        for path in imported_paths(tree)
        if any(path == p or path.startswith(p + ".") for p in prefixes)
    )
