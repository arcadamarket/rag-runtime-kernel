"""S202 — measure exactly which stored hashes disagree with the live content.

Read-only. Exists because `verify_hashes` is implemented, correct, and consumed
by nothing: `audit` reports 0 errors and `verify` reports OK over a HOT whose own
integrity hash does not match its content.
"""
import json
import sys
from pathlib import Path

RAG_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAG_DIR))
from rag_kernel import persistence  # noqa: E402

hot = json.loads((RAG_DIR / "RAG_MASTER.json").read_text(encoding="utf-8-sig"))
problems = persistence.verify_hashes(hot)

print(f"verify_hashes -> {len(problems)} problem(s)")
for p in problems:
    print("  ", str(p)[:160])

meta = hot.get("meta") or {}
for k in sorted(meta):
    if "hash" in k.lower():
        print(f"  meta.{k} = {str(meta[k])[:40]}")
for k in sorted(hot):
    if "hash" in k.lower():
        print(f"  hot.{k} = {str(hot[k])[:40]}")
print("  meta.written_by_session =", meta.get("written_by_session"))
print("  meta.last_checkpoint_seq =", meta.get("last_checkpoint_seq") or meta.get("seq"))
