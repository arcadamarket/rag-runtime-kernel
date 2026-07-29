"""S181 — identify operating_protocol rules still holding VERBATIM INIT-spec text.

SPEC-UNIVERSAL-8-UNHARDENED: a rule whose stored value equals the spec's own
declared value is UNHARDENED. Adoption reports it "identical" in both kernels,
which reads as agreement but is really absence — neither deployment carries
field-earned content for it, so birth transmits boilerplate.

Authority A (same as transplant): the universal key set is whatever SpecParser
produces from the named INIT spec. Read-only; never writes.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

RAG_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAG_DIR))

from rag_kernel.spec_parser import SpecParser  # noqa: E402
from rag_kernel.transplant import universal_keys_from_spec  # noqa: E402

DEFAULT_SPEC = "INIT_UNIVERSAL_RUNTIME_KERNEL_v3.2.8.md"


def main(spec_name: str = DEFAULT_SPEC, preview: str = "400") -> int:
    spec_path = RAG_DIR / spec_name
    keys, spec_version = universal_keys_from_spec(spec_path)
    parsed = SpecParser().parse_file(spec_path)
    spec_op = (parsed.merged or {}).get("operating_protocol") or {}
    rag = json.loads((RAG_DIR / "RAG_MASTER.json").read_text(encoding="utf-8"))
    op = rag.get("operating_protocol") or {}
    width = int(preview)

    identical: list[str] = []
    hardened: list[str] = []
    missing: list[str] = []
    for key in sorted(keys):
        if key not in op:
            missing.append(key)
        elif op[key] == spec_op.get(key):
            identical.append(key)
        else:
            hardened.append(key)

    print(f"spec v{spec_version} ({spec_name}) — universal keys: {len(keys)}")
    print(f"  hardened (value diverges from spec): {len(hardened)}")
    print(f"  VERBATIM SPEC TEXT (unhardened):     {len(identical)}")
    print(f"  missing from this kernel:            {len(missing)}")
    print()
    for key in identical:
        val = spec_op.get(key)
        text = val if isinstance(val, str) else json.dumps(val, ensure_ascii=False)
        print(f"--- {key} ---")
        print(f"    {text[:width]}")
        print()
    if missing:
        print("MISSING FROM THIS KERNEL: " + ", ".join(missing))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(*sys.argv[1:]))
