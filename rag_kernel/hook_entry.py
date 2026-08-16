#!/usr/bin/env python3
"""Host-side entry point for the HOOK-ENFORCEMENT-LAYER (S195).

``.claude/settings.json`` cannot call a module — it calls a command line, on the
HOST, before the tool runs. That command has to satisfy three constraints the
rest of the kernel never faces:

1. It runs under the host interpreter (Windows ``python``), not the WSL one the
   session's shell work uses, so it may not assume the package is installed or
   on ``sys.path``.
2. It runs on EVERY matched tool call, so it must import almost nothing —
   ``hook_guard`` is stdlib-only and costs ~0.1s cold.
3. It must never take the session down. A hook that raises on startup is a hook
   that fires on every call and blocks nothing; this file therefore catches
   everything and exits 0, leaving ``run_gate`` to do the same at the policy
   layer (both failures are announced on stderr — see ``hook_guard`` for why the
   fail-open is declared rather than silently chosen).

It lives inside the package so it is deployed by the same mirror that deploys
the kernel: a wiring file that can drift from the logic it wires is a second
copy of the deploy-parity problem this very layer exists to report.

Usage (from .claude/settings.json):
    python "<PROJECT>/RAG/rag_kernel/hook_entry.py" --gate poll
"""

from __future__ import annotations

import os
import sys


def main(argv: "list[str] | None" = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    gate = ""
    for i, tok in enumerate(argv):
        if tok == "--gate" and i + 1 < len(argv):
            gate = argv[i + 1]
        elif tok.startswith("--gate="):
            gate = tok.split("=", 1)[1]
    if not gate:
        print("[hook_entry] no --gate given; allowing", file=sys.stderr)
        return 0

    here = os.path.dirname(os.path.abspath(__file__))       # .../RAG/rag_kernel
    pkg_parent = os.path.dirname(here)                      # .../RAG
    project_root = os.path.dirname(pkg_parent)              # the project root
    if pkg_parent not in sys.path:
        sys.path.insert(0, pkg_parent)

    try:
        from rag_kernel.hook_guard import run_gate
    except Exception as exc:  # pragma: no cover - import-time host failure
        print(f"[hook_entry] FAILED OPEN — cannot import hook_guard: "
              f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 0

    try:
        raw = sys.stdin.read()
    except Exception:  # pragma: no cover - no payload, nothing to judge
        raw = ""
    # HEARTBEAT-PROVENANCE (S200): this file is the ONLY caller entitled to
    # stamp a liveness heartbeat, because it is the only one the client invokes.
    # `_heartbeat_source` downgrades it to "test" under pytest regardless, so a
    # test that reaches this line still cannot forge the proof — which is
    # precisely what tests/test_hook_enforcement_layer.py did until S200.
    return run_gate(gate, raw, project_root=project_root, source="hook_entry")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
