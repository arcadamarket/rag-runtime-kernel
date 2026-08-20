#!/usr/bin/env python
"""Compact, bounded triage digest of the tracked-item backlog.

WHY THIS EXISTS. A backlog review has to read the NOTE of every item, because the
note is where the measurement lives — the title only says what broke. Reading 38
P1 notes out of ``items --json`` costs a 490 KB emission and blows the context
budget the token-economy rule (Rule 17) exists to protect. This renders the same
facts as a bounded digest: one block per item, notes truncated to a stated width,
so a reviewer can decide STILL-REAL / ALREADY-SATISFIED / CHEAP / REAL-WORK per
item without loading the store.

READ-ONLY. It opens RAG_MASTER.json, filters, and prints. It writes nothing, so it
is safe to run against a sealed session (CLOSE-DOUBLE-SEAL never sees it).

USAGE, from the RAG directory::

    python scripts/backlog_triage_digest.py --group P1
    python scripts/backlog_triage_digest.py --group P2 --chars 0     # full notes
    python scripts/backlog_triage_digest.py --status OPEN --ids-only

Exit 0 always when the store could be read; exit 2 when it could not, because an
unreadable store is a fact the caller must not mistake for an empty backlog.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

#: Statuses that are still owed work. Anything else is history.
LIVE_STATUSES = ("OPEN", "IN_PROGRESS")


def _default_rag() -> Path:
    here = Path(__file__).resolve().parent
    for candidate in (here.parent / "RAG_MASTER.json", Path("RAG_MASTER.json")):
        if candidate.exists():
            return candidate
    return Path("RAG_MASTER.json")


def _squash(text: str, chars: int) -> str:
    """One line, bounded. ``chars=0`` means no truncation."""
    flat = " ".join((text or "").split())
    if chars and len(flat) > chars:
        return flat[: chars - 1] + "…"
    return flat


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--rag", type=Path, default=_default_rag(),
                    help="Path to RAG_MASTER.json (default: the RAG next to this script's parent)")
    ap.add_argument("--group", type=str, default=None,
                    help="priority_group to render (e.g. P1). Default: every live item.")
    ap.add_argument("--status", type=str, default=None,
                    help=f"status filter (default: any of {', '.join(LIVE_STATUSES)})")
    ap.add_argument("--kind", type=str, default=None, help="kind filter (ERROR, TASK, …)")
    ap.add_argument("--id", dest="ids", action="append", default=None,
                    help=("render only this item id (repeatable). Overrides the "
                          "status/group filters, so a TERMINAL item can be read "
                          "back by id when re-checking a closure."))
    ap.add_argument("--chars", type=int, default=420,
                    help="truncate each note to this many characters (0 = full note)")
    ap.add_argument("--ids-only", action="store_true", help="print ids, one per line")
    args = ap.parse_args(argv)

    try:
        hot = json.loads(Path(args.rag).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        print(f"ERROR: cannot read {args.rag}: {exc}", file=sys.stderr)
        return 2

    items = hot.get("tracked_items") or []
    wanted = set(args.ids or ())
    picked = []
    for it in items:
        if not isinstance(it, dict):
            continue
        if wanted:
            if str(it.get("id") or "") in wanted:
                picked.append(it)
            continue
        status = str(it.get("status") or "")
        if args.status:
            if status != args.status:
                continue
        elif status not in LIVE_STATUSES:
            continue
        if args.group and str(it.get("priority_group") or "") != args.group:
            continue
        if args.kind and str(it.get("kind") or "") != args.kind:
            continue
        picked.append(it)

    picked.sort(key=lambda i: str(i.get("id") or ""))

    if args.ids_only:
        for it in picked:
            print(it.get("id"))
        print(f"# {len(picked)} item(s)")
        return 0

    for n, it in enumerate(picked, 1):
        print(f"--- {n}/{len(picked)}  {it.get('id')}  "
              f"[{it.get('priority_group') or 'unprioritized'} "
              f"{it.get('kind')} {it.get('status')} from {it.get('session')}]")
        print(f"    TITLE: {_squash(str(it.get('title') or ''), args.chars)}")
        note = _squash(str(it.get("note") or ""), args.chars)
        print(f"    NOTE : {note or '(none)'}")
    print(f"# {len(picked)} item(s) rendered from {args.rag}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
