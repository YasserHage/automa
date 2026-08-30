"""The catalog is consulted before the person is.

That single ordering is what makes ingestion incremental. The module asks about every
card it reveals, every match, forever - it has no memory between matches and should not
have one. What shrinks is the number of those questions that reach a human, and these
tests are what pin that, since it is the number Phase 5 reports.

Offline throughout: the cataloguer agent is replaced by a stub, because what is under
test here is the routing, not the structuring.
"""

from __future__ import annotations

import types

import pytest

from automa.agents.cataloguer import CataloguedCard
from automa.catalog import informant as inf
from automa.catalog.store import Catalog
from automa.core.game import UNAVAILABLE

TEMPURA = [{"op": "score_per_set", "size": 2, "points": 5}]


@pytest.fixture
def game() -> types.ModuleType:
    module = types.ModuleType("toy")
    module.NAME = "toy"
    module.SUPPORTED_OPS = {"score_per_set", "score_flat"}
    module.TERMS = {"count_field": {"icons"}, "flag": {"chopsticks"}}
    return module


def context(game):
    return types.SimpleNamespace(game=game)


def question(card_type: str = "tempura") -> dict:
    return {
        "kind": "card_effect",
        "card_type": card_type,
        "prompt": f"What does {card_type} do?",
    }


@pytest.fixture
def structured(monkeypatch):
    """Stand in for the cataloguer, recording what it was asked to structure."""
    calls: list[tuple[str, str]] = []

    def fake(card_type, description, **kwargs):
        calls.append((card_type, description))
        if "cannot" in description:
            return CataloguedCard(card_type, description, unresolved="does not fit")
        return CataloguedCard(card_type, description, effects=TEMPURA)

    monkeypatch.setattr(inf, "catalogue_card", fake)
    return calls


def test_a_known_card_is_answered_without_asking(game, structured):
    catalog = Catalog(game="toy")
    catalog.learn("tempura", effects=TEMPURA, description="two score five")

    asked = []
    informant = inf.catalog_informant(catalog, ask=lambda p: asked.append(p) or "x", save=False)
    answer = informant(context(game), question())

    assert answer == {"card_type": "tempura", "effects": TEMPURA}
    assert asked == [], "the person was disturbed about a card already described"
    assert structured == [], "and no model call was made either"


def test_a_new_card_is_put_to_the_person_and_remembered(game, structured):
    catalog = Catalog(game="toy")
    informant = inf.catalog_informant(catalog, ask=lambda p: "two of them score five", save=False)

    answer = informant(context(game), question())
    assert answer == {"card_type": "tempura", "effects": TEMPURA}
    assert structured == [("tempura", "two of them score five")]
    assert catalog.known() == {"tempura"}

    # The second time round it comes from the store.
    again = informant(context(game), question())
    assert again == {"card_type": "tempura", "effects": TEMPURA}
    assert len(structured) == 1, "the same card was structured twice"


def test_a_parked_card_is_never_asked_about_again(game, structured):
    catalog = Catalog(game="toy")
    catalog.park("chopsticks", "take two cards", "changes turn structure")

    asked = []
    informant = inf.catalog_informant(catalog, ask=lambda p: asked.append(p) or "x", save=False)
    answer = informant(context(game), question("chopsticks"))

    assert answer[UNAVAILABLE] is True
    assert asked == [], "a card that already defeated the vocabulary was re-asked"


def test_declining_to_answer_parks_the_card(game, structured):
    """'I cannot say' is an answer, and it has to stick or it arrives every turn."""
    catalog = Catalog(game="toy")
    informant = inf.catalog_informant(catalog, ask=lambda p: None, save=False)

    answer = informant(context(game), question())
    assert answer == {"card_type": "tempura", UNAVAILABLE: True}
    assert catalog.parked() == {"tempura"}
    assert structured == [], "no point structuring words nobody said"


def test_a_description_that_does_not_fit_is_parked_not_approximated(game, structured):
    catalog = Catalog(game="toy")
    informant = inf.catalog_informant(
        catalog, ask=lambda p: "cannot be expressed as scoring", save=False
    )

    answer = informant(context(game), question("chopsticks"))
    assert answer[UNAVAILABLE] is True
    assert catalog.parked() == {"chopsticks"}
    assert catalog.known() == set()


def test_the_module_declares_what_it_supports(game, structured, monkeypatch):
    """The caller must not restate it; a mismatch there is invisible at runtime."""
    seen = {}

    def fake(card_type, description, **kwargs):
        seen.update(kwargs)
        return CataloguedCard(card_type, description, effects=TEMPURA)

    monkeypatch.setattr(inf, "catalogue_card", fake)
    informant = inf.catalog_informant(Catalog(game="toy"), ask=lambda p: "words", save=False)
    informant(context(game), question())

    assert seen["supported"] == {"score_per_set", "score_flat"}
    assert seen["terms"] == {"count_field": {"icons"}, "flag": {"chopsticks"}}


