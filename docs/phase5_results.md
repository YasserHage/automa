# Phase 5 results — effect vocabulary and incremental card ingestion

Recorded 2026-08-30, immediately after the runs.

The game is **Sushi Go** (Gamewright). Per invariant 6 the rulebook lives in the
gitignored `files/` directory and no rule text, card text or scan is reproduced here or
anywhere in the repository. The compiled module and the catalog it produced *are*
committed, so everything downstream of the compile reproduces without the rulebook.

```bash
# 1. compile the module from the rule material              (~$1.04, 3 attempts)
AUTOMA_MODEL_COMPILER=google:gemini-3.1-pro-preview \
python -m automa.scripts.compile_game --game sushigo \
    --material files/SushiGo_rules.pdf \
    --cards src/automa/compiler/material/sushigo_cards.md \
    --catalog --matches 100

# 2. draft what the rulebook says each card does            (~$0.006, 1-2 calls)
python -m automa.scripts.build_catalog --game sushigo \
    --material files/SushiGo_rules.pdf \
    --cards src/automa/compiler/material/sushigo_cards.md

# 3. play; anything undrafted is asked once, at the table
python -m automa.scripts.play --game sushigo --catalog --seed 1
```

---

## Exit criterion

> *"A complete match describing only the cards that actually appeared, and a second match
> of the same game requiring fewer descriptions than the first."* — `plan.md` Phase 5

**Partially met.** The mechanism is built, measured and reproducible; the *complete live
match* is not yet on the record.

| requirement | status |
|---|---|
| effect vocabulary as executable data (invariant 5) | met — 12 typed operations, `catalog/vocabulary.py` |
| card catalog, persistent, separate from the module | met — `catalog/store.py`, `catalog/sushigo.json` |
| unknown-card flow through the reserved informant seam | met — `catalog/informant.py`, wired into `automa-play --catalog` |
| the escape case: a card the vocabulary cannot express | met — reached, though **not** by the card that was predicted |
| a second match asks fewer questions than the first | met, measured **12 → 0** |
| a *complete* match, played through to a terminal state | **not done** — every live match so far was abandoned part-way |

The four live matches played ran 2, 11, 4 and 4 turns and were all quit by hand; the
longest resolved 11 questions across 21 plies without error. A full Sushi Go match is 60
plies and around 30 sequential Tactician calls, so finishing one is a matter of sitting
through it rather than of anything unbuilt. Until that exists, the honest claim is that
the ingestion loop is demonstrated and counted, not that the phase's own sentence has
been satisfied.

---

## The measurement

The count that matters is **questions that reach a person**, and it is not the same as
the number of questions the module asks. The module has no memory between matches and
should not have one: it asks about every card it reveals, every match, forever. What
shrinks is how many of those questions the catalog can answer without disturbing anybody.

Measured deterministically, no model involved, on the compiled module through
`play_match` with the real informant:

| | questions the module asked | questions put to a person |
|---|---:|---:|
| first match, empty catalog | 12 | **12** |
| second match, same catalog | 12 | **0** |

One question per card *type*, not per card — 108 cards, 12 types, and a type is asked
about once however often it appears. Reproducible from a clean checkout by deleting
`catalog/sushigo.json` and playing twice.

Since drafting from the rulebook was added, an empty catalog is not the normal starting
point: the builder records what the material states, and only what it does not reach the
table. For Sushi Go that is **11 of 12 types drafted, 1 left to ask about**.

**The record now says which path answered.** `table_information` cannot distinguish an
answer read from the catalog from one somebody typed — they arrive in identical shape —
so a `catalog_answer` event carries `source` (`catalog`, `catalog_parked`, `described`,
`declined`, `parked_after_asking`) and `asked_a_person`. Added because the phase's
headline number was otherwise unrecoverable from the trajectory, which for a submitted
result is no better than not having measured it.

---

## Compiling Sushi Go: harder than DNUP, for a reason worth recording

DNUP compiled in one attempt. Sushi Go took **two runs and seven attempts**, $2.54 in
total, and every failure was informative.

| run | attempt | failed at | cause |
|---|---|---|---|
| 1 | 1 | selfplay | `Viewer.public` — the prompt showed the enum's *values* and left the member names to be guessed |
| 1 | 2 | selfplay | 100/100 matches never terminated |
| 1 | 3 | selfplay | `ply` went 0 → 0 on every apply |
| 1 | 4 | crash | response truncated at `max_tokens`; `parsed` was `None` and the loop raised `AttributeError`, losing all four attempts |
| 2 | 1–2 | selfplay | flagged all ten dealt cards unknown at once, exceeding the resolution bound |
| 2 | 3 | **passed** | |

