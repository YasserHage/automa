"""Invariants 1 and 2, enforced structurally.

`plan.md` states that no LLM decides legality and no LLM writes state. Prose in a
README cannot be checked. This can: if any module under core/ ever imports a model
client, the build fails.

This test is the executable form of the project's central architectural claim.
Do not weaken it to make a change pass - if core/ needs a model, the claim has
changed and the plan needs revisiting first.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
CORE = REPO_ROOT / "core"

FORBIDDEN_ROOTS = {"anthropic", "llm", "openai", "google", "cohere", "mistralai", "ollama"}


def _imported_roots(tree: ast.AST) -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            # node.module is None for `from . import x`, which is always local.
            if node.level == 0 and node.module:
                roots.add(node.module.split(".")[0])
    return roots


def _core_modules() -> list[Path]:
    return sorted(CORE.rglob("*.py"))


def test_core_package_exists() -> None:
    assert CORE.is_dir(), "core/ must exist - it is the deterministic zone"
    assert _core_modules(), "core/ must contain at least __init__.py"


@pytest.mark.parametrize("module", _core_modules(), ids=lambda p: p.name)
def test_core_module_imports_no_model_client(module: Path) -> None:
    tree = ast.parse(module.read_text(encoding="utf-8"), filename=str(module))
    offenders = _imported_roots(tree) & FORBIDDEN_ROOTS
    assert not offenders, (
        f"{module.relative_to(REPO_ROOT)} imports {sorted(offenders)}. "
        "The core is deterministic: it enumerates legal actions and applies "
        "transitions with no model in the loop (invariants 1 and 2 in CLAUDE.md)."
    )
