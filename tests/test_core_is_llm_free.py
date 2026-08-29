"""Invariants 1 and 2, enforced structurally.

`plan.md` states that no LLM decides legality and no LLM writes state. Prose in a
README cannot be checked. This can: if any module under core/ ever imports a model
client, the build fails.

This test is the executable form of the project's central architectural claim.
Do not weaken it to make a change pass - if core/ needs a model, the claim has
changed and the plan needs revisiting first.

Invariant 4 ("the core imports no game") is the same walk over the same files but a
different claim, so it lives in `test_core_is_game_agnostic.py`; both share the
walker in `_ast_imports.py`.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from _ast_imports import REPO_ROOT, core_modules, offenders

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


def test_core_package_exists() -> None:
    assert core_modules(), "automa.core must contain at least __init__.py"


@pytest.mark.parametrize("module", core_modules(), ids=lambda p: p.name)
def test_core_module_imports_no_model_client(module: Path) -> None:
    found = offenders(module.read_text(encoding="utf-8"), FORBIDDEN_PREFIXES, str(module))
    assert not found, (
        f"{module.relative_to(REPO_ROOT)} imports {found}. "
        "The core is deterministic: it enumerates legal actions and applies "
        "transitions with no model in the loop (invariants 1 and 2 in CLAUDE.md)."
    )


def test_the_check_actually_catches_a_violation() -> None:
    """Guard the guard - a detector that cannot fail is not protecting anything."""
    assert offenders(
        "from automa.llm import call_llm\nimport anthropic\n", FORBIDDEN_PREFIXES
    ) == ["anthropic", "automa.llm"]
    # ...and does not fire on the package's own legitimate imports.
    assert not offenders(
        "from automa.core.game import State\nimport json\n", FORBIDDEN_PREFIXES
    )
