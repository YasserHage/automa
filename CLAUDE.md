# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project state

**Phase 5 is substantially complete; its exit criterion is partially met.** A third
game, Sushi Go, is compiled and installed, its card effects are learned rather than
hardcoded, and the ingestion loop is wired end to end: the module declares what it does
not know, the core asks through the `informant` seam, a Cataloguer agent turns a person's
sentence into typed operations, and the answer is stored so nobody is asked twice. A
separate Catalog Builder drafts from the rulebook beforehand, so only what the material
omits reaches the table. Measured: an empty catalog puts 12 questions to a person, the
second match 0. **What is missing is a complete live match** - every one played so far was
abandoned part-way. Results in `docs/phase5_results.md`; earlier phases in
`docs/phase4_results.md`, `docs/phase3_results.md` and `docs/phase2_results.md`.

Invariant 5 now has something to bite on, and invariant 4's `unknown` convention is
exercised by a shipped game rather than only by deliberately broken test modules.

**The lesson worth carrying from Phase 4:** the compiler's first module passed 200 matches
of self-play with zero violations while crashing the moment anyone rendered the board.
`describe_state` was the one contract method the Phase 1 harness never called. Generated
code is a better test of a validator than hand-written code, because it does not share the
author's assumptions.

**The lesson worth carrying from Phase 5:** the same thing happened again, twice, and both
times the defect was mine. Self-play knew nothing about the `unknown` state key, so a
module that correctly declared missing information deadlocked; the model escaped by
inventing a no-op action that could not advance `ply`, and two of four compile attempts
went on it. Then, separately: **four vocabulary fields carry a name the catalog and the
module must agree on** - `count_field`, `flag`, `resource`, `category` - and a mismatch
never raises. The module looks for a name nobody wrote, its rule silently never fires, and
the game plays on producing plausible numbers. Maki scored a median 61 instead of 49 with
the win rate sitting at a healthy 50/50; the entire chopsticks mechanic was switched off,
0 actions offered against 1,435. I guarded one field and the second instance arrived within
the hour on another. Modules now declare `TERMS`, and where they do not, the structuring
agents fall back on naming a card's marker after the card type.

**Two things `plan.md` predicted that turned out wrong**, both worth remembering before
trusting a prediction in it. Chopsticks was named as the card the vocabulary could not
express; in fact the compiler split it correctly - the rule into the module as turn
structure, the trigger into the catalog as `set_flag` - and only a Cataloguer prompt that
assumed every card scores broke the chain. And a hand-written reference is not what makes
a compiled module trustworthy; the gates are.

**Since Phase 5, and outside its scope: the physical deck is now the default.** Phases 6
and 7 are abandoned for time, so how a match starts is settled here rather than in Phase
7. A module no longer deals itself a hand: it declares the components it is missing
(`unknown` of `kind: "components"`, carrying a `slot` and a `count`) and the person reads
the real cards off the table. Answers are plain strings the *module* interprets, the count
is the only validation, and an unrecognised name is passed through rather than refused -
for a game whose cards are learned, that is how a new card arrives. Nobody to ask means
`unavailable`, which is when the seeded shuffle happens, so self-play, the fingerprint and
the eval battery all still deal exactly what they always did. `--simulate` and `--auto`
take that path deliberately. Note the consequence for the state contract: `initial_state`
routinely returns a state that is *waiting*, so any caller that steps a fresh state must
`settle()` it first (`core/game.py`), and `check_invariants` must call an undealt state
sound rather than broken.

**Since then, and outside every phase: there is a local web UI** (`automa-web`,
`src/automa/web/`). It is an *additional surface* and changes nothing measured — the eval
battery, the reproduction guide and every changelog number still run through the CLI. It
amends the `V1 interface | CLI` locked decision rather than reversing it, because it
invalidates no completed work. Two bugs it exposed are worth carrying: `pending` must
**nest** a question's payload rather than spread it (the payload's own `kind` overwrote the
route discriminator, so every answer was refused with a 409), and a game's identity is its
**module name**, not its `NAME` — `dnup.py` and `dnup_compiled.py` both declare
`NAME = "dnup"`, so keying on `NAME` made one unreachable. Both are the Phase 4
`describe_state` shape: a seam nothing exercised until a second consumer arrived.

