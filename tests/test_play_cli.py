"""The terminal interface: reading a human's turn without trusting the keyboard.

The CLI is the only place a person touches the system in Phase 2, so the input path
has to survive a typo, a blank line, a word where a number belongs, and someone
closing the terminal mid-match - none of which should corrupt a trajectory or crash
out of the writer's context manager.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from automa.core.game import AUTOMA, HUMAN
from automa.core.loader import load_game
from automa.core.match import MatchAbandoned, TurnContext, play_match, random_decider, viewer_for
from automa.scripts.play import announce, human_decider, paced
from automa.trajectory import TrajectoryWriter


def context(trajectory=None) -> TurnContext:
    game = load_game("nim")
    state = game.initial_state(1)
    return TurnContext(
        game=game,
        state=state,
        player=state["to_play"],
        viewer=viewer_for(state["to_play"]),
        actions=game.legal_actions(state),
        turn=0,
        ply=0,
        trajectory=trajectory,
    )


def typing(monkeypatch: pytest.MonkeyPatch, *lines: str) -> None:
    """Feed the given lines to `input()`, in order."""
    queue = list(lines)
    monkeypatch.setattr("builtins.input", lambda _prompt="": queue.pop(0))


def test_a_valid_number_is_taken_as_the_choice(monkeypatch, capsys) -> None:
    typing(monkeypatch, "2")
    assert human_decider()(context()).index == 2


@pytest.mark.parametrize("bad", ["", "  ", "abc", "99", "-1", "1.5"])
def test_unusable_input_reprompts_rather_than_crashing(monkeypatch, capsys, bad: str) -> None:
    typing(monkeypatch, bad, "0")
    assert human_decider()(context()).index == 0
    assert "Try again" in capsys.readouterr().out


@pytest.mark.parametrize("word", ["q", "Q", "quit", "exit"])
def test_quitting_ends_the_match_cleanly(monkeypatch, capsys, word: str) -> None:
    typing(monkeypatch, word)
    with pytest.raises(MatchAbandoned):
        human_decider()(context())


def test_a_closed_terminal_ends_the_match_cleanly(monkeypatch, capsys) -> None:
    """Ctrl-D or a closed pipe must not look like a crash."""

    def closed(_prompt: str = "") -> str:
        raise EOFError

    monkeypatch.setattr("builtins.input", closed)
    with pytest.raises(MatchAbandoned):
        human_decider()(context())


def test_the_human_turn_is_recorded_with_what_they_were_shown(
    monkeypatch, capsys, tmp_path: Path
) -> None:
    typing(monkeypatch, "1")
    with TrajectoryWriter("cli", directory=tmp_path) as traj:
        ctx = context(traj)
        human_decider()(ctx)
        path = traj.path

    events = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x]
    turn = next(e for e in events if e["type"] == "human_checkpoint")
    assert turn["chosen_index"] == 1
    assert turn["chosen"] == ctx.actions[1]["label"]
    assert turn["shown"] == ctx.view()


def test_the_human_sees_their_own_view_and_a_numbered_list(monkeypatch, capsys) -> None:
    typing(monkeypatch, "0")
    ctx = context()
    human_decider()(ctx)
    out = capsys.readouterr().out
    assert ctx.view() in out
    assert f"0. {ctx.actions[0]['label']}" in out


def test_announce_narrates_only_automas_moves(capsys) -> None:
    """The human just typed their move; repeating it back is noise."""
    from automa.core.match import Decision

    ctx = context()
    human_ctx = TurnContext(**{**ctx.__dict__, "player": HUMAN})
    announce(human_ctx, Decision(index=0, justification="mine"), ctx.actions[0])
    assert capsys.readouterr().out == ""

    automa_ctx = TurnContext(**{**ctx.__dict__, "player": AUTOMA})
    announce(automa_ctx, Decision(index=0, justification="keeps it even"), ctx.actions[0])
    out = capsys.readouterr().out
    assert ctx.actions[0]["label"] in out
    assert "keeps it even" in out


def test_pacing_wraps_a_decider_without_changing_its_answer(monkeypatch) -> None:
    slept: list[float] = []
    monkeypatch.setattr("automa.scripts.play.time.sleep", slept.append)

    assert paced(lambda ctx: 3, 0.0)(context()) == 3, "zero delay should not wrap at all"
    assert not slept

    assert paced(lambda ctx: 3, 1.5)(context()) == 3
    assert slept == [1.5]


def test_a_human_can_play_a_whole_match_from_piped_input(monkeypatch, capsys) -> None:
    """End to end through the real loop, with the keyboard scripted to always pick 0."""
    monkeypatch.setattr("builtins.input", lambda _prompt="": "0")
    game = load_game("nim")

    result = play_match(
        game,
        {AUTOMA: random_decider(0), HUMAN: human_decider()},
        seed=1,
        observer=announce,
    )
    assert result.outcome == "terminal"
    assert result.winner in game.PLAYERS
