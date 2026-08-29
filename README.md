# Automa — an AI opponent for physical board games

> **Status: Phase 0 of 9.** The skeleton and logging spine are in place. There is no
> game logic yet. This README is a stub; the full write-up (intended user, bottleneck,
> value, Improvement Changelog, failure mode, hot take) is Phase 8. See `plan.md` for
> the execution plan and `CLAUDE.md` for the invariants.

You play a physical board or card game at the table with real components. Automa plays
the other side.

Using a raw LLM for this fails visibly: it invents rules that do not exist, loses track
of state within a few turns, and cannot handle games it was never trained on. Automa is
generic — you configure a game once by submitting its rule material, and the system
compiles an executable module for it.

## Architecture in one paragraph

A **deterministic core** (pure Python, no model) holds match state, enumerates the legal
actions from the game module, and applies transitions. A **Tactician** runs every turn:
it receives the public board, Automa's hand, the active policy, and a *numbered list of
legal actions*, and returns an index. A **Strategist** runs rarely and produces a policy
rather than a move plan. Because the core enumerates and the model only picks an index,
rule hallucination is impossible by construction rather than by instruction.

## Setup

Requires Python 3.11+ (developed and pinned against 3.12).

```bash
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
copy .env.example .env      # then add your ANTHROPIC_API_KEY
```

## Commands

```bash
.venv\Scripts\python -m pytest                    # full suite, offline, no API key needed
.venv\Scripts\python -m ruff check .              # lint
.venv\Scripts\python -m automa.scripts.hello_llm  # one real LLM call -> a trajectory
.venv\Scripts\python -m automa.trajectory.show trajectories\<file>.jsonl [--full]
```

The last two are also installed as `automa-hello` and `automa-show`.

## Layout

All code lives under one package, so the repository root stays to four directories:
`src/`, `tests/`, `docs/`, `trajectories/`.

| Path | What lives there |
|---|---|
| `src/automa/core/` | Deterministic zone. Never imports a model client — enforced by a test. |
| `src/automa/llm/` | The single call wrapper every model call passes through, plus pricing. |
| `src/automa/llm/providers/` | Provider seam. Anthropic implemented; Gemini stubbed. |
| `src/automa/trajectory/` | JSONL event log — one file per run — and a reader. |
| `src/automa/agents/` | Tactician (Phase 2), Strategist (Phase 6). |
| `src/automa/compiler/` | Rule material → executable game module (Phase 4). |
| `src/automa/games/` | Hand-written game modules (Phase 1). |
| `src/automa/eval/` | Baseline and evaluation harness (Phase 3). |
| `trajectories/` | Run output. Gitignored; representative runs are committed explicitly. |

Mind the near-identical pair: `src/automa/trajectory/` is the code, `trajectories/` is the
data it writes.

## Invariants

Enforced, not aspirational:

1. No LLM decides legality. The core enumerates; the LLM chooses an index.
2. No LLM writes state. Only the core applies transitions.
3. Every prompt sent and every response received are logged in `trajectories/`.
4. No game module is imported by the core. The core is game-agnostic.
5. Card effects are executable data structures, never prose interpreted at runtime.
6. No commercial rulebook or card text is committed to this repository.

Invariants 1 and 2 are checked by `tests/test_core_is_llm_free.py`, which fails the
build if anything under `core/` imports a model client. Invariant 3 holds because
`llm.call.call_llm` is the only function in the project that talks to a model.