def test_a_question_of_an_unknown_kind_is_declined_not_guessed(game, structured):
    catalog = Catalog(game="toy")
    informant = inf.catalog_informant(catalog, ask=lambda p: "six", save=False)

    answer = informant(context(game), {"kind": "die_roll", "prompt": "what did it show?"})
    assert answer[UNAVAILABLE] is True
    assert structured == [], "a card cataloguer must not answer a question about dice"


def test_an_unattended_run_answers_nothing_and_still_returns(game, structured):
    """`--auto` has nobody at the table; the match must still finish."""
    catalog = Catalog(game="toy")
    informant = inf.catalog_informant(catalog, ask=inf.never_asks, save=False)

    assert informant(context(game), question())[UNAVAILABLE] is True
    assert catalog.parked() == {"tempura"}


def test_what_was_learned_is_reported_to_the_caller(game, structured):
    events: list[tuple[str, bool]] = []
    catalog = Catalog(game="toy")
    informant = inf.catalog_informant(
        catalog,
        ask=lambda p: "two score five",
        save=False,
        on_learn=lambda c, stored: events.append((c, stored)),
    )
    informant(context(game), question())
    informant(context(game), question("chopsticks"))

    assert events == [("tempura", True), ("chopsticks", True)]


def test_the_cataloguers_call_lands_in_the_match_trajectory(game, tmp_path, monkeypatch):
    """Invariant 3: every prompt and response is logged, this one most of all.

    A card described at the table is the one moment a person's words enter the game, so
    it is the call the record can least afford to miss. It was missed: the play CLI built
    the informant before opening its trajectory, and `call_llm` logs nothing when handed
    `None` - silently, because a missing log has nothing to fail.
    """
    import json

    from _providers import ScriptedProvider, structured_response

    import automa.llm.call as call_module
    from automa.contracts import CardEffects, EffectOp
    from automa.trajectory import TrajectoryWriter

    provider = ScriptedProvider(
        structured_response(CardEffects(effects=[EffectOp(op="score_flat", points=2)]))
    )
    monkeypatch.setattr(call_module, "resolve", lambda _name: provider)

    with TrajectoryWriter("toy", directory=tmp_path) as traj:
        informant = inf.catalog_informant(
            Catalog(game="toy"), ask=lambda p: "worth two", trajectory=traj, save=False
        )
        informant(context(game), question("salmon_nigiri"))
        path = traj.path

    events = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x]
    calls = [e for e in events if e.get("type") == "llm_call"]
    assert calls, "the cataloguer's call was not recorded"
    assert calls[0]["agent"] == "cataloguer"
    assert "worth two" in str(calls[0]["messages"]), "the person's own words must be in the log"


def test_the_record_says_which_path_answered(game, structured, tmp_path):
    """Phase 5's headline number is a count of questions that reached a person.

    An answer read from the catalog and an answer typed by somebody arrive in identical
    shape, so `table_information` alone cannot tell them apart - and a metric that cannot
    be recovered from the record is one a reader has to take on trust.
    """
    import json

    from automa.trajectory import TrajectoryWriter

    catalog = Catalog(game="toy")
    catalog.learn("tempura", effects=TEMPURA, description="known already")

    with TrajectoryWriter("toy", directory=tmp_path) as traj:
        informant = inf.catalog_informant(
            catalog, ask=lambda p: "worth two", trajectory=traj, save=False
        )
        informant(context(game), question("tempura"))        # from the catalog
        informant(context(game), question("salmon_nigiri"))  # from the person
        path = traj.path

    events = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x]
    answers = {e["card_type"]: e for e in events if e.get("event") == "catalog_answer"}

    assert answers["tempura"]["source"] == "catalog"
    assert answers["tempura"]["asked_a_person"] is False
    assert answers["salmon_nigiri"]["source"] == "described"
    assert answers["salmon_nigiri"]["asked_a_person"] is True

    asked = sum(1 for e in answers.values() if e["asked_a_person"])
    assert asked == 1, "the metric must be recoverable by counting"


def test_a_parked_card_is_recorded_as_not_having_been_asked(game, structured, tmp_path):
    """Being skipped because it is parked is not the same as being put to somebody."""
    import json

    from automa.trajectory import TrajectoryWriter

    catalog = Catalog(game="toy")
    catalog.park("chopsticks", "take two", "turn structure")

    with TrajectoryWriter("toy", directory=tmp_path) as traj:
        informant = inf.catalog_informant(catalog, ask=lambda p: "x", trajectory=traj, save=False)
        informant(context(game), question("chopsticks"))
        path = traj.path

    events = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x]
    answer = next(e for e in events if e.get("event") == "catalog_answer")
    assert answer["source"] == "catalog_parked"
    assert answer["asked_a_person"] is False
