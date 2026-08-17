#!/usr/bin/env python
"""GATE CENSUS — for every operating_protocol rule, is it GATED or is it a HOPE?

GATE-OR-HOPE-PRINCIPLE has been P1 and open since S192 and the classification was
never performed, so the project has never known which of its own rules can be
broken silently. This is that census, mechanised, so it is re-runnable and cannot
drift into an opinion.

METHOD, stated so it can be argued with. For each rule key it searches the kernel
for a REFERENCE to that rule and classifies by what kind of code refers to it:

  GATED     the key is named in a hook gate, a drift_audit clause, or a verb that
            can REFUSE (raise / return non-zero / print ERROR near the reference).
  DETECTOR  the key is named somewhere that only reports - a render, a note, a
            docstring in a non-refusing path.
  ORPHAN    the key appears NOWHERE outside operating_protocol itself. Nothing
            reads it, so it cannot be enforced, reported, or even noticed.

LIMIT, declared rather than hidden: this measures REFERENCE, not semantics. A rule
named inside a gate that does not actually implement it still reads as GATED here.
The census narrows 57 rules to the ones worth reading by hand; it does not replace
that reading. An ORPHAN result, by contrast, is decisive: nothing references it.
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

RAG_DIR = Path(__file__).resolve().parent.parent
ROOT = RAG_DIR.parent
WT = ROOT / "GIT WORKTREES" / "rag-runtime-kernel"

#: Files whose reference implies a refusal is possible.
GATING = ("rag_kernel/hook_guard.py", "rag_kernel/drift_audit.py",
          "rag_kernel/hook_entry.py", "rag_kernel/state_machine.py",
          "rag_kernel/generated_guards.py", "rag_kernel/guardgen.py")
REFUSE = re.compile(r"\b(raise|return\s+1|ERROR|REFUS|Decision\(|FAIL)", re.I)


def main() -> int:
    sys.path.insert(0, str(RAG_DIR))
    from rag_kernel import persistence                       # noqa: PLC0415
    hot = json.loads((RAG_DIR / "RAG_MASTER.json").read_text(encoding="utf-8-sig"))
    for p in persistence.verify_hashes(hot):
        print(f"WARNING verify_hashes: {p}", file=sys.stderr)
    rules = sorted((hot.get("operating_protocol") or {}).keys())

    # One pass over the source tree; 57 greps would be 57 walks.
    corpus: list[tuple[str, str]] = []
    for base in (WT / "rag_kernel", WT / "tests", WT / "scripts", RAG_DIR / "scripts"):
        if not base.is_dir():
            continue
        for f in base.rglob("*.py"):
            if "__pycache__" in f.parts:
                continue
            try:
                corpus.append((f.relative_to(ROOT).as_posix(),
                               f.read_text(encoding="utf-8", errors="replace")))
            except OSError:
                continue

    verdicts: dict[str, tuple[str, str]] = {}
    for key in rules:
        pat = re.compile(r"\b" + re.escape(key) + r"\b")
        hits = [(path, text) for path, text in corpus if pat.search(text)]
        if not hits:
            verdicts[key] = ("ORPHAN", "referenced by no code at all")
            continue
        gating_hit = next((p for p, _ in hits
                           if any(p.endswith(g) for g in GATING)), None)
        if gating_hit:
            verdicts[key] = ("GATED", gating_hit)
            continue
        refusing = None
        for path, text in hits:
            for m in pat.finditer(text):
                window = text[max(0, m.start() - 400): m.start() + 400]
                if REFUSE.search(window):
                    refusing = path
                    break
            if refusing:
                break
        verdicts[key] = (("GATED", refusing) if refusing
                         else ("DETECTOR", hits[0][0]))

    tally = Counter(v for v, _ in verdicts.values())
    for verdict in ("ORPHAN", "DETECTOR", "GATED"):
        names = [k for k, (v, _) in verdicts.items() if v == verdict]
        print(f"\n=== {verdict} ({len(names)}) ===")
        for k in names:
            print(f"  {k:38} {verdicts[k][1]}")
    print(f"\nCENSUS: {dict(tally)} over {len(rules)} rules")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
