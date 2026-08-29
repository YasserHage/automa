"""Generate the baseline's rules brief by having a model read the rulebook.

    python -m automa.scripts.build_rules_brief --game dnup --pdf files/dnup_rules.pdf

Run once, reviewed, then committed. Nobody reproducing the project needs to run it -
which matters, because the rulebook cannot ship with the repository.

**Why a model writes this and not me.** `dnup.py` encodes DNUP's rules correctly
because I read the scan to write it. If I also wrote the prose the baseline plays
from, I would be on both sides of the comparison, which is exactly the unconscious
strawman `plan.md` warns about. Handing the same source material to a model removes my
hand from the baseline's only knowledge of the game. My remaining role is to review
for *completeness* - I may add a rule the model missed, never remove or soften one -
and any such edit is recorded in the file header so the intervention stays auditable.

The call goes through :func:`automa.llm.call_llm` like every other, so the pages sent
and the brief returned are both in a trajectory (invariant 3). It is also the first
exercise of the image path that Phase 4's Compiler will depend on.

Invariant 6 still holds: what gets committed is a paraphrase, never transcribed
rulebook text, and the PDF stays in the gitignored `files/` directory.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

from automa import config
from automa.compiler.ingest import IngestError, as_message_parts, pdf_page_images
from automa.eval.baseline import RULES_DIR
from automa.llm import call_llm
from automa.trajectory import TrajectoryWriter

INSTRUCTIONS = """\
The images are the pages of a rulebook for a two-player game. Read them and write a \
complete, self-contained description of the rules in your own words.

This description will be the *only* thing another player ever sees about this game - \
they will never see these pages. Write it so that someone who reads it can play \
correctly without guessing. In particular be exact about:

* what a turn consists of, in order, and how turn order proceeds between the players;
* every action a player may take on their turn, and the conditions under which each \
one is or is not allowed;
* any rule that causes cards to move between the table and a hand, and whether \
anything changes about a card when it moves;
* how a round ends, how the game ends, and who wins;
* any exception or special case, including how the very first turn differs.

Write it in your own words as clear structured Markdown with headings. Do not copy \
sentences from the pages. Do not add strategy advice, and do not invent rules that are \
not shown - if something is genuinely unclear from the pages, say so explicitly under \
a heading "Unclear from the rulebook" rather than guessing.\
"""

HEADER = """\
# {game} - rules

*Generated on {date} by `{model}` reading the rulebook page images, via
`python -m automa.scripts.build_rules_brief`. Written by a model rather than by the
author of `{game}.py` on purpose: the baseline's only knowledge of this game must not
come from the same person who wrote the module it is measured against.*

*Reviewed for completeness before use. Any rule added by hand after generation is
listed here; nothing was removed or softened.*

**Hand-added after review:** none.

---

"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--game", required=True)
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--max-tokens", type=int, default=8192)
    args = parser.parse_args(argv)

    try:
        pages = pdf_page_images(args.pdf)
    except IngestError as exc:
        print(exc, file=sys.stderr)
        return 1
    print(f"{len(pages)} page image(s) from {args.pdf}")
    for page in pages:
        print(f"   {page.describe()}")

    out = args.out or RULES_DIR / f"{args.game}.md"
    with TrajectoryWriter(f"rules-brief-{args.game}", config=config.describe()) as traj:
        traj.emit(
            "core_event",
            event="rule_material_ingested",
            game=args.game,
            source=str(args.pdf),
            pages=[p.describe() for p in pages],
        )
        result = call_llm(
            agent="compiler",
            messages=[{"role": "user", "content": as_message_parts(pages, INSTRUCTIONS)}],
            max_tokens=args.max_tokens,
            trajectory=traj,
        )
        path = traj.path

    header = HEADER.format(
        game=args.game,
        date=datetime.now(UTC).date().isoformat(),
        model=result.model,
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(header + result.text.strip() + "\n", encoding="utf-8")

    print(f"\nwritten: {out}  ({len(result.text):,} chars)")
    print(f"tokens in={result.input_tokens} out={result.output_tokens} "
          f"cost=${result.cost_usd:.6f} latency={result.latency_ms}ms")
    print(f"trajectory: {path}")
    print("\nREVIEW THIS BEFORE USING IT. A brief that is wrong makes the baseline fail")
    print("for the wrong reason and invalidates the comparison.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