**Still open, carried forward:** maki is wrong in the shipped Sushi Go catalog and is fixed
by recompiling under `TERMS`, deferred by decision to Phase 8, which reuses this game. No
complete live Sushi Go match exists. `src/automa/compiler/material/sushigo_cards.md` was
hand-written far tidier than a real submission would be, which flatters the compile result
and must not be repeated for Phase 8's held-out game. `call_llm` accepts
`trajectory=None` and logs nothing, so invariant 3 holds by callers remembering rather than
by construction - one breach already occurred and was fixed.

Before writing any code, re-read `plan.md` in full; it is the authoritative spec for
architecture, phase scope, and exit criteria. Do not paraphrase or summarize it away — refer
back to it directly each session.

The code lives in a `src/` layout under a single `automa` package, so the repository root
holds only `src/`, `tests/`, `docs/`, and `trajectories/`. This deviates from the flat
`core/`, `compiler/`, … layout `plan.md` describes: the packages and their responsibilities
are unchanged, only their nesting is. The one consequence is that documented commands gain
an `automa.` prefix — `python -m automa.eval.run`, not `python -m eval.run`.

## What this project is

**Automa** is a multi-agent system that acts as an opponent in physical board and card games.
The user plays at the table with real components; Automa controls the other side.

A raw LLM playing directly (the baseline) invents rules that don't exist, loses track of
state within a few turns, and can't handle games it wasn't trained on. Automa is generic:
the user submits rule material once per game (PDF manual, pasted text, card images) and a
Compiler Agent produces an executable Python module plus a card catalog. Subsequent sessions
reuse the cached module.

## Architecture

Two zones: **setup** (once per game) and **match** (each session).

- **Setup:** a Compiler Agent reads submitted rule material and produces (1) a Python module
  implementing a fixed interface, (2) a structured card catalog, (3) a plain-language summary
  shown to the user for approval before any match begins. The generated module is validated
  with random self-play before it reaches the user.
- **Match:** two LLM agents run at different speeds around a deterministic core.
  - **Strategist** (slow cycle): runs at match start and on specific triggers, producing a
    *policy* (priorities/weights), not a move plan.
  - **Tactician** (fast cycle, every turn): receives Automa's own view of the board, the
    active policy, and a numbered list of legal actions; returns **an index and nothing
    else**. `plan.md` also specifies a one-line justification; it was measured (about half
    of every response's output tokens, read by nothing downstream, and often confidently
    wrong) and removed by decision.
  - **Deterministic core** (pure Python, no LLM): maintains match state, runs the game module
    to enumerate legal actions each turn, applies state transitions, and exposes different
    views to each agent — the tactician never sees what it shouldn't.

**Why legality is deterministic, not LLM:** no LLM ever decides whether an action is legal.
The core enumerates legal actions from state + module; the LLM only chooses among them. This
makes rule hallucination structurally impossible, and concentrates error in setup
(compilation), where it's auditable, rather than distributed across every turn.

