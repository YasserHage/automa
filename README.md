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
it receives Automa's own view of the board, the active policy, and a *numbered list of
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
| **V3 — learn the cards at the table** | Sushi Go, compiled from its rulebook, with card effects deliberately kept *out* of the module: it reads them from a catalog at runtime and declares `unknown` for a card nobody has described. The first time a card type is played the match pauses, asks the person in plain words, and a Cataloguer agent turns their sentence into typed operations from a closed vocabulary. This is invariant 5 made real — card effects as executable data, never prose read at runtime — and the answer to "games with dozens of cards do not need full description at setup". | Compiled on **3 attempts**, $1.04, after a first run of 4 failed attempts ($1.51). Validation: **300/300 matches terminal, 0 violations**, exactly 60 plies each. Ingestion measured deterministically through `play_match`: first match with an empty catalog puts **12 questions** to a person, second match **0** — one per card *type*, not per card. Four live matches resolved 11 questions across 21 plies without error; none was played through to a terminal state. <br>[full write-up](docs/phase5_results.md) · [`catalog/sushigo.json`](catalog/sushigo.json) | Kept. The exit criterion is **partially met** — the loop is built and counted, but no complete live match exists yet, and saying otherwise would be the easiest lie in this document. **The compiler was a better test of my harness than my own games were, again.** Two of the four first-run failures were a deadlock I had built: `random_playout` knew nothing about the `unknown` key, so a module that correctly declared missing information and stopped was structurally unvalidatable. The model escaped the only way the contract allowed — inventing a no-op `wait` action, which then could not advance `ply`. Self-play now answers every question the way a person who cannot say would. And when a later attempt was told it had declared too many unknowns at once, it changed *when* it asks — only for a card played face-up — and wrote that down as an assumption. That is the phase's mechanism, reached by the compiler because the harness pushed back. |
| **V4 — draft the catalog from the rulebook first** | The table is the wrong place to learn something the rulebook already says. A separate Catalog Builder agent reads the same material the compiler read and records what each card is worth, leaving only what the material does not state to be asked in play. Deliberately a **separate process** from compilation: one produces a module, the other data, they fail separately, and a game whose values are printed on the cards compiles fine and drafts nothing. | **11 of 12 card types drafted** from the Sushi Go rulebook for **$0.006**, against $1.04 to compile — three orders of magnitude cheaper, and it moves 11 of the 12 questions out of the match. The 12th (chopsticks) is left absent and asked once at the table. <br>[full write-up](docs/phase5_results.md) | Kept, and it exposed the phase's most instructive class of bug. Four vocabulary fields carry a **name** both sides must agree on (`count_field`, `flag`, `resource`, `category`), and a disagreement never raises — the module looks for a name nobody wrote and its rule silently never fires. Maki was catalogued as `count_field: "maki_icons"` against a module testing `"icons"`: three separate majority contests instead of one, **median winning score 61 against 49**, win rate a healthy 50/50 throughout. Chopsticks was catalogued as `flag: "extra_card_from_hand"` against a module testing `"chopsticks"`: **0 chopsticks actions offered across 60 matches, against 1,435** when named correctly. I fixed the first by guarding one field, and the second arrived within the hour on another — so the guard is now general, and modules declare every name their rules look for. |
| **V5 — play with the deck that is on the table** | Every module dealt itself a shuffled hand, so a match was a *simulation* of the game in front of the person rather than the game itself. A module now declares the components it is missing — `{"kind": "components", "slot": "hands.automa", "count": 13}` — and the person reads the real cards off the table. The seeded shuffle survives only as what happens when nobody can say, which is the path `--simulate`, `--auto` and every self-play playout take. | DNUP asks two questions per round and plays exactly the hands it is given; orientation, which is the hidden state the game turns on, is taken as read rather than invented. 500/500 self-play matches still terminal with 0 violations, and the unanswered path deals byte-identically to the old code, so no seeded fixture or eval case moved. `tests/test_components_supply.py` · `tests/test_table_informant.py` | Kept. Two decisions did the work. **The answer is a list of plain strings the module interprets** — nothing between the person and the module understands cards, so the core and the informant stay generic for games nobody has written. And **the count is the only validation**: a name the game has never heard of is passed straight through, because for a game whose cards are learned that is not an error, it is how a new card arrives. The alternative — only asking when no seed was passed — would have cost nothing today and hidden the whole path from self-play, which is precisely how both Phase 5 silent-failure bugs happened. |
| **Reverted — the Tactician's one-line justification** | `plan.md` specifies that the Tactician *"returns an index + one-line justification"*, and it did, from Phase 2 onwards: every decision came back with a sentence explaining itself, which was printed in the CLI and stored in the trajectory. The intent was an auditable decision record. Removed after four phases of it running in production. | Measured over **382 real decisions** across DNUP and Sushi Go: median **39 output tokens** per response, of which the justification was a median of 78 characters — roughly **20 tokens, about half the output of every single turn**. Nothing downstream ever read it: not the core, not the eval harness, not the metrics. And Phase 3 had already found the lines actively misleading — on solved Nim positions the agent scored **1/6 optimal** while explaining each choice fluently and wrongly (*"take all 3 from heap B to balance the remaining heaps"*). <br>[Phase 3 write-up](docs/phase3_results.md) | **Dropped.** It cost half the output tokens of every turn on a tier where latency is the binding constraint (3.1s median, 50.3s worst), bought nothing any code consumed, and created a false impression of reasoning — the most expensive thing on the list. The honest reading is that it was **documentation of a decision, not the decision itself**: the model picks an index and then writes a sentence about it, so the sentence is a post-hoc rationalisation with no causal link to the choice. What makes a Tactician move auditable is kept and is stronger: the trajectory holds the exact prompt, the numbered list of legal actions, and the index returned — enough to replay the decision, which a sentence never was. The cost is a thinner agent-trajectory deliverable, and that is the reason to record this rather than quietly ship it. The Baseline keeps its justification: it *names* a move rather than choosing an index, and its Phase 3 numbers are already measured and compared. |
| **V6 — a web surface over the same calls** | Automa did several things well that were invisible unless you read a terminal transcript: it enumerates every legal action, it compiles a rulebook behind a human approval gate, it asks what is on the table and what a card does and never asks twice. A local web UI gives those a surface. `play_match` is a blocking loop with no `step()` and no coroutine anywhere, so rather than re-deriving it from request handlers — which would discard the `IllegalDecision` guard, the intervention accounting and every trajectory event — a match runs **unmodified on a daemon thread**, with its decider and both askers blocked on queues that HTTP handlers feed, and SSE carrying events back. | A complete offline Nim match driven through the real HTTP API and SSE: **8 turns, 8 plies, 0 LLM calls, $0**, and the trajectory it wrote replays unchanged under `python -m automa.trajectory.show`, `human_checkpoint` events included. The physical-deck path works end to end over HTTP: a deliberate miscount is re-asked carrying its explanatory note, a correct 13-card answer is accepted, and the module builds 14 legal actions from the cards read off the table. Catalog editor: an invalid effect refused **422** with the store's own problem strings verbatim, a valid one stored, park and forget round-tripping. Suite **467 → 555 passed**, ruff clean. **Not yet verified live: a Tactician match and a compile through the UI.** | Kept — and the interface immediately found two silent bugs of exactly the kind this project keeps producing. **`pending` was built by spreading the question's payload**, which carries its own `kind` (`components`/`card_effect`) and therefore overwrote the `question`/`turn` discriminator the routes dispatch on: `/answer` returned 409 for *every question in every match*, so nothing could ever be answered from the browser. The service tests missed it because they call `ask()` and check what it returns, never the shape the route validates against. **And the games list was keyed on the module's `NAME`** — but `dnup.py` and `dnup_compiled.py` both declare `NAME = "dnup"`, producing two identical rows, one of which addressed a module nobody could reach, plus a reload guard that could miss. The loader resolves the *module* name, so that is the identity. Both are the Phase 4 `describe_state` shape again: a seam nothing exercised until a second consumer showed up. The genuinely new capability is the **catalog audit banner** — stored entries that no longer validate against the current vocabulary, which the CLI has no surface for at all. |
*Further entries land as later phases are measured.*

