"""Subprocess entry point: the only place generated code is executed.

    python -m automa.compiler._runner <module.py> [--matches N] [--compare-with dnup]

Imports a candidate module, runs every deterministic gate against it, and prints a JSON
verdict on the last line of stdout. Run as a child process with a timeout by
`automa.compiler.sandbox`, so a module that hangs, crashes or exhausts memory costs one
killed child rather than the session.

Nothing here reaches a model. Every gate is ordinary Python and finishes in seconds,
which is what lets a failed generation cost one cheap cycle instead of a long one.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import traceback
from pathlib import Path
from types import ModuleType


def load_from_path(path: Path) -> ModuleType:
    """Import a module file directly, without installing it into the package tree."""
    spec = importlib.util.spec_from_file_location(f"_candidate_{path.stem}", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot build an import spec for {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def verdict(ok: bool, stage: str, problems: list[str], **extra: object) -> int:
    print(json.dumps({"ok": ok, "stage": stage, "problems": problems, **extra}))
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("path", type=Path)
    parser.add_argument("--matches", type=int, default=200)
    args = parser.parse_args(argv)

    # -- gate 1: does it import at all?
    try:
        module = load_from_path(args.path)
    except Exception as exc:
        return verdict(
            False, "import",
            [f"{type(exc).__name__}: {exc}", traceback.format_exc()[-1500:]],
        )

    # -- gate 2: does it implement the contract?
    from automa.core.game import conforms

    problems = conforms(module)
    if problems:
        return verdict(False, "conforms", problems)

    # -- gate 3: is it sound? (Phase 1's harness, reused verbatim)
    from automa.core.selfplay import validate_module

    try:
        report = validate_module(module, matches=args.matches)
    except Exception as exc:
        return verdict(
            False, "selfplay",
            [f"the harness itself crashed: {type(exc).__name__}: {exc}"],
        )

    if not report.ok:
        # Every violation carries the seed that produced it, so the repair message is
        # reproducible rather than a vague complaint.
        problems = [str(v) for v in report.violations[:12]]
        if not problems:
            # A match can fail without breaking any single check: it simply never
            # ends. That produces an empty violation list, and handing the repair loop
            # "failed, no details" would be worse than useless - the model would have
            # nothing to act on. Say what actually happened.
            aborted = report.outcomes.get("aborted", 0)
            errored = report.outcomes.get("error", 0)
            if aborted:
                problems.append(
                    f"{aborted} of {args.matches} matches never reached a terminal "
                    f"state within {report.max_plies} actions. The game does not end: "
                    "check that some quantity strictly decreases, that a win condition "
                    "is reachable, and that no action can be repeated forever without "
                    "progress."
                )
            if errored:
                problems.append(f"{errored} of {args.matches} matches ended in an error.")
            if not problems:
                problems.append(f"self-play did not complete cleanly: {report.outcomes}")
        return verdict(
            False, "selfplay", problems,
            outcomes=report.outcomes,
            matches=args.matches,
        )

    # -- measurement, not a gate.
    #
    # The fingerprint is collected because it is cheap and a reviewer wants to see it,
    # but nothing here compares it to anything. A comparison needs a reference module,
    # and in the product there is none: the user submits rule material for a game
    # nobody has implemented, which is the entire point. Gating on a reference would
    # mean the compiler only works for games that already exist.
    from automa.compiler import fingerprint as fp

    return verdict(
        True, "passed", [],
        outcomes=report.outcomes,
        # What the module calls itself. The installed filename is taken from this so
        # that the loader name, NAME and the catalog filename cannot drift apart.
        name=module.NAME,
        # How many questions the module asked across validation. A catalog-driven game
        # reporting zero has not exercised the path it was told to implement, which a
        # reviewer should see even though it passes every gate.
        unknowns=report.unknowns,
        fingerprint=fp.collect(module, matches=min(args.matches, 40)).to_dict(),
        state_keys=fp.state_key_report(module),
    )


if __name__ == "__main__":
    sys.exit(main())
