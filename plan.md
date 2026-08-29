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

---

## Locked decisions

Do not revisit during execution. A change here invalidates completed work.

| Decision | Choice | Reason |
|---|---|---|
| Language | Python 3.11+ | LLM SDKs, subprocess for sandbox |
| Legality | Enumerated by code, never by LLM | Hallucination impossible by construction |
| Game module | Generated Python with fixed interface | Full expressiveness for arbitrary games |
| Automa's hand | Open — visible to the human | Physical logistics + auditability |
| V1 interface | CLI | UI does not score on the rubric |
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

### Phase 6 — Strategist

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

### Phase 7 — State reconciliation

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