Run 1's attempts 2 and 3 look like different bugs and are the same one, and it was
**mine, not the model's**. `random_playout` knew nothing about the `unknown` state key, so
a module that correctly declared missing information and stopped was structurally
unvalidatable: the harness called `legal_actions` on a state the module had said it could
not proceed from. The model escaped the trap the only way the contract left open — by
inventing a no-op action:

```python
def legal_actions(state):
    if "unknown" in state:
        return [{LABEL: "Wait for catalog resolution", "type": "wait"}]

def apply(state, action):
    if action.get("type") == "wait":
        return state          # ply never advances
```

That satisfies "never return an empty list off-terminal" and violates "ply strictly
increases". Two of four attempts were spent on a deadlock the harness caused.

The fix is that self-play now answers, exactly where `play_match` does, with the one
answer available for every question in every game: *the fact could not be supplied*
(`UNAVAILABLE`). It is not a degenerate stand-in — it is the case a module is most likely
to get wrong, because play must continue with a gap in what is known. A module that cannot
finish a match when nothing is ever described was never fit for the table.

Run 2's first two failures were then caught by a check that fix introduced, and the
model's repair is the best thing in this phase: told it had declared too many unknowns at
once, it changed *when* it asks — only when a card is played face-up, never for a card
sitting in a hand — and wrote that down as an assumption rather than hiding it. That is
incremental ingestion, arrived at by the compiler because the harness pushed back.

**Validation of the accepted module:** 300/300 matches terminal, zero violations, exactly
60 plies every match (2 players × 10 cards × 3 rounds), 3,567 questions asked and answered
`unavailable`. Every match a draw, correctly — with nothing described, nothing scores.

---

## The finding that matters: names that both sides must agree on

The vocabulary has four fields whose value is a **name** rather than a number —
`count_field`, `flag`, `resource`, `category`. Each names something the module looks for:
a quantity it totals, a marker its rules test, a group of cards it recognises, a resource
it tracks.

These are the only places where a catalog and a module can disagree while both remain
internally valid, and **the disagreement never raises**. The module looks for a name
nobody wrote, its rule quietly never fires, and the game plays on producing plausible
numbers. Two shipped before it was generalised.

**Maki, on `count_field`.** The catalog said `count_field: "maki_icons"`; the module tests
`== "icons"`. It fell through to counting *card copies*, and because the dedupe key is
`count_field or card`, three maki types became three separate majority contests instead of
one over the combined icon count. Maki can pay 18 a round instead of 6.

| | median winning score |
|---|---:|
| `count_field: "icons"` (correct) | 49 |
| `count_field` absent | 61 |

A tie at 6 icons each scored 15–6. Win rate stayed 50/50 throughout, because the error is
symmetric — nothing looks broken, the game simply plays inflated.

**Chopsticks, on `flag`.** Worse, because it was invisible. The compiler had implemented
the whole mechanic — `legal_actions` offers *"use chopsticks to play X and Y"*, `apply`
returns the card to hand — and gated it on the catalog raising a flag:

```python
def is_chopsticks(card, catalog):
    for eff in catalog['effects'].get(card, []):
        if eff.get('op') == 'set_flag' and eff.get('flag') == 'chopsticks':
            return True
```

The cataloguer recorded `set_flag(flag="extra_card_from_hand")`. Measured over 60 matches:
**0 chopsticks actions offered, against 1,435 with the flag named correctly.** A whole
mechanic switched off by a string.

The first of these was fixed by adding a `COUNT_FIELDS` declaration for one field. The
second arrived within the hour, on another. Patching one field and leaving three is what
produced the second instance, so the guard is now general: `NAMED_FIELDS` covers all four,
modules declare `TERMS = {"flag": {"chopsticks"}, "count_field": {"icons"}}`,
`unsupported()` checks every field, `Catalog.learn` refuses a mismatch, and both agents are
told the permitted names per field. Where a module declares nothing — every module compiled
before `TERMS` existed — the agents fall back on a convention: **name a card's marker after
the card type**, which is what the compiler does unprompted, so the two ends agree by
default rather than by luck.

---

## Chopsticks was not the escape case

`plan.md` predicted it would be: *"it does not change scoring, it changes how many cards
you take this turn, which is turn structure rather than a card effect. It will hit this
path, and that is a genuine result."*