**Memory has four separate types**, each with different visibility:
- Private state (Automa's hand/hidden resources — tactician only, never shown in the interface)
- Public state (board, discards, scores — everyone, including the user)
- Deduction memory (inferences about the opponent's hand, derived only from public info)
- Strategic memory (active policy and why it was chosen)

**State reconciliation:** the physical table is ground truth; the internal model is a copy
that will diverge. Every N turns the system declares what it believes is on the table and
asks for confirmation.

**Incremental card ingestion:** unseen cards are treated as generic deck members until they
appear, at which point the system pauses, asks for a description, saves it to the catalog,
and resumes — so later matches of the same game need fewer descriptions than the first.

## Invariants

These are non-negotiable across every phase:

1. No LLM decides legality. The core enumerates; the LLM chooses an index.
2. No LLM writes state. Only the core applies transitions.
3. Every prompt sent and every response received are logged in `trajectories/`.
4. No game module is imported by the core. The core is game-agnostic.
5. Card effects are executable data structures, never prose interpreted at runtime.
6. No commercial rulebook or card text is committed to the repository.

## Locked decisions

Do not revisit these during execution — a change here invalidates completed work.

| Decision | Choice | Reason |
|---|---|---|
| Language | Python 3.11+ | LLM SDKs, subprocess for sandbox |
| Legality | Enumerated by code, never by LLM | Hallucination impossible by construction |
| Game module | Generated Python with fixed interface | Full expressiveness for arbitrary games |
| Automa's hand | **Hidden from the human** (reversed 2026-08-30; was open) | Showing it hands the opponent perfect information and spoils the match. The original reasoning was physical - the person plays Automa's side, so they would see the cards anyway - but that is a table problem, not an interface one, and auditability is served better by the trajectory, which records every state Automa saw. |
| V1 interface | CLI | UI does not score on the rubric |
| Camera | Optional, only if time allows | Not on the critical path |

## Hackathon constraints and goals (micro1 Agentic Workflows Hackathon)

This project is built for the micro1 Agentic Workflows Hackathon (`docs/micro1 - First
Hackathon97ce7c5.pdf`). Every phase's work should be evaluated against these external
requirements, not just against `plan.md`'s own exit criteria.

**Four questions to keep answering, in order:** who has this problem, what bottleneck
makes it worth solving, does the agent solve it well, can another person reproduce the
result.

**Ground rules (baseline requirements for eligibility):**
1. Building with already-known tools/components is fine.
2. Make explicit what existed before the competition vs. what was added during it.
3. Respect the license/service terms of every tool and component used.
4. Keep consequential actions inside a sandbox or simulation; require human approval
   before the action actually happens.
5. A qualified human reviewer must be part of any solution that could significantly
   affect someone.
6. The use case must be legal and ethical, treating people and their data responsibly.
7. Only use data allowed to be shared — public, synthetic, or approved anonymous data.
8. No credentials or private information in the submission.
9. Every result claim must be tied to submitted evidence.
10. Judges must have enough access to run the project and reproduce the main result.

For this project specifically, ground rule 4 and 5 are already satisfied structurally:
Automa never executes an action without the deterministic core validating legality, and
the human player is always present at the table as the reviewer of every Automa move.
Ground rule 7 maps to invariant 6 (no commercial rulebook or card text committed to the
repo) — hand-written game modules and any bundled rule text/examples must use original,
public-domain, or synthetic material, not scanned/pasted commercial manuals.

**Judging rubric (100 points total) — weight effort accordingly:**

| Criterion | Points | What strong work looks like |
|---|---|---|
| Problem & User Value | 15 | Clearly defined user and bottleneck (README section 1) |
| Agent Solution & Engineering | 30 | Agents used purposefully; design choices (context, tools, memory, verification, skills, orchestration) demonstrably help — the largest single category |
| End to End Quality | 20 | A complete, self-contained run producing a result the intended user would actually accept, not an obviously AI-generated draft |
| Measured Improvement | 15 | Real gains over a fair baseline, connected to the changelog |
| Reproducibility | 15 | Another person can run baseline + solution from a clean environment and reach the main result |
| Hot Take / Insights | 5 | An observed failure mode turned into a practical lesson |

**Required final deliverables (all four, no substitutions):**
1. Complete solution code + agent instructions + README (intended user, bottleneck, why
   it matters) + labeled Improvement Changelog (every meaningful iteration, connected to
   evidence) + main failure mode + hot take.
2. Reproduction guide for a clean environment: exact commands for solution, baseline,
   and evaluation; required data; versions; approximate runtime and cost.
3. Solution video, up to 5 minutes: problem → baseline → one full realistic execution →
   final comparison → changelog highlights → biggest contributing change → one removed
   experiment.
4. Representative agent trajectories for every agent used (Strategist, Tactician,
   Compiler), each traceable from instructions → tool responses → feedback → retries or
   human checkpoints → final result.

**Baseline and evaluation requirements:** the baseline must be a simple, fair reference
(e.g. one direct prompt with basic instructions playing straight from a rules
description) given the same task and evaluation cases as the solution — this is exactly
what `plan.md` Phase 3 defines. Report a primary metric that reflects real user value
(here: turns without corrective intervention), plus secondary metrics (illegal actions
per match, cost per turn, latency per turn), using 10+ cases where practical and
including at least one deliberately hard case with an explanation of what it revealed.

## Structure and commands

```
src/automa/
  config.py          per-role model map, "provider:model" strings, env-overridable
  core/              deterministic zone — NEVER imports a model client (see below)
  llm/               the only code that reaches a model
    call.py          call_llm() — the single wrapper (invariant 3)
    pricing.py       priced / free-tier / unknown, kept strictly apart
    providers/       vendor seam: base protocol, gemini (default), anthropic
  trajectory/        JSONL event log + `show` renderer   <- code
  agents/            Tactician (Phase 2), Strategist (Phase 6)
  games/             hand-written game modules (Phase 1)
  compiler/          rule material -> game module (Phase 4)
  table.py           what is on the physical table, read out by the person
  catalog/           what each card does, as data (Phase 5)
    vocabulary.py    the closed set of effect operations, and their validation
    store.py         load / learn / park / forget, persisted per game
    build.py         rule material -> a drafted catalog (a separate agent)
    informant.py     answers the module's questions: catalog first, person second
  eval/              baseline + harness (Phase 3)
  web/               local web UI - an ADDITIONAL surface; the CLI stays canonical
    app.py           create_app(): routes, static mount, lifespan drain on shutdown
    routes/          thin HTTP adapters, nothing but validation and status codes
    services/        every domain call, as pure functions returning plain dicts
    sessions/jobs    a match or a compile on a daemon thread, driven through queues
    static/          plain HTML/CSS/JS, no build step, no CDN
  scripts/           runnable entry points
tests/
  fixtures/          reference data a test loads on purpose, never found by accident
catalog/             per-game card effects, written by runs, never committed
trajectories/        JSONL output, gitignored                <- data
```

Note `trajectory/` (code) versus `trajectories/` (output) — one character apart.

Environment: Python 3.12, `.venv` at the repo root, editable install.

```bash
.venv\Scripts\python -m pytest                        # offline, no API key needed
.venv\Scripts\python -m ruff check .
.venv\Scripts\python -m automa.scripts.hello_llm      # or: automa-hello
.venv\Scripts\python -m automa.trajectory.show <file> # or: automa-show
.venv\Scripts\python -m automa.scripts.validate_games --matches 500 \
    --json docs/validation/phase1.json                # or: automa-validate
.venv\Scripts\python -m automa.scripts.play --game dnup          # or: automa-play
.venv\Scripts\python -m automa.scripts.play --game dnup --simulate  # deal internally, testing only
.venv\Scripts\python -m automa.scripts.play --game dnup --auto    # unattended, no human
.venv\Scripts\python -m automa.scripts.build_catalog --game sushigo \
    --material files/SushiGo_rules.pdf \
    --cards src/automa/compiler/material/sushigo_cards.md    # or: automa-catalog
.venv\Scripts\python -m automa.scripts.play --game sushigo --catalog  # learns at the table
.venv\Scripts\python -m automa.scripts.web                       # or: automa-web
```

`automa-web` serves the UI on `127.0.0.1:8765`. It is an **additional surface**: no
measured result runs through it, `automa.eval` does not know it exists, and the
reproduction guide is unchanged. It binds loopback only and refuses a non-loopback
`--host` outright (it executes compiled game modules in-process and has no auth);
`AUTOMA_WEB_ALLOW_REMOTE=1` overrides deliberately awkwardly. There is no `--reload` —
it needs an undeclared dependency and would restart mid-match.

Adding a console script needs `pip install -e . --no-deps` before the short name resolves;
the `python -m` form works immediately.

- `python -m automa.eval.run --target baseline` and `--target agent` (Phase 3) run the
  same fixed case battery against the baseline and the Automa solution and produce
  comparable JSON output.
- The game module contract lives in `src/automa/core/game.py` as a `Protocol` whose members
  are all `@staticmethod` — a game is a *module*, not an instance, and without that no module
  can structurally match. It is `initial_state(seed=None)`, `legal_actions(state)`,
  `apply(state, action)`, `is_terminal(state)`, `winner(state)`,
  `describe_state(state, viewer)`, plus optional `check_invariants(state)`. Three deviations
  from the six methods `plan.md` names, each sanctioned by "if the interface needs to change
  to fit the second game, change it now": `seed` (Phase 3 needs reproducible fixtures, and
  shuffling once at setup is what keeps `apply` pure), `check_invariants` (the core cannot
  know generically that a game conserves its components, so the module declares it), and the
  `NAME` / `PLAYERS` constants. `winner` returns `None` for a draw *only*, and raises
  `ValueError` off-terminal.
- Every state carries three core-owned keys: `to_play`, `turn` (player turns, non-decreasing)
  and `ply` (applications of `apply`, strictly increasing). `ply` is what bounds a match;
  `turn` cannot, because a multi-step action advances the state without advancing the turn.
  The optional `pending` key is how a sub-decision belonging to the player whose turn it
  *isn't* is expressed.
- Every LLM call must go through `automa.llm.call_llm`, which logs agent name, full
  prompt, full response, tokens, cost, latency, and retries to a per-match JSONL
  trajectory. Never call a provider SDK directly from anywhere else.

### Games

`nim`, `dnup` and `sushigo`, chosen to be structurally distant so the interface cannot be
shaped by one of them. Nim has no hand, no deck and no hidden information; DNUP has hidden hands, a
contested shared table and two play areas per player. Nim also carries `optimal_actions()`
— **outside** the contract, never used in play — which gives Phase 3 and Phase 6 a
perfect-play oracle, so decision *quality* is measurable and not just legality.

`dnup` deals nothing itself: at the start of every round it asks the table for both
hands, 13 cards each, read out as `up/down` pairs. Orientation is the hidden state the
game turns on, so it is taken as read rather than guessed, and the star card falls out of
the answer, so nobody is asked who opens. A token matching no printed card is kept and
reported by `check_invariants` rather than refused. `dnup` implements the two-player mode
of a real published game (Asmodee). Per invariant 6
the rulebook lives in the gitignored `files/` directory and **no rule text, card text or
scan is reproduced anywhere in the repository** — the module implements mechanics and
paraphrases in its docstring. The rulebook does not list the deck, so `DECK` was
transcribed from the physical cards. Its skew is load-bearing and pinned by a test: low
values are plentiful (1 and 2 appear seven times each), high values scarce (8 four times,
9 three, 10 exactly once, so a value-10 set can never exceed one card). One thing remains
an assumption, labelled in the module: deal orientation is taken to be random, which is
what happens physically but is not stated in the rules.

`sushigo` is the Phase 5 game and the first with **effect cards, a draw of sorts, and
simultaneous selection**. It is compiler-generated (`automa-compile --catalog`), installed
under its own `NAME`, and carries the generated-module header - do not hand-edit it, and
do not lint it. What makes it different from the other two is that it knows the *rules* and
not the *cards*: every card effect is read from `catalog/sushigo.json` at runtime, and a
card nobody has described sets the `unknown` state key so the core can ask. Its rulebook
(Gamewright) is likewise gitignored under invariant 6.

**The catalog is never committed.** `catalog/` is the product's output directory, filled by
a run and by answers given at the table; `tests/test_catalog_is_earned.py` fails if a JSON
file there is ever tracked by git. A shipped catalog would answer questions the product
never asked and make the Phase 5 number meaningless. The hand-written reference effects
used to exercise the module deliberately live in `tests/fixtures/` under a name that cannot
be mistaken for one.

The same rulebook is the Phase 4 compiler's input, which is why a real game is worth more
here than an invented one: "the compiler reproduces the hand-written module" becomes a test
against rules somebody else wrote. Note the rulebook PDF is an **image-only scan** with no
text layer, so Phase 4's ingestion cannot assume `pdftotext` will work.

### The decider seam

`automa/core/match.py` runs a match without knowing the game *or* whether a model is
involved. `play_match` takes one `Decider` per player — `Callable[[TurnContext], int |
Decision]` — enumerates the legal actions, hands them over, gets an **index** back, and
applies the action itself. Invariant 1 stops being a rule anyone has to remember and becomes
a type: a decider cannot express an illegal action, because the only thing it can return is
a position in a list the core built. An out-of-range index raises `IllegalDecision` rather
than being clamped.

That is what keeps the loop in `core/`. The tactician imports `automa.llm` and therefore
lives in `agents/`; the human decider does terminal I/O and lives in `scripts/play.py`; the
core does neither. Phase 3 reuses `play_match` with a baseline decider, and Phase 6 with two
tacticians — only the deciders change.

`initial=` remains unused: it was reserved for Phase 7, which is abandoned, and it is the
wrong seam for playing with real cards anyway — it makes the *caller* build a game-specific
state, which the core must never know how to do.

`informant=` is how the table speaks instead. **An informant is not a decider.** It supplies
information only the table has — which cards were dealt, a drawn card, a die roll, what an
unfamiliar card does — and supplying a fact is neither a decision nor a corrective
intervention; counting it as either would corrupt the primary metric. It is counted as
`MatchResult.informed` and logged as `table_information`.

Two kinds of question travel that one seam, and two informants answer them, joined by
`match.compose`, which asks each in turn and takes the first answer that is not
`unavailable`:

- `kind: "components"` — *which* cards these are. Answered by `automa/table.py` straight
  from the person, with **no model involved**: an identity is a fact, not prose needing
  structuring. The module's `count` is the only validation, and a name it does not
  recognise is passed through, never refused — for a catalog game that unrecognised name
  flows on into the next kind of question and becomes a card described once.
- `kind: "card_effect"` — what a card *does*. Answered by `automa/catalog/informant.py`:
  catalog first, person second, structured by the Cataloguer into vocabulary operations.

A module that is told `unavailable` falls back to its own seeded deal, which is what every
self-play playout answers and therefore what validates the path offline.

**A third implementation of that seam is queue-backed.** `web/services/session.py` runs
`play_match` unmodified on a daemon thread and blocks its callbacks on `queue.Queue`
objects that HTTP handlers feed from outside — the decider waits for a move, the two
askers wait for an answer. Three things about it are load-bearing. The decider *re-loops*
on an out-of-range index rather than passing it on, because `IllegalDecision` is not
caught by the loop's `except MatchAbandoned` and would kill the match with no `match_end`.
A **timed-out or abandoned ask must raise, never return `None`** — `catalog_informant`
parks a card only *after* `ask()` returns, so a `None` on a dropped connection would
permanently park it as "nobody at the table could describe it" and corrupt the Phase 5
question count for every future match. And `pending` nests the question's payload rather
than spreading it: a question carries its own `kind`, which spreading used to overwrite
the discriminator the routes dispatch on, so every answer was refused with a 409.

The web session must never call `settle()` — it always has a real informant, and settling
first would answer every question `unavailable` before that informant ever saw it.

When a tactician call fails after its retries, the decider plays a random legal action,
marks the `Decision` as a fallback, and emits `core_event/tactician_fallback`. The match
survives a provider outage, the substitution is on the record rather than hidden, and it
still cannot be illegal — the core enumerated the list either way. `MatchResult.fallbacks`
counts them separately.

### Agent contracts

`src/automa/contracts.py` holds every agent's output type — `TacticianResponse`,
`StrategistPolicy`, `CompilerOutput` — in one file, so the whole surface where a model
can influence the game is reviewable at a glance. Pass one as `output_format=` to
`call_llm`; it comes back validated on `result.parsed`. The module imports only Pydantic,
so `automa.core` may depend on it without breaching invariants 1 and 2.

Do not add a second structured-output layer (e.g. `instructor`). Both providers cover it
natively — `client.messages.parse` on Anthropic, `response_schema` on Gemini — and
`call_llm` already removes parsing from every caller, so instructor was evaluated and
dropped as redundant. If it is ever reintroduced, disable its retries: `call_llm` owns
retry-with-feedback, and a nested loop would hide attempts from the trajectory, breaking
invariant 3.

**Models.** Every role defaults to `google:gemini-3.5-flash-lite` (free tier), set once as
`config.DEFAULT_MODEL`. Resolution order is `AUTOMA_MODEL_<ROLE>` → `AUTOMA_MODEL` →
`DEFAULT_MODELS`, so moving the whole system, or one agent, needs no code change. Keep all
roles on one model unless there is a reason not to: the Phase 3 baseline shares the agents'
model, which is what makes that comparison a test of the architecture rather than of the
model. A model named in either variable should also have an entry in `llm/pricing.py`, or
its cost logs as `unpriced_model` instead of a number.

Rate limits and availability, not cost, are the binding constraint. Measured on
`gemini-3.6-flash`: two real 503s inside a three-call Nim match (retried, recovered on
attempt 3), and per-call latency of 3.1s median / 50.3s worst across ten DNUP turns.
**The whole Gemini 2.5 family is retired for new API keys** — `gemini-2.5-flash`,
`-flash-lite` and `-pro` all return `404 ... no longer available to new users` on
`generateContent`. Confirmed by direct probe, not inferred. Do not reintroduce any of them,
and note the trap: all three still appear in `models.list` for a key that cannot call them,
so enumerating a model is never evidence that it will run. Only a real `generateContent`
proves availability. A key from a Google project predating the retirement keeps 2.5 access;
a freshly issued key does not.

### How the invariants are enforced

Invariants 1 and 2 are checked by `tests/test_core_is_llm_free.py`, which AST-walks every
module under `src/automa/core/` and fails if one imports `anthropic`, `automa.llm`, or any
other provider SDK. If that test starts failing, the project's central architectural claim
has stopped being true — fix the import, do not relax the test. Invariant 3 holds because
`call_llm` is the only function that talks to a model.

Invariant 4 is the same walk over the same files but a different claim, so it lives in
`tests/test_core_is_game_agnostic.py`; both share the walker in `tests/_ast_imports.py`.
Read precisely, invariant 4 forbids a *static* dependency: game code obviously runs, since
the core calls it to enumerate legal actions every turn, but nothing under `automa.core` may
name a particular game. `automa/core/loader.py` is where that line is drawn — it resolves
`automa.games.<name>` through `importlib` at runtime, and the test is what stops anyone
"simplifying" that into a plain import.

Invariant 5 is enforced by `automa/catalog/vocabulary.py`, whose `VOCABULARY` is a closed
set of twelve typed operations. A card's effect is a list of those operations, and a
description that cannot be expressed in them is *parked* for a person rather than
approximated — the one place this architecture forbids a model from inventing behaviour.
Nothing evaluates a string, and no agent emits anything but data that has to validate.
Neither Phase 1 game exercises it (Nim has no cards; DNUP's cards are value pairs); Sushi
Go does.

Adding an operation to that set is a deliberate act by a person editing that file, never
something that can happen during a match. Note the trap the vocabulary carries: four
fields — `count_field`, `flag`, `resource`, `category` — hold a *name* rather than a
number, and it is the only place a catalog and a module can disagree while both stay
valid. The disagreement never raises; the module looks for a name nobody wrote and its
rule silently never fires. Modules declare `TERMS` so the mismatch is caught, and
`tests/test_count_field_agreement.py` is what keeps that true.

The `pending` convention is still used by no shipped game — Sushi Go expresses simultaneous
selection through hidden sequential choice, not `pending` — so it remains exercised only by
the deliberately broken modules in `tests/test_selfplay.py`. An untested convention would
be worse than none.

## Git

**The user handles all commits in this repository. Never run `git commit`.** Finish the
work, run the tests and lint, leave the changes in the working tree, and report what
changed — the user stages and commits. Do not run `git add` on their behalf either, so
their staging decisions stay open. Reading git state (`status`, `log`, `diff`) is fine.

Commit boundaries and messages are how the user narrates this project's history, and for
a hackathon submission that history is itself a graded deliverable.

## Working phase by phase

The plan is executed one phase at a time (Phase 0 → Phase 9), each with a strict exit
criterion in `plan.md` — do not advance to the next phase's work until the current one's
exit criterion is met. Phase 3 (baseline + eval harness) and Phase 8 (held-out game + final
deliverables) must never be cut for time; if scope must shrink, cut Phase 9, then Phase 6,
then reduce Phase 5 to one game (see the Effort distribution table in `plan.md`).

The **Improvement Changelog** table in `plan.md` must be filled immediately after running
`python -m automa.eval.run` for a version — never reconstructed later — and must include at least
one experiment that was tried and discarded, with what was tried and why it was dropped.
