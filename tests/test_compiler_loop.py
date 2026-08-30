"""The generate -> validate -> repair loop, on scripted responses. No network.

This loop is the phase's agentic core, and the thing worth testing is not that a good
module passes - it is that a bad one produces feedback specific enough to repair from.
`plan.md` concentrates error in compilation because compilation is auditable; that only
holds if the audit says *what* was wrong, with the seed that reproduces it.
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest
from _providers import ScriptedProvider, structured_response

from automa.compiler import generate
from automa.compiler.ingest import Material, PageImage
from automa.contracts import CompilerOutput
from automa.trajectory import TrajectoryWriter

MODEL = "anthropic:claude-haiku-4-5"
GOOD_SOURCE = Path("src/automa/games/nim.py").read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def fixed_model_and_material(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTOMA_MODEL_COMPILER", MODEL)
    # One tiny fake page. Ingestion has its own tests, and a real PDF would make these
    # slow for no extra coverage. Patched at `read_material` because that is the seam
    # the compiler actually calls - it decides text-or-images from the file itself.
    monkeypatch.setattr(
        generate.ingest,
        "read_material",
        lambda path: Material(
            source=str(path),
            pages=[PageImage(object_id=1, data=b"\xff\xd8fake", mime_type="image/jpeg")],
        ),
    )


def compiled(source: str, summary: str = "A game.", **kw) -> object:
    return structured_response(
        CompilerOutput(
            module_source=source,
            plain_language_summary=summary,
            assumptions=kw.get("assumptions", []),
            open_questions=kw.get("open_questions", []),
        )
    )


def run(tmp_path: Path, *responses, attempts: int = 3):
    with TrajectoryWriter("compile", directory=tmp_path) as traj:
        result = generate.compile_game(
            "toy",
            material=Path("unused.pdf"),
            extra_material="the cards are 1..5",
            out_path=tmp_path / "toy.py",
            trajectory=traj,
            max_attempts=attempts,
            matches=5,
            timeout_s=90,
        )
        path = traj.path
    events = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x]
    return result, events


def test_a_good_module_compiles_on_the_first_attempt(install_provider, tmp_path) -> None:
    install_provider(ScriptedProvider(compiled(GOOD_SOURCE)))

    result, events = run(tmp_path, attempts=3)

    assert result.ok
    assert len(result.attempts) == 1
    assert result.path.exists()
    assert result.report["fingerprint"]["action_kinds"] == []  # nim has no `kind` key
    assert any(e.get("event") == "rule_material_ingested" for e in events)


def test_a_forbidden_import_is_caught_before_anything_runs(install_provider, tmp_path) -> None:
    """The scan is the first gate precisely so this source never executes."""
    provider = install_provider(
        ScriptedProvider(compiled("import os\nNAME='x'\n"), compiled(GOOD_SOURCE))
    )

    result, _ = run(tmp_path, attempts=3)

    assert result.ok
    assert result.attempts[0].stage == "scan"
    assert "os" in result.attempts[0].problems[0]
    followup = provider.requests[1].messages[-1]["content"]
    assert "'scan' gate" in followup
    assert "state manipulation" in followup


def test_a_module_failing_selfplay_is_repaired_with_the_seed(install_provider, tmp_path) -> None:
    """The whole value of the loop: reproducible feedback, not 'self-play failed'."""
    mutating = textwrap.dedent(
        '''
        from automa.core.game import DEFAULT_PLAYERS
        NAME = "toy"
        PLAYERS = DEFAULT_PLAYERS

        def initial_state(seed=None):
            return {"to_play": "automa", "turn": 0, "ply": 0, "n": 3}

        def legal_actions(state):
            return [{"label": "tick", "step": 1}]

        def apply(state, action):
            state["n"] -= 1          # mutates its input - the classic defect
            return {**state, "turn": state["turn"] + 1, "ply": state["ply"] + 1}

        def is_terminal(state):
            return state["n"] <= 0

        def winner(state):
            if not is_terminal(state):
                raise ValueError("not terminal")
            return "automa"

        def describe_state(state, viewer):
            return f"n={state['n']}"
        '''
    )
    provider = install_provider(ScriptedProvider(compiled(mutating), compiled(GOOD_SOURCE)))

    result, _ = run(tmp_path, attempts=3)

    assert result.ok
    assert result.attempts[0].stage == "selfplay"
    problems = " ".join(result.attempts[0].problems)
    assert "apply_does_not_mutate_its_input" in problems
    assert "seed" in problems, "feedback must be reproducible"

    followup = provider.requests[1].messages[-1]["content"]
    assert "seed" in followup and "reproduce" in followup
    assert "whole file" in followup, "a diff would not be applicable"


def test_a_module_that_never_terminates_is_reported_as_such(install_provider, tmp_path) -> None:
    endless = textwrap.dedent(
        '''
        from automa.core.game import DEFAULT_PLAYERS
        NAME = "toy"
        PLAYERS = DEFAULT_PLAYERS
        def initial_state(seed=None):
            return {"to_play": "automa", "turn": 0, "ply": 0}
        def legal_actions(state):
            return [{"label": "wait"}]
        def apply(state, action):
            return {**state, "turn": state["turn"] + 1, "ply": state["ply"] + 1}
        def is_terminal(state):
            return False
        def winner(state):
            raise ValueError("not terminal")
        def describe_state(state, viewer):
            return "forever"
        '''
    )
    install_provider(ScriptedProvider(compiled(endless), compiled(GOOD_SOURCE)))

    result, _ = run(tmp_path, attempts=3)

    assert result.ok
    assert result.attempts[0].stage == "selfplay"
    assert any("terminal" in p or "abort" in p.lower() for p in result.attempts[0].problems)


def test_the_loop_gives_up_and_leaves_the_source_for_inspection(
    install_provider, tmp_path
) -> None:
    install_provider(ScriptedProvider(*[compiled("import os\nNAME='x'\n")] * 3))

    result, events = run(tmp_path, attempts=3)

    assert not result.ok
    assert len(result.attempts) == 3
    assert result.output is not None, "the last attempt is kept for a human to look at"
    assert sum(e.get("event") == "compile_attempt" for e in events) == 3


def test_fenced_source_is_accepted(install_provider, tmp_path) -> None:
    """Markdown fences are a formatting habit, not a compilation error."""
    install_provider(ScriptedProvider(compiled(f"```python\n{GOOD_SOURCE}\n```")))
    result, _ = run(tmp_path, attempts=2)
    assert result.ok


def test_every_attempt_is_on_the_record(install_provider, tmp_path) -> None:
    """The Compiler's trajectory deliverable: instructions -> failure -> repair -> result."""
    install_provider(ScriptedProvider(compiled("import socket\nNAME='x'\n"), compiled(GOOD_SOURCE)))

    result, events = run(tmp_path, attempts=3)

    attempts = [e for e in events if e.get("event") == "compile_attempt"]
    assert [a["attempt"] for a in attempts] == [1, 2]
    assert attempts[0]["ok"] is False and attempts[0]["stage"] == "scan"
    assert attempts[1]["ok"] is True
    calls = [e for e in events if e["type"] == "llm_call"]
    assert len(calls) == 2, "both generations are logged with their full prompts"
    assert result.ok


