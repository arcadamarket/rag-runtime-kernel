#!/usr/bin/env python
"""ROOT-TIDY — classify everything in the project root against the ledgers.

STANDING OPERATOR DIRECTIVE (S202): keep the project root clean, automatically,
and NEVER delete something whose value is uncertain. Uncertain items are moved to
a quarantine folder INSIDE the root for the operator to review; deletion is the
operator's call, never the agent's.

WHY THIS IS A SCRIPT AND NOT A HABIT: a tidy-up performed by judgement leaves no
record of what was considered and spared, so the next session re-derives the same
decisions and eventually gets one wrong. This classifies every entry against the
two ledgers that actually know - the baked-asset registry and the boot-map - and
prints its reasoning.

CLASSES
  PROTECTED  a registered baked asset, or canonical state, or live machinery.
             Never moved. Moving a registered asset without deregistering it
             first is ASSET-DEREGISTER-BEFORE-MOVE, already a logged defect.
  KEEP       not registered, but structurally live (git worktree, .claude, RAG).
  SCRATCH    this project's own transient output: boot transcripts, suite logs.
             Safe to clear; regenerated on demand.
  QUARANTINE everything else - unregistered, unmapped, no live consumer found.
             MOVED, NOT DELETED, into _QUARANTINE_S202/ with a manifest.

Default is REPORT. Nothing moves without --apply.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
RAG = ROOT / "RAG"
QUARANTINE = ROOT / "_QUARANTINE_S202"

#: Live machinery. Present for the project to function at all.
KEEP_EXACT = {
    "RAG", "GIT WORKTREES", ".claude", ".mcp.json", "CLAUDE.md", "toolchain",
    "_QUARANTINE_S202",
}

#: This project's own regenerable output.
SCRATCH_DIRS = {".boot", "__pycache__", ".pytest_cache"}


def registered_asset_names() -> set[str]:
    """Root-level names that the baked-asset registry points at."""
    names: set[str] = set()
    ctx = RAG / "RAG_CONTEXT.json"
    if not ctx.exists():
        return names
    try:
        doc = json.loads(ctx.read_text(encoding="utf-8-sig"))
    except Exception:                                     # noqa: BLE001
        return names
    # MEASURED, not guessed (S202): baked_assets is
    #   {"_protocol": "<prose>", "assets": [ {asset_id, path, purpose, ...}, ... ]}
    # The first cut of this function iterated baked_assets as a list of records
    # and then as its .values(), and BOTH produced an empty set — which the
    # classifier read as "nothing in the root is a registered asset" and
    # cheerfully proposed quarantining BLUEPRINT_ONLINE_BIZ_CLONE.md and
    # RUNBOOK_CLONE_INIT_S179.md, which are registered. That is why this script
    # reports by default and moves nothing without --apply: a classifier that
    # silently returns an empty ledger looks exactly like a clean root.
    ba = doc.get("baked_assets") or {}
    if isinstance(ba, dict):
        records = ba.get("assets") or [v for v in ba.values() if isinstance(v, dict)]
    else:
        records = ba
    for rec in records:
        if not isinstance(rec, dict):
            continue                       # e.g. the "_protocol" prose entry
        p = str(rec.get("path") or "")
        if not p:
            continue
        head = p.replace("\\", "/").split("/")[0]
        if head:
            names.add(head)
    return names


def mapped_names() -> set[str]:
    """Root-level names the boot-map covers."""
    names: set[str] = set()
    man = RAG / "BOOTMAP_MANIFEST.json"
    if not man.exists():
        return names
    try:
        doc = json.loads(man.read_text(encoding="utf-8-sig"))
    except Exception:                                     # noqa: BLE001
        return names
    entries = doc.get("files") or doc.get("entries") or doc.get("map") or {}
    keys = entries.keys() if isinstance(entries, dict) else entries
    for k in keys:
        head = str(k).replace("\\", "/").split("/")[0]
        if head:
            names.add(head)
    return names


def classify() -> list[tuple[str, str, str]]:
    assets, mapped = registered_asset_names(), mapped_names()
    out: list[tuple[str, str, str]] = []
    for entry in sorted(ROOT.iterdir(), key=lambda p: p.name.lower()):
        n = entry.name
        if n in KEEP_EXACT:
            out.append((n, "KEEP", "live machinery"))
        elif n in assets:
            out.append((n, "PROTECTED", "registered baked asset"))
        elif n in SCRATCH_DIRS:
            out.append((n, "SCRATCH", "regenerable output"))
        elif n in mapped:
            out.append((n, "QUARANTINE", "boot-mapped but no registry record and no live consumer"))
        else:
            out.append((n, "QUARANTINE", "unregistered and unmapped"))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="actually move QUARANTINE entries (default: report only)")
    args = ap.parse_args()

    rows = classify()
    width = max(len(n) for n, _, _ in rows)
    for n, cls, why in rows:
        print(f"  [{cls:<10}] {n:<{width}}  {why}")

    to_move = [n for n, c, _ in rows if c == "QUARANTINE"]
    print(f"\n  {len(to_move)} entry(ies) would be quarantined "
          f"(moved to {QUARANTINE.name}/, NOT deleted).")
    if not args.apply:
        print("  REPORT ONLY. Re-run with --apply to move them.")
        return 0

    QUARANTINE.mkdir(exist_ok=True)
    manifest = []
    for n, cls, why in rows:
        if cls != "QUARANTINE":
            continue
        src, dst = ROOT / n, QUARANTINE / n
        if dst.exists():
            print(f"  skip (already quarantined): {n}")
            continue
        shutil.move(str(src), str(dst))
        manifest.append({"name": n, "reason": why})
        print(f"  moved -> {QUARANTINE.name}/{n}")
    (QUARANTINE / "MANIFEST.json").write_text(
        json.dumps({"session": "S202", "note":
                    "Moved out of the project root by scripts/root_tidy.py because no "
                    "ledger claims them. NOTHING HERE IS DELETED. Review and decide; "
                    "move anything back if it is still wanted.",
                    "entries": manifest}, indent=2) + "\n", encoding="utf-8")
    print(f"\n  Manifest: {QUARANTINE / 'MANIFEST.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
