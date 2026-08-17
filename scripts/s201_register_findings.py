#!/usr/bin/env python
"""Register every S201 finding into the canonical stores. ONE run, idempotent-ish.

L4 of the audit protocol — EVERYTHING IS A SCRIPT: a finding that reaches the RAG
because an agent remembered to type eleven `add` commands is a hope. This is the
deterministic form, so the same set lands the same way and can be re-read, diffed
and re-run.

Every item below was MEASURED at S201 on the shipped interpreter
(Windows / CPython 3.14.3), not inherited and not asserted.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

RAG_DIR = Path(__file__).resolve().parent.parent
SESSION = "S201"

# (id, kind, status, title, note)
FINDINGS = [
    # ---------------------------------------------------------------- fixed
    ("MCP-STDIO-FRAMING-S201", "ERROR", "RESOLVED",
     "MCP server wrote LSP Content-Length framing on a newline-delimited transport",
     "Root cause of ACTIVATION-GAP-S197. mcp_transport._write_message emitted "
     "'Content-Length: N\\r\\n\\r\\n{json}'. MCP stdio is one JSON object per line, "
     "so every real client hung at initialize and the server registered zero "
     "tools. Fixed b05dc51 lineage; verified with a client-shaped probe: "
     "initialize answered, rag_wait executed. Does NOT by itself close "
     "ACTIVATION-GAP-S197, whose condition is a live rag_wait FOUND through the "
     "client after a restart."),

    ("PROBE-DEFINES-ITS-OWN-PROTOCOL-S201", "ERROR", "OPEN",
     "S198 verified the MCP server with a probe speaking the server's own dialect",
     "The S198 handoff recorded 'VERIFIED over stdio: initialize, tools/list 13 "
     "tools, rag_wait FOUND'. All true, and all against a wire format no client "
     "speaks, because the probe was built from the same assumption as the server. "
     "This is the S200 heartbeat finding in a new place. RULE ADOPTED: A PROBE MAY "
     "NOT DEFINE THE PROTOCOL IT VERIFIES. Stays OPEN until that rule is a gate, "
     "not a sentence."),

    ("TEST-PINNED-THE-WRONG-PROTOCOL-S201", "ERROR", "RESOLVED",
     "test_write_message asserted Content-Length, protecting the defect with coverage",
     "The suite was green about a server that could never load. A test that pins "
     "the wrong contract does not merely miss a bug, it defends it. Now asserts "
     "one line of JSON, no Content-Length, parseable."),

    ("GATE-REFUSED-THE-MANDATED-BOOT-S201", "ERROR", "RESOLVED",
     "sandbox-state gate refused the boot command CLAUDE.md mandates",
     "The hook layer's FIRST live firing in its existence refused "
     "'session-start --rag RAG_MASTER.json'. The predicate asked whether the "
     "command CONTAINED the canonical filename and never what named it. Now "
     "judged per shell segment: canonical state reached outside a governed kernel "
     "verb. Laundering by an adjacent verb stays refused. 8 cases verified."),

    ("GATE-REASON-ASSERTED-FALSE-ENVIRONMENT-S201", "ERROR", "RESOLVED",
     "sandbox-state refusal text claimed 'this shell is the Cowork sandbox'",
     "False under Claude Code, where Bash is an allowlisted transport, and it "
     "routed the agent to tmux-mcp which is not on the Windows PATH. A refusal "
     "that misnames the environment sends the next agent somewhere that does not "
     "exist."),

    ("DETACH-RUN-DEAD-ON-WINDOWS-S201", "ERROR", "RESOLVED",
     "RUN-DETACH-AWAIT could not start a process on the deployed platform",
     "THE ROOT CAUSE OF THE RECURRING POLLING DEFECTS. detach_run defaulted to "
     "shell='/bin/bash' with start_new_session=True; neither exists on Windows, so "
     "9 tests died with FileNotFoundError WinError 2. The project's most-repeated "
     "rule - launch detached, block once, never poll - had NO working "
     "implementation on the operator's machine, which is why E-081 recurred as "
     "E-116 and again as E-128 and why forensics shows polling bursts in 5 of the "
     "11 sessions S190-S200. Now resolved through rag_kernel.toolchain."),

    ("STALE-LOCK-IMMORTAL-ON-WINDOWS-S201", "ERROR", "RESOLVED",
     "persistence._pid_alive read every dead pid as ALIVE on Windows",
     "os.kill(pid, 0) raises a bare OSError on Windows for a pid that does not "
     "exist; the catch-all read that as 'unknown -> ALIVE (fail closed)'. So a "
     "killed agent bricked the deployment until someone hand-deleted the lock "
     "sidecar - the exact failure the lock's own docstring calls strictly worse "
     "than the race it prevents. Replaced with OpenProcess/GetExitCodeProcess, "
     "keeping POSIX fail-closed semantics."),

    ("S200-GUARD-RED-ON-THE-SHIPPED-PLATFORM-S201", "ERROR", "RESOLVED",
     "The S200 anti-forgery guard failed on Windows for a correctly isolated run",
     "test_suite_cannot_reach_the_production_heartbeat asserted "
     "'Path.home() not in resolved.parents'. On Windows the temp dir IS under "
     "home, so this project's single most important protection - a test may never "
     "write a fact the audit reads - reported RED on the platform that matters. A "
     "guard that cannot pass where it counts gets deleted, and then the thing it "
     "guards returns. Now asserts the actual invariant: not the production "
     "heartbeat path."),

    ("AUDITOR-WRITTEN-FOR-ANOTHER-MACHINE-S201", "ERROR", "RESOLVED",
     "grand_audit probed python3, POSIX-only jar paths, and tmux on the Windows PATH",
     "The auditor whose first law is 'nothing measured below a broken transport is "
     "trustworthy' WAS the broken transport: every probe spelled 'python3', which "
     "on this host is the Microsoft Store stub, so axis 1 reported 'kernel CLI "
     "responds :: FAIL' and every axis-2 gate measured the stub. Its jar search "
     "shelled out to POSIX find over /home /opt /usr/local, looked nowhere on "
     "Windows, and then asserted 'search completed, jar absent' - a FAIL from a "
     "search that never ran, against its own L1. Axis 1 now 15 PASS / 0 FAIL / "
     "0 UNKNOWN, including a real TLC execution."),

    ("SUITE-MEASURED-ON-A-FOREIGN-INTERPRETER-S201", "ERROR", "RESOLVED",
     "2,818 green was measured under WSL/Python 3.12; the kernel runs Windows/3.14",
     "pytest was absent from the shipped interpreter, so the inherited number "
     "described a different program on a different filesystem. Measured on the "
     "SHIPPED interpreter for the first time: 14 failures. All 14 fixed; "
     "2,818 green now stamped at b05dc51 on Windows CPython 3.14.3."),

    ("TOOLCHAIN-CONSOLIDATION-S201", "TASK", "RESOLVED",
     "One measured toolchain manifest inside the project root",
     "Five platform assumptions were written inline at their call sites where "
     "nothing could audit them. rag_kernel/toolchain.py measures them once and "
     "writes toolchain/toolchain.json inside the project root; detach_run and "
     "grand_audit consume it. Paths absolute, because a relative tool path is "
     "true only where it was measured. Operator ruling S201: nothing this project "
     "depends on lives in a side store outside the root."),

    ("GRAND-AUDITOR-UNVERSIONED-S201", "ERROR", "RESOLVED",
     "scripts/grand_audit.py was under no version control at all",
     "The project's top-level auditor lived only in the deployed RAG/scripts, "
     "outside the git worktree: no history, no travel to a clone, and every edit "
     "to it permanently uncommitted by construction. Now tracked at "
     "scripts/grand_audit.py. NOTE: it is now a two-copy asset like rag_kernel/*, "
     "and the deploy-parity gate does not yet cover scripts/ - see "
     "DEPLOY-PARITY-MISSES-SCRIPTS-S201."),

    # ---------------------------------------------------------------- open
    ("SEAL-NOT-INVALIDATED-BY-LATER-WRITES-S201", "TASK", "OPEN",
     "A seal survives writes made after it, so a transfer can falsify itself",
     "MEASURED: S200 sealed with transfer_ready=true and audit 0 errors, then "
     "committed CLAUDE.md as 3409933. That single act moved HEAD past the commit "
     "the 2,818 measurement was taken at, put a governed file outside the "
     "boot-map, and left current_status pinned to the old hash. The operator's "
     "first boot met 5 errors under a banner saying zero. FIX: transfer_ready and "
     "the test-gate stamp must go false on the next governed or git write after a "
     "seal. Ships with SealInvalidation.tla + a _naive.cfg that fails without it."),

    ("GATE-FALSE-POSITIVE-ON-PROSE-S201", "TASK", "OPEN",
     "sandbox-state refuses a command that only NAMES canonical state in prose",
     "MEASURED S201: 'git commit -m \"...RAG_MASTER.json...\"' is refused. The "
     "command performs no state access; the filename appears in a message body. "
     "The commit was reworded rather than the gate weakened. FIX: operand-aware "
     "parsing - a filename inside a -m/--message argument is not an operand. Until "
     "then, do not arm the hard ERROR gate over it."),

    ("ITEMS-VERB-CRASHES-ON-WINDOWS-S201", "ERROR", "OPEN",
     "rag_kernel items dies with UnicodeEncodeError on the operator's console",
     "cp1252 cannot encode U+26A0 in an item title, so the MANDATED read path for "
     "state raises mid-render. Worked around all session with PYTHONIOENCODING=utf-8. "
     "FIX: force UTF-8 on stdout/stderr at kernel entry on Windows, unconditionally. "
     "This is a sanctioned verb failing on the only platform this project runs on."),

    ("BOOT-DOC-COMMANDS-A-STUB-INTERPRETER-S201", "ERROR", "OPEN",
     "CLAUDE.md tells every session to boot with python3, which is the Store stub",
     "CLAUDE.md section 1 mandates 'python3 -m rag_kernel session-start' as the "
     "first action of every session. On this host python3 prints a Microsoft Store "
     "advert and exits. .claude/settings.json line 49 records 'python3.exe on this "
     "host is the WindowsApps stub', MEASURED at S195 - the fact was known and "
     "written into the handoff anyway. FIX: CLAUDE.md must carry the measured "
     "interpreter from toolchain/toolchain.json, not a guessed name."),

    ("DEPLOY-PARITY-MISSES-SCRIPTS-S201", "TASK", "OPEN",
     "The deploy-parity gate covers rag_kernel/*.py only, not scripts/",
     "grand_audit.py is now a two-copy asset (worktree + deployed RAG/scripts) with "
     "no gate watching the pair, which is the drift the gate exists to prevent. "
     "FIX: widen the PostToolUse deploy-parity matcher to scripts/*.py."),

    ("MEASUREMENT-PROVENANCE-S201", "TASK", "OPEN",
     "A recorded measurement does not carry the platform and interpreter it was taken on",
     "The generalisation of SUITE-MEASURED-ON-A-FOREIGN-INTERPRETER-S201. '2,818 "
     "green' was recorded with a commit but no interpreter and no platform, so a "
     "number measured under WSL/3.12 read as a fact about Windows/3.14 for eight "
     "sessions. FIX: every measured stamp records interpreter, platform and commit, "
     "and the audit REFUSES a claim measured somewhere other than where it ships. "
     "Ships with MeasurementProvenance.tla + _naive.cfg."),

    ("SELF-CERTIFYING-EVIDENCE-GATE-S201", "TASK", "OPEN",
     "Generalise the S200 heartbeat fix from one file into a refusal",
     "FOUR instances of one class are now measured: pytest stamping the heartbeat "
     "the audit read (S200); the S198 probe speaking the server's own dialect; "
     "test_write_message pinning the wrong protocol; and the transfer commit "
     "falsifying its own seal. THE CLASS: a check that supplies its own evidence "
     "certifies whatever it is pointed at. FIX: no audit input may have the thing "
     "under test as its only writer. Ships with SelfCertifyingEvidence.tla + "
     "_naive.cfg."),
]


def main() -> int:
    failures = 0
    for item_id, kind, status, title, note in FINDINGS:
        cmd = [sys.executable, "-m", "rag_kernel", "add", item_id, title,
               "--kind", kind, "--status", status, "--session", SESSION,
               "--note", note]
        r = subprocess.run(cmd, cwd=str(RAG_DIR), capture_output=True, text=True)
        ok = r.returncode == 0
        failures += not ok
        tail = (r.stdout or r.stderr).strip().splitlines()
        print(f"{'ok  ' if ok else 'FAIL'} {item_id:44} {tail[-1][:70] if tail else ''}")
    print(f"\nREGISTERED {len(FINDINGS) - failures}/{len(FINDINGS)}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
