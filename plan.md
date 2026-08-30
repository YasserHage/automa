# Automa — AI Agent for Physical Boardgames
## V1 Execution Plan — micro1 Agentic Workflows Hackathon

---

## What this project is

**Automa** is a multi-agent system that acts as an opponent in physical board and card
games. The user plays at the table with real components; Automa controls the other side.

The core problem: there is no satisfying way to play a physical boardgame against AI
today. Using a raw LLM (e.g. ChatGPT) fails quickly and visibly — it invents rules that
don't exist, loses track of state within a few turns, and cannot handle games it was
never trained on. Digital apps exist for popular titles, but require manual porting,
covering less than 1% of available games.

Automa solves this by being **generic**: it works with any game the user configures.
Configuration happens once per game — the user submits rule material and the system
compiles an executable module for that game. Subsequent sessions use the cached module.

**Primary audience:** players who want to play solo or test new games without assembling
a group.

**Secondary case:** game designers who want early playtesting without recruiting players.

---

## Architecture

Two zones: setup (once per game) and match (each session).

### Setup — once per game

The user submits rule material: PDF manual, pasted text, card images. A **Compiler
Agent** reads this material and produces:

1. A **Python module** for the game, implementing a fixed interface
2. A **card catalog** with structured card effects
3. A **plain-language summary** of what it understood — shown to the user for approval
   before any match begins

The generated module is validated automatically with random self-play before reaching
the user.

### Match — each session

Two LLM agents operate at different speeds:

**Strategist (slow cycle):** runs at match start and on specific triggers. Does not
produce a move plan — produces a *policy*: priorities and weights that guide tactical
decisions. Example: "in this match, denying resource X to the opponent is worth more
than direct scoring." The policy is stable across many turns.

**Tactician (fast cycle, every turn):** receives the public board view, Automa's
current hand, the active policy, and a **numbered list of legal actions**. Returns an
index and a one-line justification.

Between the two agents, a **deterministic core** (pure Python, no LLM):
- Maintains the full match state
- Runs the game module to enumerate legal actions before each turn
- Applies state transitions
- Exposes different views to each agent: the tactician never receives what it
  shouldn't see

### Why legality is deterministic, not LLM

The central architecture decision: no LLM ever decides whether an action is legal. The
core enumerates legal actions from the current state and module; the LLM chooses among
them.

This makes rule hallucination structurally impossible — there is no channel through
which an illegal move reaches the game. Error is concentrated in setup (compilation),
where it is auditable and correctable, rather than distributed across every turn of
every match.

### Memory — four separate types

- **Private state:** Automa's hand and hidden resources. Only the tactician reads it.
  Never exposed to the interface.
- **Public state:** board, discards, scores. Everyone reads it, including the user.
- **Deduction memory:** cards seen, card counts, inferences about the opponent's hand
  — derived only from public information.
- **Strategic memory:** active policy and why it was chosen.

### State reconciliation

The physical table is the source of truth. The internal model is a copy. They will
diverge. Every N turns the system declares what it believes is on the table and asks
for confirmation. Divergence caught early is trivial to fix; accumulated divergence
invalidates the match.

### Incremental card ingestion

Games with dozens of cards do not need to be fully described at setup. Unseen cards
are treated as generic deck members (counted, not interpreted). When an unknown card
appears, the system pauses, asks for a description, saves it to the catalog, and
resumes. The second match of the same game asks for fewer descriptions than the first.

---

## What differentiates from the baseline

**Baseline:** an LLM receives a text description of the rules and the current state,
and plays directly. No core, no module, no enumeration. Well-written prompt, capable
model.

The baseline fails in measurable ways:
- Proposes illegal actions (rule hallucination)
- Loses resource accounting across turns
- Has no reconciliation mechanism when state diverges

The solution does not fail in these dimensions by construction, not by instruction.

---

## Primary metric

**Turns without corrective intervention:** how many turns Automa runs before the human
needs to correct a rule, a state, or an impossible move.

Aggregates compilation, accounting, and decision quality into a single metric that
directly reflects what the player experiences.

Secondary metrics: illegal actions per match, cost per turn, latency per turn.

---

## Improvement Changelog

Each entry corresponds to a complete, measured version. Fill each evidence cell
immediately after running `python -m eval.run` — do not reconstruct later. If an
experiment was attempted and discarded during development of a version, add a row for
it with what was tried, the result, and why it was dropped. The submission requires
at least one removed experiment.

