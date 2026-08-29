# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project state

This repository currently contains only `plan.md`, the full execution plan for the project.
No code, folder structure, or dependency manifest exists yet — Phase 0 (skeleton) has not
been started. Before writing any code, re-read `plan.md` in full; it is the authoritative
spec for architecture, phase scope, and exit criteria. Do not paraphrase or summarize it away
— refer back to it directly each session.

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

## Planned structure and commands (per `plan.md`, not yet built)

Phase 0 establishes: `core/`, `compiler/`, `agents/`, `games/`, `eval/`, `trajectories/`, and
a `pyproject.toml` with pinned versions. Once built:

- `python -m eval.run --target baseline` and `--target agent` run the same fixed case
  battery against the baseline and the Automa solution and produce comparable JSON output.
- The game module contract is fixed: `initial_state()`, `legal_actions(state)`,
  `apply(state, action)`, `is_terminal(state)`, `winner(state)`, `describe_state(state, viewer)`.
- Every LLM call must go through a single wrapper that logs agent name, full prompt, full
  response, tokens, cost, latency, and retries to a per-match JSONL trajectory file.

## Working phase by phase

The plan is executed one phase at a time (Phase 0 → Phase 9), each with a strict exit
criterion in `plan.md` — do not advance to the next phase's work until the current one's
exit criterion is met. Phase 3 (baseline + eval harness) and Phase 8 (held-out game + final
deliverables) must never be cut for time; if scope must shrink, cut Phase 9, then Phase 6,
then reduce Phase 5 to one game (see the Effort distribution table in `plan.md`).

The **Improvement Changelog** table in `plan.md` must be filled immediately after running
`python -m eval.run` for a version — never reconstructed later — and must include at least
one experiment that was tried and discarded, with what was tried and why it was dropped.
