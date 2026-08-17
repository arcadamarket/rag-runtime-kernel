#!/usr/bin/env python
"""Bank every S204/S205 finding in ONE governed run. L4: this is not typing.

Written because the alternative is twelve remembered `add` commands, and a
transfer that depends on remembering is the failure Rule 45 names.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

RAG = Path(__file__).resolve().parent.parent
S = "S205"

# (id, kind, title, note, P)
ITEMS = [
    ("LEAN-RAG-INVERTED-LOADED-STORE-S204", "ERROR",
     "The loaded store is 4x the bulk store and S201-S204 notes are part of why",
     "MEASURED S204, found by the OPERATOR and not by any gate - which is itself the finding. "
     "The LOADED master store is 664 KB; the bulk non-loaded context store is 167 KB; the cold "
     "store is 11 KB. The store paid for at every boot is four times the one designed to hold "
     "volume. S201-S204 added 39 tracked_items with notes of 1500-2500 chars, roughly 9 percent "
     "of the loaded store, and every future session pays that at boot - violating Rule 17 "
     "token_economy as well as lean_rag_storage. THE REAL CONFLICT, a design defect not mere "
     "verbosity: Rule 43 retro_clarity demands provenance inside the artifact so a successor "
     "needs no transcript, while lean_rag_storage demands a thin loaded store. S201-S204 "
     "resolved that silently in favour of retro_clarity and nothing measured the cost. "
     "RESOLUTION: measurement bodies belong in the bulk store; the item carries verdict, "
     "priority and a pointer. OWED: (1) a size clause in drift_audit that FAILS when the loaded "
     "store exceeds the bulk store - the gate is the deliverable; (2) migrate long S201-S204 "
     "note bodies behind pointers; (3) add/note refuse an oversized body unless it goes to bulk.",
     "P1"),

    ("COLD-BOOT-HAS-NO-RULES-S205", "ERROR",
     "An agent whose session-start refuses receives no rules at all, exactly when it needs them",
     "MEASURED S205 by reading the successor's Claude Code transcript (238 records, 58 tool "
     "calls) after its boot was refused. With no session open it had no operating_protocol, so "
     "it violated three rules it had never been given: it used the host scratchpad as its "
     "working directory (9 of 58 calls) against the no-side-store directive; it issued 13 "
     "rag_wait calls against 4 get-command-result, the WAIT-FOR-USED-AS-A-POLL-S198 signature, "
     "with Rule 44 no_polling undelivered; and its git discovery failed on all four commands "
     "because WORKTREE-PATH-UNRECORDED left it hunting for the repository. IT ALSO GOT THINGS "
     "RIGHT UNAIDED: first action through tmux-mcp, zero canonical writes before READY, and it "
     "went straight to gate_census.py and policy_census.py as the handoff instructs. THE "
     "STRUCTURAL POINT: discipline that arrives only through a successful boot is absent "
     "precisely at a cold start, which is when a fresh agent is least equipped to improvise. "
     "OWED: a refused boot must still render the boot-critical rule subset and say plainly that "
     "no session is open, so a blocked agent is governed rather than blind.",
     "P1"),

    ("SCRATCH-OUTSIDE-ROOT-S205", "ERROR",
     "The host scratchpad is used as project working storage, against the no-side-store rule",
     "OPERATOR DIRECTIVE: nothing this project depends on may live outside the project root. "
     "MEASURED S205: the successor wrote boot.txt, boot_status.txt and git.txt into the Claude "
     "host scratchpad and also to /tmp inside WSL; S201-S202 did the same with gate_check.py and "
     "a rule draft. NEITHER AGENT WAS BEING CARELESS - the Claude Code system prompt explicitly "
     "instructs agents to put temporary files in that scratchpad, and no RAG rule says "
     "otherwise, so every agent resolves the conflict in favour of the host. A conflict that is "
     "not encoded is decided by whoever speaks last. OWED: (1) an operating_protocol rule fixing "
     "project scratch at RAG/.boot/ and forbidding the host scratchpad; (2) a drift_audit clause "
     "that FAILS when project files are found in the host scratchpad, so the rule is a gate and "
     "not a preference.",
     "P1"),

    ("SEAL-ORDER-IN-AGENT-HANDS-S205", "ERROR",
     "The close ordering that keeps a seal true is agent discipline, and it failed twice",
     "MEASURED S205 and it is the direct cause of the successor's failed boot. After sealing "
     "S204 this agent re-rendered CLAUDE.md and committed the artefacts, moving HEAD from "
     "93dac18 to f157e38 AFTER the test-gate measurement and AFTER transfer_ready was set. The "
     "next boot then refused on a stale gate and a stale current_status. This is "
     "SEAL-NOT-INVALIDATED-BY-LATER-WRITES-S201 and RENDERED-CLAUDEMD-CHURNS-THE-SEAL-S203 "
     "occurring together, committed by the very agent that banked both. The ordering that keeps "
     "a seal true - render, commit, THEN measure, then seal, then nothing - exists only as "
     "discipline, and per Rule 45 discipline is not a control. OWED: session-end performs the "
     "render and the commit itself before it measures, so no agent can order it wrongly; and the "
     "seal is invalidated by any subsequent write, git or governed.",
     "P1"),

    ("BOOT-REFUSAL-WITHOUT-REMEDY-S205", "TASK",
     "A refused boot prints the diagnosis but not the one command that fixes it",
     "MEASURED S205: session-start refused with 'test gate STALE (measured @ 93dac18, live @ "
     "f157e38)' and 'current_status states HEAD 93dac18'. The kernel knew exactly what was wrong "
     "and that two commands fix it - tests --run and refresh-current-status - and printed "
     "neither. The successor stopped and the operator had to intervene. A gate that can name its "
     "own remedy and does not is making the human the recovery mechanism. OWED: every "
     "carry-forward refusal prints the exact copy-pasteable remedy for each finding it raises "
     "(Rule 43 retro_clarity applied to the kernel's own output).",
     "P2"),

    ("RESOLVED-CITES-DEAD-PATHS-S205", "ERROR",
     "3 RESOLVED items cite artifacts that do not exist, one of them closed by this agent",
     "MEASURED by grand_audit axis 3 at S205: 'cited artifacts exist on disk :: 3 cite only dead "
     "paths: MCP-REGISTRATION-SURFACE-S199, PI-BOOT-BLOCK-RECONCILE, PI-S176-PASTE'. The first "
     "was closed at S203 BY THIS AGENT citing .mcp.json, and the path does not resolve - so an "
     "evidence citation was accepted that points at nothing, in the same session that banked "
     "RESOLVE-EVIDENCE-GATE-NOT-ENFORCED-S202. Axis 3 also reports 10 of 324 RESOLVED items "
     "citing no artifact at all. OWED: resolve must verify the artifact path resolves at the "
     "moment of the transition, not merely that a string was supplied.",
     "P2"),

    ("KERNEL-DEAD-WIRING-S205", "TASK",
     "hook_entry is imported by nothing, and two scripts are referenced nowhere",
     "MEASURED by grand_audit axis 10 at S205: 'every kernel module is imported somewhere :: "
     "never imported outside itself/tests: hook_entry' and 'every script in RAG/scripts is "
     "referenced :: referenced nowhere: hash_probe_s202.py, s202_triage.py'. hook_entry is the "
     "process the hook layer actually executes, so its being unimported is expected and the "
     "check is wrong about it - that itself needs recording rather than silently tolerating a "
     "permanent FAIL. The two scripts are S202 one-offs that did their job; they are either "
     "registered with a purpose or removed. A permanent known-FAIL trains everyone to ignore the "
     "axis.",
     "P3"),
]

NOTES = [
    ("WORKTREE-PATH-UNRECORDED",
     "S205 CONFIRMED IN THE FIELD: the successor's git discovery failed on all four commands "
     "with 'fatal: not a git repository' because it searched the project root and RAG/, where no "
     "repository exists - the only git tree is GIT WORKTREES/rag-runtime-kernel. This item "
     "predicted exactly that. CHEAP FIX with high value: record the worktree path in "
     "current_status and render it in the generated CLAUDE.md, so no agent ever has to discover "
     "it."),
]


def run(args: list[str]) -> bool:
    r = subprocess.run([sys.executable, "-m", "rag_kernel", *args],
                       cwd=str(RAG), capture_output=True, text=True)
    out = (r.stdout or r.stderr).strip().splitlines()
    print(f"{'ok  ' if r.returncode == 0 else 'FAIL'} {out[-1][:100] if out else ''}")
    return r.returncode == 0


def main() -> int:
    bad = 0
    for iid, kind, title, note, pri in ITEMS:
        bad += not run(["add", iid, title, "--kind", kind, "--status", "OPEN",
                        "--session", S, "--note", note])
        bad += not run(["priority", iid, pri, "--session", S])
    for iid, note in NOTES:
        bad += not run(["note", iid, note, "--session", S])
    print(f"\nBANK {'PASS' if not bad else 'FAIL'} ({bad} failure(s))")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
