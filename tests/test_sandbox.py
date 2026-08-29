"""The sandbox around generated code. Nothing here reaches a model.

Two properties matter, and the second is the one that would actually hurt if it broke:

* the static scan rejects source that must never be executed, and it runs *before*
  anything executes;
* a module that hangs, crashes or refuses to import costs one killed child process, not
  the session.

The scan is not a security boundary and this file does not pretend it is. It stops a
careless generation - which is the realistic failure - from touching the filesystem or
the import machinery, and the subprocess timeout bounds everything else.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from automa.compiler.sandbox import (
    ALLOWED_IMPORTS,
    scan_source,
    validate_in_subprocess,
)

GOOD = textwrap.dedent(
    """
    import random
    from itertools import combinations
    from automa.core.game import TO_PLAY, LABEL

    NAME = "toy"
    PLAYERS = ("automa", "human")
    """
)


def test_a_reasonable_module_passes() -> None:
    result = scan_source(GOOD)
    assert result.ok, result.problems
    assert "random" in result.imports


@pytest.mark.parametrize(
    "source",
    [
        "import os",
        "import sys",
        "import subprocess",
        "from os import path",
        "import requests",
        "from pathlib import Path",
        "import socket",
    ],
    ids=lambda s: s.replace(" ", "_"),
)
def test_imports_outside_the_allowlist_are_rejected(source: str) -> None:
    """A game module shuffles, combines and counts. It never needs the outside world."""
    result = scan_source(source)
    assert not result.ok
    assert "not allowed" in result.problems[0]


@pytest.mark.parametrize(
    "source",
    [
        "x = eval('1+1')",
        "exec('y = 2')",
        "m = __import__('os')",
        "f = open('/etc/passwd')",
        "c = compile('1', '<s>', 'eval')",
        "g = (lambda: 0).__globals__",
        "subs = int.__subclasses__()",
        "b = {}.__class__",
    ],
    ids=lambda s: s[:24].replace(" ", "_"),
)
def test_escape_hatches_are_rejected(source: str) -> None:
    result = scan_source(source)
    assert not result.ok, f"{source!r} should have been rejected"


def test_source_that_does_not_parse_is_reported_not_raised() -> None:
    """The message goes back to the model as a repair instruction, so it must be text."""
    result = scan_source("def broken(:\n    pass")
    assert not result.ok
    assert "does not parse" in result.problems[0]


def test_relative_imports_are_rejected() -> None:
    """A generated module has to stand alone; a relative import assumes a package."""
    result = scan_source("from . import helpers")
    assert not result.ok
    assert "self-contained" in result.problems[0]


def test_the_allowlist_covers_what_the_hand_written_games_actually_use() -> None:
    """If the real modules would not pass, the allowlist is wrong, not the modules."""
    for name in ("nim", "dnup"):
        source = Path(f"src/automa/games/{name}.py").read_text(encoding="utf-8")
        result = scan_source(source, filename=name)
        assert result.ok, f"{name}.py would be rejected by the scan: {result.problems}"


def test_allowlist_does_not_quietly_include_the_dangerous_ones() -> None:
    for name in ("os", "sys", "subprocess", "pathlib", "socket", "importlib"):
        assert name not in ALLOWED_IMPORTS


# -- the subprocess -------------------------------------------------------


def test_a_module_that_hangs_is_killed_and_reported(tmp_path: Path) -> None:
    """The failure mode a timeout exists for. Reported, not raised: the repair loop
    needs to tell the model its module does not terminate."""
    path = tmp_path / "hangs.py"
    path.write_text("while True:\n    pass\n", encoding="utf-8")

    verdict = validate_in_subprocess(path, matches=1, timeout_s=5)

    assert not verdict["ok"]
    assert verdict["stage"] == "timeout"
    assert "looping forever" in verdict["problems"][0]


def test_a_module_that_raises_on_import_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "boom.py"
    path.write_text("raise RuntimeError('boom')\n", encoding="utf-8")

    verdict = validate_in_subprocess(path, matches=1, timeout_s=60)

    assert not verdict["ok"]
    assert verdict["stage"] == "import"
    assert "boom" in verdict["problems"][0]


def test_a_module_missing_the_interface_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "empty.py"
    path.write_text("NAME = 'empty'\n", encoding="utf-8")

    verdict = validate_in_subprocess(path, matches=1, timeout_s=60)

    assert not verdict["ok"]
    assert verdict["stage"] == "conforms"
    assert any("legal_actions" in p for p in verdict["problems"])


def test_the_hand_written_module_passes_every_gate() -> None:
    """The control. If dnup.py could not pass, no generated module ever would."""
    verdict = validate_in_subprocess(
        Path("src/automa/games/dnup.py"), matches=20, timeout_s=180
    )
    assert verdict["ok"], verdict.get("problems")
    assert verdict["stage"] == "passed"
    assert verdict["fingerprint"]["component_count"] == 26


def test_no_gate_depends_on_a_reference_implementation() -> None:
    """Every gate must work for a game nobody has implemented.

    That is the product: rule material in, a module out, for a game with no existing
    implementation anywhere. A gate that compared the result to a reference could only
    ever validate games that already existed. The fingerprint is still *collected* -
    it is a measurement and needs no reference - but nothing here compares it.
    """
    import inspect

    from automa.compiler import sandbox

    assert "compare_with" not in inspect.signature(sandbox.validate_in_subprocess).parameters

    # The verdict carries a fingerprint (a measurement, no reference needed) and never
    # a comparison, so no verdict can ever depend on one.
    verdict = validate_in_subprocess(Path("src/automa/games/nim.py"), matches=5, timeout_s=120)
    assert verdict["ok"]
    assert "fingerprint" in verdict
    assert "comparison" not in verdict
