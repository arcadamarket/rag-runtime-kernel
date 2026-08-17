#!/usr/bin/env python
"""S202 TRIAGE — resolve what is actually done, and give every item a P-group.

Two jobs, both deterministic, both scripted because a triage done by hand is a
triage nobody can re-read (audit protocol L4, and Rule 43 RETRO-CLARITY).

JOB 1 — HONEST STATUS. Two S201 items were repaired and verified in the same
session that opened them, and were then left reading OPEN. That is the exact
status dishonesty this project keeps catching in its own history (Rule 14), so
it is closed here with the evidence each one actually has.

JOB 2 — NO UNPRIORITIZED ITEM. 12 active items carried no P-group, which means
the priority burn-down was reporting a ranking over an incomplete set. Every one
is assigned below WITH ITS REASON, because a bucket without a reason is a guess
that the next session inherits as a fact.

THE RANKING RULE USED, stated so it can be argued with:
  P1  the project cannot be trusted to report on itself until this is fixed,
      OR it is one measured step from closing a P1 that is already open.
  P2  a real defect with a known fix that costs a session or less.
  P3  correctness/hygiene debt that compounds slowly.
  P4  evidence and documentation debt.
  P5  outside the kernel's critical path.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

RAG_DIR = Path(__file__).resolve().parent.parent
ROOT = RAG_DIR.parent
SESSION = "S202"

# (item_id, reason, [artifact paths that MUST exist])
RESOLVE = [
    ("ITEMS-VERB-CRASHES-ON-WINDOWS-S201",
     "FIXED and verified live at S201: _force_utf8_console() runs at kernel entry "
     "before any verb can print, and `rag_kernel items --status OPEN` was then run "
     "with PYTHONIOENCODING unset and rendered the full backlog including the U+26A0 "
     "title that used to raise UnicodeEncodeError. Closed on the live call, not on the diff.",
     ["GIT WORKTREES/rag-runtime-kernel/rag_kernel/__main__.py"]),

    ("DEPLOY-PARITY-MISSES-SCRIPTS-S201",
     "FIXED and verified by the gate FIRING: _KERNEL_SOURCE now matches "
     "(rag_kernel|scripts)/*.py, and the PostToolUse deploy-parity hook then reported "
     "drift on scripts/grand_audit.py at the moment of the edit, in production, four "
     "times during S201. Closed on a gate that fired, which is the standard this "
     "project set for itself.",
     ["GIT WORKTREES/rag-runtime-kernel/rag_kernel/hook_guard.py"]),
]

# (item_id, P-group, why this bucket)
PRIORITY = [
    # ---- P1: self-report integrity, or one measured step from closing a P1
    ("SEAL-NOT-INVALIDATED-BY-LATER-WRITES-S201", "P1",
     "This is the defect that handed the operator a broken transfer while the banner "
     "said transfer_ready=true. Until a write after a seal invalidates that seal, no "
     "close this project produces can be trusted, including this one."),
    ("SELF-CERTIFYING-EVIDENCE-GATE-S201", "P1",
     "Four measured instances of the class that caused the eight-session drift. Every "
     "other gate is only as good as the evidence it reads."),
    ("MEASUREMENT-PROVENANCE-S201", "P1",
     "'2,818 green' described a different interpreter and platform than the one that "
     "ships, for eight sessions. A number without its provenance is the mechanism by "
     "which this project lied to itself."),
    ("MCP-REGISTRATION-SURFACE-S199", "P1",
     "Closes together with ACTIVATION-GAP-S197, which is already P1 and is now ONE "
     "client restart from its resolve condition since the stdio framing was fixed."),
    ("GATE-FALSE-POSITIVE-ON-PROSE-S201", "P1",
     "Blocks re-arming the hard hook ERROR gate: arming enforcement over a gate that "
     "still refuses honest commits would wedge the project. It gates a P1, so it is one."),

    # ---- P2: real defect, known fix, a session or less
    ("ERRLOG-ENTRY-ORPHANS-ITS-OWN-ID-S201", "P2",
     "session-end manufactures the orphan its own recheck then refuses. Every clean "
     "close that logs an error hits it. Known fix: bank the id in the same write."),
    ("SESSION-DELTA-COUNTS-ADDS-AS-NOTHING-S201", "P2",
     "The delta ledger reports a 24-item session as zero movement, and the same "
     "computation produced the historical +58 that the whole recovery narrative rests on."),
    ("DEPLOY-PARITY-MISSES-SCRIPTS-S201", "P2",
     "Retained for the record; resolved this session."),
    ("CLONE-MCP-JSON-NOT-GENERATED", "P2",
     "Every clone is born with the kernel unreachable over MCP and nothing tells its "
     "first session. Now demonstrably worse than believed: the server also had a wire "
     "framing bug, so no clone could ever have loaded it."),
    ("TLC-CONCURRENCY-GUARD-BLIND-S201", "P2",
     "The guard that exists because concurrent model checking produced the S189 "
     "artefact results is inert on the deployed platform."),

    # ---- P3: correctness and hygiene debt that compounds
    ("FORMAL-NAIVE-CONSTANT-INVARIANT-S201", "P3",
     "Two naive configs error instead of refuting, so two gates have no proof they "
     "earn their place. Real, but 9 of 11 specs do carry that proof."),
    ("SESSION-LOG-ROOT-CENSUS", "P3", "Hygiene debt over the log corpus."),
    ("ASSET-REGISTRY-OUTSIDE-GIT-S199", "P3",
     "85 of 127 registered assets sit outside any repository. Not urgent while the "
     "operator's machine is the only host, and fatal the day it is not."),

    # ---- P4: evidence and documentation debt
    ("RUNBOOK-CLONE-BIRTH-REMEASURE", "P4",
     "Documentation provenance debt; the runbook's numbers are stamped at an older runtime."),
    ("ITEMS-VERB-CRASHES-ON-WINDOWS-S201", "P2",
     "Retained for the record; resolved this session."),

    # ---- re-ranking of an INHERITED priority, with the reason for the move
    ("RESIDENT-SUPERVISOR", "P3",
     "RE-RANKED S202, down from P1, per the explicit instruction in CLAUDE.md section 4: "
     "'if the section 2 checks pass, the hook layer already gives you refusal and this "
     "drops in priority'. They passed. The hook layer fired three times in production at "
     "S201 - it refused the first command of the session, reported deploy-parity on the "
     "kernel edits, and refused an undeclared transport. The refusal capability this item "
     "was going to build now exists. What remains is consolidation, not capability, so it "
     "must stop outranking the defects that are still lying to the operator."),
]


def run(args: list[str]) -> bool:
    r = subprocess.run([sys.executable, "-m", "rag_kernel", *args],
                       cwd=str(RAG_DIR), capture_output=True, text=True)
    line = (r.stdout or r.stderr).strip().splitlines()
    print(f"{'ok  ' if r.returncode == 0 else 'FAIL'} {line[-1][:104] if line else ''}")
    return r.returncode == 0


def main() -> int:
    fails = 0
    print("== JOB 1: resolve what is actually done ==")
    for item, reason, artifacts in RESOLVE:
        args = ["resolve", item, "--session", SESSION, "--reason", reason]
        for a in artifacts:
            args += ["--artifact", str(ROOT / a)]
        fails += not run(args)

    print("\n== JOB 2: every active item gets a P-group, with a reason ==")
    for item, group, why in PRIORITY:
        fails += not run(["priority", item, group, "--session", SESSION])
        print(f"       {group}  {item}: {why[:88]}")

    print(f"\nTRIAGE {'PASS' if not fails else 'FAIL'} ({fails} failure(s))")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
