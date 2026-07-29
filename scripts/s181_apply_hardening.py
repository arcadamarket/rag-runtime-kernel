"""S181 — apply the hardened values for SPEC-UNIVERSAL-8-UNHARDENED.

Drives the GOVERNED `update-rule` verb once per key (tool_contract: every
canonical write goes through a guarded verb, never a hand edit). Values come
from s181_hardened_rules.json; long values are passed via --value-file so no
rule text ever crosses a shell command line.

Usage:  python3 scripts/s181_apply_hardening.py [--apply] [--session S181]
Default is DRY-RUN.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

RAG_DIR = Path(__file__).resolve().parent.parent
VALUES = Path(__file__).resolve().parent / "s181_hardened_rules.json"


def main(argv: list[str]) -> int:
    apply = "--apply" in argv
    session = "S181"
    if "--session" in argv:
        session = argv[argv.index("--session") + 1]

    payload = json.loads(VALUES.read_text(encoding="utf-8"))
    keys = [k for k in payload if not k.startswith("_")]
    rag = json.loads((RAG_DIR / "RAG_MASTER.json").read_text(encoding="utf-8"))
    op = rag.get("operating_protocol") or {}

    print(f"{'APPLY' if apply else 'DRY-RUN'} — {len(keys)} rule(s), session {session}\n")
    failures = 0
    for key in keys:
        value = payload[key]
        if key not in op:
            print(f"  REFUSE {key}: absent from operating_protocol (update, not create)")
            failures += 1
            continue
        if op[key] == value:
            print(f"  SKIP   {key}: already at the hardened value")
            continue
        with tempfile.NamedTemporaryFile(
            "w", suffix=".txt", delete=False, encoding="utf-8"
        ) as fh:
            fh.write(value)
            tmp = fh.name
        cmd = [
            sys.executable, "-m", "rag_kernel", "update-rule", key,
            "--value-file", tmp, "--session", session,
        ]
        if not apply:
            cmd.append("--dry-run")
        proc = subprocess.run(cmd, cwd=RAG_DIR, capture_output=True, text=True)
        Path(tmp).unlink(missing_ok=True)
        tail = (proc.stdout + proc.stderr).strip().splitlines()
        note = tail[-1][:150] if tail else ""
        status = "OK  " if proc.returncode == 0 else "FAIL"
        if proc.returncode != 0:
            failures += 1
        print(f"  {status}   {key}  ({len(value)} chars)  {note}")

    print(f"\nfailures: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
