#!/usr/bin/env python
"""POLICY CENSUS — the REVERSE sweep: policy that lives in CODE, not in the RAG.

WHY THIS EXISTS, and it is the operator's standing complaint made mechanical.
scripts/gate_census.py sweeps RAG -> code: for each operating_protocol rule, is
anything enforcing it? It found 31 orphans. But it is structurally blind in the
other direction, and that blindness is how S203 discovered BY ACCIDENT that
"NEVER poll a running command (E-081)" — the most-violated discipline in this
project's history — was never a rule at all. It is a hardcoded print inside the
[BOOT-FRAME] block of __main__.py. A rule that lives in a print statement cannot
be classified, cannot be rendered into the boot document, and cannot be amended
by update-rule. It is decoration wearing the voice of policy.

So this is the other half: sweep CODE -> RAG. Find every string literal in the
kernel that SPEAKS LIKE POLICY, and ask whether any operating_protocol key
accounts for it.

  UNGOVERNED  policy-shaped text in code that no rule key can be matched to.
              Every one of these is a rule the RAG does not know it has.
  TRACED      policy-shaped text that overlaps an existing rule's wording.

Together the two censuses answer the question the project could never answer:
what is under control, and what is merely written down somewhere.

Read-only. Needs no session. Run it whenever the kernel changes.
"""
from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

RAG_DIR = Path(__file__).resolve().parent.parent
ROOT = RAG_DIR.parent
WT = ROOT / "GIT WORKTREES" / "rag-runtime-kernel"

#: Language that makes a string an instruction rather than a message.
POLICY = re.compile(
    r"\b(NEVER|ALWAYS|MUST NOT|MUST\b|FORBIDDEN|REFUSED?|PROHIBIT|"
    r"HARD RULE|DO NOT|SHALL|REQUIRED|MANDATOR)", re.I)
#: Noise: test fixtures, argparse help, and our own censuses quoting policy.
SKIP_FILES = ("tests/", "scripts/policy_census.py", "scripts/gate_census.py",
              "scripts/pi_coverage_check.py")
STOP = set("""the a an and or of to in is are be for it that this with as at by on
not no if then than from can may must will shall do does did have has had you your
we our they their he she them these those which what when where who whom whose""".split())


def words(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z_]{4,}", s.lower()) if w not in STOP}


def main() -> int:
    rag = json.loads((RAG_DIR / "RAG_MASTER.json").read_text(encoding="utf-8-sig"))
    op = rag.get("operating_protocol") or {}
    rule_vocab = {k: words(k.replace("_", " ") + " " + str(v)) for k, v in op.items()}

    ungoverned: list[tuple[str, int, str]] = []
    traced = 0
    scanned = 0

    for f in sorted((WT / "rag_kernel").rglob("*.py")):
        rel = f.relative_to(WT).as_posix()
        if "__pycache__" in rel or any(s in rel for s in SKIP_FILES):
            continue
        try:
            tree = ast.parse(f.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            text = " ".join(node.value.split())
            if len(text) < 45 or not POLICY.search(text):
                continue
            scanned += 1
            w = words(text)
            best = max(((k, len(w & v) / max(len(w), 1))
                        for k, v in rule_vocab.items()),
                       key=lambda kv: kv[1], default=("", 0.0))
            if best[1] >= 0.30:
                traced += 1
            else:
                ungoverned.append((rel, node.lineno, text[:150]))

    print(f"policy-shaped strings scanned: {scanned}   traced to a rule: {traced}"
          f"   UNGOVERNED: {len(ungoverned)}")
    by_file: dict[str, int] = {}
    for rel, _, _ in ungoverned:
        by_file[rel] = by_file.get(rel, 0) + 1
    print("\n=== UNGOVERNED POLICY BY FILE ===")
    for rel, n in sorted(by_file.items(), key=lambda kv: -kv[1])[:15]:
        print(f"  {n:>4}  {rel}")
    print("\n=== SAMPLE (first 12) ===")
    for rel, line, text in ungoverned[:12]:
        print(f"  {rel}:{line}\n      {text}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
