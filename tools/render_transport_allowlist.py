#!/usr/bin/env python3
"""Render (or verify) the transport-allowlist projection.

THIS FILE IS A SHIM. All of the logic lives in ``rag_kernel.transport_projection``
so that the auditor and this CLI run the SAME code — one implementation, two
callers, one test suite.

It used to be the other way round: the whole renderer lived here, in a file that
existed only inside one deployment's ``RAG/tools/`` directory, untracked by git
and outside the test gate. That meant the ``--check`` everyone pointed at as the
drift gate could not travel to a clone and could not be regression-tested.
S198 moved the logic into the package and reduced this file to argument parsing.

Usage
    python tools/render_transport_allowlist.py            # write the projection
    python tools/render_transport_allowlist.py --check    # verify, exit 1 on drift

The projection is also verified by ``rag_kernel audit`` on every run (S198), so
``--check`` is now a convenience rather than the only line of defence.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE.parent) not in sys.path:  # allow running as a plain script
    sys.path.insert(0, str(_HERE.parent))

from rag_kernel import transport_projection as tp  # noqa: E402


def _rag_path(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit)
    return _HERE.parent / "RAG_MASTER.json"


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(description="Render/verify the transport allowlist projection.")
    ap.add_argument("--rag", default=None)
    ap.add_argument("--project-root", default=None)
    ap.add_argument("--check", action="store_true",
                    help="verify the projection matches the rule; exit 1 on drift")
    args = ap.parse_args(argv)

    rag_path = _rag_path(args.rag)
    root = Path(args.project_root) if args.project_root else rag_path.resolve().parent.parent

    try:
        rule_text = tp.rule_text_from_rag(rag_path)
    except tp.ProjectionError as exc:
        print(f"REFUSE: {exc}", file=sys.stderr)
        return 1

    if args.check:
        reasons = tp.drift_reasons(rule_text, root)
        if reasons:
            for reason in reasons:
                print(f"DRIFT: {reason}", file=sys.stderr)
            return 1
        print(f"OK: projection matches operating_protocol.{tp.RULE_KEY} "
              f"({len(tp.extract_patterns(rule_text))} patterns).")
        return 0

    try:
        out_path = tp.render(rule_text, root)
    except tp.ProjectionError as exc:
        print(f"REFUSE: {exc}", file=sys.stderr)
        return 1

    print(f"rendered {out_path}")
    for pattern in tp.extract_patterns(rule_text):
        print(f"  allow {pattern}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
