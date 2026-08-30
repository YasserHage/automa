"""Installing a compiled module: the one step in the compiler pipeline a person gates.

Extracted from `scripts/compile_game.py`, where this used to be trapped inside
`main()` - which meant the CLI was the only caller that could ever install a compiled
module. The web UI's compile screen needs the identical logic behind its own approval
button, and importing a `main()` written for `argparse` and `input()` is not that.

Nothing here decides *whether* to install - that judgement belongs to whoever read
the plain-language summary, the assumptions and the open questions. This only knows
how to check whether the target exists and differs, and how to write the file.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from automa import config

#: Where a compiled module is installed, absent an explicit `install_to`. Anchored to
#: the repo root rather than the process's working directory, for the same reason
#: `catalog.store.CATALOG_DIR` being cwd-relative is a trap `web/paths.py` avoids.
GAMES_DIR = config.REPO_ROOT / "src" / "automa" / "games"


class InstallRefused(RuntimeError):
    """The target exists, differs, and no overwrite was authorised."""


@dataclass(frozen=True)
class InstallPlan:
    """What installing ``staging`` would do, computed before anything is written."""

    staging: Path
    target: Path
    installed_name: str
    exists: bool
    differs: bool


def plan_install(
    staging: Path, *, installed_name: str, install_to: Path | None = None
) -> InstallPlan:
    """Where a compiled module would land, and whether that would overwrite something.

    Named after what the module calls itself - ``result.report["name"]``, the
    module's own ``NAME`` - never the name it was requested under, so that
    ``--game X`` always loads a module whose ``NAME`` is ``X`` and whose catalog is
    ``catalog/X.json``. Three names that must agree, kept as one.
    """
    target = install_to or GAMES_DIR / f"{installed_name}.py"
    exists = target.exists()
    differs = exists and (
        target.read_text(encoding="utf-8") != staging.read_text(encoding="utf-8")
    )
    return InstallPlan(
        staging=staging, target=target, installed_name=installed_name, exists=exists,
        differs=differs,
    )


def install_compiled_game(plan: InstallPlan, *, overwrite: bool = False) -> Path:
    """Write the staged module to its target. Refuses a differing target unannounced.

    A target that exists and is byte-identical is written again anyway - a no-op in
    effect, and simpler than special-casing it - but a target that exists and
    *differs* is refused unless ``overwrite`` says the caller already asked.
    """
    if plan.exists and plan.differs and not overwrite:
        raise InstallRefused(
            f"{plan.target} already exists and differs from this build; "
            "pass overwrite=True to replace it"
        )
    plan.target.parent.mkdir(parents=True, exist_ok=True)
    plan.target.write_text(plan.staging.read_text(encoding="utf-8"), encoding="utf-8")
    return plan.target
