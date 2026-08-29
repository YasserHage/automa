"""JSONL trajectory writer - one file per run."""

from __future__ import annotations

import json
import subprocess
import sys
import time
import traceback
import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Any

from trajectory.events import SCHEMA_VERSION, EventType


def _git_commit() -> dict[str, Any]:
    """Identify the code that produced this run, so a trajectory ties back to a commit.

    Returns nulls rather than raising when there is no repository or no commit yet -
    a missing commit must not stop a match from being logged.
    """
    def _run(*args: str) -> str | None:
        try:
            out = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=5,
                cwd=Path(__file__).resolve().parent.parent,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return out.stdout.strip() if out.returncode == 0 else None

    commit = _run("git", "rev-parse", "HEAD")
    status = _run("git", "status", "--porcelain")
    return {"git_commit": commit, "git_dirty": bool(status) if status is not None else None}


def _utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


class TrajectoryWriter:
    """Append-only JSONL log for a single run.

    Used as a context manager. Every event is flushed immediately: a match that
    crashes mid-turn must still leave a usable log behind, which is the whole
    point of writing the spine before the game logic.

        with TrajectoryWriter(label="hello") as traj:
            traj.emit("note", text="starting")
    """

    def __init__(
        self,
        label: str,
        *,
        directory: Path | str | None = None,
        config: dict[str, Any] | None = None,
        seeds: dict[str, Any] | None = None,
        run_id: str | None = None,
    ) -> None:
        from config import TRAJECTORY_DIR  # imported late so tests can redirect it

        self.label = label
        self.run_id = run_id or uuid.uuid4().hex[:12]
        self.directory = Path(directory) if directory is not None else TRAJECTORY_DIR
        self._config = config or {}
        self._seeds = seeds or {}

        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.path = self.directory / f"{stamp}-{label}-{self.run_id}.jsonl"

        self._fh = None
        self._seq = 0
        self._started: float | None = None

        # Running totals, reported in run_end.
        self.total_cost_usd = 0.0
        self.unpriced_calls = 0
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.llm_calls = 0

    # -- lifecycle ---------------------------------------------------------

    def __enter__(self) -> TrajectoryWriter:
        self.directory.mkdir(parents=True, exist_ok=True)
        self._fh = self.path.open("w", encoding="utf-8")
        self._started = time.monotonic()
        self.emit(
            "run_start",
            schema_version=SCHEMA_VERSION,
            run_id=self.run_id,
            label=self.label,
            config=self._config,
            seeds=self._seeds,
            python=sys.version.split()[0],
            **_git_commit(),
        )
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool:
        if exc is not None:
            self.emit(
                "error",
                exception=type(exc).__name__,
                message=str(exc),
                traceback="".join(traceback.format_exception(exc_type, exc, tb)),
            )
        self.emit(
            "run_end",
            outcome="error" if exc is not None else "ok",
            wall_ms=round((time.monotonic() - (self._started or time.monotonic())) * 1000, 1),
            llm_calls=self.llm_calls,
            total_cost_usd=round(self.total_cost_usd, 6),
            unpriced_calls=self.unpriced_calls,
            total_input_tokens=self.total_input_tokens,
            total_output_tokens=self.total_output_tokens,
        )
        if self._fh is not None:
            self._fh.close()
            self._fh = None
        return False  # never swallow the exception

    # -- writing -----------------------------------------------------------

    def emit(self, event_type: EventType, **fields: Any) -> int:
        """Write one event. Returns its sequence number."""
        if self._fh is None:
            raise RuntimeError("TrajectoryWriter used outside its `with` block")
        self._seq += 1
        record = {"seq": self._seq, "ts": _utcnow(), "type": event_type, **fields}
        # default=str so an unexpected object degrades to a readable string instead
        # of losing the whole event.
        self._fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        self._fh.flush()
        return self._seq

    def record_llm_usage(
        self,
        *,
        cost_usd: float | None,
        input_tokens: int,
        output_tokens: int,
    ) -> None:
        """Fold one attempt's usage into the run totals."""
        self.llm_calls += 1
        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens
        if cost_usd is None:
            # Never let an unknown price masquerade as free.
            self.unpriced_calls += 1
        else:
            self.total_cost_usd += cost_usd
