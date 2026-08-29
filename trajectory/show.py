"""Render a trajectory as readable text.

    python -m trajectory.show trajectories/<file>.jsonl [--full]

The submission asks for trajectories that are easy to follow from the agent's
instructions through to the final result. Raw JSONL is the archival format; this
is the reading format. ``--full`` stops truncating prompts and responses.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

RULE = "─" * 78


def _clip(text: str, limit: int | None) -> str:
    text = text.rstrip()
    if limit is None or len(text) <= limit:
        return text
    return f"{text[:limit]}… [{len(text) - limit} more chars, use --full]"

def _indent(text: str, prefix: str = "    ") -> str:
    return "\n".join(prefix + line for line in text.splitlines())


def _render_messages(messages: list[dict[str, Any]], limit: int | None) -> str:
    out = []
    for msg in messages:
        content = msg.get("content", "")
        if not isinstance(content, str):
            content = json.dumps(content, ensure_ascii=False)
        out.append(f"  [{msg.get('role')}]\n{_indent(_clip(content, limit), '    ')}")
    return "\n".join(out)


def render(path: Path, limit: int | None) -> str:
    lines: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        e = json.loads(raw)
        seq, etype = e.get("seq"), e.get("type")
        head = f"#{seq:<4} {etype}"

        if etype == "run_start":
            lines.append(RULE)
            lines.append(f"{head}  run={e.get('run_id')} label={e.get('label')}")
            lines.append(f"      python={e.get('python')} commit={e.get('git_commit')} "
                         f"dirty={e.get('git_dirty')}")
            lines.append(f"      config={json.dumps(e.get('config'), ensure_ascii=False)}")
            lines.append(RULE)

        elif etype == "llm_call":
            outcome = e.get("outcome")
            marker = {"ok": "OK", "validation_failed": "REJECTED", "api_error": "API ERROR"}
            lines.append("")
            lines.append(
                f"{head}  agent={e.get('agent')} model={e.get('model')} "
                f"attempt={e.get('attempt')} -> {marker.get(outcome, outcome)}"
            )
            if e.get("system"):
                lines.append("  [system]")
                lines.append(_indent(_clip(e["system"], limit)))
            if e.get("messages"):
                lines.append(_render_messages(e["messages"], limit))
            if e.get("response_text"):
                lines.append("  [assistant]")
                lines.append(_indent(_clip(e["response_text"], limit)))
            if e.get("parsed") is not None:
                lines.append(f"  [parsed] {json.dumps(e['parsed'], ensure_ascii=False)}")
            if e.get("rejection"):
                lines.append(f"  [rejected] {e['rejection']}")
            if e.get("error"):
                lines.append(f"  [error] {e['error']}")
            cost = e.get("cost_usd")
            cost_str = "unpriced" if cost is None else f"${cost:.6f}"
            lines.append(
                f"  [usage] in={e.get('input_tokens')} out={e.get('output_tokens')} "
                f"cost={cost_str} ({e.get('pricing_note')}) "
                f"latency={e.get('latency_ms')}ms stop={e.get('stop_reason')}"
            )

        elif etype == "run_end":
            total = e.get("total_cost_usd")
            lines.append("")
            lines.append(RULE)
            lines.append(f"{head}  outcome={e.get('outcome')} wall={e.get('wall_ms')}ms")
            cost_str = f"${total:.6f}" if total is not None else "n/a"
            lines.append(
                f"      llm_calls={e.get('llm_calls')} "
                f"tokens in={e.get('total_input_tokens')} out={e.get('total_output_tokens')} "
                f"total_cost={cost_str}"
            )
            if e.get("unpriced_calls"):
                lines.append(f"      WARNING: {e['unpriced_calls']} call(s) had no known price")
            lines.append(RULE)

        else:
            payload = {k: v for k, v in e.items() if k not in {"seq", "ts", "type"}}
            lines.append(f"{head}  {json.dumps(payload, ensure_ascii=False)[:400]}")

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render a trajectory JSONL file.")
    parser.add_argument("path", type=Path)
    parser.add_argument("--full", action="store_true", help="do not truncate long text")
    args = parser.parse_args(argv)

    if not args.path.exists():
        print(f"No such trajectory: {args.path}", file=sys.stderr)
        return 1
    print(render(args.path, None if args.full else 600))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
