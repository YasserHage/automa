"""Installing a compiled module - the step a person gates, extracted from the CLI.

The logic used to live inside `compile_game.main()`, which meant only the CLI could
ever install. These tests pin the behaviour the extraction had to preserve exactly,
because `tests/test_compiler_loop.py` and the CLI's own output depend on it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from automa.compiler.install import (
    InstallRefused,
    install_compiled_game,
    plan_install,
)


def staged(tmp_path: Path, text: str = "NAME = 'toy'\n") -> Path:
    path = tmp_path / "toy_compiled.py"
    path.write_text(text, encoding="utf-8")
    return path


def test_a_fresh_install_writes_the_module(tmp_path: Path) -> None:
    staging = staged(tmp_path)
    target = tmp_path / "games" / "toy.py"

    plan = plan_install(staging, installed_name="toy", install_to=target)
    assert not plan.exists and not plan.differs

    written = install_compiled_game(plan)
    assert written == target
    assert target.read_text(encoding="utf-8") == staging.read_text(encoding="utf-8")


def test_an_identical_target_is_not_a_conflict(tmp_path: Path) -> None:
    """Recompiling to the same bytes must not ask a person to confirm anything."""
    staging = staged(tmp_path)
    target = tmp_path / "toy.py"
    target.write_text(staging.read_text(encoding="utf-8"), encoding="utf-8")

    plan = plan_install(staging, installed_name="toy", install_to=target)
    assert plan.exists and not plan.differs
    install_compiled_game(plan)  # no overwrite needed


def test_a_differing_target_is_refused_without_an_explicit_overwrite(tmp_path: Path) -> None:
    staging = staged(tmp_path, "NAME = 'new'\n")
    target = tmp_path / "toy.py"
    target.write_text("NAME = 'old'\n", encoding="utf-8")

    plan = plan_install(staging, installed_name="toy", install_to=target)
    assert plan.exists and plan.differs

    with pytest.raises(InstallRefused):
        install_compiled_game(plan)
    assert target.read_text(encoding="utf-8") == "NAME = 'old'\n", "nothing was written"

    install_compiled_game(plan, overwrite=True)
    assert target.read_text(encoding="utf-8") == "NAME = 'new'\n"


def test_the_installed_name_decides_the_filename(tmp_path: Path) -> None:
    """Loader name, module NAME and catalog filename are three names that must agree.

    The module's own reported `NAME` wins over whatever it was requested under, so
    `--game X` always loads a module calling itself X, reading `catalog/X.json`.
    """
    staging = staged(tmp_path)
    plan = plan_install(staging, installed_name="sushigo")
    assert plan.target.name == "sushigo.py"
    assert plan.installed_name == "sushigo"


def test_the_default_target_is_anchored_to_the_repo_not_the_cwd(tmp_path: Path) -> None:
    """A server or script started from elsewhere must not install into a stray tree."""
    from automa import config

    plan = plan_install(staged(tmp_path), installed_name="toy")
    assert plan.target.is_absolute()
    assert plan.target.parent == config.REPO_ROOT / "src" / "automa" / "games"


def test_the_view_note_lives_with_the_other_prompt_constants() -> None:
    """Two callers compile now - the CLI and the web - and one copy of this text.

    Two copies could drift, and a web-compiled module would then land on a different
    side of the hand-privacy decision from a CLI-compiled one: a locked decision
    reversed silently, by duplication.
    """
    from automa.compiler import generate
    from automa.scripts import compile_game

    assert compile_game.VIEW_NOTE is generate.VIEW_NOTE
    assert "belongs in the `automa` view and nowhere else" in generate.VIEW_NOTE
