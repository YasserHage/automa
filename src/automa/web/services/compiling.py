"""Compiling rule material into a game module, as a background job with live progress.

Named `compiling`, not `compile`: `compile` is a builtin, and
`from automa.web.services import compile` reads badly and risks shadowing it.

Two things this module is careful about. **Progress** comes from subclassing
`TrajectoryWriter` rather than tailing its JSONL from outside - `compile_game` exposes
no callback, and tailing a file being written by another thread means partial-line
races, polling latency and a rendezvous problem before the file even exists. The
subclass publishes on `emit()` and changes nothing about what is written, which
invariant 3 depends on and `tests/test_web_streaming.py` pins.

**Nothing is installed without a person.** The job stops at `awaiting_approval` and
stays there; `approve()` is the only path to `install_compiled_game`, and it requires
an explicit acknowledgement the server checks rather than trusting the client. There
is deliberately no web equivalent of the CLI's `--yes`.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from automa import config
from automa.compiler.generate import VIEW_NOTE, catalog_requirements, compile_game
from automa.compiler.ingest import IngestError
from automa.compiler.install import InstallPlan, InstallRefused, install_compiled_game, plan_install
from automa.trajectory import TrajectoryWriter
from automa.web.paths import staging_path
from automa.web.streaming import Broadcaster


class BroadcastingTrajectory(TrajectoryWriter):
    """A `TrajectoryWriter` that also publishes every event it writes.

    Byte-identical file output: `emit` calls `super().emit` first and publishes
    afterwards, so the JSONL a compile leaves behind is exactly what a plain writer
    would have produced. That is invariant 3 untouched, and it is worth a test.
    """

    def __init__(self, *args: Any, broadcaster: Broadcaster, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._broadcaster = broadcaster

    def emit(self, event_type: str, **fields: Any) -> int:
        seq = super().emit(event_type, **fields)
        self._broadcaster.publish("trajectory", seq=seq, type=event_type, **fields)
        return seq


class JobNotFoundError(KeyError):
    """No compile job with this id."""


class JobStateError(RuntimeError):
    """The job is not in a state where this action makes sense."""


#: One compile at a time, process-wide. Each can cost real money and run for minutes,
#: and two at once would contend for the same rate-limited free tier and the same
#: staging directory.
_compile_slot = threading.Semaphore(1)


@dataclass
class CompileJob:
    """One compile, from upload through the approval gate to install."""

    id: str
    game: str
    material: Path
    cards: Path | None
    use_catalog: bool
    attempts: int
    matches: int
    broadcaster: Broadcaster = field(default_factory=Broadcaster)

    status: str = "queued"  # queued | running | awaiting_approval | installed | discarded | failed
    result: Any = None
    error: str | None = None
    plan: InstallPlan | None = None
    trajectory_path: Path | None = None
    llm_calls: int = 0
    cost_usd: float = 0.0
    thread: threading.Thread | None = None
    last_seen: float = field(default_factory=time.monotonic)

    def snapshot(self) -> dict[str, Any]:
        result = self.result
        output = getattr(result, "output", None)
        return {
            "id": self.id,
            "game": self.game,
            "status": self.status,
            "error": self.error,
            "use_catalog": self.use_catalog,
            "llm_calls": self.llm_calls,
            "cost_usd": self.cost_usd,
            "trajectory": str(self.trajectory_path) if self.trajectory_path else None,
            "attempts": [
                {
                    "number": a.number,
                    "stage": a.stage,
                    "ok": a.ok,
                    "problems": a.problems,
                    "source_chars": len(a.source),
                }
                for a in (getattr(result, "attempts", None) or [])
            ],
            "approval": (
                {
                    "plain_language_summary": output.plain_language_summary,
                    "assumptions": list(output.assumptions),
                    "open_questions": list(output.open_questions),
                    "module_source": getattr(result, "path", None)
                    and Path(result.path).read_text(encoding="utf-8"),
                    "state_keys": (getattr(result, "report", None) or {}).get("state_keys"),
                    "unknowns": (getattr(result, "report", None) or {}).get("unknowns"),
                    "installed_name": self.plan.installed_name if self.plan else None,
                    "target": str(self.plan.target) if self.plan else None,
                    "target_exists": self.plan.exists if self.plan else False,
                    "target_differs": self.plan.differs if self.plan else False,
                }
                if self.status == "awaiting_approval" and output is not None
                else None
            ),
        }


def _run(job: CompileJob) -> None:
    acquired = _compile_slot.acquire(timeout=1.0)
    if not acquired:
        job.status = "failed"
        job.error = "another compile is already running; only one runs at a time"
        job.broadcaster.publish("compile_failed", message=job.error)
        job.broadcaster.close()
        return
    try:
        extra = ""
        if job.cards is not None:
            extra = (
                "## Additional submitted material\n\n"
                "The rulebook does not list the cards; the user supplied them separately.\n\n"
                + job.cards.read_text(encoding="utf-8")
            )

        job.status = "running"
        staging = staging_path(job.game)
        with BroadcastingTrajectory(
            f"compile-{job.game}", config=config.describe(), broadcaster=job.broadcaster
        ) as traj:
            job.trajectory_path = traj.path
            job.broadcaster.publish(
                "compile_start",
                game=job.game,
                attempts=job.attempts,
                matches=job.matches,
                model=config.model_for("compiler"),
                trajectory=str(traj.path),
                note=(
                    "A compile runs for minutes, not seconds, and makes real API calls. "
                    "The running cost below is what it has spent so far."
                ),
            )
            try:
                result = compile_game(
                    job.game,
                    material=job.material,
                    extra_material=extra,
                    view_note=VIEW_NOTE,
                    out_path=staging,
                    trajectory=traj,
                    catalog_note=catalog_requirements() if job.use_catalog else "",
                    max_attempts=job.attempts,
                    matches=job.matches,
                )
            except IngestError as exc:
                job.status = "failed"
                job.error = str(exc)
                job.broadcaster.publish("compile_failed", message=str(exc))
                return
            job.llm_calls, job.cost_usd = traj.llm_calls, traj.total_cost_usd

        job.result = result
        if not result.ok:
            job.status = "failed"
            job.error = f"compilation failed after {len(result.attempts)} attempt(s)"
            job.broadcaster.publish(
                "compile_failed", message=job.error, staging=str(staging)
            )
            return

        installed_name = result.report.get("name") or job.game
        job.plan = plan_install(staging, installed_name=installed_name)
        job.status = "awaiting_approval"
        job.broadcaster.publish(
            "compile_ready",
            installed_name=installed_name,
            target=str(job.plan.target),
            target_exists=job.plan.exists,
            target_differs=job.plan.differs,
            llm_calls=job.llm_calls,
            cost_usd=job.cost_usd,
            note="Nothing is written to src/automa/games/ until you approve it.",
        )
    except BaseException as exc:  # noqa: BLE001 - reported, never swallowed silently
        job.status = "failed"
        job.error = f"{type(exc).__name__}: {exc}"
        job.broadcaster.publish("error", kind=type(exc).__name__, message=str(exc))
    finally:
        _compile_slot.release()
        if job.status in {"failed"}:
            job.broadcaster.close()


class JobRegistry:
    """Every compile job this process has started. Constructed once by `create_app()`."""

    def __init__(self, *, max_jobs: int = 20) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, CompileJob] = {}
        self._max_jobs = max_jobs

    def start(
        self,
        *,
        game: str,
        material: Path,
        cards: Path | None,
        use_catalog: bool,
        attempts: int,
        matches: int,
    ) -> CompileJob:
        job = CompileJob(
            id=uuid.uuid4().hex[:12],
            game=game,
            material=material,
            cards=cards,
            use_catalog=use_catalog,
            attempts=attempts,
            matches=matches,
        )
        job.thread = threading.Thread(
            target=_run, args=(job,), daemon=True, name=f"automa-web-compile-{job.id}"
        )
        with self._lock:
            self._jobs[job.id] = job
            if len(self._jobs) > self._max_jobs:
                for jid, existing in sorted(self._jobs.items(), key=lambda kv: kv[1].last_seen):
                    if existing.status in {"installed", "discarded", "failed"}:
                        self._jobs.pop(jid, None)
                        break
        job.thread.start()
        return job

    def get(self, job_id: str) -> CompileJob:
        job = self._jobs.get(job_id)
        if job is None:
            raise JobNotFoundError(job_id)
        job.last_seen = time.monotonic()
        return job

    def approve(self, job_id: str, *, overwrite: bool = False) -> dict[str, Any]:
        """Install the staged module. The only path from a compile to `games/`."""
        job = self.get(job_id)
        if job.status != "awaiting_approval" or job.plan is None:
            raise JobStateError(f"job {job_id} is {job.status!r}, not awaiting approval")
        try:
            target = install_compiled_game(job.plan, overwrite=overwrite)
        except InstallRefused as exc:
            raise JobStateError(str(exc)) from exc
        job.status = "installed"
        job.broadcaster.publish(
            "install_done", target=str(target), installed_name=job.plan.installed_name
        )
        job.broadcaster.close()
        return {"installed_name": job.plan.installed_name, "target": str(target)}

    def reject(self, job_id: str) -> dict[str, Any]:
        """Leave the staged module on disk, marked discarded. Nothing is installed."""
        job = self.get(job_id)
        if job.status != "awaiting_approval":
            raise JobStateError(f"job {job_id} is {job.status!r}, not awaiting approval")
        job.status = "discarded"
        staging = job.plan.staging if job.plan else None
        job.broadcaster.publish("compile_failed", message="discarded by the reviewer")
        job.broadcaster.close()
        return {"discarded": True, "staging": str(staging) if staging else None}

    def shutdown(self) -> None:
        with self._lock:
            jobs = list(self._jobs.values())
        for job in jobs:
            job.broadcaster.close()
            if job.thread is not None:
                job.thread.join(timeout=2.0)
