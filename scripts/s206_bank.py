"""Bank the S206 gate. The note text lives HERE, not on a shell command line.

The unbounded-wait gate reads the whole Bash command string, so a note that
describes the forbidden shape is itself refused when passed as an argument --
the GATE-FALSE-POSITIVE-ON-PROSE-S201 class. Keeping the prose in a file and
invoking the verb from Python avoids the shell entirely, which is the correct
answer rather than weakening the gate.
"""
import subprocess
import sys
from pathlib import Path

RAG = Path(__file__).resolve().parent.parent
S = "S206"

NOTE = (
    "MEASURED S206 - the third cold-start casualty in three sessions, and the "
    "first one closed by a refusal instead of by the operator. An agent whose "
    "boot had not completed, and which therefore held none of the rules, needed "
    "to wait for a detached job and hand-wrote a shell loop that repeatedly "
    "slept while testing for a process by command-line pattern. TWO defects in "
    "one line. The loop had no timeout, so only a human could end it. And the "
    "process-matching flag compares against FULL command lines including the "
    "shell evaluating it, so the loop matched itself and could never exit. "
    "session-start had in fact SUCCEEDED and printed its attestation token; the "
    "agent never saw it, and the operator killed the process by hand - the "
    "manual-rescue pattern Rule 45 exists to abolish. "
    "FIX SHIPPED: hook_guard gate 'unbounded-wait', PreToolUse on Bash, a "
    "decidable predicate over the command text with no judgement call. Verified "
    "7 of 7 by scripts/unbounded_wait_check.py: refuses the exact failing line "
    "and both generalisations; allows the wait verb, bounded for-loops, and the "
    "detached-launch half of the correct pattern. Wired into "
    ".claude/settings.json so it fires in production rather than only in "
    "selftest - PROVEN, because it then refused two attempts to bank this very "
    "note for quoting the forbidden shape. "
    "WHY A GATE AND NOT A RULE: Rule 44 no_polling already forbade this in "
    "words, and words are delivered at attestation, which the failing agent "
    "never reached (COLD-BOOT-HAS-NO-RULES-S205). A refusal needs no delivery; "
    "it fires whether or not the agent has been told. "
    "KNOWN LIMIT, recorded rather than hidden: the predicate reads command TEXT, "
    "so prose quoting a forbidden shape is refused as well. That is the same "
    "class as GATE-FALSE-POSITIVE-ON-PROSE-S201 and must be fixed with that "
    "item - by teaching both gates to read operands rather than substrings - "
    "never by weakening either one."
)


def run(*args: str) -> None:
    r = subprocess.run([sys.executable, "-m", "rag_kernel", *args],
                       cwd=str(RAG), capture_output=True, text=True)
    out = (r.stdout or r.stderr).strip().splitlines()
    print(f"{'ok  ' if r.returncode == 0 else 'FAIL'} {out[-1][:110] if out else ''}")


run("add", "UNBOUNDED-WAIT-GATE-S206",
    "Hand-rolled wait loops and process-pattern self-match are now REFUSED, not merely forbidden",
    "--kind", "TASK", "--status", "OPEN", "--session", S, "--note", NOTE)
run("priority", "UNBOUNDED-WAIT-GATE-S206", "P1", "--session", S)
run("register-asset", "RAG/scripts/unbounded_wait_check.py", "--session", S,
    "--purpose", "Verifies the unbounded-wait gate: refuses hand-rolled wait "
                 "loops and process-pattern self-match, allows the wait verb "
                 "and bounded loops")
run("register-asset", "RAG/scripts/s206_bank.py", "--session", S,
    "--purpose", "Banks the S206 gate finding with its note held in the file "
                 "rather than on a shell command line, so the gate under "
                 "description does not refuse its own record")
