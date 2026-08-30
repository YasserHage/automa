"""Serve the Automa web UI on localhost.

    python -m automa.scripts.web            (or: automa-web)  -> http://127.0.0.1:8765

**Loopback only, and it means it.** This server imports and executes compiled game
modules in-process - compilation itself validates in a subprocess sandbox, but once a
module is installed it runs like any other import - and it has no authentication of any
kind. A non-loopback `--host` is therefore refused outright rather than warned about,
unless `AUTOMA_WEB_ALLOW_REMOTE=1` says somebody has thought about it.

No `--reload` either: it needs `watchfiles`, which is not a declared dependency, and it
would restart the server mid-match, killing every running session's trajectory.
"""

from __future__ import annotations

import argparse
import ipaddress
import os
import sys

#: Set to "1" to allow binding a non-loopback address. Deliberately awkward.
ALLOW_REMOTE_VAR = "AUTOMA_WEB_ALLOW_REMOTE"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765


def is_loopback(host: str) -> bool:
    if host in {"localhost", ""}:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default=DEFAULT_HOST, help="default: %(default)s (loopback)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="default: %(default)s")
    parser.add_argument(
        "--log-level", default="info", choices=["critical", "error", "warning", "info", "debug"]
    )
    args = parser.parse_args(argv)

    if not is_loopback(args.host) and os.environ.get(ALLOW_REMOTE_VAR) != "1":
        print(
            f"refusing to bind {args.host}: this server runs compiled game modules "
            f"in-process and has no authentication.\n"
            f"Set {ALLOW_REMOTE_VAR}=1 if you genuinely want it reachable from the network.",
            file=sys.stderr,
        )
        return 2

    import uvicorn

    from automa.web import create_app

    print(f"Automa web UI on http://{args.host}:{args.port}   (the CLI stays canonical)")
    uvicorn.run(create_app(), host=args.host, port=args.port, log_level=args.log_level)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