**An earlier removed experiment**, a model comparison rather than a design change:
`gemini-3.6-flash` was A/B'd against `gemini-3.5-flash-lite` over ten identical DNUP
decisions and dropped — it was faster in total (73s vs 153s, flash-lite having a heavy
latency tail) but emitted 736 output tokens per decision against flash-lite's 39, because
it reasons at length and reasoning bills as output. That is roughly 11× the cost, and
~22× once its promotional pricing ends. Measurements in
[`docs/phase2_results.md`](docs/phase2_results.md).

**Two entries above are things that were built and then taken out**, which the submission asks for explicitly. The Tactician's justification is the more instructive of the two: it was in the original specification, it ran for four phases, and it was removed on evidence rather than taste. The second is smaller and lives inside V4.

**A removed experiment: validating the whole catalog draft at once.** When the Catalog
Builder first returned `score_per_set` with a set size and no points for tempura and
sashimi, the obvious fix was a validation callback on the call, so `call_llm` would retry
with the specific problem fed back — the same retry-with-feedback the Cataloguer uses for
a card described at the table. It made things worse. `call_llm` gives up after its
attempts and raises, so one card missing a field threw away the other eleven that had come
back correct. Eleven good entries lost to two bad ones is a worse failure than the one it
fixed. Replaced with a bounded repair round: the malformed cards are named and re-asked on
their own, once, and anything still malformed is left **absent** so the table asks — never
parked, because parking marks a card described and a model's slip is not the card telling
us it cannot be expressed. Kept from the discarded version: the insight that a malformed
answer and an inexpressible card are different things and must not share a path.

