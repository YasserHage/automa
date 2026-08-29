"""Trajectory logging: JSONL, one file per run."""

from automa.trajectory.events import SCHEMA_VERSION
from automa.trajectory.writer import TrajectoryWriter

__all__ = ["SCHEMA_VERSION", "TrajectoryWriter"]
