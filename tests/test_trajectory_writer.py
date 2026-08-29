"""The trajectory format is a deliverable, so its guarantees are tested directly."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trajectory import SCHEMA_VERSION, TrajectoryWriter


def read_events(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_run_is_bracketed_by_start_and_end(tmp_path: Path) -> None:
    with TrajectoryWriter("test", directory=tmp_path, config={"k": "v"}) as traj:
        traj.emit("note", text="hello")
        path = traj.path

    events = read_events(path)
    assert events[0]["type"] == "run_start"
    assert events[0]["schema_version"] == SCHEMA_VERSION
    assert events[0]["config"] == {"k": "v"}
    assert events[-1]["type"] == "run_end"
    assert events[-1]["outcome"] == "ok"


def test_every_line_is_valid_json_with_strictly_increasing_seq(tmp_path: Path) -> None:
    with TrajectoryWriter("test", directory=tmp_path) as traj:
        for i in range(5):
            traj.emit("note", i=i)
        path = traj.path

    events = read_events(path)
    seqs = [e["seq"] for e in events]
    assert seqs == sorted(seqs)
    assert len(set(seqs)) == len(seqs)
    assert all("ts" in e and "type" in e for e in events)


def test_crash_still_leaves_a_complete_log(tmp_path: Path) -> None:
    """A match that dies mid-turn must not take its trajectory down with it."""
    path_holder = {}
    with pytest.raises(ValueError, match="boom"):
        with TrajectoryWriter("crash", directory=tmp_path) as traj:
            path_holder["path"] = traj.path
            traj.emit("note", text="before the crash")
            raise ValueError("boom")

    events = read_events(path_holder["path"])
    assert [e["type"] for e in events[-2:]] == ["error", "run_end"]
    assert events[-2]["exception"] == "ValueError"
    assert "boom" in events[-2]["message"]
    assert events[-1]["outcome"] == "error"


def test_usage_totals_separate_unpriced_calls_from_free_ones(tmp_path: Path) -> None:
    with TrajectoryWriter("totals", directory=tmp_path) as traj:
        traj.record_llm_usage(cost_usd=0.25, input_tokens=100, output_tokens=10)
        traj.record_llm_usage(cost_usd=None, input_tokens=200, output_tokens=20)
        path = traj.path

    end = read_events(path)[-1]
    assert end["llm_calls"] == 2
    assert end["total_cost_usd"] == 0.25
    # The unpriced call is counted, not folded into the total as zero.
    assert end["unpriced_calls"] == 1
    assert end["total_input_tokens"] == 300
    assert end["total_output_tokens"] == 30


def test_writing_outside_the_context_manager_is_an_error(tmp_path: Path) -> None:
    traj = TrajectoryWriter("unopened", directory=tmp_path)
    with pytest.raises(RuntimeError, match="outside its `with` block"):
        traj.emit("note", text="nope")