def test_the_prompt_carries_the_contract_and_the_submitted_material(
    install_provider, tmp_path
) -> None:
    provider = install_provider(ScriptedProvider(compiled(GOOD_SOURCE)))

    run(tmp_path, attempts=2)

    parts = provider.requests[0].messages[0]["content"]
    text = next(p["text"] for p in parts if p["type"] == "text")
    assert "initial_state" in text and "check_invariants" in text, "the interface"
    assert "the cards are 1..5" in text, "the submitted card list"
    assert "MUST NOT mutate" in text
    assert any(p["type"] == "image" for p in parts), "the rulebook pages"


def test_the_contract_shown_is_read_from_the_source_of_truth() -> None:
    """Restating the interface in the prompt would let it drift from `conforms()`,
    and the compiler would then fail for a reason unrelated to the rules."""
    text = generate.contract_text()
    for member in ("initial_state", "legal_actions", "apply", "is_terminal", "winner",
                   "describe_state", "check_invariants"):
        assert member in text
    assert "to_play" in text and "ply" in text


def test_no_gate_needs_a_reference_implementation(install_provider, tmp_path) -> None:
    """The product compiles games nobody has implemented, so no gate may need one.

    A fingerprint comparison against a hand-written module is a Phase 4 measuring
    instrument - there is a DNUP to compare against only by accident of this project's
    history. Gating on it would mean the compiler could only validate games that
    already existed, which is the opposite of the point.
    """
    import inspect

    # No way to pass a reference, so no gate can consult one.
    assert "compare_with" not in inspect.signature(generate.compile_game).parameters

    # And no repair path exists for a "behaves differently from the reference" failure,
    # because that failure cannot be raised.
    assert '"fingerprint":' not in inspect.getsource(generate._repair)


