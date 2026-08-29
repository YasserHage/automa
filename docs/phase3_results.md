# Phase 3 results — baseline and evaluation harness

Recorded 2026-08-29, immediately after the runs. Every number comes from a committed
result file and can be regenerated:

```bash
python -m automa.eval.run --target baseline --workers 4 --json docs/validation/phase3_baseline.json
python -m automa.eval.run --target agent    --workers 4 --json docs/validation/phase3_agent.json
python -m automa.eval.report docs/validation/phase3_*.json
```

Evidence: [`phase3_baseline.json`](validation/phase3_baseline.json),
[`phase3_agent.json`](validation/phase3_agent.json),
[`phase3_comparison.md`](validation/phase3_comparison.md).

---

## Exit criterion

> *"`--target baseline` and `--target agent` produce results on the same cases. First
> changelog row filled with a real number."* — `plan.md` Phase 3

| requirement | status |
|---|---|
| both targets run on the same cases and seeds | met — 16 fixed positions, 13 match seeds, identical for both |
| comparable JSON output | met — same schema, rendered side by side by `eval.report` |
| metrics instrumented | met — turns without intervention, illegal actions/match, cost/turn, latency/turn |
| changelog row with a real number | met — README and `plan.md`, both rows |
| offline suite passes with no API key | met |

---

## What was measured

Two arms, same model (`gemini-3.5-flash-lite`), same questions. The only difference is
that the agent is handed a numbered list of legal actions and returns an index, while
the baseline is given the rules and names its own move.

| | baseline | agent |
|---|---:|---:|
| **DNUP** interventions per match | 20.7 | **0** |
| DNUP clean matches | 0/3 | 3/3 |
| DNUP wins vs random | 0/3 | 2/3 |
| **Nim** interventions per match | 0 | 0 |
| Nim wins vs random | 5/10 | 6/10 |
| **Battery** legal moves | 15/16 | 16/16 |
| **Battery** optimal (Nim, solved) | **3/6** | **1/6** |
| cost per turn (DNUP) | $0.000868 | $0.000291 |
| wall clock | 16.9 min | 21.7 min |

Totals across all 13 matches: **62 corrections needed versus none**, on 178 calls
($0.14) against 281 calls ($0.08).

Per-category legality on the battery, which is where a *specific* missable rule would
show up rather than "the rules" in general:

| category | baseline | agent |
|---|---|---|
| obvious_legality | 5/5 | 5/5 |
| subtle_legality (displacement) | 2/2 | 2/2 |
| conditional_branch | 2/2 | 2/2 |
| orientation_accounting | **1/2** | 2/2 |
| terminal | 2/2 | 2/2 |
| control | 3/3 | 3/3 |

The baseline's single battery failure was `dnup-mid`, an orientation-accounting case —
tracking that a card taken from the table flips to its other value. That is DNUP's real
bookkeeping burden, and it is the one category where the baseline slipped.

---

## The three findings

### 1. The baseline is not uniformly bad, and that is the interesting part

On Nim it proposed **zero** illegal moves across ten matches. For a game with three
action shapes and no hidden state, the deterministic core buys nothing at all. It
collapses on DNUP, needing a correction before the first turn is out.

So the defensible claim is narrower than "LLMs cannot follow rules": **the core earns its
place as game complexity rises**. A project that only ever tested on Nim would have
concluded the whole architecture was pointless — which is a good argument for `plan.md`'s
insistence on two structurally distant games from Phase 1 onward.

### 2. Half the headline is not a result

The agent's zero illegal moves was fixed before the run started. The Tactician never
names a move: it is handed a list the core has already filtered to legal moves and
replies with a number, so an illegal move is not an output it can produce. Counting them
is like counting black cards drawn from a deck of red ones.

`62 → 0` therefore does **not** show the model understood DNUP better. It shows the model
was never in a position to break the rules. `eval/report.py` labels that row
`structural, not measured` so no reader mistakes it for a discovery.

### 3. Enumeration appears to have made decision quality *worse*

The finding that points against the architecture, and the one worth keeping:

| | baseline | agent |
|---|---|---|
| optimal moves on solved Nim positions | 3/6 | **1/6** |

Handed a numbered menu, the model argues about options — *"take all 3 from heap B to
balance the remaining heaps"* — rather than reasoning about the board. Removing the
possibility of an illegal move may also remove the pressure to think about why a move is
good.

**n=6, so this is a signal and not a result.** But it is the number Phase 6's Strategist
has to beat, and it would have been easy and dishonest to leave out.

---

## Two fairness bugs found and fixed mid-phase

`plan.md` warns that "building the baseline after the final solution is how hackathons
are lost — you unconsciously build a strawman". Both bugs below were biased *against* the
baseline, which is the direction to expect from someone who also wrote the agent.

**The action format only showed two of four action shapes.** It was derived from DNUP's
opening position, where the table is empty, so only `play` and `rotate` exist. The
baseline would never have seen the JSON keys for `take` or `add` and would have failed on
**formatting** when it wanted one — the precise failure the design exists to rule out.
Fixed by collecting shapes across a reference playout.

**A correct move written the wrong way was scored as a rules error.** Given a Nim board
rendered `A: oo (2)  B: ooo (3)`, the baseline answered `{"heap": "B", "count": 2}` — the
right move, with the heap named the way the *display* names it instead of as the integer
index. The run was stopped and restarted after drawing the line explicitly:

| question | kind | treatment |
|---|---|---|
| is this a *well-formed* action for this game? | formatting | retried, with valid shapes fed back |
| is this action *legal right now*? | rules | never retried — being told is the intervention being counted |

Shape signatures come from the game's whole action vocabulary rather than the current
legal set, so the feedback leaks nothing about the position. A test asserts that.

The fairness guarantee is itself a test: `test_the_baseline_is_never_shown_the_legal_actions`
asserts no legal-action label appears in the baseline's prompt, *and* that the rules and
state do.

---

## How the baseline was set up, and why it can be audited

- **Same model** as the Tactician, forced by `config.DEFAULT_MODELS` and asserted by
  `tests/test_config.py`.
- **The DNUP rules brief was written by a model reading the rulebook**, not by the author
  of `dnup.py`, who would otherwise have been on both sides of the comparison. It is
  committed at `src/automa/eval/rules/dnup.md` with every hand-edit recorded in its
  header: one rule added (hidden hands, which the model omitted), one unsupported
  inference removed, and the 3–5 player rules deliberately excluded as superseded.
- **Never penalised for formatting**, never told a move was illegal.
- Its proposed action is matched against the legal set exactly, then again ignoring list
  order — leniency that runs in its favour.

---

## Caveats on reading the numbers

**The primary metric is capped by match length.** Mean turns-before-intervention went
0.3 → 171, but the agent's DNUP matches happened to run longer (247/123/143) than the
baseline's (124/60/120). Quoting that ratio would flatter the result; **62 corrections
versus none** is the defensible statement.

**Cheaper per turn is not a model difference.** The agent made *more* calls (281 vs 178)
and still cost less, because the baseline re-sends the entire 8.6k-character rules brief
on every single turn — it has no memory of the rules between calls.

**Win rate is reported but not headline.** Phase 2 recorded a win that followed a losing
move; match outcome alone proves nothing.

---

## Still open

- Nim decision quality is `n=6` per arm — too thin to draw a conclusion from, and the
  regression it hints at is unexplained. Phase 6's target.
- No LLM has played a *compiled* module; that end-to-end link is owed before Phase 8.
- No person has typed a move into a live match (carried from Phase 2).
- The `effort="low"` thinking A/B is still unrun.
