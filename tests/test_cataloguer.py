"""The Cataloguer, on scripted responses. Nothing here reaches a network.

This agent turns a sentence a person said at the table into executable data. The thing
worth testing is not that it works when the card fits the vocabulary - it is what happens
when the card *does not*, because that is where a model would be tempted to approximate.

Approximating is the failure that matters. A card recorded as doing the wrong thing plays
a silently incorrect game; a card recorded as not-yet-understood asks a person for help.
The second is always the right answer, and these tests hold the agent to it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from _providers import ScriptedProvider, structured_response

from automa.agents.cataloguer import catalogue_card
from automa.catalog import store
from automa.contracts import CardEffects, EffectOp
from automa.trajectory import TrajectoryWriter

MODEL = "anthropic:claude-haiku-4-5"


@pytest.fixture(autouse=True)
def fixed_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTOMA_MODEL_CATALOGUER", MODEL)
    monkeypatch.setenv("AUTOMA_MODEL", MODEL)


def structured(*ops: dict, cannot: str = "") -> object:
    return structured_response(
        CardEffects(effects=[EffectOp(**o) for o in ops], cannot_express=cannot)
    )


def events(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x]


# -- the ordinary path ----------------------------------------------------


def test_a_describable_card_becomes_executable_data(install_provider) -> None:
    install_provider(ScriptedProvider(structured({"op": "score_per_set", "size": 2, "points": 5})))

    card = catalogue_card("tempura", "you need two of them and they score five")

    assert card.ok
    assert card.effects == [{"op": "score_per_set", "size": 2, "points": 5}]
    assert not card.unresolved


def test_the_prompt_carries_the_vocabulary_and_the_description(install_provider) -> None:
    provider = install_provider(ScriptedProvider(structured({"op": "score_flat", "points": 3})))

    catalogue_card("pudding", "worth three points each")

    prompt = provider.requests[0].messages[0]["content"]
    assert "score_per_set" in prompt, "the vocabulary must be shown"
    assert "worth three points each" in prompt, "and what the person actually said"
    assert "complete" in prompt, "and that the list is closed"


def test_the_result_is_accepted_by_the_catalog(install_provider) -> None:
    """The two halves have to agree: what the agent emits must be storable."""
    install_provider(ScriptedProvider(structured({"op": "score_per_set", "size": 3, "points": 10})))

    card = catalogue_card("sashimi", "three of them for ten points")
    catalog = store.Catalog(game="toy")

    assert catalog.learn(card.card_type, card.effects, card.description) == []
    assert catalog.known() == {"sashimi"}


# -- invalid output is repaired, not stored -------------------------------


def test_operations_that_will_not_execute_are_retried_with_the_reason(install_provider) -> None:
    provider = install_provider(
        ScriptedProvider(
            structured({"op": "score_per_set", "size": 2}),  # missing `points`
            structured({"op": "score_per_set", "size": 2, "points": 5}),
        )
    )

    card = catalogue_card("tempura", "two for five")

    assert card.ok
    assert len(provider.requests) == 2, "a fixable mistake should be fed back and fixed"
    feedback = provider.requests[1].messages[2]["content"]
    assert "will not execute" in feedback
    assert "points" in feedback


def test_an_invented_operation_is_rejected_and_fed_back(install_provider) -> None:
    """The vocabulary is closed. A model must not widen it by writing a new op name."""
    provider = install_provider(
        ScriptedProvider(
            structured({"op": "summon_dragon", "points": 99}),
            structured({"op": "score_flat", "points": 3}),
        )
    )

    card = catalogue_card("pudding", "three points each")

    assert card.ok
    assert "unknown operation" in provider.requests[1].messages[2]["content"]


# -- the escape case ------------------------------------------------------


def test_a_card_that_does_not_fit_is_declined_not_approximated(install_provider) -> None:
    """Chopsticks is the real case: it changes how many cards you take on a turn, which
    no scoring operation can express."""
    install_provider(
        ScriptedProvider(structured(cannot="it changes how many cards you take, not scoring"))
    )

    card = catalogue_card("chopsticks", "lets you take two cards on a later turn")

    assert not card.ok
    assert "not scoring" in card.unresolved
    assert not card.effects


def test_declining_is_accepted_immediately_and_never_retried(install_provider) -> None:
    """Retrying a refusal would pressure the model into inventing something."""
    provider = install_provider(
        ScriptedProvider(structured(cannot="turn structure, not scoring"))
    )

    catalogue_card("chopsticks", "take two cards")

    assert len(provider.requests) == 1


def test_a_declined_card_is_parked_rather_than_treated_as_inert(install_provider) -> None:
    """Treating it as having no effect would silently play a different game."""
    install_provider(ScriptedProvider(structured(cannot="turn structure")))

    card = catalogue_card("chopsticks", "take two cards")
    catalog = store.Catalog(game="toy")
    catalog.park(card.card_type, card.description, card.unresolved)

    assert catalog.parked() == {"chopsticks"}
    assert catalog.known() == set()
    assert catalog.effects_for("chopsticks") == []


def test_exhausting_attempts_parks_the_card_instead_of_raising(install_provider) -> None:
    """A card that never comes back valid is a gap for a person, not a crash."""
    install_provider(ScriptedProvider(*[structured({"op": "nonsense"})] * 3))

    card = catalogue_card("mystery", "does something strange")

    assert not card.ok
    assert card.unresolved
    assert "could not be expressed" in card.unresolved


def test_an_unreachable_provider_parks_rather_than_guesses(install_provider) -> None:
    install_provider(ScriptedProvider(*[TimeoutError("503 unavailable")] * 3))

    card = catalogue_card("tempura", "two for five")

    assert not card.ok, "an outage must never produce a confident wrong answer"
    assert card.unresolved


# -- the record -----------------------------------------------------------


def test_every_attempt_is_on_the_trajectory(install_provider, tmp_path: Path) -> None:
    install_provider(
        ScriptedProvider(
            structured({"op": "score_per_set", "size": 2}),
            structured({"op": "score_per_set", "size": 2, "points": 5}),
        )
    )

    with TrajectoryWriter("cat", directory=tmp_path) as traj:
        catalogue_card("tempura", "two for five", game="toy", trajectory=traj)
        path = traj.path

    calls = [e for e in events(path) if e["type"] == "llm_call"]
    assert [e["outcome"] for e in calls] == ["validation_failed", "ok"]
    assert calls[0]["tags"]["card"] == "tempura"