def test_a_module_is_accepted_without_anything_to_compare_it_to(
    install_provider, tmp_path
) -> None:
    """The realistic case: a brand-new game with no reference anywhere."""
    install_provider(ScriptedProvider(compiled(GOOD_SOURCE)))
    result, _ = run(tmp_path, attempts=2)
    assert result.ok
    assert "fingerprint" in result.report, "still measured, just not compared"
    assert "comparison" not in result.report, "nothing to compare against, and none needed"


def test_text_material_is_sent_as_text_not_images(install_provider, tmp_path, monkeypatch) -> None:
    """A rulebook with a text layer must not be sent as page images.

    The Sushi Go rulebook has both: 21KB of text and several hundred *card
    illustrations*. Sending it down the image path would deliver hundreds of thumbnails
    and no rules, and the compiler would produce a confidently wrong module rather than
    an error.
    """
    monkeypatch.setattr(
        generate.ingest,
        "read_material",
        lambda path: Material(source=str(path), text="RULES OF PLAY: take a card, pass the rest."),
    )
    provider = install_provider(ScriptedProvider(compiled(GOOD_SOURCE)))

    result, events = run(tmp_path, attempts=2)

    assert result.ok
    content = provider.requests[0].messages[0]["content"]
    assert isinstance(content, str), "text material must not be wrapped as image parts"
    assert "take a card, pass the rest" in content
    ingested = [e for e in events if e.get("event") == "rule_material_ingested"]
    assert ingested and ingested[0]["kind"] == "text"


def test_the_catalog_note_is_only_sent_when_asked_for(install_provider, tmp_path) -> None:
    """Most games know what their cards do at compile time; only some learn at runtime."""
    provider = install_provider(ScriptedProvider(compiled(GOOD_SOURCE)))
    run(tmp_path, attempts=2)
    parts = provider.requests[0].messages[0]["content"]
    text = next(p["text"] for p in parts if p["type"] == "text")
    assert "come from a catalog" not in text


def test_the_catalog_note_tells_the_compiler_what_it_must_implement() -> None:
    """Generated from the vocabulary, so instructions cannot drift from what validates."""
    note = generate.catalog_requirements()
    assert "Do NOT hardcode" in note
    assert "resolve_unknown" in note
    assert "SUPPORTED_OPS" in note
    assert 'state["unknown"]' in note
    # And it carries the real operation list, not a restatement of it.
    from automa.catalog.vocabulary import VOCABULARY
    for name in VOCABULARY:
        assert name in note


def test_the_catalog_note_can_be_narrowed_to_what_a_game_implements() -> None:
    note = generate.catalog_requirements({"score_per_set", "multiply_next"})
    assert "score_per_set" in note
    assert "discard_opponent" not in note



def test_the_contract_spells_out_the_viewer_member_names() -> None:
    """Regression from two compiles in a row.

    Both the DNUP and Sushi Go modules wrote `Viewer.public` and lost a repair round to
    it. The contract text showed the enum's *values* - `(public, automa, human)` - and
    never its member names, so lowercase was a reasonable inference from what it was
    given. That is a defect in the prompt, not in the model.
    """
    from automa.core.game import Viewer

    text = generate.contract_text()
    for member in Viewer:
        assert f"Viewer.{member.name}" in text, f"{member.name} is not spelled out"
    assert "UPPERCASE" in text


def test_a_truncated_response_is_a_failed_attempt_not_a_crash(install_provider, tmp_path) -> None:
    """Regression from the Sushi Go compile.

    A call can succeed and still parse to nothing - the usual cause is the answer
    running into `max_tokens` mid-JSON, so the provider returns text, the schema never
    validates, and `parsed` is None. The loop assumed a completed call always parsed,
    and died with an AttributeError three lines later, losing four attempts of work.
    """
    from automa.llm.providers.base import LLMResponse

    truncated = LLMResponse(
        text='{"module_source": "def initial_state(',  # cut off mid-JSON
        parsed=None,
        stop_reason="MAX_TOKENS",
        input_tokens=100,
        output_tokens=32768,
        raw_content=[],
    )
    provider = install_provider(ScriptedProvider(truncated, compiled(GOOD_SOURCE)))

    result, _ = run(tmp_path, attempts=3)

    assert result.ok, "the loop should recover, not crash"
    assert result.attempts[0].stage == "truncated"
    assert "cut off" in result.attempts[0].problems[0]
    followup = provider.requests[1].messages[-1]["content"]
    assert "keep it compact" in followup