| Version | What changed and why | Evidence | Decision / Learning |
|---|---|---|---|
| Baseline | LLM with rules prompt + state, no agent architecture | DNUP: 20.7 illegal moves/match, 0/3 clean, first correction at turn 0.3, 0/3 wins. Nim: 0 illegal, 10/10 clean, 5/10 wins. Battery 15/16 legal, 3/6 optimal. `docs/validation/phase3_baseline.json` | Starting point - and not a strawman. It plays Nim flawlessly; it collapses on DNUP. The architecture's value scales with game complexity, and testing only on Nim would have shown it buying nothing. |
| V1 | Deterministic core + hand-written module + tactician with enumeration (no strategist yet) | DNUP: 0 interventions across 513 turns, 3/3 clean, 2/3 wins. Nim: 0 interventions, 6/10 wins. Battery 16/16 legal, **1/6 optimal**. `docs/validation/phase3_agent.json` | Kept, but the two halves read differently. Illegal moves went 62 -> 0, and that was fixed before the run: the tactician is handed a list already filtered to legal moves and replies with a number, so it cannot output an illegal one. That shows it was never able to break the rules, not that it understood them. The real finding is that decision quality *fell* (3/6 -> 1/6 optimal, n=6): enumeration removes illegal moves and may also remove the pressure to reason about the board. This is the number Phase 6's Strategist has to beat. |
| V2 | Rule compiler: module generated from the scanned rulebook + card list, gated by static scan, subprocess sandbox, 200-match self-play and human approval | Compiled in 1 attempt, $0.35. Passes conforms + 200/200 self-play + the parametrised contract and view tests. Tactician played it: 79 turns, 40 calls, 0 fallbacks. `docs/phase4_results.md` | Kept. Independently reached the same rules and flagged two real edge cases as assumptions. The key finding was a hole in *my* validator: its first module passed 200 self-play matches while crashing on `describe_state`, the one contract method the Phase 1 harness never called. Generated code tested the validator better than hand-written code did. |
| V3 | Effect vocabulary + incremental card ingestion: Sushi Go compiled with card effects kept out of the module and read from a catalog at runtime; unknown cards asked at the table and structured by a Cataloguer agent into typed operations | Compiled in 3 attempts, $1.04 (after a 4-attempt failed run, $1.51). 300/300 self-play matches terminal, 0 violations, 60 plies each. Ingestion: first match with an empty catalog asks a person 12 questions, second asks 0. Four live matches resolved 11 questions across 21 plies; none completed. `docs/phase5_results.md` | Kept; exit criterion **partially met** - the loop is measured, a complete live match is still owed. Two of the four first-run failures were a deadlock in *my* harness: `random_playout` did not know about the `unknown` key, so a module that correctly declared missing information was unvalidatable, and the model escaped by inventing a no-op action that could not advance `ply`. Self-play now answers every question as a person who cannot say would. Told it had declared too many unknowns at once, a later attempt changed *when* it asks - only for a card played face-up - and recorded that as an assumption. |
| V4 | Catalog Builder: a separate agent reads the same rule material and drafts what each card is worth before play, leaving only what the material does not state to be asked at the table | 11 of 12 Sushi Go card types drafted for $0.006, against $1.04 to compile the module. `docs/phase5_results.md` | Kept, and it exposed the phase's sharpest bug class. Four vocabulary fields carry a *name* both sides must agree on (`count_field`, `flag`, `resource`, `category`) and a mismatch never raises - the module looks for a name nobody wrote and its rule silently never fires. Maki: `maki_icons` against `icons`, median winning score 61 vs 49, win rate an unremarkable 50/50 throughout. Chopsticks: `extra_card_from_hand` against `chopsticks`, 0 chopsticks actions offered across 60 matches vs 1,435. Guarding one field let the second through within the hour, so modules now declare every name their rules look for. |
| REVERTED | Tactician returns an index **and a one-line justification**, as specified in this document | 382 real decisions: median 39 output tokens per response, ~20 of them the justification (median 78 chars). Read by nothing downstream. Phase 3: 1/6 optimal on solved Nim positions while explaining each choice fluently and wrongly. `docs/phase3_results.md` | Dropped after four phases in production. Half the output of every turn, on a tier where latency is the binding constraint, for a sentence no code consumed and that created a false impression of reasoning - the model picks an index and then writes about it, so the line is post-hoc rationalisation with no causal link to the choice. Auditability is kept and is stronger: the trajectory holds the prompt, the numbered actions and the index, which replays the decision as a sentence never could. The Baseline keeps its justification - it names a move rather than choosing an index, and its numbers are already measured. |
| REVERTED | Automa's hand open to the human (a locked decision in this document) | Played with it; it hands the opponent perfect information and spoils the match. `tests/test_views.py` | Reversed 2026-08-30. Each hand is now private to its owner. The original reasoning - somebody plays Automa's side physically, so they see the cards anyway - is about the table and not the interface, and the trajectory already makes every move auditable after the fact. A change to a locked decision invalidates completed work, and it did: the view tests now assert the opposite of what they did. `sushigo` still shows its hand until it is recompiled, because it is generated code. Clarified when the physical deck landed: this was always a decision about what the interface re-displays, not about hiding cards from the person who deals them - they read Automa's opening hand off the table to enter it, and that is fine. |
| V5 | **The physical deck is the default.** A module declares the components it is missing (`kind: "components"`) instead of dealing itself a hand; the person reads the cards off the table and only the *count* is checked. `--simulate` and `--auto` keep the seeded internal deal for testing | DNUP asks 2 questions per round and plays the supplied hands; 500/500 self-play matches still terminal with 0 violations, and the unanswered path deals byte-identically to the old `_deal`, so every seeded fixture and the whole eval battery are unchanged. `tests/test_components_supply.py`, `tests/test_table_informant.py` | Kept. The seam was already there and half-built: `Informant` was documented from Phase 5 as answering "what was drawn", and only the card-effects half existed. Two design choices carried the weight. Answers are plain strings the *module* interprets, so the core and the informant stay generic. And a module always asks, rather than asking only when no seed was passed - conditioning on the seed would have cost nothing today and hidden the table path from self-play, which is exactly how the two Phase 5 silent-failure bugs happened. |
| REMOVED | Phases 6 (Strategist) and 7 (state reconciliation) | Not attempted; time spent on the physical-deck path instead, which Phase 7 had reserved `play_match(initial=)` for | Dropped by decision under time pressure, in the cut order this document specifies (9, then 6, then 5's scope). The consequence is on the record rather than hidden: V1's finding that enumeration *lowered* decision quality (3/6 -> 1/6 optimal on solved Nim positions) was the number Phase 6 existed to beat, and it now stands unanswered. Phase 7's checkpointing is gone too, so a divergence between the table and the model is caught only when `check_invariants` notices a card that does not exist - which is why a mistyped card is reported rather than refused. |
| REMOVED | Validating the whole catalog draft in one `call_llm` validator, so a malformed card would be retried with the problem fed back | One card missing a field discarded the other eleven that were correct, because `call_llm` raises once its attempts are exhausted. `docs/phase5_results.md` | Dropped. A worse failure than the one it fixed. Replaced by a bounded repair round that re-asks only the malformed cards and leaves anything still malformed **absent** rather than parked - a model's slip is not the card telling us it cannot be expressed, and parking would make it permanently unaskable. |
| V6 | **A local web UI**, as an additional surface over the same service calls. `play_match` is blocking and has no `step()`, so a match runs unmodified on a daemon thread with its decider and both askers blocked on queues that HTTP handlers feed; SSE carries the events back. Amends the `V1 interface | CLI` locked decision without invalidating anything - the eval battery, the reproduction guide and every measured result still run through the CLI | A complete offline Nim match played through the real HTTP API and SSE: **8 turns, 8 plies, 0 LLM calls, $0**, and its trajectory replays unchanged under `python -m automa.trajectory.show`, `human_checkpoint` events included. The physical-deck path drives end to end over HTTP: a miscount is re-asked carrying its `note`, a correct 13-card answer is accepted, and the module builds 14 legal actions from the cards read out. Catalog editor over HTTP: an invalid effect is refused **422** with the store's own problem strings verbatim, a valid one stored, park and forget round-trip. Suite 467 -> **555 passed**, ruff clean, same 20 pre-existing `sushigo-old` collection errors. **Not yet verified live: a Tactician match and a compile through the UI.** | Kept. The interface is where two silent bugs surfaced that no unit test would have. **`pending` was built by spreading the question's payload**, which carries its own `kind` (`components`/`card_effect`) and so overwrote the `question`/`turn` discriminator the routes dispatch on - `/answer` returned 409 for every question in every match, and nothing could be answered from the browser. The service tests missed it because they call `ask()` and check the return value, never the shape the route validates against. **And the games list was keyed on the module's `NAME`**, but `dnup.py` and `dnup_compiled.py` both declare `NAME = "dnup"`: two identical rows, one addressing a module nobody could reach, and a reload guard that could miss. The loader resolves the *module* name, so that is the identity; `NAME` only says where the catalog lives, and the two busy-checks are now separate questions. Both bugs are the same shape as Phase 4's `describe_state`: a seam nothing exercised until a second consumer arrived. |

---

## Locked decisions

Do not revisit during execution. A change here invalidates completed work.

| Decision | Choice | Reason |
|---|---|---|
| Language | Python 3.11+ | LLM SDKs, subprocess for sandbox |
| Legality | Enumerated by code, never by LLM | Hallucination impossible by construction |
| Game module | Generated Python with fixed interface | Full expressiveness for arbitrary games |
| Automa's hand | **Not re-displayed to the human** (reversed 2026-08-30; was open) | This is an interface decision, not an information barrier. Each hand is rendered only in its owner's view, so the human is not handed perfect information move by move. They do read Automa's opening hand off the physical table when they enter the deal - somebody has to, and Automa cannot see the table - and that is expected rather than a leak. |
| V1 interface | CLI, plus a local web UI (amended, was CLI only) | The CLI stays canonical — the reproduction guide, the eval battery and every measured result run through it unchanged. The web UI is an additional surface over the same service calls, added after the scoring work was done; a UI still doesn't score on the rubric, which is exactly why keeping it off the critical path until then was correct. Unlike the hand-privacy reversal, this invalidates no completed work. |
| Camera | Optional, only if time allows | Not on the critical path |

### Invariants — copy to CLAUDE.md at repository root

```
1. No LLM decides legality. The core enumerates; the LLM chooses an index.
2. No LLM writes state. Only the core applies transitions.
3. Every prompt sent and every response received are logged in trajectories/.
4. No game module is imported by the core. The core is game-agnostic.
5. Card effects are executable data structures, never prose interpreted at runtime.
6. No commercial rulebook or card text is committed to the repository.
```

---

## Execution phases

One phase per working session. Do not advance without meeting the exit criterion.

### Phase 0 — Skeleton and logging spine

**Why first:** the trajectory format is a hackathon deliverable. Defining it later
means reconstructing logs at the end, which does not work in practice.

- Folder structure: `core/`, `compiler/`, `agents/`, `games/`, `eval/`, `trajectories/`
- Single LLM call wrapper. Every call passes through it. Logs: agent name, full
  prompt, full response, tokens, cost, latency, retries
- Trajectory format: JSONL, one file per match
- `pyproject.toml` with pinned versions
- `CLAUDE.md` with the invariants above

**Exit criterion:** an example script makes one LLM call and produces a readable
trajectory file with cost and latency filled in.

---

### Phase 1 — Game module contract, written by hand

**Why before the compiler:** you need a concrete target before generating code for it.
And you get the human-written ground truth for free — to measure compilation quality
later.

- Fixed interface: `initial_state()`, `legal_actions(state)`, `apply(state, action)`,
  `is_terminal(state)`, `winner(state)`, `describe_state(state, viewer)`
- Hand-written module for **two distinct games**: one simple (traditional card game or
  invented), one with at least 6 card types with individual effects. Two games from the
  start force the interface to be genuinely generic
- Random self-play harness: detects non-terminating matches, states with no legal
  action, exceptions, component conservation violations

**Exit criterion:** 500 random matches per game complete without exception and without
violating structural invariants. If the interface needs to change to fit the second
game, change it now.

---

### Phase 2 — Match loop with the tactician

**What it is:** first playable end-to-end match. No compiler, no strategist yet.

- Core maintains state, exposes the three views (strategist, tactician, interface)
- Tactician: receives public view + Automa's hand + numbered list of legal actions,
  returns an index and a one-line justification
- CLI: shows public state, presents the human's legal actions as a numbered list,
  accepts a number
- Full trajectory logged per match

**Exit criterion:** complete match playable through the terminal on both games, with
trajectory showing each tactician decision and justification.

---

### Phase 3 — Baseline and evaluation harness

**Why now and not at the end:** building the baseline after the final solution is how
hackathons are lost — you unconsciously build a strawman. Build it now, while you
don't yet know what will work.

- Baseline: LLM receives rules description as text + current state, plays directly,
  no core or enumeration. Well-written prompt — the baseline must be fair
- Battery of 12+ fixed, seeded states with ground truth. Cover: obvious legality,
  subtle legality, conditional card effect, resource accounting, terminal state,
  one control case
- `eval/run.py` runs baseline and solution on the same cases, outputs comparable JSON
- Metrics instrumented: turns without intervention, illegal actions per match, cost
  per turn, latency per turn

**Exit criterion:** `python -m eval.run --target baseline` and `--target agent` produce
results on the same cases. First changelog row filled with a real number.

---

### Phase 4 — Rule compiler

**What it does:** replaces hand-written modules with modules generated from user-
submitted rule material.

- Ingestion: PDF, pasted text, card images
- Compiler produces: Python module conforming to the interface + plain-language summary
- Automatic validation in four layers before reaching the user:
  1. Random self-play (reuses the Phase 1 harness)
  2. Structural invariants
  3. Golden cases extracted from the manual's own examples
  4. Plain-language summary presented for human approval
- Generated module executed in subprocess with timeout

**Exit criterion:** compiler reproduces, on its own, a module functionally equivalent
to the one written by hand in Phase 1 — validated by running the same case battery
against the generated module. This is the strongest test in the project: there is a
human-written ground truth to compare against.

---

### Phase 5 — Effect vocabulary and incremental ingestion

**What it solves:** games with dozens of cards do not need full description at setup.

- Compiler extracts from the manual the vocabulary of game primitives: gain resource,
  force discard, activate on condition, score by set, etc.
- Persistent card catalog per game, separate from the module
- Unknown card flow: system pauses → requests description → structures it with the
  vocabulary → saves to catalog → resumes match
- Escape case: effect that does not fit the vocabulary. Define behavior: reject, extend
  the vocabulary, or flag for human arbitration

**Exit criterion:** complete match with 20+ card types, describing only cards that
actually appeared. Second match of the same game requires fewer descriptions than the
first.

---

### Phase 6 — Strategist  *(ABANDONED — see the Improvement Changelog)*

Not attempted.

**What it adds:** Automa stops playing turn-by-turn and starts playing with direction.

- Strategist produces a policy (priorities and weights, not a move plan)
- Replanning triggers: match start, phase change declared by the module, N turns
  without progress on the prioritized metric
- Tactician receives the active policy in its prompt
- Strategic memory: records previous policy and why it was replaced

**Exit criterion:** run the battery before and after. If win rate against a random
player does not improve, the strategist becomes a changelog entry as a removed
experiment — an equally valid result.

---

### Phase 7 — State reconciliation  *(ABANDONED — see the Improvement Changelog)*

Not attempted. The reserved `play_match(initial=)` seam went unused; supplying the
physical deal through the `components` question covers the part that mattered most,
and nothing checkpoints a long match against the table.

**What it solves:** silent divergence between the physical table and the internal model
over a long match.

- Checkpoint every N turns: system declares what it believes is on the table, human
  confirms or corrects
- Human turn parsing confirmed before being applied
- Manual state correction without restarting the match

**Exit criterion:** 30+ turn match without undetected divergence.

---

### Phase 8 — Held-out game, final evaluation, and deliverables

**What it proves:** real genericity — a game never seen during development.

- Choose a third game, never used in the project. Run the full pipeline: rule material
  → compilation → validation → match. This is the challenging case required by the
  submission
- Final evaluation: baseline vs solution, all cases, all games
- README: user, bottleneck, value, architecture, labeled Improvement Changelog,
  primary failure mode, hot take
- Reproduction guide: exact commands, versions, required data, runtime, cost
- Representative trajectories: one with an action retry, one with a human checkpoint
- Video following the submission order: problem → baseline → full execution →
  comparison → changelog → main contributing change → removed experiment

**Exit criterion:** another person, on a clean machine, reaches the main result
following only the guide. Test this for real — fresh container, clone, no improvising.

---

### Phase 9 — Camera (optional)

Only if time remains after Phase 8 is complete.

Minimum scope with highest return: batch card ingestion. User passes the deck in front
of the camera once; the compiler extracts and populates the catalog; user reviews and
approves before the first match.

Never on the critical path. The main result runs without the camera.

---

## Effort distribution

| Phase | Weight | Risk |
|---|---|---|
| 0 — skeleton | 5% | low |
| 1 — hand-written contract | 15% | low, high return |
| 2 — match loop | 10% | low |
| 3 — baseline and eval | 15% | low, critical |
| 4 — compiler | 25% | high |
| 5 — vocabulary | 10% | medium |
| 6 — strategist | 5% | low |
| 7 — reconciliation | 5% | low |
| 8 — deliverables | 10% | underestimated by everyone |

If time runs short, cut in this order: Phase 9, Phase 6, reduce Phase 5 scope to one
game. Never cut Phase 3 or Phase 8.

---

## How to run each session in Claude Code

One phase per session. At the start of each session:

1. Paste the project context (sections "What this project is" and "Architecture")
2. Point to `CLAUDE.md` and ask for confirmation of the invariants
3. Paste the scope and exit criterion of the current phase
4. Ask for a file plan before any code
5. Require tests that verify the exit criterion, not decorative tests

At the end of each phase that produces a measured result, fill the changelog entry
immediately with the number from `eval/run.py` and the result file. A changelog
reconstructed at the end is fiction — judges recognize it. Also record experiments
discarded during the phase, even if they never reached a complete version.