That prediction was wrong, and interestingly so. The compiler split the card in two
without being asked: the **rule** went into the module as turn structure, and the
**fact that this card triggers it** stayed data, expressed as `set_flag`, which the
vocabulary has had all along for exactly this — *"a persistent marker on its owner, which
another card or rule can test."* The module even lists `set_flag` in its `SUPPORTED_OPS`.

Every piece was in place, and the cataloguer broke the chain anyway, because its system
prompt said its job was translating a description into **scoring** operations. A card with
no points on it read as inexpressible, so it was parked — and parked cards are never asked
about again, which made the mechanic permanently unreachable.

Both structuring agents are now told plainly that not every card scores, that a card
changing what its owner may *do* is a `set_flag` the compiled rules already test, and that
reaching for `cannot_express` because a card has no number on it leaves the module's own
rule switched off.

**The escape case was still reached**, twice, honestly: by a card whose description could
not be structured before that fix, and by declining to answer at all — which parks a card
with `nobody at the table could describe it`. What has *not* happened is a card that
genuinely defeats the vocabulary. The boundary is real but this game has not found it.

The general lesson, which will recur: **a card that looks inexpressible may be one the
compiler already implemented and is waiting to be switched on.** The catalog does not only
hold scores; it holds the facts the module's rules test.

---

## Absent is not parked

Two states look alike and must never be confused:

- **absent** — nobody has looked at this card yet, and the table will ask about it;
- **parked** — somebody looked and the vocabulary could not hold it; never asked again.

Parking is deliberately permanent, because asking a second time about a card that already
defeated the vocabulary wastes a person's attention. That makes a *wrong* park permanent
too, which is how chopsticks became unreachable.

Two consequences. The catalog builder leaves a card the material never mentions **absent**,
not parked — including one it claims to have described but returns no operations for.
And `Catalog.forget` plus `build_catalog --forget CARD` exist so a wrong answer can be
withdrawn, since nothing else could dislodge one short of editing the file by hand.

A malformed answer gets the same care: a model that wrote `score_per_set` with a set size
and no points has not discovered that tempura is inexpressible, it has left a field out.
Those cards are named and re-asked once, on their own; anything still malformed is left
absent for the table. An early version validated the whole draft at once, which meant one
bad card threw away the other eleven when retries ran out — worse than the bug it fixed.

---

## Costs

| step | calls | cost |
|---|---:|---:|
| compile, run 1 (failed, 4 attempts) | 5 | $1.51 |
| compile, run 2 (succeeded, 3 attempts) | 4 | $1.04 |
| catalog drafting, 5 runs including re-drafts | 8 | $0.026 |
| live matches (Tactician), 4 partial | 24 | $0.004 |

Compilation dominates by three orders of magnitude, and it happens once per game.
Drafting a whole rulebook's worth of card effects costs less than a cent — which is the
argument for doing it before the table rather than during it.

---

## Still open

**Maki is still wrong in the shipped catalog.** The installed module predates `TERMS`, so
it declares nothing, and no convention can guess `icons`. Recompiling fixes it by
construction — the module would declare `TERMS`, the builder would be told the name, and
the store would reject anything else. Deferred by decision: Sushi Go is Phase 8's game
and the recompile happens there.

**No complete live match**, as above.

**The submitted card list is too tidy.** `sushigo_cards.md` was written by hand as a clean
markdown table with exact counts, an arithmetic totals line, and a pre-grouped categories
section pairing nigiri and maki. A real person types a rough list. Every bit of that
structure is work the compiler did not have to do, so "compiled in 3 attempts" is a mild
overstatement — the input was written by somebody who already knew what the compiler
needed. Recorded here rather than fixed, and not to be repeated for Phase 8's held-out
game, where pre-structuring the material would mean the held-out test measures nothing.

**Invariant 3 was breached during this phase and is fixed.** The play CLI built the
informant before opening its trajectory, so the Cataloguer call that structures a person's
words reached a model unlogged — the one call the record can least afford to lose, since
it is where a person's words enter the game. The general weakness stands: `call_llm`
accepts `trajectory=None` and stays silent, so invariant 3 holds by callers remembering
rather than by construction.

**The vocabulary is still mine.** `plan.md`'s first deliverable — a model deriving the
effect primitives from the manual — was dropped by decision at the start of the phase. A
game needing a primitive nobody anticipated hits the escape case, which is the honest
boundary rather than a hidden one.

**Carried forward unchanged:** the Nim decision-quality regression from Phase 3 (1/6
optimal against the baseline's 3/6) is Phase 6's target; the compiled DNUP module ignores
its `viewer` argument and collides on `NAME`.
