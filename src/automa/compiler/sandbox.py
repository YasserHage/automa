"""Running code a model wrote, without trusting it.

The Compiler produces Python that nobody has read yet, and the rest of the project then
imports it and calls it every turn. That is the sharpest edge in the whole system, so
this module draws two lines around it:

**Read it before running it.** The source is walked as an AST and checked against an
allowlist first. A module that imports `os`, calls `exec`, or reaches for a dunder never
gets executed at all - the cheapest way to survive hostile generated code is not to run
it. This is the same technique `tests/test_core_is_llm_free.py` uses to enforce
invariants 1 and 2, pointed at a different problem.

**Never in this process.** Everything that does run, runs in a subprocess with a
wall-clock timeout, so an infinite loop or a memory bomb costs one killed child rather
than the session. Nothing generated is ever imported into the parent.

Neither line makes this a security boundary. A determined attacker with control of the
model's output could still find something; the allowlist is drawn tightly enough that a
*careless* generation - which is the realistic failure - cannot do damage, and the
timeout bounds the rest. `plan.md` requires the subprocess and timeout explicitly.
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

#: Everything a game module could legitimately need, and nothing else. A game is pure
#: state manipulation: it shuffles, it combines, it counts. It never needs the
#: filesystem, the network, the process table, or the import system.
ALLOWED_IMPORTS: frozenset[str] = frozenset(
    {
        "random",
        "itertools",
        "dataclasses",
        "typing",
        "collections",
        "collections.abc",
        "math",
        "json",
        "copy",
        "enum",
        "functools",
        "operator",
        "string",
        # Modules read what a person typed at the table - "1/5", "salmon_nigiri" - and
        # matching those onto a component table is what `re` is for. Pure computation:
        # it reaches no file, socket or process, which is the line this list draws.
        "re",
        "__future__",
        "automa.core.game",
    }
)

#: Names that are never acceptable in generated source, whatever they are used for.
FORBIDDEN_NAMES: frozenset[str] = frozenset(
    {
        "exec",
        "eval",
        "compile",
        "__import__",
        "open",
        "input",
        "breakpoint",
        "globals",
        "locals",
        "vars",
        "getattr",
        "setattr",
        "delattr",
    }
)

#: Attribute access that reaches into the runtime rather than the game.
FORBIDDEN_ATTRS: frozenset[str] = frozenset(
    {
        "__globals__",
        "__builtins__",
        "__subclasses__",
        "__bases__",
        "__mro__",
        "__code__",
        "__class__",
        "__dict__",
        "__loader__",
        "__spec__",
    }
)

DEFAULT_TIMEOUT_S = 120


class SandboxError(RuntimeError):
    """The sandbox itself could not complete - not a verdict on the module."""


@dataclass
class ScanResult:
    """What the static scan found. Empty ``problems`` means it may be executed."""

    problems: list[str] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def scan_source(source: str, filename: str = "<generated>") -> ScanResult:
    """Check generated source against the allowlist. Never executes anything.

    Returns problems as readable strings rather than raising, because they are fed
    straight back to the model as repair instructions - and a model repairs better from
    "you imported os, which is not allowed; a game module needs only state manipulation"
    than from a traceback.
    """
    result = ScanResult()
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError as exc:
        result.problems.append(f"the source does not parse: {exc}")
        return result

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                result.imports.append(alias.name)
                if not _import_allowed(alias.name):
                    result.problems.append(
                        f"line {node.lineno}: imports {alias.name!r}, which is not "
                        f"allowed. A game module needs only state manipulation; "
                        f"permitted imports are {sorted(ALLOWED_IMPORTS)}."
                    )
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level:
                result.problems.append(
                    f"line {node.lineno}: relative import; a game module must be "
                    "self-contained and importable on its own."
                )
                continue
            result.imports.append(module)
            if not _import_allowed(module):
                result.problems.append(
                    f"line {node.lineno}: imports from {module!r}, which is not "
                    f"allowed. Permitted imports are {sorted(ALLOWED_IMPORTS)}."
                )
        elif isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
            result.problems.append(
                f"line {node.lineno}: uses {node.id!r}, which is not allowed in a "
                "game module."
            )
        elif isinstance(node, ast.Attribute) and node.attr in FORBIDDEN_ATTRS:
            result.problems.append(
                f"line {node.lineno}: accesses {node.attr!r}, which reaches into the "
                "runtime rather than the game."
            )

    return result


def _import_allowed(dotted: str) -> bool:
    """Allow an exact match or a submodule of something allowed."""
    return any(dotted == a or dotted.startswith(a + ".") for a in ALLOWED_IMPORTS)


def validate_in_subprocess(
    path: Path,
    *,
    matches: int = 200,
    timeout_s: int = DEFAULT_TIMEOUT_S,
) -> dict:
    """Import, conform-check, self-play and fingerprint a module - in a child process.

    Args:
        path: The module file to validate. Must already have passed :func:`scan_source`.
        matches: Random self-play matches. 200 is enough to surface a real defect in
            seconds; the full 500 is for the final gate.

    Returns the runner's JSON verdict. A timeout is reported as a failure of the module
    (it did not terminate) rather than raised, because "your module hangs" is exactly the
    feedback the repair loop needs.
    """
    command = [
        sys.executable,
        "-m",
        "automa.compiler._runner",
        str(path),
        "--matches",
        str(matches),
    ]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "stage": "timeout",
            "problems": [
                f"validation did not finish within {timeout_s}s. The module is most "
                "likely looping forever - check that every action makes progress and "
                "that the game always reaches a terminal state."
            ],
        }
    except OSError as exc:
        raise SandboxError(f"could not start the validation subprocess: {exc}") from exc

    for line in reversed(completed.stdout.splitlines()):
        if line.startswith("{"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue

    return {
        "ok": False,
        "stage": "crash",
        "problems": [
            f"validation crashed (exit {completed.returncode}) without a verdict.",
            (completed.stderr or completed.stdout or "").strip()[-2000:],
        ],
    }
