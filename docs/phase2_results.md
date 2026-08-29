# Phase 2 results — match loop with the Tactician

Recorded 2026-08-29, immediately after the runs. Every number here comes from a
trajectory in `trajectories/` and can be regenerated with:

```bash
python -m automa.scripts.match_stats trajectories/*.jsonl --json docs/validation/phase2_metrics.json
```

Machine-readable form: [`validation/phase2_metrics.json`](validation/phase2_metrics.json).

---

## Exit criterion

> *"Complete match playable through the terminal on both games, with trajectory
> showing each tactician decision and justification."* — `plan.md` Phase 2

| requirement | status | evidence |
|---|---|---|
| complete match on both games | met | `nim` 6 turns, `dnup` 91 turns, both `outcome=terminal` |
| every Tactician decision logged with a justification | met | 3/3 and 46/46 |
| CLI shows state, numbered actions, accepts a number | met | implemented; 17 tests in `tests/test_play_cli.py` |
| full trajectory per match | met | one JSONL file per match |
| offline suite passes with no API key | met | 209 tests, ruff clean |

**One caveat, stated plainly:** both live matches were run with `--auto`, so a seeded
random opponent played the human side. No person has typed a move into a live match.
The interactive path is implemented and tested (including a full match driven through
`input()`, bad input, and `q` to quit), so the capability exists and a judge can
exercise it — but the live demonstration is still owed, and Phase 8's video needs it
regardless.

---

## Measured results

| game | model | winner | turns | calls | cost | $/turn | latency p50 / mean / max |
|---|---|---|---:|---:|---:|---:|---|
| nim | gemini-3.6-flash | automa | 6 | 3 | $0.003365 | $0.001122 | 8.2 / 19.4 / 43.4 s |
| nim | gemini-3.5-flash-lite | automa | 6 | 3 | $0.000533 | $0.000178 | 17.7 / 16.8 / 32.0 s |
| dnup | gemini-3.5-flash-lite | automa | 91 | 46 | $0.013872 | $0.000302 | 10.6 / 20.3 / 75.7 s |

Costs are **recomputed** from logged token counts at current prices, not read back
from the trajectories — see "Pricing was wrong" below.

### Verification beyond the criterion

- **Both trajectories replay exactly.** Re-applying the logged action indices to a
  fresh `initial_state(seed)` reproduces every action label and the identical
  terminal state, 0 mismatches. The log is a record, not a narrative.
- **Invariant 1 is checkable from the log alone.** Every `action_applied` event is
  preceded by a `legal_actions_enumerated` event at the same ply, containing the
  chosen action at the reported index. Asserted in `tests/test_match.py`.
- **DNUP turn order is correct.** All 91 turns verified against the rulebook's
  structure: the ★ holder opens with a *single* turn on Play Area 2, then turns come
  in consecutive pairs, 0 rotation violations. Full table:
  [`validation/phase2_dnup_turn_order.md`](validation/phase2_dnup_turn_order.md).

---

## Decision quality — the first real signal

Nim is solved, so `nim.optimal_actions()` (nim-sum, itself verified against
brute-force search in Phase 1) scores play exactly rather than by win/loss:

| model | optimal moves | won? |
|---|---|---|
| gemini-3.6-flash | **3/3** | yes |
| gemini-3.5-flash-lite | **2/3** | yes |

Flash-lite's error, at ply 3, is worth recording in full because it is the clearest
demonstration in the project so far of why win/loss is not a sufficient metric:

```
heaps [0,3,1], nim-sum 2  ->  Automa took 1 from heap C, leaving [0,3,0]
justification: "Taking the last object from heap C leaves heap B with 2,
                setting up a winning position."
correct move : take 2 from heap B, leaving [0,1,1] (nim-sum 0)
```

Two things went wrong at once. The move is **losing** — it hands the opponent a
position they win by taking all three. And the justification is **factually wrong
about the board**: it leaves heap B with 3, not 2. Automa won the match anyway,
because the random opponent immediately blundered back.

So a win was recorded for a losing move. `n=3` makes this a weak signal about the
model, but a strong one about methodology: **the Phase 3 battery must not score on
match outcome alone**, and the random opponent is too weak to expose bad play.

---

