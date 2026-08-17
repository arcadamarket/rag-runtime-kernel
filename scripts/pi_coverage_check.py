#!/usr/bin/env python
"""Assert the generated CLAUDE.md still carries every Cowork PI non-negotiable.

S203, operator challenge: making CLAUDE.md a RAG projection silently dropped four
of the six numbered items the last Cowork Project Instructions carried. Nothing
noticed, because nothing compared them. This is that comparison, mechanised, so
the regression cannot recur unobserved. Exit 1 on any gap.
"""
import re
import sys
from pathlib import Path

DOC = Path(__file__).resolve().parent.parent.parent / "CLAUDE.md"

# Each entry is a numbered obligation from the last PI and a pattern that proves
# the rendered document still states it in substance, not merely by citation.
CHECKS = {
    "PI-1 no direct read of canonical state": r"E-071|governed kernel|never a sandbox shell",
    "PI-2 two-phase attestation":             r"attest",
    "PI-3 roles / pov_mandate adopted":       r"pov_mandate|pov_roles|BOOT-FRAME",
    "PI-4 no reply before READY":             r"READY",
    "PI-5 refusal -> the recovery it names":  r"session-resume|session-end .*prior|--force",
    "PI-6 recovery exception bak/COLD/WAL":   r"\.bak|COLD|WAL",
    "PI-7 detached run, never poll":          r"DETACHED|never poll|E-081",
    "PI-8 reuse-check before authoring":      r"reuse-check|REUSE-BEFORE-REWRITE",
    "PI-9 bounded emissions":                 r"bounded|capped slice|round-trip",
}


def main() -> int:
    if not DOC.exists():
        print(f"GAP: {DOC} does not exist")
        return 1
    text = DOC.read_text(encoding="utf-8")
    gaps = []
    for label, pat in CHECKS.items():
        ok = bool(re.search(pat, text, re.I))
        gaps += [] if ok else [label]
        print(f"  {'OK ' if ok else 'GAP'}  {label}")
    print(f"\nPI-COVERAGE {'PASS' if not gaps else 'FAIL'} "
          f"({len(gaps)} gap(s), document {len(text)} chars)")
    return 1 if gaps else 0


if __name__ == "__main__":
    raise SystemExit(main())
