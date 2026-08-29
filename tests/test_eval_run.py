"""The harness end to end, on scripted responses. No network.

The exit criterion is that `--target baseline` and `--target agent` produce results on
the same cases. That is checkable offline, and it should be: discovering the two arms
disagree about what they were asked, after an hour of live matches, would be a bad way
to find out.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from _providers import ScriptedProvider, decision_response, structured_response

from automa.contracts import BaselineMove
from automa.eval import run as run_module
from automa.eval.cases import BATTERY
from automa.eval.report import render

MODEL = "anthropic:claude-haiku-4-5"


@pytest.fixture(autouse=True)
def fixed_models(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTOMA_MODEL", MODEL)


def a_move(action: dict) -> object:
    return structured_response(
        BaselineMove(action_json=json.dumps(action), justification="scripted")
    )


def run(target: str, tmp_path: Path, argv: list[str]) -> dict:
    out = tmp_path / f"{target}.json"
    run_module.main(
        ["--target", target, "--json", str(out), "--trajectory-dir", str(tmp_path), *argv]
    )
    return json.loads(out.read_text(encoding="utf-8"))


# -- the exit criterion ----------------------------------------------------


def test_both_targets_answer_the_same_cases(install_provider, tmp_path, capsys) -> None:
    """The heart of the exit criterion, checked without spending an hour to find out."""
    # Scoped to Nim: the DNUP brief is a generated artifact (see
    # `scripts/build_rules_brief.py`), and the suite must pass before it exists.
    install_provider(ScriptedProvider(*[a_move({"heap": 0, "count": 1})] * 400))
    baseline = run("baseline", tmp_path, ["--cases-only", "--game", "nim"])

    install_provider(ScriptedProvider(*[decision_response(0)] * 400))
    agent = run("agent", tmp_path, ["--cases-only", "--game", "nim"])

    assert [c["case_id"] for c in baseline["case_results"]] == [
        c["case_id"] for c in agent["case_results"]
    ]
    assert baseline["case_results"], "the battery must not be empty"
    assert len(BATTERY) >= 12
    # Same shape, so the two files are directly comparable.
    assert baseline["summary"].keys() == agent["summary"].keys()
    assert baseline["summary"]["cases"].keys() == agent["summary"]["cases"].keys()


def test_the_agent_is_never_illegal_on_the_battery(install_provider, tmp_path, capsys) -> None:
    """Structural, not measured: an index into the legal list cannot be illegal."""
    install_provider(ScriptedProvider(*[decision_response(0)] * 400))
    agent = run("agent", tmp_path, ["--cases-only"])
    assert all(c["legal"] for c in agent["case_results"])
    assert agent["summary"]["cases"]["legal_rate"] == 1.0


def test_a_baseline_that_always_answers_illegally_scores_zero(
    install_provider, tmp_path, capsys
) -> None:
    """The metric has to be able to register failure, or it measures nothing."""
    install_provider(ScriptedProvider(*[a_move({"heap": 99, "count": 99})] * 400))
    baseline = run("baseline", tmp_path, ["--cases-only", "--game", "nim"])
    assert baseline["summary"]["cases"]["legal_rate"] == 0.0
    assert not any(c["legal"] for c in baseline["case_results"])


def test_quality_is_scored_only_where_an_oracle_exists(
    install_provider, tmp_path, capsys
) -> None:
    install_provider(ScriptedProvider(*[decision_response(0)] * 400))
    agent = run("agent", tmp_path, ["--cases-only"])

    nim = [c for c in agent["case_results"] if c["game"] == "nim"]
    dnup = [c for c in agent["case_results"] if c["game"] == "dnup"]
    assert all(c["optimal"] is not None for c in nim), "Nim is solved; score it"
    assert all(c["optimal"] is None for c in dnup), "DNUP is unsolved; inventing a score would lie"
    assert agent["summary"]["cases"]["quality_n"] == len(nim)


# -- matches ---------------------------------------------------------------


def test_matches_produce_the_primary_metric(install_provider, tmp_path, capsys) -> None:
    install_provider(ScriptedProvider(*[decision_response(0)] * 400))
    agent = run("agent", tmp_path, ["--matches-only", "--game", "nim",
                                    "--nim-matches", "2", "--workers", "1"])

    assert len(agent["match_results"]) == 2
    for match in agent["match_results"]:
        assert match["interventions"] == 0
        assert match["clean_match"]
        assert match["turns_without_intervention"] == match["turns"]
    assert agent["summary"]["matches"]["nim"]["turns_without_intervention_mean"] > 0


def test_an_illegal_baseline_lowers_the_primary_metric(
    install_provider, tmp_path, capsys
) -> None:
    """A baseline that never gets a move right must score worse than a clean one."""
    install_provider(ScriptedProvider(*[a_move({"heap": 99, "count": 99})] * 400))
    baseline = run("baseline", tmp_path, ["--matches-only", "--game", "nim",
                                          "--nim-matches", "2", "--workers", "1"])

    nim = baseline["summary"]["matches"]["nim"]
    assert nim["interventions_per_match"] > 0
    assert nim["clean_matches"] == 0
    assert all(m["outcome"] == "terminal" for m in baseline["match_results"]), (
        "an illegal proposal must not end the match - it is corrected and play continues"
    )


def test_matches_run_concurrently_without_losing_results(
    install_provider, tmp_path, capsys
) -> None:
    install_provider(ScriptedProvider(*[decision_response(0)] * 800))
    agent = run("agent", tmp_path, ["--matches-only", "--game", "nim",
                                    "--nim-matches", "4", "--workers", "4"])
    assert len(agent["match_results"]) == 4
    assert len({m["seed"] for m in agent["match_results"]}) == 4


# -- the report ------------------------------------------------------------


def test_the_report_renders_both_arms_and_labels_the_structural_row(
    install_provider, tmp_path, capsys
) -> None:
    install_provider(ScriptedProvider(*[a_move({"heap": 0, "count": 1})] * 400))
    baseline = run("baseline", tmp_path, ["--cases-only", "--game", "nim"])
    install_provider(ScriptedProvider(*[decision_response(0)] * 400))
    agent = run("agent", tmp_path, ["--cases-only", "--game", "nim"])

    text = render({"baseline": baseline, "agent": agent})
    assert "baseline" in text and "agent" in text
    assert "CASE BATTERY" in text
    assert "by category" in text


def test_a_missing_rules_brief_fails_loudly_and_says_how_to_make_one() -> None:
    """The baseline cannot play a game it has never been told the rules of.

    Falling back to some default prose would be far worse than refusing: the arm
    would still produce numbers, and they would measure nothing.
    """
    from automa.eval.baseline import MissingBriefError, rules_brief

    with pytest.raises(MissingBriefError, match="build_rules_brief"):
        rules_brief("no_such_game")