## Model selection

Settled on **`google:gemini-3.5-flash-lite`**, configured via `AUTOMA_MODEL` in
`.env` with `config.DEFAULT_MODEL` as the committed fallback.

**Gemini 2.5 is unavailable.** The whole 2.5 family — `flash`, `flash-lite`, `pro` —
returns `404 ... no longer available to new users` on `generateContent`. Confirmed by
direct probe, not inferred from documentation. A trap worth recording: all three
still appear in `models.list` for a key that cannot call them, so enumerating a model
is never evidence it will run. A key from a Google project predating the retirement
keeps access; a newly issued key does not.

**Cost, measured rather than quoted.** List prices suggest flash-3.6 is only 2.17×
flash-lite. The real gap on this workload is far larger, because flash-3.6 *thinks*:

| model | median output tokens per decision | cost for the 46-call DNUP match |
|---|---:|---:|
| gemini-3.5-flash-lite | 39 | $0.0139 |
| gemini-3.6-flash | 736 | $0.1502 (**10.8×**) |

Thinking tokens bill as output at $3.75/M, so reasoning dominates the bill. From
2027-01-01, when flash-3.6's promotional pricing ends ($1.50/$7.50), the same
comparison becomes **21.7×**.

**Thinking is not disabled anywhere.** Nothing passes `effort` or `thinking`, so the
provider sends `thinking_config=None` — which means *use the model default*, not
*off*. Every response from both models carries a `thought_signature` (46/46 and
17/17). Setting `effort="low"` maps to `thinking_budget=0` and is already wired in
`_EFFORT_BUDGETS`; **untested**, and a live candidate for cutting both cost and
latency, at unknown cost to quality.

**Latency is dominated by variance, not by model.** 97% of a match's wall clock is
spent inside the API call. Flash-lite is bimodal — an A/B on ten identical DNUP
decisions gave it a 1.2 s median against flash-3.6's 5.7 s, yet flash-lite lost on
*total* time (153 s vs 73 s) because of a heavy tail (max 64 s). The same tail shows
in the match: p50 10.6 s, max 75.7 s. All measurements were on the paid key.

---

## Findings worth carrying into the changelog

**1. A bug the exit-criterion run found, that the tests had missed.**
`call_llm` documents *"Raises: LLMCallError"*, but on the final attempt of a
transport failure it re-raised the vendor's own `ServerError`. The Tactician's
fallback catches `LLMCallError` — so a run of five consecutive 503s sailed straight
past the handler written for exactly that case and killed a DNUP match at ply 5. The
fallback had only ever been tested against *validation* failures, which never take
that path. Fixed so an exhausted transient failure raises `LLMCallError` (the
fallback fires, the match survives) while a non-retryable error such as a 404 still
propagates loudly — degrading a misconfiguration into ninety turns of random play
would be worse than crashing. Two regression tests added.

**2. Pricing was wrong, and the design caught it.**
`pricing.py` marked both Gemini models `free_tier=True`, so every call logged
`$0.000000`. On a paid key that made the cost metric useless. It was recoverable
because trajectories log raw token counts beside the computed cost, so the true
figures were recomputed retroactively — and because `pricing_note` is written into
every event, the old zeros were stamped `free_tier`, labelled as an assumption rather
than passed off as a measurement. Now `$0.30/$2.50` and `$0.75/$3.75`, `list_price`.

**3. Cost is not the deciding factor.** Scaled to a Phase 3 battery of 12 cases × 2
arms × ~46 calls, flash-lite costs ~$0.33 and flash-3.6 ~$3.60. Both are negligible.
Decision quality and latency should decide the model, not price.

**4. A portability bug that would have hit a judge.** `automa-show` used box-drawing
characters and crashed with `UnicodeEncodeError` on a cp1252 Windows console. All
source is ASCII-only now.

---

## Still open

- No live human-played match (gap above).
- `effort="low"` A/B never run — cost, latency and quality all unmeasured.
- Nim decision quality is `n=3` per model; too thin to choose a model on.
- The Phase 1 validation report carries no git commit or timestamp, so its numbers
  cannot be tied to a revision.
- The DNUP "every round ends with an empty hand" check exists only as a throwaway
  script, not a permanent test.