**A reverted locked decision: Automa's hand was open to the human.** `plan.md` locked it as *"Open — visible to the human"* on the reasoning that somebody has to play Automa's side at the physical table, so they would see its cards regardless, and showing them made every move auditable. Reversed on 2026-08-30 after playing with it: on screen it simply hands the opponent perfect information and spoils the match. The physical argument was about the table, not the interface, and auditability is served better by the trajectory, which records every state Automa saw. Recorded as a reversal rather than an edit because it is a locked decision, and `plan.md` says a change to one invalidates completed work — here it invalidates the Phase 1–5 view tests, which were rewritten to assert the opposite. Clarified in V5: this is a decision about what the interface keeps re-displaying, not an information barrier against the person dealing the cards — they read Automa's opening hand off the table to enter it, because Automa cannot see the table and somebody has to.

Phase 6 (Strategist) and Phase 7 (state reconciliation) were **abandoned for time**, in the
cut order `plan.md` specifies. The cost is on the record: V1's finding that enumeration
*lowered* decision quality is the number the Strategist existed to beat, and it stands
unanswered.


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

.venv\Scripts\python -m automa.scripts.play --game dnup             # play with your own deck
.venv\Scripts\python -m automa.scripts.play --game dnup --simulate  # let the game deal, for testing
```

**Playing uses the cards on your table.** The game asks what was dealt, you read it out,
and only the number of cards is checked — a name it does not recognise is passed through,
not refused. Press Enter if you cannot say and it deals its own. `--seed` and `--simulate`
exist for testing a game without the physical deck in front of you.

The first three are also installed as `automa-hello`, `automa-show` and `automa-play`.

## Web UI

```bash
.venv\Scripts\python -m automa.scripts.web     # or: automa-web  ->  http://127.0.0.1:8765
```

**The CLI stays canonical.** No measured result runs through the UI: `automa.eval`, the
reproduction guide and every number in the changelog above are produced by the commands
in the previous section, unchanged. The web layer is a thin adapter over the same
functions — routes do HTTP, services hold the calls, and the deterministic core is
untouched.

Four screens:

| Screen | What it does |
|---|---|
| **Games** | Every installed module with its catalog counts, a `conforms` badge, and buttons to play, validate (self-play, no model) or reload it. |
| **Add a game** | Upload a rulebook, see what was actually read from it *before* paying for a compile, watch the attempts stream in with a running cost, then read the summary and approve. Nothing reaches `src/automa/games/` until you click. |
| **Catalog** | Known and parked cards, an editor generated from the effect vocabulary itself, and a banner for stored entries that no longer validate — which the CLI has no equivalent for. |
| **Play** | A chat transcript beside a live state panel. Legal actions are buttons, never a text box; questions about the table and about a card are drawn distinctly from moves. |

**Loopback only, and it means it.** The server imports and executes compiled game modules
in-process and has no authentication, so a non-loopback `--host` is refused outright
rather than warned about. Set `AUTOMA_WEB_ALLOW_REMOTE=1` only if you have thought about
what that means.

**What costs money.** Offline play makes zero API calls — a seeded random opponent,
nothing asked, nothing saved — and reading the cards off your table costs nothing either
way, because no model is involved in that path. Live play is one call per Automa turn.
Compiling runs for **minutes, not seconds**, and the screen shows a running total rather
than a bare spinner.

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
| `src/automa/web/` | Local web UI. An additional surface — routes are thin, services are pure and offline-testable, and nothing measured runs through it. |
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
