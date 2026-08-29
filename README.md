# Automa — an AI opponent for physical board games

> **Status: Phase 3 of 9 complete.** Two hand-written games, a playable match loop with
> the Tactician, and a measured baseline-versus-agent comparison. The Improvement
> Changelog below carries real numbers; the full write-up (intended user, bottleneck,
> value, failure mode, hot take) is Phase 8. See `plan.md` for the execution plan and
> `CLAUDE.md` for the invariants.

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

## Improvement Changelog

Every number below comes from a committed result file and can be regenerated with the
commands in [Evaluation](#evaluation). Entries are written immediately after the run
that produced them.

| Stage | What was tried and why | Evidence | Decision / learning |
|---|---|---|---|
| **Baseline** | One LLM playing straight from a written rules brief plus the current board. No deterministic core, no enumeration of legal moves — it reads the rules and names its move. This is the obvious way to build the thing, so it is what the architecture has to beat. Same model as the agent (`gemini-3.5-flash-lite`), a rules brief written by a *different* model reading the rulebook, and no penalty for formatting mistakes. | **Nim** (10 matches): 0 interventions, 10/10 clean matches, 5/10 wins vs random. **DNUP** (3 matches): **20.7 illegal moves per match**, 0/3 clean, first correction needed after **0.3 turns**, 0/3 wins. **Case battery** (16 fixed positions): 15/16 legal; 3/6 optimal on the solved Nim positions. Cost $0.0003–0.0009 per turn.<br>[`phase3_baseline.json`](docs/validation/phase3_baseline.json) · [full write-up](docs/phase3_results.md) | Established the starting point — and immediately complicated the thesis. The baseline is **not** uniformly bad: on Nim it never once proposed an illegal move, so for a game with three action shapes and no hidden state, the whole architecture buys nothing. It collapses on DNUP, needing a correction before the first turn is out. So the claim worth defending is narrower and more honest than "LLMs can't follow rules": the deterministic core earns its place as game complexity rises, and a project that only ever tested on Nim would have concluded the opposite. Its Nim mistakes are also the wrong *kind* of mistake to fix with enumeration — "reducing heap 0 from 6 to 1 makes the nim-sum zero" is legal, confidently argued, and arithmetically false. |
| **V1 — enumerate, then choose** | The deterministic core enumerates every legal action from the game module and the Tactician returns an *index* into that list. Same model, same rules, same seeds; the only change is that the model picks from a list instead of naming a move. If rule hallucination is structural rather than a prompting problem, this should remove it entirely and change nothing else. | **DNUP** (3 matches, 513 turns): **0 interventions**, 3/3 clean, 2/3 wins vs 0/3. **Nim**: 0 interventions, 6/10 wins. **Case battery**: 16/16 legal — but **1/6 optimal on Nim, against the baseline's 3/6**. Cost fell to $0.000291/turn on DNUP from $0.000868.<br>[`phase3_agent.json`](docs/validation/phase3_agent.json) · [comparison](docs/validation/phase3_comparison.md) · [full write-up](docs/phase3_results.md) | Kept — but read the two halves of this result very differently. Illegal moves went from 62 to 0, and that outcome was fixed before the run started: the Tactician never names a move, it is handed a list the core has already filtered to legal moves and replies with a number, so an illegal move is not something it can output. Counting them is like counting black cards drawn from a deck of red ones. It does not show the model understood DNUP better; it shows the model was never in a position to break the rules. The finding worth having is the one pointing the other way: **enumeration appears to have made decision quality worse**, 3/6 → 1/6 optimal on solved Nim positions. n=6, so this is a signal and not a result, but the direction is suggestive — handed a numbered menu, the model argues about options ("take all 3 from heap B to balance the remaining heaps") instead of reasoning about the board. Removing the possibility of an illegal move may also remove the pressure to think about why a move is good. That is the Strategist's problem in Phase 6, and this is now the number to beat. |
| **V2 — compile the module instead of writing it** | Replace the hand-written game module with one a model writes from the submitted rule material: 8 scanned rulebook pages plus a card list, compiled against the fixed interface, then put through a static scan, a subprocess sandbox, 200 matches of random self-play and a human approval gate. This is the product claim — configure a game once, play it forever — and until now nothing had demonstrated it. | Compiled on **1 attempt**, $0.35, 28.6k output tokens. Passes `conforms`, 200/200 self-play matches, and the same parametrised contract and view tests the hand-written games face. A Tactician then played a full match on it: **79 turns, 40 calls, $0.0096, 0 fallbacks, 0 interventions**. An earlier run needed 2 attempts, repairing a real defect from feedback that named the seed and ply.<br>[full write-up](docs/phase4_results.md) · [validation](docs/validation/phase4_validation.json) | Kept. The compiler independently reached the same rules — and two of its stated assumptions are edge cases I had to reason through by hand (a play area is always empty when you play into it; receiving a returned set means you did not empty your hand). **The most valuable output was not the module.** Its first attempt passed 200 matches of self-play while crashing the instant anyone looked at the board: `describe_state` was the one contract method my harness never called. Three phases of hand-written games never exposed that, because both of mine happened to render fine. A module written by something that had never seen my conventions was a better test of the validator than either game I wrote. |

*Further entries land as later phases are measured.*

**Still owed: a removed experiment.** The submission requires at least one entry that was
tried and abandoned, and this changelog does not yet have a strong one. The nearest
candidate is a model comparison rather than a design change: `gemini-3.6-flash` was
A/B'd against `gemini-3.5-flash-lite` over ten identical DNUP decisions and dropped —
it was faster in total (73s vs 153s, flash-lite having a heavy latency tail) but emitted
736 output tokens per decision against flash-lite's 39, because it reasons at length and
reasoning bills as output. That is roughly 11× the cost, and ~22× once its promotional
pricing ends. Measurements in [`docs/phase2_results.md`](docs/phase2_results.md).
`plan.md` expects the real one from Phase 6: if the Strategist does not improve win rate
against a random opponent, it becomes a removed experiment rather than a feature.

**Two cautions on reading the table.** The primary metric — mean turns before an
intervention — is capped by match length, and the agent's DNUP matches happened to run
longer (247/123/143 turns) than the baseline's (124/60/120). Quoting the ratio
(0.3 → 171) would flatter the result; the honest statement is *62 corrections needed
versus none*. And the agent being cheaper per turn despite making more calls is not a
model difference: the baseline re-sends the entire 8.6k-character rules brief on every
single turn, because it has no memory of the rules between calls.

## Evaluation

Both arms answer the same fixed positions and play the same seeded matches, so the two
result files are directly comparable.

```bash
.venv\Scripts\python -m automa.eval.run --target baseline --workers 4 \
    --json docs/validation/phase3_baseline.json
.venv\Scripts\python -m automa.eval.run --target agent --workers 4 \
    --json docs/validation/phase3_agent.json
.venv\Scripts\python -m automa.eval.report docs/validation/phase3_*.json
```

Roughly 20 minutes and under $0.20 per arm on the Gemini free-to-cheap tier. The
baseline's rules brief lives at `src/automa/eval/rules/`, committed so the fairness of
the comparison can be audited rather than taken on trust.

## Setup

Requires Python 3.11+ (developed and pinned against 3.12).

```bash
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
copy .env.example .env      # then add your GEMINI_API_KEY
```

Every role runs on `google:gemini-3.5-flash-lite` by default, so a Gemini API key is all
you need. Change the model without touching code by setting `AUTOMA_MODEL` in `.env`, or
`AUTOMA_MODEL_<ROLE>` to move one agent on its own. The Anthropic backend is fully
implemented too — point a role at it with
`AUTOMA_MODEL_<ROLE>=anthropic:claude-opus-5` and set `ANTHROPIC_API_KEY`.

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
| `src/automa/llm/providers/` | Provider seam. Gemini and Anthropic both implemented. |
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
