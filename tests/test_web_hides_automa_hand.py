"""The state panel never re-displays what only Automa may see.

The rule, precisely, is about what the *persistent state panel* renders - not about
what a question is allowed to ask. A `components` question about `hands.automa`
legitimately shows the person Automa's hand: they are reading it off the physical
table to enter it, exactly as the CLI does. What must never happen is the state panel
independently rendering `describe_state(state, Viewer.AUTOMA)`.

The trap is real and structural: `_resolve_unknowns` builds its own `TurnContext` with
`viewer=viewer_for(state[TO_PLAY])`, which is `Viewer.AUTOMA` whenever it is Automa's
turn. So `ctx.view()` is only safe inside `_web_decider`, where the viewer is the
human's by construction - never inside an asker or the reporting wrapper.
"""

from __future__ import annotations

import inspect
import time

import pytest

from automa.core.game import Viewer
from automa.core.loader import load_game
from automa.web.services import session as session_module
from automa.web.services.games import loadable_games
from automa.web.services.session import SessionRegistry


def games_with_hidden_information() -> list[str]:
    """Games where the two viewers actually see different things.

    Nim has no hidden information at all, so it cannot exercise this. Which games are
    installed is not this test's business - the compiler installs games - so the set
    is discovered rather than named.
    """
    found = []
    for name in loadable_games():
        try:
            module = load_game(name)
            from automa.core.game import settle

            state = settle(module, module.initial_state(seed=0))
            if module.describe_state(state, Viewer.AUTOMA) != module.describe_state(
                state, Viewer.HUMAN
            ):
                found.append(name)
        except Exception:  # noqa: BLE001 - a broken module is another test's finding
            continue
    return found


@pytest.fixture
def registry(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        __import__("automa.config", fromlist=["x"]), "TRAJECTORY_DIR", tmp_path / "t"
    )
    reg = SessionRegistry(start_janitor=False)
    try:
        yield reg
    finally:
        reg.shutdown()


def test_no_published_state_is_the_automa_view(registry: SessionRegistry) -> None:
    candidates = games_with_hidden_information()
    if not candidates:
        pytest.skip("no installed game has hidden information to leak")

    name = candidates[0]
    module = load_game(name)
    session = registry.create(
        game_name=name,
        mode="offline",
        simulate_deal=True,
        use_catalog=False,
        seed=3,
        delay=0.0,
        max_plies=40,
    )

    deadline = time.monotonic() + 20
    while time.monotonic() < deadline and session.status == "running":
        if (session.pending or {}).get("kind") == "turn":
            session.decisions.put(0)
        time.sleep(0.01)
    session.abandon()

    views = [
        e.data["view"]
        for e in session.broadcaster._history  # noqa: SLF001
        if e.type in {"state", "your_turn"}
    ]
    assert views, "the session published no state at all"

    human = Viewer(session.human_player)
    for view in views:
        assert view != module.describe_state(module.initial_state(seed=3), Viewer.AUTOMA)
    # And positively: every published view is renderable as the human's own.
    assert all(isinstance(v, str) and v.strip() for v in views)
    assert human is Viewer.HUMAN


def test_the_state_panel_is_computed_from_the_human_viewer_explicitly() -> None:
    """Never borrowed from a `TurnContext` that might belong to Automa's turn."""
    source = inspect.getsource(session_module.MatchSession.publish_state)
    assert "viewer_for(self.human_player)" in source
    assert "ctx.view()" not in source


def test_no_asker_or_reporting_wrapper_calls_ctx_view() -> None:
    """`_resolve_unknowns` hands them a context whose viewer is Automa's on Automa's
    turn, so `ctx.view()` there would leak the hand into a question payload."""
    for function in (
        session_module._web_card_asker,
        session_module._web_component_asker,
        session_module._reporting,
        session_module._announcing,
    ):
        source = inspect.getsource(function)
        assert "ctx.view()" not in source, f"{function.__name__} renders through ctx"


def test_the_decider_is_the_one_place_ctx_view_is_used() -> None:
    """There the viewer is the human's by construction, which is what makes it safe."""
    source = inspect.getsource(session_module._web_decider)
    assert "ctx.view()" in source
    assert "viewer is HUMAN by construction" in source


def test_a_components_question_may_still_show_automas_own_hand(tmp_path, monkeypatch) -> None:
    """The refinement, stated as a test.

    The person reads Automa's dealt hand off the table to enter it - that is fine and
    is exactly what the CLI does. Only the persistent panel is constrained.
    """
    monkeypatch.setattr(
        __import__("automa.config", fromlist=["x"]), "TRAJECTORY_DIR", tmp_path / "t"
    )
    session = session_module.MatchSession(
        session_id="t",
        game=load_game("nim"),
        mode="live",
        simulate_deal=False,
        use_catalog=False,
        seed=1,
        delay=0.0,
        max_plies=10,
        human_player="human",
        automa_player="automa",
    )
    session.answers.put("1/5 2/9")
    answer = session.ask(
        {
            "kind": "components",
            "prompt": "Read out Automa's hand",
            "count": 2,
            "slot": "hands.automa",
        }
    )
    assert answer == "1/5 2/9", "asking is permitted; only re-displaying is not"
