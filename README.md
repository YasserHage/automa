# Automa — an AI opponent for physical board games

**Everything in this repository was built during the hackathon[https://www.hackerearth.com/community/challenges/hackathon/micro1-frontier-engineering-challenge-2026/]** — code, prompts, games,
catalog and eval harness alike; there is no pre-existing project this extends.

## Problem

**Who has this problem.** Anyone who owns a two-player board or card game and wants to
play it without a second person at the table. Most published games have no solo mode —
the box assumes two people are present.

**The bottleneck.** The direct fix is to prompt an LLM with the rules and let it play the
other side, and that fails visibly: it invents rules that do not exist, loses track of
state within a few turns, and cannot handle a game it was never trained on. A hand-written
program per game would work, but does not scale — every game still on a shelf needs its
own, written by someone who already knows its rules.

**Why it matters.** Automa is generic instead: you submit a game's rule material once —
a rulebook PDF, pasted text, card photos — and a Compiler Agent produces an executable
module for it, so the same system plays Nim, DNUP or Sushi Go without being told
game-specific rules in code. You play at the table with the real components; Automa plays
the other side.

**Does it solve it well.** On DNUP — the game where a raw prompt fails hardest — the
agent needed **zero corrective interventions across all three matches**, against 62 for
the baseline (20.7 illegal moves per match), and the 16-case battery went **16/16 legal**.
Card ingestion is autonomous and degrades the way it should: an empty catalog puts **12
questions** to a person once, a second match on the same catalog puts **0**, and a
Catalog Builder agent drafts most of that catalog from the rulebook for **$0.006** before
anyone is asked anything at all.

It is not an unqualified win. Enumeration measurably **hurt** decision quality on Nim's
solved positions — 3/6 → 1/6 optimal — and that regression is reported in
[Primary Evaluation](#primary-evaluation) rather than smoothed over.

## Architecture

A **deterministic core** (pure Python, no model) holds match state, enumerates the legal
actions from the game module, and applies transitions. A **Tactician** runs every turn:
it receives Automa's own view of the board, the active policy, and a *numbered list of
legal actions*, and returns an index. Because the core enumerates and the model only picks an index,
rule hallucination is impossible by construction rather than by instruction.

That turn loop is the smallest part of the system. Three other mechanisms carry most of
the design, and each is where a changelog entry below came from:

- **The Compiler turns rule material into an executable module** (setup, once per game).
  A model reads the submitted rulebook and writes Python against a fixed interface,
  which is then put through four game-agnostic gates — a static scan, a subprocess
  sandbox, 200 matches of random self-play, and a human approval gate — with each
  failure fed back naming the seed and ply that reproduce it. No gate compares the
  result to a reference implementation, because in the product there is none: the whole
  point is compiling games nobody has implemented.
- **Card effects are data drawn from a closed vocabulary**, never prose read at runtime.
  A module knows the *rules*; a per-game catalog knows what each *card* does, as a list
  of typed operations validated against twelve permitted ops. A description that will
  not fit is **parked for a person rather than approximated** — the one place the design
  forbids a model from inventing behaviour. Two agents fill that catalog: a **Catalog
  Builder** drafts from the rulebook up front for fractions of a cent, and a
  **Cataloguer** turns a sentence someone says at the table into operations for whatever
  the material never stated.
- **The table is asked for what only the table knows.** A module declares the components
  it is missing and the person reads the real cards off the table, so a match is the
  game in front of you rather than a simulation of it. Both kinds of question — *which
  cards are these* and *what does this card do* — travel one `informant` seam, and
  supplying a fact is deliberately **not** counted as a corrective intervention, which
  is what keeps the primary metric honest. Nobody to ask means the module falls back to
  its own seeded deal, which is exactly what every offline self-play run takes.

Two boundaries are enforced by tests rather than convention: nothing under `core/` may
import a model client or name a specific game (games resolve by name at runtime), and
every model call in the project goes through a single wrapper that logs the full prompt,
response, tokens, cost and latency to a per-match JSONL trajectory. A provider seam sits
under that wrapper, so moving one agent — or the whole system — to another model is an
environment variable, not a code change.

## Primary Evaluation

**Primary metric: corrective interventions a person has to make.** This is what
`plan.md` names as the primary metric back in Phase 3 and the one that maps directly to
user value: every intervention is a moment the person at the table has to stop the match
and fix a move the system got wrong, which is the exact failure "Problem" above
describes a direct LLM prompt falling into. It is reported two ways below — per match,
and as mean turns survived before the first one. The per-match count leads because the
per-turn form is capped by match length, which is a confound the tables should not hide.
Both targets — the
**Simple Baseline** (one LLM, given a written rules brief and the current board, naming
its move directly) and the **Agent Solution** (this project: the deterministic core
enumerates legal actions, the Tactician returns an index) — ran the same 16-case
battery and the same match seeds, same model (`gemini-3.5-flash-lite`), so the only
variable is enumeration itself. Full numbers:
[`docs/validation/phase3_comparison.md`](docs/validation/phase3_comparison.md),
reproducible with the two commands under [Reproduction Guide](#run-the-baseline-and-the-agent-evaluation).

**DNUP — the headline result, where hidden state and a contested shared table give a
raw prompt room to invent an illegal move:**

| Metric | Simple Baseline | Agent Solution | Change |
|---|---|---|---|
| **Corrective interventions per match** — primary metric, stated without the length confound | 20.67 | **0** | 62 corrections across 3 matches → **0** |
| Turns before a corrective intervention (mean) — same metric, per-turn form | 0.3 | 171 | capped by match length; see below |
| Clean matches (no intervention needed) | 0/3 | 3/3 | +3 |
| Illegal moves proposed, case battery (16 fixed positions, both games) | 15/16 (94%) | 16/16 (100%) | +1 |
| Cost per turn | $0.000868 | $0.000291 | −66% |
| Median LLM call latency | 1,165 ms | 1,313 ms | +13% (more calls per turn under enumeration; not the binding cost — see below) |

**Nim — the honest control, not the headline: three action shapes, no hidden state, so
a raw prompt never had room to break a rule here either.**

| Metric | Simple Baseline | Agent Solution | Change |
|---|---|---|---|
| Interventions per match | 0 | 0 | no change — nothing to intervene on |
| Optimal moves, solved Nim positions (case battery) | 3/6 (50%) | 1/6 (17%) | **worse** — enumeration is not free; see V1 in the changelog |
| Wins vs. random opponent | 5/10 | 6/10 | +1 |
| Cost per turn | $0.000299 | $0.000182 | −39% |

**Why the second row is not the headline.** 0.3 → 171 turns looks like a 570× gain; it
isn't, because that form is capped by match length and the agent's matches happened to
run longer (247/123/143 turns vs. the baseline's 124/60/120). The honest statement is the
row above it — **62 corrective interventions versus zero** — which carries no length
confound, and it is why the tables lead with the per-match count. The agent being cheaper per turn
despite calling the model more often is not a model difference either: the baseline
re-sends the entire 8.6k-character rules brief on every single turn because it carries
no memory of the rules between calls, while the Tactician's per-turn prompt stays short.
And the Nim result is the one number in this table that should give pause rather than
comfort: enumeration removed every illegal move but measurably **hurt decision quality**
on Nim's solved positions (3/6 → 1/6 optimal) — see the V1 changelog row and Hot Take
below for why that is read as a real finding, not noise to explain away.

## Improvement Changelog

Every number below comes from a committed result file and can be regenerated with the
commands in [Reproduction Guide](#reproduction-guide). Entries are written immediately
after the run that produced them.

| Stage | What was tried and why | Evidence | Decision / learning |
|---|---|---|---|
| **Baseline** | One LLM playing straight from a written rules brief plus the current board. No deterministic core, no enumeration of legal moves — it reads the rules and names its move. This is the obvious way to build the thing, so it is what the architecture has to beat. Same model as the agent (`gemini-3.5-flash-lite`), a rules brief written by a *different* model reading the rulebook, and no penalty for formatting mistakes. | **Nim** (10 matches): 0 interventions, 10/10 clean matches, 5/10 wins vs random. **DNUP** (3 matches): **20.7 illegal moves per match**, 0/3 clean, first correction needed after **0.3 turns**, 0/3 wins. **Case battery** (16 fixed positions): 15/16 legal; 3/6 optimal on the solved Nim positions. Cost $0.0003–0.0009 per turn.<br>[`phase3_baseline.json`](docs/validation/phase3_baseline.json) · [full write-up](docs/phase3_results.md) | Established the starting point — and immediately complicated the thesis. The baseline is **not** uniformly bad: on Nim it never once proposed an illegal move, so for a game with three action shapes and no hidden state, the whole architecture buys nothing. It collapses on DNUP, needing a correction before the first turn is out. So the claim worth defending is narrower and more honest than "LLMs can't follow rules": the deterministic core earns its place as game complexity rises, and a project that only ever tested on Nim would have concluded the opposite. Its Nim mistakes are also the wrong *kind* of mistake to fix with enumeration — "reducing heap 0 from 6 to 1 makes the nim-sum zero" is legal, confidently argued, and arithmetically false. |
| **V1 — enumerate, then choose** | The deterministic core enumerates every legal action from the game module and the Tactician returns an *index* into that list. Same model, same rules, same seeds; the only change is that the model picks from a list instead of naming a move. If rule hallucination is structural rather than a prompting problem, this should remove it entirely and change nothing else. | **DNUP** (3 matches, 513 turns): **0 interventions**, 3/3 clean, 2/3 wins vs 0/3. **Nim**: 0 interventions, 6/10 wins. **Case battery**: 16/16 legal — but **1/6 optimal on Nim, against the baseline's 3/6**. Cost fell to $0.000291/turn on DNUP from $0.000868.<br>[`phase3_agent.json`](docs/validation/phase3_agent.json) · [comparison](docs/validation/phase3_comparison.md) · [full write-up](docs/phase3_results.md) | Kept — but read the two halves of this result very differently. Illegal moves went from 62 to 0, and that outcome was fixed before the run started: the Tactician never names a move, it is handed a list the core has already filtered to legal moves and replies with a number, so an illegal move is not something it can output. Counting them is like counting black cards drawn from a deck of red ones. It does not show the model understood DNUP better; it shows the model was never in a position to break the rules. The finding worth having is the one pointing the other way: **enumeration appears to have made decision quality worse**, 3/6 → 1/6 optimal on solved Nim positions. n=6, so this is a signal and not a result, but the direction is suggestive — handed a numbered menu, the model argues about options ("take all 3 from heap B to balance the remaining heaps") instead of reasoning about the board. Removing the possibility of an illegal move may also remove the pressure to think about why a move is good. A Strategist component was meant to weigh policy against exactly this regression — it was never built, so the finding stands unaddressed. |
| **V2 — compile the module instead of writing it** | Replace the hand-written game module with one a model writes from the submitted rule material: 8 scanned rulebook pages plus a card list, compiled against the fixed interface, then put through a static scan, a subprocess sandbox, 200 matches of random self-play and a human approval gate. This is the product claim — configure a game once, play it forever — and until now nothing had demonstrated it. | Compiled on **1 attempt**, $0.35, 28.6k output tokens. Passes `conforms`, 200/200 self-play matches, and the same parametrised contract and view tests the hand-written games face. A Tactician then played a full match on it: **79 turns, 40 calls, $0.0096, 0 fallbacks, 0 interventions**. An earlier run needed 2 attempts, repairing a real defect from feedback that named the seed and ply.<br>[full write-up](docs/phase4_results.md) · [validation](docs/validation/phase4_validation.json) | Kept. The compiler independently reached the same rules — and two of its stated assumptions are edge cases I had to reason through by hand (a play area is always empty when you play into it; receiving a returned set means you did not empty your hand). **The most valuable output was not the module.** Its first attempt passed 200 matches of self-play while crashing the instant anyone looked at the board: `describe_state` was the one contract method my harness never called. Every hand-written game so far never exposed that, because both happened to render fine. A module written by something that had never seen my conventions was a better test of the validator than either game I wrote. |
| **V3 — learn the cards at the table** | Sushi Go, compiled from its rulebook, with card effects deliberately kept *out* of the module: it reads them from a catalog at runtime and declares `unknown` for a card nobody has described. The first time a card type is played the match pauses, asks the person in plain words, and a Cataloguer agent turns their sentence into typed operations from a closed vocabulary. This is invariant 5 made real — card effects as executable data, never prose read at runtime — and the answer to "games with dozens of cards do not need full description at setup". | Compiled on **3 attempts**, $1.04, after a first run of 4 failed attempts ($1.51). Validation: **300/300 matches terminal, 0 violations**, exactly 60 plies each. Ingestion measured deterministically through `play_match`: first match with an empty catalog puts **12 questions** to a person, second match **0** — one per card *type*, not per card. A complete live match has since been played end to end — 27 turns, 54 plies, 7 facts supplied — but its winner was decided by a pudding-count tiebreak, not points: see Main Failure Mode. <br>[full write-up](docs/phase5_results.md) · [`catalog/sushigo.json`](catalog/sushigo.json) | Kept. **The compiler was a better test of my harness than my own games were, again.** Two of the four first-run failures were a deadlock I had built: `random_playout` knew nothing about the `unknown` key, so a module that correctly declared missing information and stopped was structurally unvalidatable. The model escaped the only way the contract allowed — inventing a no-op `wait` action, which then could not advance `ply`. Self-play now answers every question the way a person who cannot say would. And when a later attempt was told it had declared too many unknowns at once, it changed *when* it asks — only for a card played face-up — and wrote that down as an assumption. That is the resulting mechanism, reached by the compiler because the harness pushed back. |
| **V4 — draft the catalog from the rulebook first** | The table is the wrong place to learn something the rulebook already says. A separate Catalog Builder agent reads the same material the compiler read and records what each card is worth, leaving only what the material does not state to be asked in play. Deliberately a **separate process** from compilation: one produces a module, the other data, they fail separately, and a game whose values are printed on the cards compiles fine and drafts nothing. | **11 of 12 card types drafted** from the Sushi Go rulebook for **$0.006**, against $1.04 to compile — three orders of magnitude cheaper, and it moves 11 of the 12 questions out of the match. The 12th (chopsticks) is left absent and asked once at the table. <br>[full write-up](docs/phase5_results.md) | Kept, and it exposed the most instructive class of bug in the whole vocabulary. Four vocabulary fields carry a **name** both sides must agree on (`count_field`, `flag`, `resource`, `category`), and a disagreement never raises — the module looks for a name nobody wrote and its rule silently never fires. Maki was catalogued as `count_field: "maki_icons"` against a module testing `"icons"`: three separate majority contests instead of one, **median winning score 61 against 49**, win rate a healthy 50/50 throughout. Chopsticks was catalogued as `flag: "extra_card_from_hand"` against a module testing `"chopsticks"`: **0 chopsticks actions offered across 60 matches, against 1,435** when named correctly. I fixed the first by guarding one field, and the second arrived within the hour on another — so the guard is now general, and modules declare every name their rules look for. |
| **V5 — play with the deck that is on the table** | Every module dealt itself a shuffled hand, so a match was a *simulation* of the game in front of the person rather than the game itself. A module now declares the components it is missing — `{"kind": "components", "slot": "hands.automa", "count": 13}` — and the person reads the real cards off the table. The seeded shuffle survives only as what happens when nobody can say, which is the path `--simulate`, `--auto` and every self-play playout take. | DNUP asks two questions per round and plays exactly the hands it is given; orientation, which is the hidden state the game turns on, is taken as read rather than invented. 500/500 self-play matches still terminal with 0 violations, and the unanswered path deals byte-identically to the old code, so no seeded fixture or eval case moved. `tests/test_components_supply.py` · `tests/test_table_informant.py` | Kept. Two decisions did the work. **The answer is a list of plain strings the module interprets** — nothing between the person and the module understands cards, so the core and the informant stay generic for games nobody has written. And **the count is the only validation**: a name the game has never heard of is passed straight through, because for a game whose cards are learned that is not an error, it is how a new card arrives. The alternative — only asking when no seed was passed — would have cost nothing today and hidden the whole path from self-play, which is precisely how both silent-failure bugs above happened. |
| **Reverted — the Tactician's one-line justification** | The Tactician originally returned an index plus a one-line justification: every decision came back with a sentence explaining itself, which was printed in the CLI and stored in the trajectory. The intent was an auditable decision record. Removed after it ran in production across every game built since. | Measured over **382 real decisions** across DNUP and Sushi Go: median **39 output tokens** per response, of which the justification was a median of 78 characters — roughly **20 tokens, about half the output of every single turn**. Nothing downstream ever read it: not the core, not the eval harness, not the metrics. And the baseline comparison above had already found the lines actively misleading — on solved Nim positions the agent scored **1/6 optimal** while explaining each choice fluently and wrongly (*"take all 3 from heap B to balance the remaining heaps"*). <br>[baseline write-up](docs/phase3_results.md) | **Dropped.** It cost half the output tokens of every turn on a tier where latency is the binding constraint (3.1s median, 50.3s worst), bought nothing any code consumed, and created a false impression of reasoning — the most expensive thing on the list. The honest reading is that it was **documentation of a decision, not the decision itself**: the model picks an index and then writes a sentence about it, so the sentence is a post-hoc rationalisation with no causal link to the choice. What makes a Tactician move auditable is kept and is stronger: the trajectory holds the exact prompt, the numbered list of legal actions, and the index returned — enough to replay the decision, which a sentence never was. The cost is a thinner agent-trajectory deliverable, and that is the reason to record this rather than quietly ship it. The Baseline keeps its justification: it *names* a move rather than choosing an index, and its numbers above are already measured and compared. |
| **V6 — a web surface over the same calls** | Automa did several things well that were invisible unless you read a terminal transcript: it enumerates every legal action, it compiles a rulebook behind a human approval gate, it asks what is on the table and what a card does and never asks twice. A local web UI gives those a surface. `play_match` is a blocking loop with no `step()` and no coroutine anywhere, so rather than re-deriving it from request handlers — which would discard the `IllegalDecision` guard, the intervention accounting and every trajectory event — a match runs **unmodified on a daemon thread**, with its decider and both askers blocked on queues that HTTP handlers feed, and SSE carrying events back. | A complete offline Nim match driven through the real HTTP API and SSE: **8 turns, 8 plies, 0 LLM calls, $0**, and the trajectory it wrote replays unchanged under `python -m automa.trajectory.show`, `human_checkpoint` events included. The physical-deck path works end to end over HTTP: a deliberate miscount is re-asked carrying its explanatory note, a correct 13-card answer is accepted, and the module builds 14 legal actions from the cards read off the table. Catalog editor: an invalid effect refused **422** with the store's own problem strings verbatim, a valid one stored, park and forget round-tripping. Suite **467 → 555 passed**, ruff clean (555 when V6 landed; **575** as of submission, after the later test fixes noted in the reproduction guide). A compile and a full Tactician match through the web UI have both since completed successfully (see Agent Trajectories). | Kept — and the interface immediately found two silent bugs of exactly the kind this project keeps producing. **`pending` was built by spreading the question's payload**, which carries its own `kind` (`components`/`card_effect`) and therefore overwrote the `question`/`turn` discriminator the routes dispatch on: `/answer` returned 409 for *every question in every match*, so nothing could ever be answered from the browser. The service tests missed it because they call `ask()` and check what it returns, never the shape the route validates against. **And the games list was keyed on the module's `NAME`** — but `dnup.py` and `dnup_compiled.py` both declare `NAME = "dnup"`, producing two identical rows, one of which addressed a module nobody could reach, plus a reload guard that could miss. The loader resolves the *module* name, so that is the identity. Both are the same `describe_state` shape as before: a seam nothing exercised until a second consumer showed up. The genuinely new capability is the **catalog audit banner** — stored entries that no longer validate against the current vocabulary, which the CLI has no surface for at all. |

**An earlier removed experiment**, a model comparison rather than a design change:
`gemini-3.6-flash` was A/B'd against `gemini-3.5-flash-lite` over ten identical DNUP
decisions and dropped — it was faster in total (73s vs 153s, flash-lite having a heavy
latency tail) but emitted 736 output tokens per decision against flash-lite's 39, because
it reasons at length and reasoning bills as output. That is roughly 11× the cost, and
~22× once its promotional pricing ends. Measurements in
[`docs/phase2_results.md`](docs/phase2_results.md).

**Two entries above are things that were built and then taken out**, which the submission asks for explicitly. The Tactician's justification is the more instructive of the two: it was in the original specification, it ran in production for a long stretch, and it was removed on evidence rather than taste. The second is smaller and lives inside V4.

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

**A reverted decision: Automa's hand was open to the human.** It was originally designed visible on the reasoning that somebody has to play Automa's side at the physical table, so they would see its cards regardless, and showing them made every move auditable. Reversed on 2026-08-30 after playing with it: on screen it simply hands the opponent perfect information and spoils the match. The physical argument was about the table, not the interface, and auditability is served better by the trajectory, which records every state Automa saw. Recorded as a reversal rather than a silent edit because it invalidates every earlier view test, which were rewritten to assert the opposite. Clarified in V5: this is a decision about what the interface keeps re-displaying, not an information barrier against the person dealing the cards — they read Automa's opening hand off the table to enter it, because Automa cannot see the table and somebody has to.

A Strategist and a state-reconciliation mechanism were designed but never built. The cost
is on the record: V1's finding that enumeration *lowered* decision quality is the
regression a Strategist would need to address, and it stands unaddressed.

The full baseline-vs-agent comparison, with both cautions on how to read the primary
metric's ratio, is now its own section: see [Primary Evaluation](#primary-evaluation)
above.

## Reproduction Guide

Written for a clean checkout. Pick one path — native or Docker — then run the same
commands either way; every command below is native-form, and the Docker section shows
the one wrapper that turns any of them into a container invocation.

### Versions this was built and measured on

Python 3.12.10. Dependencies are pinned exactly in `pyproject.toml` (not a floating
range), so a clean install reproduces the same versions this was measured against:
`google-genai==2.20.0`, `anthropic==1.2.0`, `pydantic==2.13.5`, `pypdf==6.4.0`,
`fastapi==0.118.0`, `uvicorn==0.34.3`, `pytest==9.1.1`, `ruff==0.16.5`. Every role
defaults to `google:gemini-3.5-flash-lite`, so a `GEMINI_API_KEY` is the only credential
required for everything below except the baseline/agent comparison's optional Anthropic
run.

### Required data

The offline suite, the baseline/agent evaluation, and playing the already-installed
games need **no external data at all** — `src/automa/games/*.py` and `catalog/*.json`
are committed and ready to use. The only thing that needs data you must supply yourself
is **recompiling or re-cataloguing DNUP or Sushi Go from scratch**: their rulebooks are
commercial and gitignored under invariant 6 (`files/dnup_rules.pdf`,
`files/SushiGo_rules.pdf`), so reproducing `automa-compile`/`automa-catalog` for either
needs your own legally-owned copy dropped under `files/` first. Nim needs no rule
material at all — it has none to submit.

### Setup — native

Requires Python 3.11+ (developed and pinned against 3.12).

```powershell
# Windows (PowerShell) - the platform every command in this guide is written for
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
copy .env.example .env      # then add your GEMINI_API_KEY
```

```bash
# macOS / Linux - identical apart from the venv path and the copy
python3.12 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
cp .env.example .env        # then add your GEMINI_API_KEY
```

**Every later command in this guide is written in the Windows form**, because that is
where the numbers were measured. On macOS or Linux the only change is the interpreter
path — read `.venv\Scripts\python` as `.venv/bin/python` throughout, and the commands
are otherwise identical. If that substitution is a nuisance, the Docker path below
avoids it entirely and is the better choice for a genuinely clean machine.

Change the model without touching code by setting `AUTOMA_MODEL` in `.env`, or
`AUTOMA_MODEL_<ROLE>` to move one agent on its own. The Anthropic backend is fully
implemented too — point a role at it with
`AUTOMA_MODEL_<ROLE>=anthropic:claude-opus-5` and set `ANTHROPIC_API_KEY`.

### Setup — Docker

A `Dockerfile` at the repo root builds the same environment in isolation. **This is the
recommended path on macOS or Linux, and for any clean machine** — it is platform-neutral,
needs no local Python 3.12, and runs the commands below verbatim.

```bash
docker build -t automa .
docker run --rm automa                 # full test suite, offline, no key needed - the default command
```

Anything that reaches a model needs the key passed at run time (never baked into the
image), and anything that writes output should mount it back to the host so it survives
the container exiting:

```bash
docker run --rm -e GEMINI_API_KEY \
    -v "$PWD/trajectories:/app/trajectories" \
    -v "$PWD/docs/validation:/app/docs/validation" \
    automa python -m automa.eval.run --target baseline --workers 4 \
        --json docs/validation/phase3_baseline.json
```

Substitute any command from the sections below the same way: drop the
`.venv\Scripts\python -m` prefix (the image's own Python already has the package
installed) and wrap the rest as `docker run --rm -e GEMINI_API_KEY [-v ...] automa
<command>`. `files/` is deliberately excluded from the image (`.dockerignore`) even on a
local build, so invariant 6 holds inside Docker too — mount it in explicitly
(`-v "$PWD/files:/app/files"`) only if you have your own copy of a rulebook to compile
from.

### Run the solution

```bash
.venv\Scripts\python -m automa.scripts.play --game dnup             # play with your own deck
.venv\Scripts\python -m automa.scripts.play --game dnup --simulate  # let the game deal, for testing
.venv\Scripts\python -m automa.scripts.play --game sushigo --catalog --simulate
```

**Playing uses the cards on your table.** The game asks what was dealt, you read it out,
and only the number of cards is checked — a name it does not recognise is passed through,
not refused. Press Enter if you cannot say and it deals its own. `--seed` and `--simulate`
exist for testing a game without the physical deck in front of you. A few cents and under
a minute per turn on the default model; a full match runs anywhere from a few minutes to
the better part of an hour depending on the game and provider congestion.

### Run the baseline and the agent (evaluation)

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

### Other commands

```bash
.venv\Scripts\python -m pytest                    # full suite, offline, no API key needed
.venv\Scripts\python -m ruff check .              # lint
.venv\Scripts\python -m automa.scripts.hello_llm  # one real LLM call -> a trajectory
.venv\Scripts\python -m automa.trajectory.show trajectories\<file>.jsonl [--full]
```

The first three of the whole guide's commands are also installed as `automa-hello`,
`automa-show` and `automa-play` (needs `pip install -e . --no-deps` once, first).

### What to expect

- **`pytest`**: offline, needs no API key, well under a minute — **575 passed, 0
  failed**, and `ruff check .` is clean. One fix behind that number is worth stating
  here rather than in a commit message, because it is a reproducibility bug of exactly
  the kind this section exists to rule out: the model-resolution tests read ambient
  environment, so they passed on a machine whose `.env` set `AUTOMA_MODEL` and would
  have behaved differently on yours. They now clear those variables before asserting
  anything. (Two others were tests lagging the product — a fresh state must now be
  `settle()`d before it is stepped, and a deliberate `TERMS` tripwire fired as designed
  once a compiled module finally declared them.)
- **`hello_llm`**: one call, prints a completion, writes a trajectory.
- **baseline / agent eval**: ~20 minutes and under $0.20 each, writes the two JSON files
  the changelog above cites.
- **`automa-compile`**: minutes, not seconds - $0.02 to $1.50 depending on how many
  attempts self-play forces, per the changelog above.
- **`automa-catalog`**: seconds, a fraction of a cent to a few cents.
- **`play`**: a few cents and well under a minute per Tactician turn; total match cost
  and length vary with the game and how many turns it runs.

## Agent Trajectories

Representative trajectories are committed explicitly (`git add -f trajectories\<file>.jsonl`)
so the reader is never asked to trust a run that isn't there; every other file under
`trajectories/` is gitignored output. The most recent successful run of each agent, as of
2026-08-30:

| Agent | Game | File | Result | Committed |
|---|---|---|---|---|
| **Compiler** | DNUP | `20260829-191447-compile-dnup-1b973afa97ed.jsonl` | 1 attempt, $0.35, 200/200 self-play | Yes |
| **Compiler** | Sushi Go | `20260830-174842-compile-sushigo-dfa83e6f4909.jsonl` | 3 attempts (1st: 200/200 errored; 2nd: 200/200 terminal but 12 problems; 3rd: clean, 0 problems) — the compile that produced the **currently installed** module, ~16 min | Yes |
| **Compiler** | Nim | — | Nim is hand-written and has never been run through the compiler | n/a |
| **Catalog Builder** | Sushi Go | `20260830-184752-catalog-sushigo-4e0c8def68ae.jsonl` | 12/12 card types drafted, 0 parked, $0.0071 — includes the `gain`+`score_majority` pairing fix for weighted-majority cards (maki, pudding); matches the currently installed module and `catalog/sushigo.json` exactly | Yes |
| **Tactician** | Nim | `20260830-161842-nim-4ff100cd1925.jsonl` | Complete match, 6 turns, human won | Yes |
| **Tactician** | DNUP | `20260829-192451-dnup-4a9b14024253.jsonl` | Complete match, 79 turns, automa won, $0.0096 (the match cited in the V2 changelog row) | Yes |
| **Tactician** | Sushi Go | `20260830-185025-sushigo-fa4f6a8a97fb.jsonl` | The **first complete live Sushi Go match**, played through the web UI: 27 turns/54 plies, 7 facts supplied, $0.003. `human` won — but by the pudding-count tiebreak, not points: scores stayed 0-0 the whole match, the module bug documented in Main Failure Mode below | Yes |
| **Cataloguer** | Sushi Go | `20260830-213650-sushigo-5c41c3c987b9.jsonl` | 2 real calls — `wasabi` (turn 1) and `egg_nigiri` (turn 3), the two card types left undrafted by the Catalog Builder, each asked the moment the card entered a tableau mid-round rather than at deal time; $0.0007 for the pair, $0.0012 for the whole run. Match abandoned after 3 turns/7 plies once the demonstration was captured, 7 facts supplied total | Yes |

No **Strategist** trajectory exists: the component itself was never built.

## Web UI

```bash
.venv\Scripts\python -m automa.scripts.web     # or: automa-web  ->  http://127.0.0.1:8765
```

**The CLI stays canonical.** No measured result runs through the UI: `automa.eval`, the
reproduction guide and every number in the changelog above are produced by the commands
in the previous section, unchanged. The web layer is a thin adapter over the same
functions — routes do HTTP, services hold the calls, and the deterministic core is
untouched.

Four screens: **Games** (installed modules, `conforms` badge, play/validate/reload),
**Add a game** (upload a rulebook, see what was read from it *before* paying for a
compile, watch attempts stream with a running cost, approve — nothing reaches
`src/automa/games/` until you click), **Catalog** (known and parked cards, an editor
generated from the effect vocabulary itself, and a banner for stored entries that no
longer validate — which the CLI has no equivalent for), and **Play** (legal actions as
buttons, never a text box; table and card questions drawn distinctly from moves).

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
| `src/automa/agents/` | Tactician. No Strategist exists — the component was never built. |
| `src/automa/compiler/` | Rule material → executable game module. |
| `src/automa/games/` | Hand-written game modules. |
| `src/automa/eval/` | Baseline and evaluation harness. |
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

## Main Failure Mode

The same shape of bug recurred three times: a contract member or naming convention that
nothing exercises until a second consumer arrives, and a mismatch between the two sides
that never raises. `describe_state` (V2) was the one contract method 200 self-play
matches never called. The `count_field`/`flag`/`resource`/`category` name mismatch (V4)
silently turned off Sushi Go's chopsticks mechanic for 60 matches. `pending` spreading its
own payload over its own discriminator (V6) refused every web-UI answer with a 409. All
three are documented in the changelog above, at the point each was found and fixed.

## Hot Take

Generated code is a better test of a validator than hand-written code, because it does
not share the author's assumptions. The compiled DNUP module passed 200 self-play matches
while crashing the instant anyone rendered the board — every hand-written game so far
never exposed that gap, because both games I wrote happened to render fine.
