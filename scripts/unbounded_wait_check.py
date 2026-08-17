"""Verify the unbounded-wait gate: refuses hand-rolled waits, allows the verb.

Lives in the project, not the host scratchpad (SCRATCH-OUTSIDE-ROOT-S205), and
builds its payloads from fragments so this file's own text cannot trip the gate
that guards the agent editing it.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from rag_kernel import hook_guard as g  # noqa: E402

W, S, PG = "while", "sleep", "pgrep"
CASES = [
    (f'{W} {PG} -f "rag_kernel session-start" >/dev/null; do {S} 3; done',
     False, "the exact S206 hang: unbounded loop AND pgrep self-match"),
    (f'until test -f /tmp/x; do {S} 5; done', False, "unbounded until-loop"),
    (f'{PG} -af rag_kernel', False, "pgrep -f matches the shell running it"),
    ("python -m rag_kernel wait-for /tmp/log --timeout 900 --contains DONE",
     True, "the sanctioned verb, timeout mandatory"),
    ("for i in $(seq 1 5); do echo $i; done", True, "bounded loop, no sleep"),
    ("nohup bash -c 'pytest -q > /tmp/l 2>&1; echo SUITE-DONE >> /tmp/l' &",
     True, "detached launch, the correct half of the pattern"),
    ("git status --short", True, "unrelated"),
]

fails = 0
for cmd, want, why in CASES:
    d = g.decide("unbounded-wait", {"tool_name": "Bash", "tool_input": {"command": cmd}})
    ok = d.allow is want
    fails += not ok
    print(f"{'ok  ' if ok else 'FAIL'} {'ALLOW ' if d.allow else 'REFUSE'} | {why}")

print(f"\nUNBOUNDED-WAIT-GATE {'PASS' if not fails else 'FAIL'} ({fails} failure(s))")
raise SystemExit(1 if fails else 0)
