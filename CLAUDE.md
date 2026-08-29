# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project state

**Phase 0 is complete** (skeleton and logging spine); Phase 1 is next. Before writing any
code, re-read `plan.md` in full; it is the authoritative spec for architecture, phase scope,
and exit criteria. Do not paraphrase or summarize it away — refer back to it directly each
session.

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
  - **Tactician** (fast cycle, every turn): receives the public board view, Automa's hand,
    the active policy, and a numbered list of legal actions; returns an index + one-line
    justification.
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
| Automa's hand | Open — visible to the human | Physical logistics + auditability |
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
    providers/       vendor seam: base protocol, anthropic (done), gemini (stub)
  trajectory/        JSONL event log + `show` renderer   <- code
  agents/            Tactician (Phase 2), Strategist (Phase 6)
  games/             hand-written game modules (Phase 1)
  compiler/          rule material -> game module (Phase 4)
  eval/              baseline + harness (Phase 3)
  scripts/           runnable entry points
tests/
trajectories/        JSONL output, gitignored                <- data
```

Note `trajectory/` (code) versus `trajectories/` (output) — one character apart.

Environment: Python 3.12, `.venv` at the repo root, editable install.

```bash
.venv\Scripts\python -m pytest                        # offline, no API key needed
.venv\Scripts\python -m ruff check .
.venv\Scripts\python -m automa.scripts.hello_llm      # or: automa-hello
.venv\Scripts\python -m automa.trajectory.show <file> # or: automa-show
```

- `python -m automa.eval.run --target baseline` and `--target agent` (Phase 3) run the
  same fixed case battery against the baseline and the Automa solution and produce
  comparable JSON output.
- The game module contract is fixed: `initial_state()`, `legal_actions(state)`,
  `apply(state, action)`, `is_terminal(state)`, `winner(state)`, `describe_state(state, viewer)`.
- Every LLM call must go through `automa.llm.call_llm`, which logs agent name, full
  prompt, full response, tokens, cost, latency, and retries to a per-match JSONL
  trajectory. Never call a provider SDK directly from anywhere else.

### Agent contracts

`src/automa/contracts.py` holds every agent's output type — `TacticianResponse`,
`StrategistPolicy`, `CompilerOutput` — in one file, so the whole surface where a model
can influence the game is reviewable at a glance. Pass one as `output_format=` to
`call_llm`; it comes back validated on `result.parsed`. The module imports only Pydantic,
so `automa.core` may depend on it without breaching invariants 1 and 2.

Do not add a second structured-output layer (e.g. `instructor`) on the Anthropic path:
`client.messages.parse` covers it natively and `call_llm` already removes parsing from
every caller. If `instructor` is adopted for Gemini, disable its retries — `call_llm`
owns retry-with-feedback, and a nested loop would hide attempts from the trajectory,
breaking invariant 3. See the docstring in `llm/providers/gemini_provider.py`.

### How the invariants are enforced

Invariants 1 and 2 are checked by `tests/test_core_is_llm_free.py`, which AST-walks every
module under `src/automa/core/` and fails if one imports `anthropic`, `automa.llm`, or any
other provider SDK. If that test starts failing, the project's central architectural claim
has stopped being true — fix the import, do not relax the test. Invariant 3 holds because
`call_llm` is the only function that talks to a model.

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
