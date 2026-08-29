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
CORE = REPO_ROOT / "src" / "automa" / "core"

#: Vendor SDKs, plus the project's own model-facing package. Matched against the
#: full dotted path, so `automa.llm` is caught without banning `automa` itself.
FORBIDDEN_PREFIXES = (
    "anthropic",
    "automa.llm",
    "openai",
    "google",
    "cohere",
    "mistralai",
    "ollama",
)


def _forbidden(dotted: str) -> bool:
    return any(dotted == p or dotted.startswith(p + ".") for p in FORBIDDEN_PREFIXES)


def _imported_paths(tree: ast.AST) -> set[str]:
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


def _core_modules() -> list[Path]:
    return sorted(CORE.rglob("*.py"))


def test_core_package_exists() -> None:
    assert CORE.is_dir(), "src/automa/core/ must exist - it is the deterministic zone"
    assert _core_modules(), "automa.core must contain at least __init__.py"


@pytest.mark.parametrize("module", _core_modules(), ids=lambda p: p.name)
def test_core_module_imports_no_model_client(module: Path) -> None:
    tree = ast.parse(module.read_text(encoding="utf-8"), filename=str(module))
    offenders = sorted(p for p in _imported_paths(tree) if _forbidden(p))
    assert not offenders, (
        f"{module.relative_to(REPO_ROOT)} imports {offenders}. "
        "The core is deterministic: it enumerates legal actions and applies "
        "transitions with no model in the loop (invariants 1 and 2 in CLAUDE.md)."
    )


def test_the_check_actually_catches_a_violation(tmp_path: Path) -> None:
    """Guard the guard - a detector that cannot fail is not protecting anything."""
    offender = tmp_path / "bad.py"
    offender.write_text("from automa.llm import call_llm\nimport anthropic\n")
    tree = ast.parse(offender.read_text())
    assert sorted(p for p in _imported_paths(tree) if _forbidden(p)) == [
        "anthropic",
        "automa.llm",
    ]
    # ...and does not fire on the package's own legitimate imports.
    clean = ast.parse("from automa.core.state import Match\nimport json\n")
    assert not [p for p in _imported_paths(clean) if _forbidden(p)]
