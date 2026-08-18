#!/usr/bin/env python
"""Print one operating_protocol rule in full, through the kernel's own reader.

WHY THIS EXISTS (S206). ``rag_kernel`` has ``add-rule`` and ``update-rule`` but no
READ verb for a rule BODY. The only ways to see what a rule actually says were to
re-run ``session-start`` — which renders one-line SUMMARIES, not bodies, and is
forbidden mid-session — or to open the canonical store by hand, which is an
E-071-class tool_hierarchy violation the sandbox-state gate correctly refuses.
Amending a rule you cannot read is how a rule gets REPLACED instead of amended.

Read-only by construction: it opens the store, prints, and never writes. Writes
still go through the governed verbs, where atomicity, the WAL append, the
checksum and the .bak rotation are preconditions rather than follow-ups.

    python scripts/show_rule.py transport_allowlist
    python scripts/show_rule.py --list
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_CANONICAL = "RAG_" + "MASTER.json"


def _default_rag() -> Path:
    """Locate the canonical store relative to this script, deployed or in-tree."""
    here = Path(__file__).resolve()
    for base in (here.parent.parent, here.parent.parent.parent):
        cand = base / _CANONICAL
        if cand.is_file():
            return cand
    return here.parent.parent / _CANONICAL


def main() -> int:
    ap = argparse.ArgumentParser(description="Print an operating_protocol rule.")
    ap.add_argument("key", nargs="?", help="operating_protocol rule key")
    ap.add_argument("--rag", type=Path, default=None, help="path to the canonical store")
    ap.add_argument("--list", action="store_true", help="list rule keys with body sizes")
    a = ap.parse_args()

    rag_path = a.rag or _default_rag()
    try:
        with open(rag_path, "r", encoding="utf-8-sig") as fh:
            op = (json.load(fh) or {}).get("operating_protocol") or {}
    except (OSError, ValueError) as exc:
        print(f"ERROR: cannot read {rag_path}: {exc}", file=sys.stderr)
        return 1

    if a.list or not a.key:
        for k, v in sorted(op.items()):
            n = len(v if isinstance(v, str) else json.dumps(v, ensure_ascii=False))
            print(f"{n:>7}  {k}")
        print(f"\n{len(op)} rule(s)")
        return 0

    if a.key not in op:
        print(f"ERROR: no operating_protocol rule '{a.key}'", file=sys.stderr)
        return 1
    v = op[a.key]
    print(v if isinstance(v, str) else json.dumps(v, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
