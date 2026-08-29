"""The agent contracts.

These are cheap tests, but they pin down the property that matters: the shape of
what a model is allowed to say. If a future change widens one of these types, that
is a change to how much influence a model has over the game, and it should have to
break a test to happen.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from automa.contracts import CompilerOutput, Priority, StrategistPolicy, TacticianResponse


class TestTacticianResponse:
    def test_accepts_an_index_and_a_justification(self) -> None:
        r = TacticianResponse(action_index=2, justification="Denies them the blue set.")
        assert r.action_index == 2

    def test_rejects_a_negative_index(self) -> None:
        with pytest.raises(ValidationError):
            TacticianResponse(action_index=-1, justification="nope")

    def test_cannot_express_an_action_that_is_not_in_the_list(self) -> None:
        """Invariant 1, in the type system: there is no free-text action field."""
        assert set(TacticianResponse.model_fields) == {"action_index", "justification"}

    def test_requires_a_justification(self) -> None:
        with pytest.raises(ValidationError):
            TacticianResponse(action_index=0, justification="")


class TestStrategistPolicy:
    def test_accepts_weighted_priorities(self) -> None:
        policy = StrategistPolicy(
            summary="Deny resources over direct scoring.",
            priorities=[
                Priority(description="Deny blue resources", weight=0.7),
                Priority(description="Score when free", weight=0.3),
            ],
            rationale="They cannot win without blue, and we are ahead on tempo.",
        )
        assert len(policy.priorities) == 2

    def test_requires_at_least_one_priority(self) -> None:
        with pytest.raises(ValidationError):
            StrategistPolicy(summary="s", priorities=[], rationale="r")

    def test_weights_are_bounded(self) -> None:
        with pytest.raises(ValidationError):
            Priority(description="over-eager", weight=1.5)


class TestCompilerOutput:
    def test_summary_and_source_are_required(self) -> None:
        with pytest.raises(ValidationError):
            CompilerOutput(module_source="x")

    def test_uncertainty_lists_default_to_empty_but_exist(self) -> None:
        """The Compiler must always have somewhere to admit doubt."""
        out = CompilerOutput(module_source="def initial_state(): ...", plain_language_summary="s")
        assert out.assumptions == []
        assert out.open_questions == []

    def test_carries_assumptions_for_the_human_approval_step(self) -> None:
        out = CompilerOutput(
            module_source="...",
            plain_language_summary="A trick-taking game for two.",
            assumptions=["Ties go to the non-dealer; the manual does not say."],
            open_questions=["Can a player pass on the first trick?"],
        )
        assert out.assumptions and out.open_questions
