# Phase 4 results — the rule compiler

Recorded 2026-08-29, immediately after the runs.

```bash
AUTOMA_MODEL_COMPILER=google:gemini-3.1-pro-preview \
python -m automa.scripts.compile_game --game dnup \
    --material files/dnup_rules.pdf \
    --cards src/automa/compiler/material/dnup_cards.md \
    --compare-with dnup
```

The rulebook is not in the repository (invariant 6; it is Asmodee's), so a judge cannot
re-run the compile. The compiled module is committed instead, and everything downstream
of it — validation, fingerprint, a live match — reproduces from that.

---

## Exit criterion

> *"The compiler reproduces, on its own, a module functionally equivalent to the one
> written by hand in Phase 1."* — `plan.md` Phase 4

**Met, with the reading of "equivalent" set out below.** The compiler produced a working
DNUP module from the scanned rulebook plus a card list, passing every gate, and a
Tactician has since played a complete match on it.

| requirement | status |
|---|---|
| ingestion: PDF, pasted text, card images | met — page images via `compiler/ingest.py`, card list as text |
| module conforming to the interface + plain-language summary | met — `contracts.CompilerOutput` |
| validation layer 1: random self-play | met — Phase 1's harness, reused unchanged |
| validation layer 2: structural invariants | met — `core.game.conforms` + the module's own `check_invariants` |
| validation layer 3: golden cases from the manual's examples | **not done** — see *Still open* |
| validation layer 4: summary presented for human approval | met — `automa-compile` stages to `build/` and installs only on a yes |
| generated module executed in a subprocess with a timeout | met — `compiler/sandbox.py`, plus a static scan before anything runs |

---

## What the compiler was given, and what it was not

**Given:** 8 rulebook page images, the 26-card list the user supplied, the `GameModule`
Protocol read out of `core/game.py` with `inspect.getsource`, and a list of hard
requirements (pure `apply`, JSON-round-trippable state, non-empty `legal_actions`, and so
on).

**Not given: the hand-written `dnup.py`, in any form.** This was checked rather than
assumed, across the full 48,181 characters sent to the model:

| looked for | found |
|---|---|
| its private functions (`_evict`, `_next_seat`, `_finish_round`, …) | none |
| its constants (`STAR_CARD_ID`, `ROUNDS_TO_WIN`, `_AREA_NAMES`) | none |
| its action vocabulary (`"kind": "play"`, `"take"`, `"add"`, `"rotate"`) | none |
| lines of `dnup.py` appearing verbatim | 6 of 182 — all six the `GameModule` Protocol signatures, i.e. the interface every module implements |

The behavioural evidence is stronger than the grep. The first run independently chose
`A`/`B`/`C`/`D` for its actions (the rulebook's own labels) with keys `type`/`card_ids`,
and a completely different approach to per-round shuffling — five pre-generated deals
carried in state, where `dnup.py` derives each round from `(seed, round)`. Those are not
the choices of something that had seen the reference.

The `--compare-with dnup` fingerprint runs **after** compilation as a report. No gate
consults it, because in the product there is no reference implementation to consult — a
gate needing one could only ever validate games that already existed.

---

## The two runs

| | run 1 | run 2 (after the harness fix) |
|---|---|---|
| attempts | 2 | **1** |
| model calls | 2 ok, 2 × 503 retried | 1 ok, 1 × 503 retried |
| tokens | 10,603 in / 56,755 out | 4,335 in / 28,635 out |
| cost | $0.70 | **$0.35** |
| generation latency | 269s, 180s | 331s |
| module size | 12,196 chars | 11,473 chars |

**Run 1, attempt 1** passed the static scan, imported and conformed, then self-play caught
a real defect with the seed and ply that reproduce it:

```
[seed 0 ply 130] to_play_is_a_known_player: '' is not one of ('automa', 'human')
```

Twelve of those, one per seed, all late in a match — it blanked `to_play` at the end of a
round instead of seating the next one. That exact text went back with the instruction to
*"reason about what rule produced that state rather than special-casing the symptom"*, and
attempt 2 passed, its own summary describing the fix as *"resolving the early termination
issue"*. The repair was targeted, not a re-roll.

Run 2 passed first time. `n=1` each, so that is a different roll of the dice rather than
evidence the harness fix made generation easier.

---

## A Tactician played the compiled module

The end-to-end link, and the thing Phase 4's scope originally cut for speed:

```
automa-play --game dnup_compiled --seed 1 --auto
-> automa wins after 79 turns, 40 tactician calls, $0.0096, 0 fallbacks
```

The whole chain now runs: scanned rulebook -> model-written module -> static scan ->
sandboxed self-play -> deterministic core -> Tactician choosing an index every turn. Its
reasoning is indistinguishable from play on the hand-written game ("Play a set of value 2
into Area 2 to start establishing control there"), which is the point - the Tactician
never knows or cares which module it is playing.

One wrinkle worth recording: the compiled module sets `NAME = "dnup"`, the same as the
hand-written one, even though its file is `dnup_compiled.py`. The loader keys on the
filename so nothing breaks, but trajectories and fingerprint tables both label it `dnup`,
which is confusing when the two sit side by side. The compiler was never told the name had
to be unique.

---

## The finding that matters: the compiler found a hole in my validation

Run 1's module passed **200 matches of random self-play with zero violations** — and
crashed with `AttributeError: type object 'Viewer' has no attribute 'human'` the moment
anyone looked at the board. It had written `Viewer.human` for `Viewer.HUMAN`.

The contract has six methods. The Phase 1 harness exercised five. **`describe_state` — the
one the entire human interface rests on — was never called by anything.** Three phases of
hand-written games never exposed it, because both of mine happened to render fine. It took
a module written by something that had never seen my conventions to find it.

Fixed: the harness now renders every viewer at the opening position and every 25 plies,
with four regression tests. One of those tests documents the trade-off rather than hiding
it — periodic sampling catches breakage that appears as the board fills, but a fault
confined to the plies between samples can still slip past, which is why `test_views.py`
exercises rendering directly as well.

This is the strongest argument in the project so far for compiling a module rather than
hand-writing one: **the generated module was a better test of the validator than either
hand-written game.**

---

## What the compiler understood

Its own summary of run 2, which is what a person reads at the approval gate, correctly
describes the two-play-area turn structure, the opening single-turn exception for the star
card holder, the automatic discard at the start of an area's turn, all four actions, the
displacement rule in both its forms, rotation on any move to hand, and best-of-three.

Two of its stated **assumptions** are genuine rules questions I had to resolve myself when
hand-writing the module:

- *"when placing a new set into an area, that area is always empty and cannot conflict
  with itself"* — correct, and a consequence of the discard step that is not obvious.
- *"playing your last card but immediately receiving a returned set means you do not win
  the round"* — a real edge case in the interaction between winning and displacement.

Its one **open question** — whether shuffling should randomise card orientation — is
exactly the assumption flagged in `dnup.py`'s own docstring as unstated in the rulebook.
It found the same gap independently.

---

## Where the compiled module differs, and where it is wrong

**Different but legitimate.** Run 2 converged on `play`/`take`/`add`/`rotate` and `cards`
without having seen them. It carries `future_rounds` in state — pre-generated deals — to
keep `apply` pure, where `dnup.py` derives each round from the seed. Both are valid
answers to the same constraint.

**Actually wrong, and not caught by any gate:** all three viewers get identical text, so
the human cannot see their own hand. The cause is visible in a lint warning -
`F401: automa.core.game.Viewer imported but unused` - the module imports the viewer enum
and then ignores the parameter entirely. Privacy fails in the *safe* direction — the human's
hand is correctly hidden from everyone and Automa's is correctly open — but a person
playing through the CLI would be shown `Hand: 13 card(s)` where their cards should be.
The generic view tests only assert that rendering works and does not leak the seed;
`test_views.py`'s asymmetric privacy properties are written against `dnup` specifically
and do not parametrise.

**The fingerprint is too strict.** On run 1 it reported `DIFFERENT GAME` for three
differences, and two were pure naming: `A/B/C/D` versus `add/play/rotate/take`, and
`type/card_ids` versus `kind/cards`. Same four actions, same structure. It compares
*labels* where it should compare *shape*, so it would reject almost any correct module
that chose different names. Only the component count (130 vs 26) was a real difference,
and that was a legitimate design choice.

---

## A note on linting generated code

`ruff` flags seven issues in the compiled module: import ordering, five long lines, and
one unused import. Generated modules are excluded from lint in `pyproject.toml`, because
holding a model's output to this project's house style would fail the build over
something that says nothing about whether the game is implemented correctly. They are
still held to the *contract*: `conforms`, self-play, the sandbox, and the same
parametrised contract and view tests the hand-written games face.

The exception proves the value of reading the warnings anyway. `F401: Viewer imported but
unused` was the tell for the view bug above - the module takes a `viewer` argument and
never consults it.

---

## Cost and time

$1.05 across both runs, 8 model calls (3 of them 503s that the retry budget absorbed),
about 25 minutes of wall clock. `gemini-3.1-pro-preview` at $2.00/$12.00 per 1M, priced
into `llm/pricing.py` before the first run so no figure logged as `unpriced_model`.

The model is heavily congested: a probe returned 503 on 4 consecutive attempts before
succeeding. The compile loop was changed to catch `LLMCallError` and treat an unreachable
provider as a failed attempt rather than losing the whole run — the same failure mode that
killed a DNUP match in Phase 2.

---

## Still open

- **Golden cases from the rulebook's worked examples** (validation layer 3) are not
  written. They can only be authored against the schema the compiler chose, and that
  schema only existed once the module did.
- **The fingerprint compares names, not structure**, and should be fixed before it is
  used to judge anything.
- **The human view bug** above — the compiled module should be repaired or recompiled.
- **No golden-case or view check runs inside the compile loop**, so both of the above
  passed every gate.
- Carried forward: no person has typed a move into a live match; the `effort="low"` A/B
  is unrun; the Nim decision-quality regression from Phase 3 is unexplained.
