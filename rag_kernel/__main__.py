"""CLI entry point for the RAG Runtime Kernel.

Usage:
    python -m rag_kernel init --spec path/to/INIT_v3.1.8.md [--output RAG/] [--root-project ...]
    python -m rag_kernel health [--path .]
    python -m rag_kernel serve --project ~/my-project/RAG [--port 7437] [--host 127.0.0.1]
    python -m rag_kernel mcp --project ~/my-project/RAG
    python -m rag_kernel configure --rag RAG/RAG_MASTER.json --context project_context.json [--consume]
    python -m rag_kernel session start S1 [--rag-dir RAG/]
    python -m rag_kernel session close S1 [--rag-dir RAG/]
    python -m rag_kernel checkpoint --rag RAG/RAG_MASTER.json --session S1 --summary "..."
    python -m rag_kernel gc [--path .] [--dry-run]
    python -m rag_kernel audit-env [--path .] [--json]
    python -m rag_kernel graph run spec.json [--project .] [--schedule levels]
    python -m rag_kernel resolve <item-id> --session S50 [--rag RAG/RAG_MASTER.json] [--reason "..."]
    python -m rag_kernel defer <item-id> --session S50 [--reason "..."]
    python -m rag_kernel items [--status OPEN] [--kind TASK] [--json]
    python -m rag_kernel context set <partition> '<json>' [--value-file F] [--rag-dir RAG/] [--dry-run]
    python -m rag_kernel context get <partition> [--rag-dir RAG/] [--json]
    python -m rag_kernel context list [--rag-dir RAG/] [--json]

Commands:
    init       Parse init prompt MD and create RAG_MASTER.json deterministically (zero tokens).
    configure  Merge project-specific context into an existing RAG_MASTER.json.
    health     Verify all rag_kernel modules are importable and functional.
    serve      Start the HTTP API server (for GPT Web / direct access).
    mcp        Start the MCP stdio server (for Claude Desktop).
    session    Start or close a session logger (wraps SessionLogger open/close).
    checkpoint Merge session summary into RAG_MASTER.json atomically.
    gc         Garbage collector — clean __pycache__, .pyc, .tmp, orphaned files.
    audit-env  Audit environment — enumerate Python versions, pip, package managers, project deps.
    graph      Run a Graph Orchestrator DAG (JSON spec) through the kernel runtime.
    resolve    Guarded lifecycle transition of a tracked item to RESOLVED
               (siblings: defer, reopen, start, discard, supersede) via drift_store.
    items      List the canonical tracked_items array (read-only render).
    render     Render legacy open_tasks/deferred_items/backlog/ERROR_LOG from tracked_items (--apply to write).
    context    Read/write the sanctioned, non-loaded RAG_CONTEXT.json project-context store (set|get|list).

Design doc reference: v3.2_ARCHITECTURE_DESIGN.md section 9
Satisfies: M-026 (CLI entry point), V33-BOOTSTRAP (init command), ENH-008 (session/checkpoint/gc), GRAPH-ORCH runtime-wiring (graph command), DRIFT-ELIM increment 3 (resolve|defer|… + items), DRIFT-ELIM increment 4 (render)

@rag-kernel-manifest
{
  "module": "rag_kernel.__main__",
  "capability": "cli",
  "description": "CLI entry point — dispatches init, health, serve, mcp, configure, session, checkpoint, gc commands",
  "commands": {
    "init": "Parse init prompt MD → RAG_MASTER.json (zero tokens)",
    "health": "Verify all modules importable and functional",
    "serve": "Start HTTP API server",
    "mcp": "Start MCP stdio server",
    "configure": "Merge project-specific context into existing RAG (--consume deletes the transient input after a verified merge, FIX-11 inc3/U3)",
    "session": "Start or close session logger (wraps SessionLogger)",
    "checkpoint": "Merge session summary into RAG_MASTER.json atomically",
    "gc": "Garbage collector — clean temp files, pycache, orphans",
    "audit-env": "Audit environment — enumerate Python versions, pip, package managers, project deps",
    "graph": "Run a Graph Orchestrator DAG (JSON spec) through the kernel runtime",
    "resolve|defer|reopen|start|discard|supersede": "Guarded lifecycle transition of a tracked item via drift_store (DRIFT-ELIM)",
    "items": "Read-only render of the canonical tracked_items array",
    "intent-audit": "Session-START plan-vs-settled gate: verify a stated plan honors the next_session_directive (ID-binding + normalized-exact restatement) and load the SOURCE decisions — KA-INTENT-FIDELITY inc2",
    "render": "Render legacy open_tasks/deferred_items/backlog/ERROR_LOG from tracked_items; --apply rewrites the legacy arrays atomically (DRIFT-ELIM increment 4)",
    "note": "Refresh a tracked item's one-line note through the guarded API (status untouched) — DRIFT-ELIM increment 5 (INS-038)",
    "cite": "Attach evidence to a tracked item without moving its status — the only path that can cite an already-RESOLVED item (EVIDENCE-AMENDMENT, S191)",
    "priority": "Set a tracked item's Rule 21 priority_group (P1..P5, or \"\" to clear) through the guarded API (status untouched) — REPORT-PRIORITY-GROUPS inc1",
    "audit": "Fail-loud session auditor: renders match canonical, supersede refs resolve, notes don't contradict status, no side stores — DRIFT-ELIM increment 5",
    "add": "Add a NEW canonical tracked item through the guarded atomic store (fail-loud on duplicate id)",
    "errlog-migrate": "Fold every ERROR_LOG.md E-number into tracked_items as kind=ERROR in one atomic, idempotent write (ERRLOG-MIGRATION, S190)",
    "acceptance": "Boot-readiness acceptance check for the kernel and every registered deployment — the question `audit` cannot answer: would a successor session actually start? (S190 P3 wiring)",
    "add-rule": "Append a NEW operating_protocol rule through the guarded atomic store (FIX-5/P3, fail-loud on existing key)",
    "update-rule": "Re-set an EXISTING operating_protocol rule (string or dict/JSON value) or one sub-key of a dict rule through the guarded atomic store (UPDATE-RULE-VERB, fail-loud on a missing target unless --create)",
    "migrate": "Migrate a DEPLOYMENT's RAG meta up to the schema this kernel speaks — declared additive ladder, reads the target's own meta, refuses to downgrade a deploy that is ahead, fails loud on an unknown origin version, no-op when already current (KA-SCHEMA-MIGRATE)",
    "refresh-current-status": "Re-stamp current_status machine-facts (runtime version + git HEAD, optional --tests count) through the guarded atomic store — the governed repair for the E-043 freshness guard (KA-CS-REFRESH)",
    "prune-current-status": "Remove ARCHIVED session-stamped keys from current_status through the guarded atomic store — the governed repair for the META-SETTER-GAP residue refresh-current-status cannot reach (S187)",
    "meta": "Read or SET a declared meta.* scalar through the guarded atomic store — REFUSE-BY-DEFAULT allowlist, containers refused by name, typed coercion, no-op when already correct (META-SETTER-GAP, S188)",
    "tests": "Measured test gate: --run executes the suite and stamps meta.test_gate with the count AND the runtime/git HEAD it was measured against; --verify grades that stamp against live facts so a cached pass decays to STALE (REPORT-TESTS-GATE-UNMEASURED, S188)",
    "forensics": "Render a session's CONDUCT from its own log — wall time, governed calls, failed verbs and their real cost, silent gaps, repeat bursts, double seals; the numbers any account of a session must cite (SELF-DIAGNOSIS-UNSOURCED, S188)",
    "list-kinds": "Print the INGEST kinds THIS deployment declares, with destinations — the authoritative set `ingest` enforces; a sender that cannot enumerate them can only guess (INGEST-KIND-UNVALIDATED, S187)",
    "measured": "List MEASURED provenance stamps in project documents and flag the ones the live runtime/spec has outrun — the machine form of 're-measure before you trust this document' (RUNBOOK-TABLE-NO-INVARIANT, S187)",
    "verify": "Deterministic post-init HOT↔COLD self-version coherence gate (FIX-2)",
    "context": "Read/write the sanctioned, non-loaded RAG_CONTEXT.json project-context store (set|get|list) — FIX-11 inc2 / U3"
  },
  "use_when": "Any CLI invocation of rag_kernel"
}
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

from rag_kernel import hook_guard
from rag_kernel.hook_guard import run_gate, selftest
from rag_kernel.api import DEFAULT_PORT, KernelApp, create_server
from rag_kernel.mcp_transport import MCPServer
from rag_kernel.session_forensics import CALLER_AGENT, CALLER_ENV

# KA-9: a whole-value human-fill session-zero placeholder ("<from user>",
# "<absolute path>") — an angle-bracket token carrying a lowercase letter or
# space. Distinct from the UPPER_SNAKE <SPEC_VERSION> the spec parser substitutes
# (left untouched here so the parser still owns it). Used by cmd_init to null
# unfilled project_context placeholders so a fresh deploy is born clean.
_PC_TEMPLATE_TOKEN_RE = re.compile(r"<[^<>]*[a-z ][^<>]*>")

# DRIFT-ELIM increment 3 — item-lifecycle CLI verbs.
# Each top-level verb maps to the ItemStatus it transitions a tracked item into;
# legality is decided by the drift_control lifecycle guard, not by the CLI.
_ITEM_VERB_STATUS = {
    "resolve": "RESOLVED",
    "defer": "DEFERRED",
    "reopen": "OPEN",
    "start": "IN_PROGRESS",
    "discard": "DISCARDED",
    "supersede": "SUPERSEDED",
}
_ITEM_VERB_HELP = {
    "resolve": "Transition a tracked item to RESOLVED (from IN_PROGRESS).",
    "defer": "Park a tracked item: -> DEFERRED.",
    "reopen": "Re-enter a DEFERRED item: DEFERRED -> OPEN.",
    "start": "Begin a tracked item: OPEN -> IN_PROGRESS.",
    "discard": "Drop a tracked item: -> DISCARDED.",
    "supersede": "Replace a tracked item: -> SUPERSEDED (requires --by).",
}


def _default_rag_path() -> Path:
    """Layout-aware default for ``--rag`` (FIX-6 / K9).

    The historical default ``RAG/RAG_MASTER.json`` assumes the command is run
    from the project root. In a nested deploy layout (``rag_kernel/`` living
    *under* ``RAG/``), running from inside the RAG dir made that default resolve
    to ``RAG/RAG/RAG_MASTER.json`` — the doubled path the eBay Session-Zero
    deploy hit (K9), which simply errors "not found".

    This resolves the RAG whether invoked from the project root OR from inside the
    RAG dir, by returning the first EXISTING candidate (a read-only existence
    probe — deterministic, no I/O beyond ``stat``):

      1. ``RAG/RAG_MASTER.json``  — run from the project root (canonical layout)
      2. ``RAG_MASTER.json``      — run from inside the RAG dir (no RAG/ prefix)

    If neither exists, it returns the canonical root-layout path so the command's
    own not-found error stays sensible. It never prepends ``RAG/`` to a path that
    already lives in the RAG dir, so it cannot double ``RAG/RAG``.
    """
    candidates = (
        Path("RAG") / "RAG_MASTER.json",  # project root
        Path("RAG_MASTER.json"),          # inside the RAG dir
    )
    for c in candidates:
        if c.exists():
            return c
    return candidates[0]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rag_kernel",
        description="RAG Runtime Kernel - OS-level runtime bridge for LLM memory persistence.",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # -- serve --
    serve_parser = subparsers.add_parser("serve", help="Start the HTTP API server.")
    serve_parser.add_argument("--project", type=Path, required=True)
    serve_parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    serve_parser.add_argument("--host", type=str, default="127.0.0.1")
    serve_parser.add_argument("--session-id", type=str, default=None)

    # -- mcp --
    mcp_parser = subparsers.add_parser("mcp", help="Start the MCP stdio server.")
    mcp_parser.add_argument("--project", type=Path, required=True)
    mcp_parser.add_argument("--session-id", type=str, default=None)

    # -- init --
    init_parser = subparsers.add_parser("init", help="Parse init prompt MD and create RAG_MASTER.json (zero tokens).")
    init_parser.add_argument("--spec", type=Path, default=None)
    init_parser.add_argument("--output", type=Path, default=None)
    init_parser.add_argument("--root-project", type=str, default="")
    init_parser.add_argument("--root-deliverables", type=str, default="")
    init_parser.add_argument("--root-rag", type=str, default="")
    init_parser.add_argument("--project-name", type=str, default="")
    init_parser.add_argument("--dry-run", action="store_true")
    init_parser.add_argument(
        "--auto-ready", action="store_true",
        help="Transition state_machine_status to READY after successful init (default: stays BOOTING)",
    )
    init_parser.add_argument(
        "--session", type=str, default="S0",
        help="Session id stamped by the first session-stamping checkpoint when "
             "--auto-ready is set (FIX-9). Default: S0 (Session Zero bootstrap).",
    )
    init_parser.add_argument(
        "--path-style", type=str, choices=["windows", "posix", "auto"], default="auto",
        help="Normalize root paths to OS-native separators (default: auto-detect)",
    )
    init_parser.add_argument(
        "--requirements", type=str, nargs="*", default=None,
        help="Create requirements.txt with listed packages (e.g., --requirements curl_cffi beautifulsoup4). "
             "Use --requirements alone (no args) to create an empty template.",
    )
    init_parser.add_argument(
        "--allow-void", action="store_true",
        help="Explicitly permit creating a void RAG when --spec is omitted (governance off). "
             "Without this, init fails loud (non-zero exit) on missing --spec — INS-046.",
    )

    # -- configure --
    config_parser = subparsers.add_parser(
        "configure",
        help="Merge project-specific context into an existing RAG.",
    )
    config_parser.add_argument(
        "--rag", type=Path, required=True,
        help="Path to existing RAG_MASTER.json to update",
    )
    config_parser.add_argument(
        "--context", type=Path, required=False, default=None,
        help="Path to context file (JSON or structured MD with rag-config blocks). "
             "Optional when --reconciliation-docs-root is given.",
    )
    config_parser.add_argument(
        "--reconciliation-docs-root", type=str, default=None, metavar="PATH",
        help="KA-RECON-DECLARE: governed declaration of meta.reconciliation_docs_root — "
             "the published-doc surface root the close-time Rule 11 reconciliation "
             "(KA-13, session-end/session-resume) resolves against. Merged through the "
             "same atomic mirror_bak writer as --context, so it never requires a "
             "hand-edit of RAG_MASTER.json. May be used alone or alongside --context.",
    )
    config_parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would change without writing",
    )
    config_parser.add_argument(
        "--consume", action="store_true",
        help="Delete the --context input file after a verified merge — one atomic, "
             "auditor-clean operation so a transient merge-input never lingers in the "
             "RAG dir as a flagged side store (FIX-11 inc3 / U3). Refuses to delete a "
             "canonical/sanctioned file (RAG_MASTER/.bak, RAG_COLD, RAG_CONTEXT). "
             "No-op under --dry-run. For NON-loaded project context, prefer "
             "`context set` into the sanctioned RAG_CONTEXT.json store instead of merging into HOT.",
    )

    # -- health --
    health_parser = subparsers.add_parser("health", help="Verify all rag_kernel modules.")
    health_parser.add_argument("--path", type=Path, default=Path("."))

    # -- session --
    session_parser = subparsers.add_parser(
        "session",
        help="Start or close a session logger.",
    )
    session_sub = session_parser.add_subparsers(dest="session_action", help="start or close")
    session_start = session_sub.add_parser("start", help="Open session logger and write session_start entry.")
    session_start.add_argument("session_id", type=str, help="Session identifier (e.g., S1, S2)")
    session_start.add_argument("--rag-dir", type=Path, default=Path("."), help="Directory containing RAG files (default: .)")
    session_close = session_sub.add_parser("close", help="Write session_end entry and close logger.")
    session_close.add_argument("session_id", type=str, help="Session identifier to close")
    session_close.add_argument("--rag-dir", type=Path, default=Path("."), help="Directory containing RAG files (default: .)")
    session_close.add_argument(
        "--force",
        action="store_true",
        help="Close even without a checkpoint by this session (UNSAFE — KA-4 override).",
    )

    # -- session-start (KA-6 / KA-10 GOVERNANCE-DETERMINISM: machine-enforced
    #    session-START ritual). One command performs the whole opening ritual so
    #    an agent cannot hand-script it and skip a step (the eBay S2/S4 drift):
    #      carry-forward gate (fail-loud) -> gc dry-run -> open session logger.
    sstart_parser = subparsers.add_parser(
        "session-start",
        help="Enforced session-start ritual: carry-forward gate (fail-loud) -> gc dry-run -> open logger.",
    )
    sstart_parser.add_argument(
        "session_id", type=str, nargs="?", default=None,
        help="Session identifier to open (e.g., S92). OPTIONAL: when omitted, "
             "AUTO-SID-DERIVE computes the next id as the increment of "
             "meta.written_by_session (zero-read boot). Pass an explicit id to override.",
    )
    sstart_parser.add_argument(
        "--rag", type=Path, default=_default_rag_path(),
        help="Path to RAG_MASTER.json (default: RAG/RAG_MASTER.json)",
    )
    # GC-BOOTROOT-FIX (S190, P1-B): the default was Path(".") = CWD, and the
    # sanctioned boot runs from RAG/. The collector therefore walked RAG/ for its
    # whole life while 283 MB of TLC state sat one level up, unseen: proven at
    # S189 as gc(root)=3 vs gc(RAG)=1. The default is now the PROJECT ROOT
    # (rag_dir.parent), resolved at the call site; --gc-path stays an override.
    sstart_parser.add_argument(
        "--gc-path", type=Path, default=None,
        help="Root to scan in the gc dry-run (default: the PROJECT ROOT, i.e. the "
             "parent of the RAG directory — not the CWD).",
    )
    sstart_parser.add_argument(
        "--no-boot-audit", action="store_true",
        help="Skip the axis-1 (TOOL FITNESS) boot gate. The session then opens "
             "without having proven its transports work THIS session "
             "(GRAND-AUDIT-AT-BOOT).",
    )
    sstart_parser.add_argument("--strict", action="store_true", help="Treat audit warnings as gate failures too")
    sstart_parser.add_argument(
        "--git-head", type=str, default=None,
        help="Expected git HEAD for the freshness check (default: auto-detect)",
    )
    sstart_parser.add_argument("--no-gc", action="store_true", help="Skip the gc dry-run scan.")
    sstart_parser.add_argument(
        "--force", action="store_true",
        help="Open the session even if the carry-forward gate fails (UNSAFE).",
    )
    # KA-14 — session-start rule-load attestation gate (BOOT -> RULES_LOADED(attested)
    # -> READY). Phase 1 (no --attest) renders the operating_protocol rule digest into
    # context and prints an attestation token; the logger is NOT opened. Phase 2
    # (--attest <token>) verifies the token against the live digest, then opens the
    # logger. This makes "the agent loaded the HOT rules" structurally unforgeable —
    # the fresh-deploy root cause (rule bodies sat on disk, never ingested).
    sstart_parser.add_argument(
        "--attest", type=str, default=None, metavar="TOKEN",
        help="Phase 2: attest the rule digest was loaded by echoing the token printed "
             "by phase 1; on a match the logger opens (READY).",
    )
    sstart_parser.add_argument(
        "--no-attest-gate", action="store_true",
        help="Open the logger in one shot WITHOUT the rule-load attestation gate "
             "(UNSAFE — re-creates the fresh-deploy unloaded-rules risk; tests/CI only).",
    )

    # -- session-end (KA-6 / KA-10: machine-enforced session-END ritual). One
    #    command performs the whole closing ritual atomically, in order, so the
    #    ran-but-never-checkpointed freeze (eBay S4) is structurally impossible:
    #      checkpoint -> close logger (KA-4 gate now passes) -> audit (fail-loud).
    send_parser = subparsers.add_parser(
        "session-end",
        help="Enforced session-end ritual: checkpoint -> close logger (KA-4 gate) -> audit (fail-loud).",
    )
    send_parser.add_argument("--rag", type=Path, required=True, help="Path to RAG_MASTER.json")
    send_parser.add_argument("--session", type=str, required=True, help="Session ID (e.g., S92)")
    send_parser.add_argument("--summary", type=str, required=True, help="Session summary string for the checkpoint")
    send_parser.add_argument("--tasks", type=str, default=None, help="JSON array of open task strings to set (replaces existing)")
    send_parser.add_argument("--status", type=str, default=None, help="New state_machine_status value")
    send_parser.add_argument("--strict", action="store_true", help="Treat audit warnings as failures too")
    send_parser.add_argument(
        "--git-head", type=str, default=None,
        help="Expected git HEAD for the audit freshness check (default: auto-detect)",
    )
    # KA-16 — fold the ERROR_LOG append into the governed close + attest the report.
    send_parser.add_argument(
        "--error-log-entry", type=str, default=None,
        help="Markdown ERROR_LOG entry to fold into the checkpoint (idempotent).",
    )
    send_parser.add_argument(
        "--error-log-id", type=str, default=None,
        help="Unique id for the ERROR_LOG entry (idempotency marker; default: <session>-checkpoint).",
    )
    send_parser.add_argument(
        "--error-log-path", type=str, default=None,
        help="ERROR_LOG.md path (default: beside the RAG).",
    )
    send_parser.add_argument(
        "--report-rendered", action="store_true",
        help="DEPRECATED (S139 WIRE-CLOSE): the close now machine-renders the "
             "canonical report itself, so attestation is automatic. Kept as a no-op "
             "for back-compat.",
    )
    # S139 WIRE-CLOSE — the close emits the deterministic canonical report verbatim
    # (Rule 12), so hand-authoring is impossible. These mirror the `report` verb's
    # external scalars; unset ones render n/a and honestly pull the verdict to AMBER.
    send_parser.add_argument("--tests", type=str, default=None,
        help="Test result summary for the close report (e.g. '1,720 green').")
    send_parser.add_argument("--tests-failing", action="store_true",
        help="Mark the test gate FAILING in the close report.")
    send_parser.add_argument("--released", dest="released", action="store_true", default=None,
        help="Assert the build is released/deployable (drives GREEN in the close report).")
    send_parser.add_argument("--unreleased", dest="released", action="store_false",
        help="Assert the build is UNRELEASED (forces AMBER in the close report).")
    send_parser.add_argument("--release-ref", type=str, default=None,
        help="Release tag/ref for the close report's release cell.")
    send_parser.add_argument("--claims-ok", dest="claims_ok", action="store_true", default=None,
        help="Assert published repo-claims reconciled (Rule 11) in the close report.")
    send_parser.add_argument("--claims-broken", dest="claims_ok", action="store_false",
        help="Assert a published repo-claim contradicts reality (forces RED).")
    send_parser.add_argument("--context-pct", type=str, default=None,
        help="LLM context-window usage for the close report (e.g. '55%%').")
    send_parser.add_argument("--milestone", type=str, default=None,
        help="Override the close report's milestone cell.")
    send_parser.add_argument("--handoff", type=str, default=None,
        help="One-line handoff/next-step note for the close report's section 7.")
    send_parser.add_argument("--no-report", action="store_true",
        help="Suppress the machine-rendered close report (not recommended).")
    # CLOSE-STEP-ERRLOG gate (S188) — the explicit "nothing to bank" declaration.
    # Without it, a close that banks no ERROR_LOG entry is REFUSED: S184-S187 each
    # sealed with error_log=false while ERROR_LOG.md went unwritten for four
    # sessions, so silence is no longer accepted as evidence of a clean session.
    send_parser.add_argument("--no-errors", action="store_true",
        help="DECLARE that this session produced no error worth an ERROR_LOG "
             "record. Required when no --error-log-entry is given; the close "
             "REFUSES to seal on silence (CLOSE-STEP-ERRLOG-UNENFORCED).")
    # FORENSICS-AS-GATE (S190) — the only way past a conduct finding, and it is
    # recorded in the close marker. There is deliberately no boolean form: an
    # override without a reason is what advisory forensics already was.
    send_parser.add_argument(
        "--accept-conduct", type=str, default=None, metavar="REASON",
        help="DECLARE the session's conduct findings (repeat bursts, failed "
             "governed calls, excess silent gaps) as accepted, with a reason "
             "that is recorded in the close marker. Without this the close "
             "REFUSES on any finding (FORENSICS-AS-GATE).",
    )
    # HANDOFF-CLAIMS-GATE (E-132) — the escape hatch, deliberately narrow.
    send_parser.add_argument(
        "--handoff-claims-unchecked", action="store_true",
        help="Skip the E-132 gate that compares numbers stated in --handoff "
             "against the measured state. Use only when a number deliberately "
             "refers to something else (another deployment, a historical run) "
             "and the handoff says so.",
    )
    # KA-13 — wire the Rule 11 published-doc reconciliation into the close audit.
    send_parser.add_argument(
        "--docs-root", type=str, default=None,
        help="Root of the published docs to reconcile at close (overrides "
             "meta.reconciliation_docs_root). Absolute, or relative to the project root.",
    )
    send_parser.add_argument(
        "--no-reconcile", action="store_true",
        help="Skip the close-time published-doc reconciliation even if "
             "meta.reconciliation_docs_root is declared.",
    )

    # -- session-resume (KA-16): detect + finish an interrupted session close. --
    sresume_parser = subparsers.add_parser(
        "session-resume",
        help="Detect and resume an interrupted (transfer_ready=false) session close.",
    )
    sresume_parser.add_argument("--rag", type=Path, required=True, help="Path to RAG_MASTER.json")
    sresume_parser.add_argument(
        "--session", type=str, default=None,
        help="Session ID to resume (default: read from the session_close marker).",
    )
    sresume_parser.add_argument(
        "--summary", type=str, default=None,
        help="Checkpoint summary (required only if the close aborted before checkpoint).",
    )
    sresume_parser.add_argument("--tasks", type=str, default=None, help="JSON array of open task strings (replaces existing)")
    sresume_parser.add_argument("--status", type=str, default=None, help="New state_machine_status value")
    sresume_parser.add_argument("--strict", action="store_true", help="Treat audit warnings as failures too")
    sresume_parser.add_argument("--git-head", type=str, default=None, help="Expected git HEAD for the audit freshness check")
    sresume_parser.add_argument("--error-log-entry", type=str, default=None, help="ERROR_LOG entry to fold (only used if checkpoint not yet done)")
    sresume_parser.add_argument("--error-log-id", type=str, default=None, help="ERROR_LOG idempotency id")
    sresume_parser.add_argument("--error-log-path", type=str, default=None, help="ERROR_LOG.md path (default: beside the RAG)")
    sresume_parser.add_argument("--report-rendered", action="store_true", help="Attest the status report was rendered (Rule 12).")
    sresume_parser.add_argument(
        "--docs-root", type=str, default=None,
        help="Root of the published docs to reconcile at close (overrides "
             "meta.reconciliation_docs_root). Absolute, or relative to the project root.",
    )
    sresume_parser.add_argument(
        "--no-reconcile", action="store_true",
        help="Skip the close-time published-doc reconciliation even if "
             "meta.reconciliation_docs_root is declared.",
    )
    sresume_parser.add_argument(
        "--no-errors", action="store_true",
        help="DECLARE that this session produced no error worth an ERROR_LOG "
             "record (same gate as session-end; a resume cannot launder silence).",
    )
    sresume_parser.add_argument(
        "--accept-conduct", type=str, default=None, metavar="REASON",
        help="DECLARE the session's conduct findings as accepted, with a reason "
             "recorded in the close marker (same gate as session-end; a resume "
             "cannot launder conduct either — FORENSICS-AS-GATE).",
    )

    # -- checkpoint --
    ckpt_parser = subparsers.add_parser(
        "checkpoint",
        help="Merge session summary into RAG_MASTER.json atomically.",
    )
    ckpt_parser.add_argument("--rag", type=Path, required=True, help="Path to RAG_MASTER.json")
    ckpt_parser.add_argument("--session", type=str, required=True, help="Session ID (e.g., S1)")
    ckpt_parser.add_argument("--summary", type=str, required=True, help="Session summary string")
    ckpt_parser.add_argument("--tasks", type=str, default=None, help="JSON array of open task strings to set (replaces existing)")
    ckpt_parser.add_argument("--status", type=str, default=None, help="New state_machine_status value")
    ckpt_parser.add_argument("--dry-run", action="store_true", help="Show what would change without writing")
    # KA-INTENT-FIDELITY inc1 — a stated handoff is persisted VERBATIM as the
    # structured next_session_directive (decision-of-record); the session-end gate
    # refuses to seal unless it landed and matches (E-055/S146 guard).
    ckpt_parser.add_argument("--handoff", type=str, default=None, help="Next-session directive/handoff; persisted verbatim into next_session_directive.")
    # KA-16 — optional ERROR_LOG fold (idempotent) as part of the governed checkpoint.
    ckpt_parser.add_argument("--error-log-entry", type=str, default=None, help="Markdown ERROR_LOG entry to fold in (idempotent).")
    ckpt_parser.add_argument("--error-log-id", type=str, default=None, help="Unique id for the ERROR_LOG entry (default: <session>-checkpoint).")
    ckpt_parser.add_argument("--error-log-path", type=str, default=None, help="ERROR_LOG.md path (default: beside the RAG).")
    # KA-18 (E-044/E-045) — session-start ordering guard. ON by default at the
    # CLI so a bare `checkpoint` refuses to seal before the mechanized
    # `session-start` has opened this session's log (the recurring slip that
    # banked state with no observability record). Explicit bypass for the rare
    # intentional case.
    ckpt_parser.add_argument(
        "--no-require-session-log", dest="require_session_log",
        action="store_false", default=True,
        help="Bypass the KA-18 guard that refuses a checkpoint when no session "
             "log is open for --session (default: guard ON).",
    )

    # -- gc --
    gc_parser = subparsers.add_parser(
        "gc",
        help="Garbage collector — clean __pycache__, .pyc, .tmp, orphaned files.",
    )
    gc_parser.add_argument("--path", type=Path, default=Path("."), help="Project root to scan (default: .)")
    gc_parser.add_argument("--dry-run", action="store_true", help="Report findings without deleting")

    # -- graph --
    graph_parser = subparsers.add_parser(
        "graph",
        help="Run a Graph Orchestrator DAG through the kernel runtime.",
    )
    graph_sub = graph_parser.add_subparsers(dest="graph_action", help="run")
    graph_run = graph_sub.add_parser("run", help="Execute a DAG spec (JSON) through the kernel.")
    graph_run.add_argument("spec", type=Path, help='JSON spec file: {"nodes": [{"id","deps","action","payload"}], "schedule": "sequential|levels"}')
    graph_run.add_argument("--project", type=Path, default=Path("."), help="Project directory (default: .)")
    graph_run.add_argument("--session-id", type=str, default=None, help="Session identifier")
    graph_run.add_argument("--schedule", type=str, default=None, help="Override schedule: sequential or levels")
    graph_run.add_argument("--stop-on-failure", action="store_true", help="Halt remaining branches on first node failure")
    graph_run.add_argument("--rollback-on-failure", action="store_true", help="Transactional: undo the whole run on any node failure")

    # -- audit-env --
    audit_parser = subparsers.add_parser(
        "audit-env",
        help="Audit environment: enumerate Python versions, pip, package managers, available tools.",
    )
    audit_parser.add_argument("--path", type=Path, default=Path("."), help="Project root to check for venvs/requirements (default: .)")
    audit_parser.add_argument("--json", dest="json_output", action="store_true", help="Output as JSON instead of human-readable")

    # -- item lifecycle verbs (DRIFT-ELIM increment 3) --
    # resolve / defer / reopen / start / discard / supersede route through the
    # drift_store mutation API; the verb selects the target ItemStatus and the
    # lifecycle guard decides legality. Each writes atomically (or fails loud).
    for _verb, _vhelp in _ITEM_VERB_HELP.items():
        vp = subparsers.add_parser(_verb, help=_vhelp)
        vp.add_argument("item_id", type=str, help="id of the tracked item")
        vp.add_argument(
            "--rag", type=Path, default=_default_rag_path(),
            help="Path to RAG_MASTER.json (default: RAG/RAG_MASTER.json)",
        )
        vp.add_argument(
            "--session", type=str, required=True,
            help="Session id recorded in the item history (audit trail)",
        )
        vp.add_argument("--reason", type=str, default="", help="One-line reason recorded in history")
        vp.add_argument("--dry-run", action="store_true", help="Check legality without writing")
        # RESOLVE-REQUIRES-EVIDENCE (S190, P1-C) — repeatable, and REQUIRED by
        # `resolve`: a DONE claim is a claim about a file that exists.
        vp.add_argument(
            "--artifact", action="append", default=None, metavar="PATH",
            help="Evidence for this transition: a path that MUST exist "
                 "(repeatable). REQUIRED by `resolve` — 131 of 175 RESOLVED "
                 "items cite none, and that is where DONE stopped meaning done.",
        )
        # SEMANTIC-PRECONDITION-GATE (S190, P1-D)
        vp.add_argument(
            "--cite", type=str, default=None, metavar="ITEM_ID",
            help="Live tracked item this write is really about. Required to "
                 "write against a TERMINAL item (resolve/defer/reopen).",
        )
        if _verb == "supersede":
            vp.add_argument("--by", type=str, required=True, help="id of the item that supersedes this one")

    # -- items (read-only render of tracked_items) --
    items_parser = subparsers.add_parser("items", help="List the canonical tracked_items array (read-only).")
    items_parser.add_argument("--rag", type=Path, default=_default_rag_path(), help="Path to RAG_MASTER.json")
    items_parser.add_argument("--status", type=str, default=None, help="Filter by status (e.g. OPEN, DEFERRED)")
    items_parser.add_argument("--kind", type=str, default=None, help="Filter by kind (e.g. TASK, MILESTONE)")
    items_parser.add_argument("--json", dest="json_output", action="store_true", help="Output as JSON instead of a table")

    # -- intent-audit (KA-INTENT-FIDELITY inc2: session-START plan-vs-settled gate) --
    intent_parser = subparsers.add_parser(
        "intent-audit",
        help="Verify a session plan honors the settled next_session_directive "
             "(ID-binding + normalized-exact restatement) — KA-INTENT-FIDELITY inc2.",
    )
    intent_parser.add_argument(
        "--rag", type=Path, default=_default_rag_path(),
        help="Path to RAG_MASTER.json (default: RAG/RAG_MASTER.json)",
    )
    intent_parser.add_argument(
        "--plan", type=str, required=True,
        help="The stated session plan — must normalized-match the stored directive text.",
    )
    intent_parser.add_argument(
        "--plan-decisions", type=str, default=None,
        help="Comma-separated tracked_item ids the plan binds to (must match the "
             "directive's decision_ids and resolve to real items).",
    )

    # -- render (DRIFT-ELIM increment 4: project tracked_items into legacy surfaces) --
    render_parser = subparsers.add_parser(
        "render",
        help="Render legacy open_tasks/deferred_items/priority_actions/backlog/ERROR_LOG from the canonical tracked_items array.",
    )
    render_parser.add_argument(
        "--rag", type=Path, default=_default_rag_path(),
        help="Path to RAG_MASTER.json (default: RAG/RAG_MASTER.json)",
    )
    render_parser.add_argument(
        "--what",
        choices=[
            "open_tasks", "deferred_items", "priority_actions",
            "backlog", "error_log", "all",
        ],
        default="all", help="Which render to emit (default: all)",
    )
    render_parser.add_argument(
        "--apply", action="store_true",
        help=(
            "Write the rendered open_tasks + deferred_items + priority_actions back "
            "into the RAG atomically (else dry-run/print only)."
        ),
    )
    render_parser.add_argument("--json", dest="json_output", action="store_true", help="Output as JSON instead of text")

    # -- report (REPORT-VERB S136: deterministic 7-section canonical status render) --
    report_parser = subparsers.add_parser(
        "report",
        help="Render the 7-section canonical status report deterministically from the RAG (Rule 12).",
    )
    report_parser.add_argument(
        "--rag", type=Path, default=_default_rag_path(),
        help="Path to RAG_MASTER.json (default: RAG/RAG_MASTER.json)",
    )
    report_parser.add_argument(
        "--session", type=str, default=None,
        help="Session id for the report heading (e.g. S136). Required unless --verify.",
    )
    # REPORT-RENDER-ATTEST (E-062) — verify a rendered/pasted report is the VERBATIM
    # kernel render, not a hand-authored / re-prosed copy. Recomputes the
    # report-attest sha256 over the report body and fails loud on mismatch/absence.
    report_parser.add_argument(
        "--verify", type=Path, default=None, metavar="FILE",
        help="Verify FILE is a verbatim `rag_kernel report` render (checks the report-attest token); fail loud if re-prosed or missing.",
    )
    report_parser.add_argument(
        "--context-pct", type=str, default=None,
        help="LLM context-window usage (external scalar the runtime cannot know), e.g. '42%%'",
    )
    report_parser.add_argument(
        "--tests", type=str, default=None,
        help="Test result summary (external — needs a suite run), e.g. '1,693 green'",
    )
    report_parser.add_argument(
        "--tests-failing", action="store_true",
        help="Mark the test gate as FAILING (else --tests, if given, is treated as passing).",
    )
    report_parser.add_argument(
        "--released", dest="released", action="store_true", default=None,
        help="Assert the current build is released/deployable (drives the release-ready + GREEN gate).",
    )
    report_parser.add_argument(
        "--unreleased", dest="released", action="store_false",
        help="Assert the current build is UNRELEASED / not deployable (forces AMBER).",
    )
    report_parser.add_argument(
        "--release-ref", type=str, default=None,
        help="Release tag/ref to show in the release-ready cell (e.g. runtime-v0.4.30).",
    )
    report_parser.add_argument(
        "--claims-ok", dest="claims_ok", action="store_true", default=None,
        help="Assert published repo-claims are reconciled with reality (Rule 11).",
    )
    report_parser.add_argument(
        "--claims-broken", dest="claims_ok", action="store_false",
        help="Assert a published repo-claim contradicts reality (forces RED).",
    )
    report_parser.add_argument(
        "--milestone", type=str, default=None,
        help="Override the milestone cell (default: newest active MILESTONE item).",
    )
    report_parser.add_argument(
        "--handoff", type=str, default=None,
        help="One-line handoff / next-step note for section 7.",
    )
    report_parser.add_argument(
        "--git-head", type=str, default=None,
        help="Override git HEAD (default: resolved live, best-effort).",
    )
    report_parser.add_argument(
        "--no-live", action="store_true",
        help="Skip live health/drift/git resolution (render purely from RAG + args).",
    )

    # -- note (DRIFT-ELIM increment 5: guarded note-update verb, INS-038) --
    note_parser = subparsers.add_parser(
        "note",
        help="Refresh a tracked item's one-line note through the guarded API (status untouched).",
    )
    note_parser.add_argument("item_id", type=str, help="id of the tracked item")
    note_parser.add_argument("note", type=str, help="new one-line note text")
    note_parser.add_argument(
        "--rag", type=Path, default=_default_rag_path(),
        help="Path to RAG_MASTER.json (default: RAG/RAG_MASTER.json)",
    )
    note_parser.add_argument(
        "--session", type=str, required=True,
        help="Session id stamped as last-touched (audit trail)",
    )
    note_parser.add_argument("--dry-run", action="store_true", help="Validate without writing")

    # -- cite (EVIDENCE-AMENDMENT S191: attach evidence without a status move) --
    cite_parser = subparsers.add_parser(
        "cite",
        help="Attach evidence to a tracked item without moving its status — the only path "
             "that can cite an already-RESOLVED item.",
    )
    cite_parser.add_argument("item_id", type=str, help="id of the tracked item")
    cite_parser.add_argument(
        "--artifact", action="append", default=[], required=True,
        help="Evidence path that MUST exist, relative to the RAG dir (repeatable).",
    )
    cite_parser.add_argument(
        "--rag", type=Path, default=_default_rag_path(),
        help="Path to RAG_MASTER.json (default: RAG/RAG_MASTER.json)",
    )
    cite_parser.add_argument(
        "--session", type=str, required=True,
        help="Session id stamped on the citation (audit trail)",
    )
    cite_parser.add_argument(
        "--reason", type=str, default="",
        help="One-line reason recorded with the citation",
    )
    cite_parser.add_argument("--dry-run", action="store_true", help="Validate without writing")

    # -- priority (REPORT-PRIORITY-GROUPS inc1: guarded priority-group assignment) --
    priority_parser = subparsers.add_parser(
        "priority",
        help="Set a tracked item's Rule 21 priority_group (P1..P5, or \"\" to clear) through the guarded API (status untouched).",
    )
    priority_parser.add_argument("item_id", type=str, help="id of the tracked item")
    priority_parser.add_argument(
        "priority_group", type=str,
        help='priority bucket: P1..P5, or "" (empty) to clear the assignment',
    )
    priority_parser.add_argument(
        "--rag", type=Path, default=_default_rag_path(),
        help="Path to RAG_MASTER.json (default: RAG/RAG_MASTER.json)",
    )
    priority_parser.add_argument(
        "--session", type=str, required=True,
        help="Session id stamped as last-touched (audit trail)",
    )
    priority_parser.add_argument("--dry-run", action="store_true", help="Validate without writing")
    # SEMANTIC-PRECONDITION-GATE (S190, P1-D) — `priority` is the verb that leaves
    # status alone, so the lifecycle guard never saw it; S189 prioritised a
    # terminal item and every gate agreed.
    priority_parser.add_argument(
        "--cite", type=str, default=None, metavar="ITEM_ID",
        help="Live tracked item this priority write is really about. Required "
             "when the named item is TERMINAL.",
    )

    # -- dedup-sessions (KA-2 increment B: governed sessions_recent row-repair) --
    dedup_parser = subparsers.add_parser(
        "dedup-sessions",
        help="Repair duplicate-bootstrap rows in sessions_recent through the guarded API (KA-2).",
    )
    dedup_parser.add_argument(
        "--rag", type=Path, default=_default_rag_path(),
        help="Path to RAG_MASTER.json (default: RAG/RAG_MASTER.json)",
    )
    dedup_parser.add_argument(
        "--keep", choices=["first", "last"], default="first",
        help="Which row of each duplicate-timestamp group to retain (default: first).",
    )
    dedup_parser.add_argument(
        "--session", type=str, default="",
        help="Session id (audit trail; recorded in the bootstrap session log).",
    )
    dedup_parser.add_argument(
        "--dry-run", action="store_true",
        help="Report the duplicate rows that would be removed without writing.",
    )

    # -- audit (DRIFT-ELIM increment 5: fail-loud session auditor) --
    audit_parser2 = subparsers.add_parser(
        "audit",
        help="Audit the RAG: renders match canonical, supersede refs resolve, notes don't contradict status, no side stores.",
    )
    audit_parser2.add_argument(
        "--rag", type=Path, default=_default_rag_path(),
        help="Path to RAG_MASTER.json (default: RAG/RAG_MASTER.json)",
    )
    audit_parser2.add_argument(
        "--strict", action="store_true",
        help="Treat warnings as failures too (exit non-zero on any finding).",
    )
    audit_parser2.add_argument(
        "--no-scan-root", dest="scan_root", action="store_false",
        help="Skip the project-root side-store scan (Rule 13 check).",
    )
    audit_parser2.add_argument(
        "--docs-root", type=Path, default=None,
        help="Enable the Rule 11 published-doc reconciliation against this docs root "
             "(reconciles the surfaces in meta.reconciliation_surfaces — defaulting to "
             "README.md / CHANGELOG.md / docs/ROADMAP.md — vs the canonical facts).",
    )
    audit_parser2.add_argument(
        "--error-log", type=Path, default=None,
        help="Path to ERROR_LOG.md for E-### record coverage (default: beside the RAG file).",
    )
    audit_parser2.add_argument(
        "--git-head", default=None,
        help="Override the git HEAD used for the current_status freshness guard (E-043). "
             "Default: auto-resolved from the RAG's git worktree; skipped if unresolvable.",
    )
    audit_parser2.add_argument("--json", dest="json_output", action="store_true", help="Output as JSON instead of text")

    # -- doctor (ENV-NORM increment 1: env + repo preflight) --
    doctor_parser = subparsers.add_parser(
        "doctor",
        help="Preflight env + repo: best python, stale .git/index.lock (fail-closed --fix), shell-policy first move.",
    )
    doctor_parser.add_argument("--path", type=Path, default=Path("."), help="Project root (default: .)")
    doctor_parser.add_argument("--rag", type=Path, default=None, help="RAG_MASTER.json to render the shell-policy first move from")
    doctor_parser.add_argument("--fix", action="store_true", help="Clear a stale index.lock when provably safe (no git running + aged)")
    doctor_parser.add_argument("--stale-after", dest="stale_after", type=float, default=60.0, help="Seconds before an unheld index.lock counts as stale (default: 60)")
    doctor_parser.add_argument("--emit-runner", dest="emit_runner", type=Path, default=None, help="Write the script-file runner template to this path and exit")
    doctor_parser.add_argument(
        "--recover", action="store_true",
        help="RECOVERY ADVISOR (BOOT-PROSE-TO-SCRIPT): assess a corrupt/unreadable RAG "
             "and stage the .bak -> COLD -> WAL -> rebuild path. Read-only unless --fix "
             "is also given, in which case the safe common-case .bak restore is applied.",
    )
    doctor_parser.add_argument("--json", dest="json_output", action="store_true", help="Output as JSON")

    # -- add (ENV-NORM increment 1: guarded ADD verb — closes the no-ADD-verb gap) --
    add_item_parser = subparsers.add_parser(
        "add",
        help="Add a NEW canonical tracked item through the guarded atomic store (fail-loud on duplicate id).",
    )
    add_item_parser.add_argument("item_id", type=str, help="id of the new tracked item")
    add_item_parser.add_argument("title", type=str, help="one-line title")
    add_item_parser.add_argument("--rag", type=Path, default=_default_rag_path(), help="Path to RAG_MASTER.json")
    add_item_parser.add_argument("--status", type=str, default="OPEN", help="initial status (default: OPEN)")
    add_item_parser.add_argument("--kind", type=str, default="TASK", help="item kind (default: TASK)")
    add_item_parser.add_argument("--session", type=str, required=True, help="session id recorded on the item (audit trail)")
    add_item_parser.add_argument("--note", type=str, default="", help="one-line note")
    add_item_parser.add_argument("--by", type=str, default=None, help="superseding item id (required if --status SUPERSEDED)")
    add_item_parser.add_argument("--dry-run", action="store_true", help="validate without writing")

    # -- acceptance (S190, P3: wire scripts/acceptance_check.py instead of deleting it) --
    acc_parser = subparsers.add_parser(
        "acceptance",
        help="Boot-readiness acceptance check for the kernel and every registered "
             "deployment: verify, audit, boot-map coverage, identity, seal, and a "
             "real read-only successor session-start.",
    )
    acc_parser.add_argument("--rag", type=Path, default=_default_rag_path(), help="Path to RAG_MASTER.json")
    acc_parser.add_argument("--script", type=str, default=None, help="acceptance_check.py path (default: RAG/scripts/)")
    acc_parser.add_argument("--timeout", type=int, default=1800, help="seconds before the check is abandoned as inconclusive")

    # -- errlog-migrate (S190, P2: ERROR_LOG.md -> tracked_items, kind=ERROR) --
    errmig_parser = subparsers.add_parser(
        "errlog-migrate",
        help="Fold every ERROR_LOG.md E-number into tracked_items as kind=ERROR "
             "in ONE atomic write (idempotent) — closes the 106-orphan gap in "
             "the ledger-continuity axis.",
    )
    errmig_parser.add_argument("--rag", type=Path, default=_default_rag_path(), help="Path to RAG_MASTER.json")
    errmig_parser.add_argument("--session", type=str, required=True, help="session id recorded on the migrated items")
    errmig_parser.add_argument("--error-log", type=str, default=None, help="ERROR_LOG.md path (default: beside the RAG)")
    errmig_parser.add_argument("--dry-run", action="store_true", help="report what would be migrated, write nothing")

    # -- un-add (KA-CUTOVER-GATE: guarded, atomic INVERSE of add for a pristine mis-add) --
    unadd_parser = subparsers.add_parser(
        "un-add",
        help="Un-add (remove) a PRISTINE mis-added tracked item — the guarded, atomic inverse of add; refuses any item that carries lifecycle history.",
    )
    unadd_parser.add_argument("item_id", type=str, help="id of the mis-added item to remove")
    unadd_parser.add_argument("--rag", type=Path, default=_default_rag_path(), help="Path to RAG_MASTER.json")
    unadd_parser.add_argument("--session", type=str, required=True, help="session id (audit trail; stamps meta.last_updated_utc)")
    unadd_parser.add_argument("--dry-run", action="store_true", help="validate without writing")

    # -- add-rule (FIX-5/P3: guarded ADD verb for operating_protocol rules) --
    add_rule_parser = subparsers.add_parser(
        "add-rule",
        help="Append a NEW operating_protocol rule through the guarded atomic store (fail-loud on an existing key).",
    )
    add_rule_parser.add_argument("key", type=str, help="operating_protocol rule key (e.g. strict_obey)")
    add_rule_parser.add_argument("value", type=str, nargs="?", default=None,
                                 help="rule text (string). Omit and use --value-file for long rules.")
    add_rule_parser.add_argument("--value-file", dest="value_file", type=Path, default=None,
                                 help="read the rule text from this file instead of the positional arg")
    add_rule_parser.add_argument("--rag", type=Path, default=_default_rag_path(), help="Path to RAG_MASTER.json")
    add_rule_parser.add_argument("--session", type=str, required=True, help="session id (audit trail; stamps meta.last_updated_utc)")
    add_rule_parser.add_argument("--allow-overwrite", dest="allow_overwrite", action="store_true",
                                 help="replace an existing rule of the same key (default: fail loud)")
    add_rule_parser.add_argument("--dry-run", action="store_true", help="validate without writing")

    # -- update-rule (UPDATE-RULE-VERB: governed re-set of dict/string operating_protocol rules) --
    update_rule_parser = subparsers.add_parser(
        "update-rule",
        help="Re-set an EXISTING operating_protocol rule (string or JSON/dict value), or one sub-key of a dict rule, through the guarded atomic store (fail-loud on a missing target unless --create).",
    )
    update_rule_parser.add_argument("key", type=str, help="operating_protocol rule key (e.g. tool_hierarchy)")
    update_rule_parser.add_argument("value", type=str, nargs="?", default=None,
                                    help="rule value (string, or JSON with --json). Omit and use --value-file for long values.")
    update_rule_parser.add_argument("--value-file", dest="value_file", type=Path, default=None,
                                    help="read the value from this file instead of the positional arg")
    update_rule_parser.add_argument("--subkey", type=str, default=None,
                                    help="set this sub-key of a dict-valued rule (e.g. file_read_write_list)")
    update_rule_parser.add_argument("--json", dest="as_json", action="store_true",
                                    help="parse the value as JSON (object/array/scalar) instead of a string")
    update_rule_parser.add_argument("--create", action="store_true",
                                    help="allow creating the key/sub-key if absent (default: fail loud — update requires an existing target)")
    update_rule_parser.add_argument("--rag", type=Path, default=_default_rag_path(), help="Path to RAG_MASTER.json")
    update_rule_parser.add_argument("--session", type=str, required=True, help="session id (audit trail; stamps meta.last_updated_utc)")
    update_rule_parser.add_argument("--dry-run", action="store_true", help="validate without writing")

    # -- refresh-current-status (KA-CS-REFRESH: governed repair of the E-043 freshness guard) --
    refresh_cs_parser = subparsers.add_parser(
        "refresh-current-status",
        help="Re-stamp current_status machine-facts (runtime version + git HEAD, optional test count) through the guarded atomic store — the governed repair for the E-043 freshness guard (KA-CS-REFRESH).",
    )
    refresh_cs_parser.add_argument("--rag", type=Path, default=_default_rag_path(), help="Path to RAG_MASTER.json")
    refresh_cs_parser.add_argument("--session", type=str, required=True, help="session id (audit trail; stamps meta.last_updated_utc)")
    refresh_cs_parser.add_argument("--version", type=str, default=None,
                                   help="runtime version to stamp (default: live rag_kernel.__version__)")
    refresh_cs_parser.add_argument("--git-head", dest="git_head", type=str, default=None,
                                   help="git HEAD sha to stamp (default: auto-resolve the worktree HEAD, like audit --git-head)")
    refresh_cs_parser.add_argument("--tests", type=int, default=None,
                                   help="also refresh the unit_tests count to this integer (comma-formatted)")
    refresh_cs_parser.add_argument("--strict", action="store_true",
                                   help="fail loud if a targeted field/token is missing instead of skipping it")
    refresh_cs_parser.add_argument("--dry-run", action="store_true",
                                   help="show the planned old->new token changes without writing")

    # -- prune-current-status (META-SETTER-GAP residue: governed removal of archived keys) --
    prune_cs_parser = subparsers.add_parser(
        "prune-current-status",
        help="Remove ARCHIVED session-stamped keys (next_session_directive_S<n>, session_finding_S<n>_E<n>, …) from current_status through the guarded atomic store — the governed repair for the META-SETTER-GAP residue that refresh-current-status cannot reach.",
    )
    prune_cs_parser.add_argument("--rag", type=Path, default=_default_rag_path(), help="Path to RAG_MASTER.json")
    prune_cs_parser.add_argument("--session", type=str, required=True, help="session id (audit trail; stamps meta.last_updated_utc)")
    prune_cs_parser.add_argument("--keys", nargs="*", default=None,
                                 help="prune ONLY these keys (each must satisfy the archived predicate; a live field REFUSES). Default: every archived key.")
    prune_cs_parser.add_argument("--list", action="store_true",
                                 help="list the archived keys and exit without writing")
    prune_cs_parser.add_argument("--dry-run", action="store_true",
                                 help="show which keys would be removed without writing")

    # -- meta (META-SETTER-GAP: the governed setter for declared meta.* scalars) --
    meta_parser = subparsers.add_parser(
        "meta",
        help="Read or SET a declared meta.* scalar through the guarded atomic store. REFUSE-BY-DEFAULT: an undeclared key is refused, a container key is refused by name, and a value that will not coerce to the declared type fails loud (META-SETTER-GAP).",
    )
    meta_parser.add_argument("--rag", type=Path, default=_default_rag_path(), help="Path to RAG_MASTER.json")
    meta_parser.add_argument("--set", dest="set_kv", metavar="KEY=VALUE", default=None,
                             help="set one declared scalar, e.g. --set written_by_session=S188")
    meta_parser.add_argument("--get", dest="get_key", metavar="KEY", default=None,
                             help="print one meta scalar and exit")
    meta_parser.add_argument("--list", dest="list_keys", action="store_true",
                             help="list the declared settable keys with their live values")
    meta_parser.add_argument("--session", type=str, default=None,
                             help="session id (required for --set; a meta write must be attributable)")
    meta_parser.add_argument("--dry-run", action="store_true",
                             help="show the planned old->new change without writing")

    # -- tests (REPORT-TESTS-GATE-UNMEASURED: the seal MEASURES the suite) --
    tests_parser = subparsers.add_parser(
        "tests",
        help="Measured test gate. --run executes the suite and stamps meta.test_gate with the count AND the runtime/git HEAD it was measured against; --verify grades that stamp against live facts (exit 1 on red, stale or unmeasured). Replaces the agent typing --tests.",
    )
    tests_parser.add_argument("--rag", type=Path, default=_default_rag_path(), help="Path to RAG_MASTER.json")
    tests_parser.add_argument("--run", action="store_true",
                              help="run the suite now and stamp the result (blocking — for a long suite, launch detached and use `wait-for`)")
    tests_parser.add_argument("--verify", action="store_true",
                              help="grade the existing stamp against the live runtime/git HEAD; exit 1 unless measured, green and current")
    tests_parser.add_argument("--show", action="store_true",
                              help="print the stored stamp and exit")
    tests_parser.add_argument("--repo", type=Path, default=None,
                              help="directory holding the suite (default: resolved from meta.reconciliation_docs_root)")
    tests_parser.add_argument("--session", type=str, default=None,
                              help="session id recorded on the stamp (required with --run)")
    tests_parser.add_argument("--timeout", type=int, default=1800,
                              help="abandon the measurement after N seconds (default 1800); nothing is stamped on timeout")
    tests_parser.add_argument("--json", dest="as_json", action="store_true",
                              help="emit machine-readable JSON")

    # -- forensics (SELF-DIAGNOSIS-UNSOURCED: render conduct from the log) --
    forensics_parser = subparsers.add_parser(
        "forensics",
        help="Render a session's CONDUCT from its own log — wall time, governed calls, failed verbs and their real cost, silent gaps, repeat bursts, double seals. The numbers any account of a session has to cite (SELF-DIAGNOSIS-UNSOURCED).",
    )
    forensics_parser.add_argument("session_id", nargs="?", default=None,
                                  help="session id (default: the newest session log beside the RAG)")
    forensics_parser.add_argument("--rag", type=Path, default=_default_rag_path(),
                                  help="Path to RAG_MASTER.json (locates the log directory)")
    forensics_parser.add_argument("--log", type=Path, default=None,
                                  help="explicit path to a session_log_<sid>.jsonl")
    forensics_parser.add_argument("--json", dest="as_json", action="store_true",
                                  help="emit machine-readable JSON")

    # -- migrate (KA-SCHEMA-MIGRATE: governed deployment-facing schema/version uplift) --
    migrate_parser = subparsers.add_parser(
        "migrate",
        help="Migrate a DEPLOYMENT's RAG meta up to the schema this kernel speaks — declared ladder, reads the target's own meta, refuses to downgrade a deploy that is ahead, fails loud on an unknown origin, no-op when current (KA-SCHEMA-MIGRATE).",
    )
    migrate_parser.add_argument("--rag", type=Path, default=_default_rag_path(),
                                help="Path to the TARGET deployment's RAG_MASTER.json")
    migrate_parser.add_argument("--session", type=str, required=True,
                                help="session id recorded in the meta.migrations audit trail")
    migrate_parser.add_argument("--spec-version", dest="spec_version", type=str, default=None,
                                help="policy/spec version to reconcile against (default: live rag_kernel.__spec_version__)")
    migrate_parser.add_argument("--dry-run", action="store_true",
                                help="show the planned migration without writing")

    # -- transplant (TRANSPLANT-CLASSIFY-AUTHORITY: governed scaffold-rule transplant) --
    transplant_parser = subparsers.add_parser(
        "transplant",
        help="Transplant MISSING universal governance rules from a SOURCE kernel into a TARGET deployment. Authority A (spec-derived): a rule is universal iff its key appears in the named INIT spec. Additive-only, project-specific rules invisible, collision on differing content is fail-loud (never overwrite), target-ahead refused, idempotent, atomic + audited.",
    )
    transplant_parser.add_argument("--rag", type=Path, default=_default_rag_path(),
                                   help="Path to the TARGET deployment's RAG_MASTER.json (the one written)")
    transplant_parser.add_argument("--source", type=Path, required=True,
                                   help="Path to the SOURCE kernel's RAG_MASTER.json (read-only authority for rule content)")
    transplant_parser.add_argument("--spec", type=Path, required=True,
                                   help="INIT spec .md whose operating_protocol keys DEFINE the universal set (Authority A)")
    transplant_parser.add_argument("--session", type=str, required=True,
                                   help="session id recorded in the meta.transplants audit trail")
    transplant_parser.add_argument("--dry-run", action="store_true",
                                   help="render every planned addition and collision line-by-line without writing")

    # -- birth-adopt (BIRTH-ADOPT-VERB: carry hardened VALUES between deployments) --
    # transplant is additive-only and halts on every differing key; migrate never
    # touches operating_protocol. This verb is the missing path: it moves an
    # IMPROVED value of an EXISTING rule, in a direction it can justify.
    adopt_parser = subparsers.add_parser(
        "birth-adopt",
        help="Carry hardened universal rule VALUES between kernel deployments. Three modes: diff (per-key, BOTH directions, with provenance and a stated reason — never writes), adopt (BIRTH path: apply every add + source-ahead move in one governed pass), update (RUNNING deployment: propagate an improved value of an EXISTING rule, optimistic-concurrency guarded). Direction is decided by the INIT spec as a third reference point, then by provenance; a true tie is REFUSED, never guessed.",
    )
    adopt_parser.add_argument("mode", choices=["diff", "adopt", "update"],
                              help="diff (read-only, mandatory first), adopt (birth), update (running deployment)")
    adopt_parser.add_argument("--rag", type=Path, default=_default_rag_path(),
                              help="Path to the TARGET deployment's RAG_MASTER.json (the only file written)")
    adopt_parser.add_argument("--source", type=Path, required=True,
                              help="Path to the SOURCE kernel's RAG_MASTER.json (read-only authority for rule content)")
    adopt_parser.add_argument("--spec", type=Path, required=True,
                              help="INIT spec .md whose operating_protocol keys DEFINE the universal set (Authority A)")
    adopt_parser.add_argument("--session", type=str, required=True,
                              help="session id stamped into meta.rule_provenance on every key this verb sets")
    adopt_parser.add_argument("--key", dest="keys", action="append", default=None,
                              help="update mode: restrict propagation to this rule key (repeatable). Omit to propagate every source-ahead key.")
    adopt_parser.add_argument("--decide", dest="decisions", action="append", default=None,
                              metavar="KEY=source|target",
                              help="adopt mode: an explicit operator ruling for a DIVERGED key (repeatable). Without one, a diverged key is a hard refusal.")
    adopt_parser.add_argument("--limit", type=int, default=0,
                              help="cap the rendered move list (Rule 17 bounded emission; 0 = all)")
    adopt_parser.add_argument("--force", action="store_true",
                              help="update mode: overwrite even when the target's live value no longer matches its recorded provenance hash")
    adopt_parser.add_argument("--dry-run", action="store_true",
                              help="render what adopt/update would apply without writing")

    # -- ingest (BLUEPRINT-INGEST-PROTOCOL: document -> governed state, with an
    #    exit predicate instead of an opinion) --
    ingest_parser = subparsers.add_parser(
        "ingest",
        help="Route a source document into governed state (RUNBOOK 5A, mechanised): RULE->operating_protocol, REFERENCE->COLD, ASSET->RAG_CONTEXT[baked_assets], TASK->tracked_items, DELIVERABLE->meta.root_deliverables. Claims are declared with `INGEST: <KIND> <id> — <text>` lines or inferred from headings. Reports the DECIDABLE exit predicate: the deployment answers what the document answers, without the document.",
    )
    ingest_parser.add_argument("document", type=Path, help="source document to ingest")
    ingest_parser.add_argument("--rag", type=Path, default=_default_rag_path(),
                               help="Path to RAG_MASTER.json")
    ingest_parser.add_argument("--rag-dir", dest="rag_dir", type=Path, default=None,
                               help="directory holding RAG_CONTEXT.json (default: the RAG dir)")
    ingest_parser.add_argument("--limit", type=int, default=0,
                               help="cap the rendered create-list (Rule 17; 0 = all)")
    ingest_parser.add_argument("--json", dest="as_json", action="store_true",
                               help="output the plan as JSON")

    # -- list-kinds (INGEST-KIND-UNVALIDATED: the enumerable half of the contract) --
    list_kinds_parser = subparsers.add_parser(
        "list-kinds",
        help="Print the INGEST kinds THIS deployment declares, with their destinations. The authoritative set a sender must use: `ingest` REFUSES any other kind, and a sender that cannot enumerate the receiver's kinds can only guess (HANDOFF-PRESCRIPTION-BAN).",
    )
    list_kinds_parser.add_argument("--json", dest="as_json", action="store_true",
                                   help="output as JSON")

    # -- measured (RUNBOOK-TABLE-NO-INVARIANT: measured tables that go stale loudly) --
    measured_parser = subparsers.add_parser(
        "measured",
        help="List the MEASURED provenance stamps in project documents and whether the live runtime/spec has moved past them — the machine form of 're-measure before you trust this document'. Exit 1 if any stamp is stale.",
    )
    measured_parser.add_argument("--rag", type=Path, default=_default_rag_path(),
                                 help="Path to RAG_MASTER.json (used to locate the project root)")
    measured_parser.add_argument("--roots", nargs="*", type=Path, default=None,
                                 help="directories to scan for *.md (default: the project root)")
    measured_parser.add_argument("--stamp", action="store_true",
                                 help="print the stamp a re-measuring session should paste into its document")
    measured_parser.add_argument("--session", type=str, default=None,
                                 help="session id to record in --stamp output")
    measured_parser.add_argument("--json", dest="as_json", action="store_true",
                                 help="output as JSON")

    # -- decide / decisions (DECISION-LEDGER-PRIMITIVE: operator rulings as state) --
    decide_parser = subparsers.add_parser(
        "decide",
        help="Record an OPERATOR RULING as a first-class governed record: the question, the alternatives that were actually on the table, which was chosen, and the tracked items it binds. Refuses a single-option 'decision' (that is a mandatory control disguised as a preference, E-092) and refuses a bind that does not resolve.",
    )
    decide_parser.add_argument("--rag", type=Path, default=_default_rag_path(),
                               help="Path to RAG_MASTER.json")
    decide_parser.add_argument("--session", type=str, required=True,
                               help="session id recorded on the ruling")
    decide_parser.add_argument("--question", type=str, required=True,
                               help="the question the operator answered")
    decide_parser.add_argument("--option", dest="options", action="append",
                               required=True,
                               help="an alternative that was on the table (repeatable; at least two)")
    decide_parser.add_argument("--chosen", type=str, required=True,
                               help="the option the operator chose (must be one of --option)")
    decide_parser.add_argument("--rationale", type=str, default="",
                               help="one-line reason recorded with the ruling")
    decide_parser.add_argument("--binds", action="append", default=None,
                               help="tracked_item id this ruling governs (repeatable)")
    decide_parser.add_argument("--supersedes", type=str, default=None,
                               help="decision id this ruling replaces (DIRECTIVE-SUPERSEDE-PATH)")
    decide_parser.add_argument("--dry-run", action="store_true",
                               help="validate without writing")

    decisions_parser = subparsers.add_parser(
        "decisions",
        help="Render the decision ledger (read-only): every operator ruling with its alternatives, what was chosen, and what it binds.",
    )
    decisions_parser.add_argument("--rag", type=Path, default=_default_rag_path(),
                                  help="Path to RAG_MASTER.json")
    decisions_parser.add_argument("--limit", type=int, default=0,
                                  help="show only the most recent N (Rule 17; 0 = all)")
    decisions_parser.add_argument("--live", action="store_true",
                                  help="hide rulings that a later decision superseded")
    decisions_parser.add_argument("--item", type=str, default=None,
                                  help="show only rulings binding this tracked_item id")

    # -- register-asset / reuse-check (REUSE-REGISTRY-GUARD: baked-asset registry) --
    # Lean-RAG: the inventory lives in the sanctioned, NON-LOADED RAG_CONTEXT.json
    # `baked_assets` partition; RAG_MASTER.json carries only the concise
    # reuse_registry_guard pointer rule. See rag_kernel.asset_registry.
    reg_asset_parser = subparsers.add_parser(
        "register-asset",
        help="Register a baked asset (path + purpose + sha256) into the sanctioned, "
             "non-loaded RAG_CONTEXT.json baked_assets partition (lean-RAG). Additive + "
             "idempotent; a rebound id or a duplicate path is fail-loud. REUSE-REGISTRY-GUARD.",
    )
    reg_asset_parser.add_argument("path", type=Path,
                                  help="path to the asset file (relative to project root or absolute)")
    # Not argparse-required: --deregister retires a record and needs no purpose.
    # The handler enforces it for every other path, so registering without one
    # is still fail-loud.
    reg_asset_parser.add_argument("--purpose", type=str, default=None,
                                  help="one-line description of what the asset does (matched by reuse-check). "
                                       "Required unless --deregister.")
    reg_asset_parser.add_argument("--deregister", action="store_true",
                                  help="RETIRE the record for this path/id: the counterpart of registering, "
                                       "for a file that has been archived or removed. The file itself is "
                                       "never touched. Fail-loud when the id is not registered.")
    reg_asset_parser.add_argument("--id", dest="asset_id", type=str, default=None,
                                  help="stable asset id (default: the project-relative POSIX path)")
    reg_asset_parser.add_argument("--session", type=str, required=True,
                                  help="session id recorded on the asset record")
    reg_asset_parser.add_argument("--update", action="store_true",
                                  help="the SAME file at the SAME path legitimately changed: re-hash "
                                       "the existing id in place and push the prior sha256 onto its "
                                       "`supersedes` lineage. Refuses to re-aim an id at a different "
                                       "path. Without this flag a content change stays fail-loud.")
    reg_asset_parser.add_argument("--rag-dir", type=Path, default=_default_rag_path().parent,
                                  help="directory holding RAG_CONTEXT.json (default: the RAG dir)")
    reg_asset_parser.add_argument("--project-root", type=Path, default=None,
                                  help="project root for portable relative-path storage (default: parent of rag-dir)")
    reg_asset_parser.add_argument("--dry-run", action="store_true",
                                  help="validate + render the record without writing")

    hook_guard_parser = subparsers.add_parser(
        "hook-guard",
        help="HOOK-ENFORCEMENT-LAYER: the decision engine behind .claude/settings.json. "
             "Reads one Claude Code hook payload on stdin and refuses the call when it "
             "violates a process rule (polling a running command, a sandbox shell or "
             "file tool touching canonical state); --selftest drives every gate through "
             "a known-bad payload and asserts the refusal, so 'the hooks are installed' "
             "is a measurement rather than a claim.",
    )
    hook_guard_parser.add_argument("--gate", type=str, default=None,
                                   help="which gate to evaluate: " + ", ".join(hook_guard.GATES))
    hook_guard_parser.add_argument("--selftest", action="store_true",
                                   help="drive every gate through a known-bad payload; exit 1 on any gate that fails to refuse")
    hook_guard_parser.add_argument("--project-root", type=Path, default=None,
                                   help="project root used to resolve the deploy-parity twin (default: walk up from the edited file)")
    hook_guard_parser.add_argument("--state-dir", type=Path, default=None,
                                   help="directory for the poll window state file (default: $RAG_HOOK_STATE_DIR or ~/.rag_kernel_hooks)")

    # -- status (OPERATOR-ONE-NUMBER: the operator's verb, not the agent's) --
    status_parser = subparsers.add_parser(
        "status",
        help="ONE line: GREEN or NOT GREEN plus the single reason. Exit 0 or 1. "
             "Composed only of already-measured terms; UNKNOWN blocks GREEN "
             "(AUDIT_PROTOCOL L2). Run by the OPERATOR — this is the verb that "
             "makes trusting an agent report unnecessary. OPERATOR-ONE-NUMBER.",
    )
    status_parser.add_argument("--rag", type=Path, default=_default_rag_path(),
                               help="Path to RAG_MASTER.json")
    status_parser.add_argument("--verbose", "-v", action="store_true",
                               help="also print every term, not just the verdict")
    status_parser.add_argument("--json", dest="json_output", action="store_true",
                               help="emit machine-readable JSON")

    reuse_check_parser = subparsers.add_parser(
        "reuse-check",
        help="Pre-write reuse guard: report any baked asset already covering a --path "
             "and/or --purpose. Fail-loud (exit 1) on a hit so you REUSE instead of "
             "rewrite; exit 0 when nothing is baked yet. REUSE-REGISTRY-GUARD.",
    )
    reuse_check_parser.add_argument("--path", type=Path, default=None,
                                    help="candidate asset path to check for prior registration")
    reuse_check_parser.add_argument("--purpose", type=str, default=None,
                                    help="candidate purpose to check (case-insensitive containment, either direction)")
    reuse_check_parser.add_argument("--rag-dir", type=Path, default=_default_rag_path().parent,
                                    help="directory holding RAG_CONTEXT.json (default: the RAG dir)")
    reuse_check_parser.add_argument("--project-root", type=Path, default=None,
                                    help="project root for portable relative-path storage (default: parent of rag-dir)")
    reuse_check_parser.add_argument("--json", dest="json_output", action="store_true",
                                    help="output matches as JSON instead of text")
    reuse_check_parser.add_argument("--fleet", action="store_true",
                                    help="also search the persisted fleet view "
                                         "(sibling deployments) — see `inventory fleet`")

    # -- session-delta (SESSION-DELTA-RITUAL: the debit/credit a session owes) --
    # Every session has hand-written this and every hand-written number has been
    # a number from memory. The close emits it automatically; the verb exists so
    # it can be inspected mid-session and so a clone can run it standalone.
    sd_parser = subparsers.add_parser(
        "session-delta",
        help="Deterministic end-of-session debit/credit report: item movements derived "
             "from tracked_items history plus counters diffed against the last "
             "persisted snapshot. SESSION-DELTA-RITUAL.",
    )
    sd_parser.add_argument("--rag", type=Path, default=_default_rag_path(),
                           help="Path to RAG_MASTER.json")
    sd_parser.add_argument("--session", type=str, default=None,
                           help="session to report on (default: meta.written_by_session)")
    sd_parser.add_argument("--repo", type=Path, default=None,
                           help="git worktree to measure HEAD/dirty/formal from")
    sd_parser.add_argument("--audit-errors", type=int, default=None,
                           help="audit error count to record (omitted = 'not measured')")
    sd_parser.add_argument("--audit-warnings", type=int, default=None,
                           help="audit warning count to record (omitted = 'not measured')")
    sd_parser.add_argument("--save-baseline", action="store_true",
                           help="persist this run's counters as the next session's before")
    sd_parser.add_argument("--out", type=Path, default=None,
                           help="also write the report to this path")
    sd_parser.add_argument("--json", dest="json_output", action="store_true",
                           help="emit the delta as JSON instead of markdown")

    # -- inventory (FLEET-INVENTORY: know what exists, here and next door) ------
    # MANDATORY, not optional. This kernel's registry sat empty for 178 sessions
    # while the runbook told every clone to register what it ships; the eBay clone
    # meanwhile shipped 47 unregistered scripts and reinvented this registry as
    # capability_ledger.py. An inventory that sees only its own deployment cannot
    # stop clone #3 rewriting what clone #1 already shipped.
    inv_parser = subparsers.add_parser(
        "inventory",
        help="Classify every file in a deployment root, list what is not yet registered, "
             "and merge the reusable surface of sibling deployments into one searchable "
             "view. Read-only except `backfill`. FLEET-INVENTORY.",
    )
    inv_parser.add_argument("mode", choices=["scan", "backfill", "fleet"],
                            help="scan: classify a root (read-only) · "
                                 "backfill: register what scan found unregistered · "
                                 "fleet: merge sibling deployments into the fleet view")
    inv_parser.add_argument("--root", type=Path, default=None,
                            help="deployment root to walk (default: the RAG dir's parent)")
    inv_parser.add_argument("--rag-dir", type=Path, default=_default_rag_path().parent,
                            help="directory holding RAG_CONTEXT.json (default: the RAG dir)")
    inv_parser.add_argument("--deployment", action="append", default=None,
                            metavar="NAME=ROOT",
                            help="sibling deployment for `fleet` mode; repeatable. "
                                 "Omit to use the `fleet` context partition.")
    inv_parser.add_argument("--session", type=str, default=None,
                            help="session id recorded on backfilled asset records")
    inv_parser.add_argument("--limit", type=int, default=40,
                            help="cap on listed entries (Rule 17 bounded emission)")
    inv_parser.add_argument("--dry-run", action="store_true",
                            help="backfill/fleet: render the plan without writing")
    inv_parser.add_argument("--json", dest="json_output", action="store_true",
                            help="Output as JSON instead of text")

    # -- verify (FIX-2: deterministic post-init self-version coherence gate) --
    verify_parser = subparsers.add_parser(
        "verify",
        help="Verify a freshly-built RAG: HOT↔COLD self-version coherence, no unsubstituted version placeholder (zero tokens).",
    )
    verify_parser.add_argument(
        "--rag", type=Path, default=_default_rag_path(),
        help="Path to RAG_MASTER.json (default: RAG/RAG_MASTER.json)",
    )
    verify_parser.add_argument(
        "--cold", type=Path, default=None,
        help="Path to RAG_COLD.json (default: RAG_COLD.json beside the RAG file)",
    )
    verify_parser.add_argument(
        "--spec", type=Path, default=None,
        help="Optional spec MD to assert HOT/COLD versions equal the spec's own version.",
    )
    verify_parser.add_argument("--json", dest="json_output", action="store_true", help="Output as JSON instead of text")

    # -- run (RUN-DETACH-AWAIT, S185): launch AND wait as ONE operation ---------
    #
    # wait-for below made waiting a verb. It did not make LAUNCHING one, so launch
    # and wait stayed two agent actions with a pollable handle between them — and
    # that window is the single generator behind four banked ERROR items (re-run
    # it, kill it, ask about it, poll it). This verb closes the window by never
    # returning control until the job is in a TERMINAL state.
    run_parser = subparsers.add_parser(
        "run",
        help="Launch a command detached AND wait for it in ONE call. Returns a "
             "terminal state — DONE 0 / FAILED 1 / TIMEOUT 2 / DIED 3 — plus a "
             "bounded tail. No intermediate handle exists to poll, deliberately.",
    )
    # dest is cmd_argv, NOT command: argparse already uses args.command for the
    # subcommand name, and shadowing it makes the dispatch table unhashable.
    run_parser.add_argument("cmd_argv", nargs="+", metavar="COMMAND",
                            help="the command to run (use -- before it if it has flags)")
    run_parser.add_argument("--log", type=Path, default=None,
                            help="transcript path (default: <cwd>/.boot/run.log). "
                                 "Always written, so a lost terminal is recovered by "
                                 "a READ, never by re-running the job.")
    run_parser.add_argument("--cwd", type=Path, default=None,
                            help="working directory for the command")
    run_parser.add_argument("--timeout", type=float, default=900.0,
                            help="upper bound in seconds (default: 900). A TIMEOUT "
                                 "means UNOBSERVED, not failed — the process is still "
                                 "alive and exit 2 says so distinctly from DIED (3).")
    run_parser.add_argument("--poll-ms", dest="poll_ms", type=int, default=1000,
                            help="internal wait interval in ms (default: 1000). The "
                                 "MACHINE waits here at zero tool round-trips; this is "
                                 "not the E-081 agent polling that is banned.")
    run_parser.add_argument("--emit", type=int, default=20,
                            help="tail lines to return (default: 20; 0 = all)")
    run_parser.add_argument("--detach", action="store_true",
                            help="accepted for symmetry and readability; detaching is "
                                 "unconditional — there is no attached mode.")
    run_parser.add_argument("--await", dest="await_", action="store_true",
                            help="accepted for symmetry; waiting is unconditional.")
    run_parser.add_argument("--kill-on-timeout", dest="kill_on_timeout",
                            action="store_true",
                            help="SIGTERM the process group if the deadline passes. "
                                 "OFF by default: killing an unobserved job that is "
                                 "still making progress is worse than waiting again.")

    # -- wait-for (WAIT-PRIMITIVE: the sanctioned blocking wait, S176 -> S180) --
    # Long jobs run DETACHED to a file (E-081). This is how you wait for one:
    # server-side, inside the sanctioned transport, zero agent round-trips.
    # Closes the hole that produced E-082b/E-085/E-086/E-089/E-090 -- five
    # consecutive sessions of polling or banned-sandbox sleeping, all because a
    # discipline with no mechanism is only advice.
    wait_parser = subparsers.add_parser(
        "wait-for",
        help="Block until a sentinel file exists (or contains a token), then return "
             "a bounded tail of it. Exit 0 found / 1 timeout / 2 usage. Touches no "
             "state and needs no RAG, so it works at session zero. WAIT-PRIMITIVE.",
    )
    wait_parser.add_argument("path", type=Path,
                             help="sentinel file to watch (its parent need not exist yet)")
    wait_parser.add_argument("--timeout", type=float, required=True,
                             help="hard upper bound in seconds, measured on the monotonic clock")
    wait_parser.add_argument("--poll-ms", dest="poll_ms", type=int, default=250,
                             help="internal stat interval in ms (default: 250). The MACHINE "
                                  "polls here, not the agent: this costs zero tool round-trips "
                                  "and is not the E-081 polling that is banned.")
    wait_parser.add_argument("--contains", type=str, default=None,
                             help="require this token inside the file, not merely its existence. "
                                  "PREFER THIS: shell redirection creates the file before the job "
                                  "writes anything, so bare existence races the writer. Have the "
                                  "job echo a DONE marker last and wait on that. A token "
                                  "that starts with a dash (e.g. --attest) is "
                                  "accepted in either spelling since S198.")
    wait_parser.add_argument("--emit", dest="emit_lines", type=int, default=0,
                             help="on success, print the last N lines of the sentinel file so one "
                                  "round-trip returns both completion AND result (Rule 17).")
    wait_parser.add_argument("--json", dest="json_output", action="store_true",
                             help="Output as JSON instead of text")

    # -- context (FIX-11 inc2 / U3: CLI group over the sanctioned RAG_CONTEXT.json store) --
    # A governed path to land project-specific context into the sanctioned,
    # NON-LOADED RAG_CONTEXT.json store inc1 introduced — instead of hand-editing
    # JSON or dropping a transient *_context.json the side-store auditor flags.
    context_parser = subparsers.add_parser(
        "context",
        help="Read/write the sanctioned, non-loaded project-context store (RAG_CONTEXT.json).",
    )
    context_sub = context_parser.add_subparsers(dest="context_action", help="set | get | list")
    _ctx_dir_default = _default_rag_path().parent

    ctx_set = context_sub.add_parser("set", help="Create/replace a context partition (atomic, no .bak).")
    ctx_set.add_argument("partition", type=str, help="top-level partition key")
    ctx_set.add_argument(
        "value", type=str, nargs="?", default=None,
        help="partition value as JSON (object/array/scalar). Omit and use --value-file for large values.",
    )
    ctx_set.add_argument("--value-file", dest="value_file", type=Path, default=None,
                         help="read the JSON value from this file instead of the positional arg")
    ctx_set.add_argument("--rag-dir", dest="rag_dir", type=Path, default=_ctx_dir_default,
                         help="directory holding RAG_CONTEXT.json (default: the RAG dir)")
    ctx_set.add_argument("--dry-run", action="store_true", help="validate without writing")

    ctx_get = context_sub.add_parser("get", help="Lazy-load and print one context partition.")
    ctx_get.add_argument("partition", type=str, help="top-level partition key")
    ctx_get.add_argument("--rag-dir", dest="rag_dir", type=Path, default=_ctx_dir_default,
                         help="directory holding RAG_CONTEXT.json (default: the RAG dir)")
    ctx_get.add_argument("--json", dest="json_output", action="store_true",
                         help="print the raw JSON value only (no header)")

    ctx_list = context_sub.add_parser("list", help="List partitions with loaded state + token budget.")
    ctx_list.add_argument("--rag-dir", dest="rag_dir", type=Path, default=_ctx_dir_default,
                          help="directory holding RAG_CONTEXT.json (default: the RAG dir)")
    ctx_list.add_argument("--json", dest="json_output", action="store_true",
                          help="output the summary as JSON")

    # -- bootmap (ROOT-FILE-MANIFEST S168) --
    # Deterministic domain boot-map: walk the project root, diff vs the sealed
    # baseline, and (optionally) refresh the baseline. The same walk the
    # session-start GC step already does — this verb exposes it standalone.
    bootmap_parser = subparsers.add_parser(
        "bootmap",
        help="Domain boot-map: walk the project root, diff vs the sealed baseline (--refresh to reseal).",
    )
    bootmap_parser.add_argument(
        "--rag", type=Path, default=_default_rag_path(),
        help="Path to RAG_MASTER.json (default: RAG/RAG_MASTER.json) — its dir holds the map sidecar",
    )
    bootmap_parser.add_argument(
        "--root", type=Path, default=None,
        help="Project root to walk (default: the RAG file's grandparent)",
    )
    bootmap_parser.add_argument(
        "--refresh", action="store_true",
        help="Reseal the persisted baseline (session-end semantics) instead of a read-only diff.",
    )
    bootmap_parser.add_argument(
        "--session", type=str, default=None,
        help="Session id stamped on a --refresh reseal (audit trail).",
    )
    bootmap_parser.add_argument("--json", dest="json_output", action="store_true",
                                help="output the map/diff as JSON")

    # -- deployment (DEPLOYMENT-REGISTRY: authorized destinations as FIELDS, S186) --
    dep_parser = subparsers.add_parser(
        "deployment",
        help=("Read or set meta.deployments fields -- notably authorized_remote "
              "and authorized_identity, the facts a push gate checks against. "
              "Refuses an unrecorded deployment and an unsettable field."),
    )
    dep_parser.add_argument("--rag", type=Path, default=None, help="Path to RAG_MASTER.json")
    dep_parser.add_argument("--list", action="store_true", help="render the registry (read-only)")
    dep_parser.add_argument("--key", type=str, default=None, help="deployment key in meta.deployments")
    dep_parser.add_argument("--field", type=str, default=None, help="field to set")
    dep_parser.add_argument("--value", type=str, default=None, help="new value")
    dep_parser.add_argument("--session", type=str, default=None, help="session id (audit trail)")

    # -- push-check (PUSH-DESTINATION GATE, S186) --
    push_parser = subparsers.add_parser(
        "push-check",
        help=("Refuse a push whose remote is not the declared authorized_remote "
              "for that deployment. REFUSE-BY-DEFAULT: an undeclared destination "
              "is refused, because absence of a declaration is not permission. "
              "Also refuses a credential embedded in the remote URL."),
    )
    push_parser.add_argument("--rag", type=Path, default=None, help="Path to RAG_MASTER.json")
    push_parser.add_argument("--deployment", type=str, required=True, help="deployment key")
    push_parser.add_argument("--root", type=Path, required=True, help="working tree holding the remote")
    push_parser.add_argument("--remote", type=str, default="origin", help="remote name")

    # -- adopt-preflight (ADOPT-DESTROYS-LOCAL-DIVERGENCE, S186) --
    pre_parser = subparsers.add_parser(
        "adopt-preflight",
        help=("Before a runtime redeploy, enumerate what the TARGET would LOSE "
              "-- modules, verbs and flags it holds that the incoming package "
              "does not -- and refuse unless --accept-local-loss. Byte-identity "
              "is a deletion mechanism."),
    )
    pre_parser.add_argument("--target", type=Path, required=True, help="dir CONTAINING the target rag_kernel/")
    pre_parser.add_argument("--source", type=Path, required=True, help="dir CONTAINING the incoming rag_kernel/")
    pre_parser.add_argument("--accept-local-loss", dest="accept_local_loss", action="store_true",
                            help="delete the listed local work deliberately")

    return parser


def cmd_init(args: argparse.Namespace) -> int:
    from rag_kernel.spec_parser import SpecParser, VOID_RAG
    import json
    from copy import deepcopy

    sp = SpecParser()

    if args.spec and args.spec.exists():
        result = sp.parse_file(args.spec)
        print(sp.report(result))
        if result.errors:
            print(f"\nWARNING: {len(result.errors)} parse errors (blocks skipped).")
        # FIX-2 fail-loud: an unresolved <SPEC_VERSION> means the spec header
        # carried no parseable version — writing now would birth a COLD↔HOT
        # drift. Refuse rather than emit a defective RAG.
        version_errs = [e for e in result.errors if e.section_id == "version"]
        if version_errs:
            print(
                "\nFATAL: unresolved self-version token(s) — refusing to write "
                "a drifted RAG (FIX-2):",
                file=sys.stderr,
            )
            for e in version_errs:
                print(f"  - {e.message}", file=sys.stderr)
            return 2
        rag = result.merged
        cold = result.cold_template
    elif args.spec and not args.spec.exists():
        print(f"Error: Spec file not found: {args.spec}", file=sys.stderr)
        return 1
    else:
        if not getattr(args, "allow_void", False):
            print(
                "Error: init requires --spec to bootstrap a governed RAG.\n"
                "  No --spec was provided, which would create a VOID RAG with no governance "
                "(the silent governance-loss failure mode, INS-046).\n"
                "  Fix: pass --spec <init_prompt.md> to bootstrap from a spec,\n"
                "  or pass --allow-void to explicitly create an empty structural RAG.",
                file=sys.stderr,
            )
            return 2
        print("No --spec provided. --allow-void set: creating void RAG with structural defaults.")
        rag = deepcopy(VOID_RAG)
        cold = None

    # Path normalization
    def normalize_path(p: str, style: str) -> str:
        """Normalize path separators based on style preference."""
        if not p:
            return p
        if style == "auto":
            # Auto-detect: if path starts with / or /mnt/, it's posix
            # If it contains a drive letter (X:), it's windows
            if len(p) >= 2 and p[1] == ":":
                style = "windows"
            elif p.startswith("/"):
                style = "posix"
            else:
                style = "windows"  # default for ambiguous
        if style == "windows":
            return p.replace("/", "\\")
        else:
            return p.replace("\\", "/")

    path_style = getattr(args, "path_style", "auto")

    if args.root_project:
        rag["meta"]["root_project"] = normalize_path(args.root_project, path_style)
    if args.root_deliverables:
        rag["meta"]["root_deliverables"] = normalize_path(args.root_deliverables, path_style)
    if args.root_rag:
        rag["meta"]["root_rag"] = normalize_path(args.root_rag, path_style)
    if args.project_name:
        rag["meta"]["project_name"] = args.project_name

    # KA-9 / spec §1182: project_context (brief/domain/end_goal/principals) ships
    # with "<from user>" session-zero template tokens. When the operator does not
    # supply a value at init, the contract is to initialize the field to null (the
    # model infers it during the boot scan) — NOT to leave the literal placeholder.
    # Leaving it is exactly the eBay Session-Zero defect: a READY RAG carrying
    # "<from user>" that the new drift_audit.check_project_context_placeholders
    # gate fails loud on. Resolving every unfilled human-fill placeholder to null
    # here makes a fresh `init` / `--auto-ready` born clean instead of failing the
    # gate by construction (same born-clean discipline as FIX-9 for K7).
    def _null_unfilled_placeholders(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if isinstance(v, str) and _PC_TEMPLATE_TOKEN_RE.fullmatch(v.strip()):
                    node[k] = None
                else:
                    _null_unfilled_placeholders(v)
        elif isinstance(node, list):
            for i, v in enumerate(node):
                if isinstance(v, str) and _PC_TEMPLATE_TOKEN_RE.fullmatch(v.strip()):
                    node[i] = None
                else:
                    _null_unfilled_placeholders(v)

    pc = rag.get("project_context")
    if isinstance(pc, (dict, list)):
        _null_unfilled_placeholders(pc)

    errors = sp.validate_rag(rag)
    if errors:
        print(f"\nValidation issues ({len(errors)}):")
        for e in errors:
            print(f"  - {e}")

    # Auto-ready (FIX-9 / U1): --auto-ready must yield a STAMPED, audit-clean RAG.
    # A bare BOOTING->READY flip used to leave meta.written_by_session="" and
    # last_checkpoint_seq=0; once READY, drift_audit.check_written_by_session
    # fails loud (it self-skips only while BOOTING), so the very first auditor run
    # on the prescribed clean-deploy path failed by construction. This is the K7
    # residual FIX-3 did not close — checkpoint stamps written_by_session, but
    # --auto-ready bypassed checkpoint entirely. Route the transition through the
    # first session-stamping checkpoint: stamp written_by_session, seq->1, the
    # session record, and mirror .bak (mirror_bak=True, matching api.checkpoint
    # do_full and the standalone `checkpoint` verb) so a fresh
    # `init --spec ... --auto-ready` is `audit --strict` clean with zero manual
    # workarounds.
    auto_ready = getattr(args, "auto_ready", False) and not errors
    if auto_ready:
        from datetime import datetime, timezone

        session_id = getattr(args, "session", None) or "S0"
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        rag["state_machine_status"] = "READY"
        rag["meta"]["last_updated_utc"] = now
        rag["meta"]["session_id"] = session_id
        rag["meta"]["written_by_session"] = session_id
        rag["meta"]["last_checkpoint_seq"] = (
            rag["meta"].get("last_checkpoint_seq", 0) + 1
        )
        sessions = rag.get("sessions_recent", [])
        sessions.append({
            "id": session_id,
            "d": now,
            "s": (
                f"{session_id}: bootstrap init via --auto-ready — first "
                "session-stamping checkpoint (FIX-9 / U1)."
            ),
        })
        rag["sessions_recent"] = sessions[-5:]
        print(
            f"\n--auto-ready: BOOTING -> READY via first session-stamping "
            f"checkpoint (session {session_id}, seq "
            f"{rag['meta']['last_checkpoint_seq']})."
        )

    if not args.dry_run:
        output_dir = args.output or Path("RAG")
        hot_path = output_dir / "RAG_MASTER.json"
        if auto_ready:
            # Stamped checkpoint write: mirror .bak for FIX-4 / K6 byte-parity,
            # matching api.checkpoint do_full and the standalone `checkpoint` verb.
            # (sp.write_rag mkdir's its parent; atomic_write_json does not, so
            # create the output dir before the atomic .tmp write.)
            from rag_kernel.persistence import atomic_write_json

            hot_path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_json(hot_path, rag, mirror_bak=True)
            written = str(hot_path)
        else:
            written = sp.write_rag(rag, hot_path)
        print(f"\nRAG_MASTER.json written to: {written}")
        if cold:
            cold_path = output_dir / "RAG_COLD.json"
            cold_written = sp.write_cold(cold, cold_path)
            print(f"RAG_COLD.json written to: {cold_written}")

        # Generate requirements.txt if --requirements was provided
        if args.requirements is not None:
            req_dir = Path(args.root_project) if args.root_project else Path(".")
            req_path = req_dir / "requirements.txt"
            packages = args.requirements  # list of package names, possibly empty
            _write_requirements(req_path, packages, dry_run=False)

        print("\nDone. Zero tokens consumed.")
    else:
        print("\n[DRY RUN] No files written.")
        print(f"RAG preview ({len(json.dumps(rag))} bytes):")
        print(json.dumps(rag, indent=2)[:500] + "...")

        if args.requirements is not None:
            req_dir = Path(args.root_project) if args.root_project else Path(".")
            req_path = req_dir / "requirements.txt"
            _write_requirements(req_path, args.requirements, dry_run=True)

    return 0 if not errors else 1


def _write_requirements(req_path: Path, packages: list[str], *, dry_run: bool = False) -> None:
    """Write a requirements.txt file with the given packages.

    If packages is empty, writes a template with comments explaining usage.
    Satisfies: INS-010 (deterministic dependency install at session-zero).
    """
    header = (
        "# Requirements file generated by rag_kernel init\n"
        "# Install: pip install -r requirements.txt\n"
        "# Pin versions for reproducibility: package==1.2.3\n"
    )
    if packages:
        content = header + "\n".join(packages) + "\n"
    else:
        content = (
            header
            + "#\n"
            + "# Add your project dependencies below, one per line:\n"
            + "# example-package>=1.0\n"
            + "# another-package==2.3.4\n"
        )

    if dry_run:
        print(f"\n[DRY RUN] Would create requirements.txt at: {req_path}")
        print(f"  Packages: {len(packages)}")
    else:
        req_path.parent.mkdir(parents=True, exist_ok=True)
        with open(req_path, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"requirements.txt written to: {req_path} ({len(packages)} packages)")


def cmd_configure(args: argparse.Namespace) -> int:
    """Merge project-specific context into an existing RAG_MASTER.json.

    Accepts two context formats:
    1. JSON file — deep-merged directly into the RAG
    2. Structured MD file — rag-config blocks extracted and merged

    Preserves all existing RAG data; context is overlaid on top.
    """
    from rag_kernel.spec_parser import SpecParser, deep_merge
    import json

    sp = SpecParser()

    # Load existing RAG
    rag_path = args.rag.resolve()
    if not rag_path.exists():
        print(f"Error: RAG file not found: {rag_path}", file=sys.stderr)
        return 1

    with open(rag_path, "r", encoding="utf-8") as f:
        existing_rag = json.load(f)

    print(f"Loaded RAG: {rag_path}")
    print(f"  Schema: {existing_rag.get('meta', {}).get('schema_version', '?')}")
    print(f"  Policy: {existing_rag.get('meta', {}).get('policy_version', '?')}")

    # KA-RECON-DECLARE: at least one input source is required. --context supplies a
    # full overlay; --reconciliation-docs-root is a single governed meta declaration.
    # Either or both may be given.
    recon_root = getattr(args, "reconciliation_docs_root", None)
    if args.context is None and recon_root is None:
        print("Error: configure needs --context and/or --reconciliation-docs-root",
              file=sys.stderr)
        return 1

    # Load context (optional)
    context_path = args.context.resolve() if args.context is not None else None
    if context_path is not None and not context_path.exists():
        print(f"Error: Context file not found: {context_path}", file=sys.stderr)
        return 1

    # --consume validation (FIX-11 inc3 / U3): refuse to delete a canonical or
    # sanctioned file BEFORE any merge, so misuse fails loud without mutating
    # state. The merge-input is meant to be a *transient* overlay; consuming the
    # RAG itself, its .bak, the COLD archive, or the sanctioned RAG_CONTEXT.json
    # store would destroy real state.
    if getattr(args, "consume", False):
        if context_path is None:
            print("Error: --consume requires a --context file to consume",
                  file=sys.stderr)
            return 1
        from rag_kernel.cold_manager import CONTEXT_FILENAME
        _protected = {
            "rag_master.json", "rag_master.json.bak", "rag_cold.json",
            CONTEXT_FILENAME.lower(),
            rag_path.name.lower(), (rag_path.name + ".bak").lower(),
        }
        if context_path.name.lower() in _protected:
            print(
                f"Error: refusing to --consume a canonical/sanctioned file: "
                f"{context_path.name}", file=sys.stderr,
            )
            return 1

    context_data: dict = {}
    if context_path is not None:
        suffix = context_path.suffix.lower()

        if suffix == ".json":
            # Direct JSON merge
            with open(context_path, "r", encoding="utf-8") as f:
                context_data = json.load(f)
            if not isinstance(context_data, dict):
                print(f"Error: Context JSON must be an object, got {type(context_data).__name__}", file=sys.stderr)
                return 1
            print(f"Context (JSON): {context_path}")
            print(f"  Keys: {list(context_data.keys())}")
        elif suffix == ".md":
            # Parse MD for rag-config blocks
            result = sp.parse_file(context_path)
            if result.errors:
                print(f"WARNING: {len(result.errors)} parse errors in context MD:")
                for e in result.errors:
                    print(f"  {e}")
            if not result.blocks:
                print(f"Error: No rag-config blocks found in {context_path}", file=sys.stderr)
                return 1
            # Merge all config blocks in order
            for block in result.blocks:
                if block.block_type == "config":
                    context_data = deep_merge(context_data, block.data)
            print(f"Context (MD): {context_path}")
            print(f"  Blocks: {len(result.blocks)}, Sections: {len(result.sections_found)}")
            print(f"  Merged keys: {list(context_data.keys())}")
        else:
            print(f"Error: Unsupported context format: {suffix} (expected .json or .md)", file=sys.stderr)
            return 1

    # KA-RECON-DECLARE: overlay the single governed meta declaration LAST, so an
    # explicit --reconciliation-docs-root wins over any value a context file also
    # carries. It rides the same deep_merge + atomic mirror_bak writer below — no
    # hand-edit of RAG_MASTER.json.
    if recon_root is not None:
        context_data = deep_merge(
            context_data, {"meta": {"reconciliation_docs_root": recon_root}})
        print(f"Declaring meta.reconciliation_docs_root = {recon_root!r}")

    # Deep merge context into existing RAG
    updated_rag = deep_merge(existing_rag, context_data)

    # Update timestamp
    from datetime import datetime, timezone
    updated_rag["meta"]["last_updated_utc"] = datetime.now(timezone.utc).isoformat()

    # Validate
    errors = sp.validate_rag(updated_rag)
    if errors:
        print(f"\nValidation issues ({len(errors)}):")
        for e in errors:
            print(f"  - {e}")

    if not args.dry_run:
        # FIX-10 (U2): configure is a canonical RAG-state writer, so it MUST
        # refresh RAG_MASTER.json.bak to byte-parity via atomic_write_json(
        # mirror_bak=True) — matching api.checkpoint do_full, the standalone
        # `checkpoint` verb (FIX-8 / E-045) and init --auto-ready (FIX-9 / U1).
        # The legacy sp.write_rag path did its own tmp+replace atomic write that
        # never touched .bak, leaving the backup one write stale — the K6 / FIX-4
        # parity-mirror gap, same family as E-045. (sp.write_rag mkdir's its parent;
        # atomic_write_json does not, so create the parent dir before the .tmp write.)
        from rag_kernel.persistence import atomic_write_json

        rag_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(rag_path, updated_rag, mirror_bak=True)
        written = str(rag_path)
        print(f"\nRAG_MASTER.json updated: {written}")
        print("Done. Zero tokens consumed.")

        # --consume (FIX-11 inc3 / U3): the merge is committed (HOT + .bak), so
        # now delete the transient input — one atomic, auditor-clean operation so
        # it never lingers in the RAG dir as a flagged side store. A failed unlink
        # is a warning, not a hard failure: the merge already succeeded.
        if getattr(args, "consume", False):
            try:
                context_path.unlink()
                print(f"Consumed merge-input (deleted): {context_path}")
            except OSError as e:
                print(f"WARNING: --consume could not delete {context_path}: {e}",
                      file=sys.stderr)
    else:
        print("\n[DRY RUN] No files written.")
        # Show diff summary
        diff_keys = [k for k in context_data if k in existing_rag]
        new_keys = [k for k in context_data if k not in existing_rag]
        if diff_keys:
            print(f"  Would update: {diff_keys}")
        if new_keys:
            print(f"  Would add: {new_keys}")
        if getattr(args, "consume", False):
            print(f"  Would consume (delete) merge-input: {context_path}")

    return 0 if not errors else 1


def cmd_health(args: argparse.Namespace) -> int:
    import importlib

    import rag_kernel

    project_path = str(args.path.resolve())
    if project_path not in sys.path:
        sys.path.insert(0, project_path)

    # Single source of truth: the kernel's module set lives in _KERNEL_MODULES
    # (rag_kernel/__init__.py), which discover() also walks. Deriving the health
    # check from it — instead of a second hand-typed copy — means health can
    # never silently disagree with discovery (INS-037; the same duplicate-
    # authority drift DRIFT-ELIM removes for project state, applied to source).
    modules = list(rag_kernel._KERNEL_MODULES)

    print("RAG Runtime Kernel - Health Check")
    print(f"Path: {project_path}")
    passed = 0
    total = len(modules)
    for mod_name in modules:
        try:
            importlib.import_module(mod_name)
            print(f"  [PASS] {mod_name}")
            passed += 1
        except Exception as e:
            print(f"  [FAIL] {mod_name}: {e}")

    # FIX-1 (K1): WAL-replay self-test. A broken write-ahead log (non-monotonic
    # seq) must not read as healthy — the eBay deploy showed 20/20 over a WAL with
    # a duplicate seq and a gap. Checks the conventional WAL locations under the
    # project; self-skips when no WAL exists (so a fresh/CLI-only project is clean).
    from pathlib import Path as _Path

    from rag_kernel.persistence import WAL

    base = _Path(project_path)
    wal_ok = True
    for cand in (base / "WAL.jsonl", base / "RAG" / "WAL.jsonl"):
        if cand.exists():
            anomalies = WAL(cand).verify_integrity()
            if anomalies:
                wal_ok = False
                print(f"  [FAIL] WAL {cand.name}: " + "; ".join(anomalies))
            else:
                print(f"  [PASS] WAL {cand.name}: strictly monotonic")

    print(f"\nResult: {passed}/{total} modules OK.")
    return 0 if (passed == total and wal_ok) else 1


def cmd_serve(args: argparse.Namespace) -> int:
    project = args.project.resolve()
    if not project.exists():
        print(f"Error: Project directory does not exist: {project}", file=sys.stderr)
        return 1

    server = create_server(project, host=args.host, port=args.port, session_id=args.session_id)
    result = server.app.boot()
    if result["status"] != "OK":
        print(f"Boot failed: {result}", file=sys.stderr)
        if result["status"] == "RECOVERY":
            print("Kernel entered RECOVERY.", file=sys.stderr)

    addr = f"{args.host}:{server.server_address[1]}"
    print(f"RAG Runtime Kernel serving on http://{addr}")
    print(f"Project: {project}")
    print(f"Session: {server.app.session_id}")
    print(f"State: {result['state']}")
    print("Press Ctrl+C to stop.")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
        server.app.close()
        server.server_close()
        print("Done.")
    return 0


def cmd_mcp(args: argparse.Namespace) -> int:
    project = args.project.resolve()
    if not project.exists():
        print(f"Error: Project directory does not exist: {project}", file=sys.stderr)
        return 1
    # ORPHAN-SESSION REGRESSION (S197). This used to be:
    #     app = KernelApp(project, session_id=args.session_id)
    #     app.boot()                      # <- eager, and args.session_id is None
    #     ... finally: app.close()        # <- clean close for a phantom session
    # Registered as an auto-start MCP server, that made EVERY client launch mint
    # a timestamp-shaped session id, open a session log, take the lock and write
    # WAL entries from a fresh seq-1 allocator. Six orphan sessions and a
    # non-monotonic WAL before the next boot's audit caught it.
    #
    # Constructing KernelApp is safe (SessionLogger opens no file until boot);
    # booting is not. So boot is now lazy — only the rag_boot tool does it, and
    # only when the caller named the session — and close is symmetric with it.
    # rag_wait, the reason this server exists for an agent, is stateless and
    # needs no boot at all.
    app = KernelApp(project, session_id=args.session_id)
    server = MCPServer(app, session_id_explicit=args.session_id is not None)
    try:
        server.run()
    except KeyboardInterrupt:
        pass
    finally:
        if server.booted:
            app.close()
    return 0


def cmd_graph(args: argparse.Namespace) -> int:
    """Execute a Graph Orchestrator DAG through the kernel runtime.

    Reads a JSON spec ({"nodes": [...], "schedule": "...", ...}), boots a
    KernelApp on the project, and routes the DAG through KernelApp.run_graph
    (the v4.0 runtime-wiring entry). Prints the execution report as JSON.
    """
    if args.graph_action != "run":
        print("Usage: rag_kernel graph run <spec.json> [--project DIR]", file=sys.stderr)
        return 1

    spec_path = args.spec.resolve()
    if not spec_path.exists():
        print(f"Error: spec file does not exist: {spec_path}", file=sys.stderr)
        return 1
    try:
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        print(f"Error: cannot read spec: {e}", file=sys.stderr)
        return 1

    nodes = spec.get("nodes", [])
    schedule = args.schedule or spec.get("schedule", "sequential")
    stop_on_failure = args.stop_on_failure or bool(spec.get("stop_on_failure", False))
    rollback_on_failure = args.rollback_on_failure or bool(spec.get("rollback_on_failure", False))

    project = args.project.resolve()
    if not project.exists():
        print(f"Error: project directory does not exist: {project}", file=sys.stderr)
        return 1

    app = KernelApp(project, session_id=args.session_id)
    boot = app.boot()
    if boot.get("status") not in ("OK", "READY"):
        print(f"Warning: boot returned {boot.get('status')}: {boot}", file=sys.stderr)
    try:
        result = app.run_graph(
            nodes,
            schedule=schedule,
            stop_on_failure=stop_on_failure,
            rollback_on_failure=rollback_on_failure,
        )
        print(json.dumps(result, indent=2, ensure_ascii=False))
    finally:
        app.close()
    return 1 if isinstance(result, dict) and "error" in result else 0


#: Verbs whose write is meaningless once the item they name is terminal
#: (SEMANTIC-PRECONDITION-GATE, S190, P1-D).
_PRECONDITION_VERBS = frozenset({"priority", "reopen", "resolve", "defer"})


def _resolve_artifact(raw: str, rag_path: Path) -> "Path | None":
    """Locate a claimed evidence artifact, or None if it does not exist.

    Tried as given (absolute or CWD-relative), then relative to the RAG dir, then
    relative to the project root — the three places a path is honestly written
    from. Existence is the whole test: a path that resolves to nothing is not
    evidence, it is a sentence.
    """
    cand = Path(raw).expanduser()
    tries = [cand]
    if not cand.is_absolute():
        tries += [rag_path.parent / cand, rag_path.parent.parent / cand]
    for t in tries:
        try:
            if t.exists():
                return t.resolve()
        except OSError:
            continue
    return None


def _refuse_without_evidence(args: argparse.Namespace, rag_path: Path) -> "list[str] | None":
    """RESOLVE-REQUIRES-EVIDENCE (S190, P1-C). Returns resolved artifact paths.

    The S189 grand audit found 131 of 175 RESOLVED items citing no artifact at
    all, and 11 more citing only dead paths — 81% of the project's completion
    record unfalsifiable. A DONE claim is now a claim ABOUT A FILE, and the file
    must exist at the moment of the claim. Prints and returns None on refusal.
    """
    raw = list(getattr(args, "artifact", None) or [])
    if not raw:
        print(
            f"ERROR: RESOLVE-REQUIRES-EVIDENCE — refusing to mark {args.item_id} "
            "RESOLVED with no artifact.\n"
            "  resolve <id> --session <sid> --artifact <path> [--artifact <path>...]\n"
            "  The path must EXIST when the claim is made (absolute, or relative "
            "to the RAG dir or the project root). 131 of 175 RESOLVED items carry "
            "no artifact; this is where that stops.",
            file=sys.stderr,
        )
        return None
    resolved, missing = [], []
    for r in raw:
        got = _resolve_artifact(r, rag_path)
        (resolved.append(str(got)) if got else missing.append(r))
    if missing:
        print(
            "ERROR: RESOLVE-REQUIRES-EVIDENCE — artifact(s) do not exist: "
            + ", ".join(missing)
            + "\n  A dead path is the same evidence as no path (11 items already "
              "cite only dead paths). Nothing was written.",
            file=sys.stderr,
        )
        return None
    return resolved


def _refuse_terminal_write(verb: str, item, cite: "str | None", store) -> "int | None":
    """SEMANTIC-PRECONDITION-GATE (S190, P1-D). 1 = refused, None = proceed.

    The lifecycle guard already refuses illegal STATUS moves, so a terminal item
    cannot be re-resolved. It says nothing about the writes that leave status
    alone: S189 set a priority_group on MARKETING-LANDING, an item that was
    already terminal, and every gate in the kernel agreed. Priority on a closed
    item is not a small mistake — it is a plan being made against a world that no
    longer exists.

    The escape is ``--cite <id>``, and it must resolve to a LIVE tracked item:
    the successor the write is really about. A citation that names nothing, or
    names another corpse, is not an argument.
    """
    if verb not in _PRECONDITION_VERBS or not item.is_terminal:
        return None
    where = f"{item.id} is {item.status.value} (terminal)"
    if not cite:
        print(
            f"ERROR: SEMANTIC-PRECONDITION-GATE — refusing `{verb}` on a terminal "
            f"item: {where}.\n"
            "  A terminal item takes no plans. If this write belongs to a live "
            "successor, name it:\n"
            f"    {verb} {item.id} ... --cite <live-item-id>\n"
            "  (S189 wrote a priority onto a terminal item and nothing objected.)",
            file=sys.stderr,
        )
        return 1
    try:
        cited = store.get(cite)
    except Exception:
        cited = None
    if cited is None or cite not in store:
        print(
            f"ERROR: SEMANTIC-PRECONDITION-GATE — --cite {cite!r} resolves to no "
            f"tracked item; {where}. Nothing was written.",
            file=sys.stderr,
        )
        return 1
    if cited.is_terminal:
        print(
            f"ERROR: SEMANTIC-PRECONDITION-GATE — --cite {cite!r} is itself "
            f"{cited.status.value} (terminal); {where}. Cite a LIVE item or open "
            "one. Nothing was written.",
            file=sys.stderr,
        )
        return 1
    print(f"  SEMANTIC-PRECONDITION: {where}; proceeding on citation of live item {cite}.")
    return None


def cmd_item_transition(args: argparse.Namespace) -> int:
    """Apply one guarded lifecycle transition to a tracked item (DRIFT-ELIM inc 3).

    The verb (resolve/defer/reopen/start/discard/supersede) selects the target
    ItemStatus; drift_control's lifecycle guard decides legality and drift_store
    persists atomically (tmp -> verify -> .bak -> rename). An illegal move, an
    unknown id, or a bad RAG file fails LOUD and writes nothing (exit 1) — there
    is deliberately no "just set the field" path.
    """
    from rag_kernel.drift_control import (
        ItemStateError,
        ItemValidationError,
        legal_status_transition,
    )
    from rag_kernel.drift_store import (
        DriftStoreError,
        TrackedItemStore,
        load_hot,
        transition_in_file,
    )

    target = _ITEM_VERB_STATUS[args.command]
    rag_path = args.rag.resolve()
    if not rag_path.exists():
        print(f"Error: RAG file not found: {rag_path}", file=sys.stderr)
        return 1

    superseded_by = getattr(args, "by", None)

    # Read current state first: gives a clear before->after message and lets
    # --dry-run report legality without touching the file.
    try:
        store = TrackedItemStore.from_hot(load_hot(rag_path))
        current = store.get(args.item_id)
    except DriftStoreError as e:  # bad JSON / not a list / unknown id
        print(f"Error: {e}", file=sys.stderr)
        return 1

    # SEMANTIC-PRECONDITION-GATE (P1-D) and RESOLVE-REQUIRES-EVIDENCE (P1-C).
    # Both run BEFORE --dry-run returns, so a dry run tells the truth about
    # whether the real thing would be accepted.
    if _refuse_terminal_write(args.command, current, getattr(args, "cite", None), store):
        return 1
    evidence: "list[str]" = []
    # The evidence gate applies to a transition that is otherwise LEGAL. An
    # illegal move must report its illegality — telling an author to attach an
    # artifact to a move the lifecycle forbids is a false repair.
    if args.command == "resolve" and legal_status_transition(current.status, target):
        got = _refuse_without_evidence(args, rag_path)
        if got is None:
            return 1
        evidence = got

    if args.dry_run:
        if not legal_status_transition(current.status, target):
            print(
                f"[DRY RUN] ILLEGAL: {args.item_id} {current.status.value} -> {target}",
                file=sys.stderr,
            )
            return 1
        print(f"[DRY RUN] {args.item_id}: {current.status.value} -> {target} (no write)")
        return 0

    # The evidence travels INTO the ledger with the transition, as a FIELD:
    # a claim whose artifact lives only in a chat window is the 131-item problem
    # restated, and one buried in prose is a problem for the next auditor.
    # ``reason`` stays verbatim — it is the author's sentence, not a carrier bag.
    cite = getattr(args, "cite", None)
    recorded = list(evidence)
    if cite:
        recorded.append(f"cite:{cite}")

    try:
        transition_in_file(
            rag_path,
            args.item_id,
            target,
            session=args.session,
            reason=args.reason,
            superseded_by=superseded_by,
            artifacts=recorded,
        )
    except (ItemStateError, ItemValidationError, DriftStoreError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    print(f"{args.item_id}: {current.status.value} -> {target}  [session {args.session}]")
    return 0


def cmd_items(args: argparse.Namespace) -> int:
    """Render the canonical tracked_items array (read-only, no mutation).

    A status report or any doc mention of item status is a *render* of this
    array (DRIFT-ELIM); this command is the direct renderer.
    """
    from rag_kernel.drift_store import DriftStoreError, TrackedItemStore, load_hot

    rag_path = args.rag.resolve()
    if not rag_path.exists():
        print(f"Error: RAG file not found: {rag_path}", file=sys.stderr)
        return 1
    try:
        store = TrackedItemStore.from_hot(load_hot(rag_path))
    except DriftStoreError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    items = list(store)
    if args.status:
        want = args.status.upper()
        items = [it for it in items if it.status.value == want]
    if args.kind:
        want_k = args.kind.upper()
        items = [it for it in items if it.kind.value == want_k]

    if getattr(args, "json_output", False):
        print(json.dumps([it.to_dict() for it in items], indent=2, ensure_ascii=False))
        return 0

    if not items:
        print("(no tracked items match)")
        return 0
    width = max(len(it.id) for it in items)
    print(f"{len(items)} tracked item(s):")
    for it in items:
        sup = f"  -> {it.superseded_by}" if it.superseded_by else ""
        print(f"  {it.id:<{width}}  {it.status.value:<12} {it.kind.value:<10} {it.title}{sup}")
    return 0


def cmd_intent_audit(args: argparse.Namespace) -> int:
    """KA-INTENT-FIDELITY inc2 — session-START plan-vs-settled audit (fail-loud).

    The opening counterpart to inc1's closing seal gate. inc1 persisted the prior
    session's directive verbatim as a structured ``next_session_directive``
    (decision-of-record); this verb verifies the NEW session's stated PLAN honors
    it, closing the other half of E-055 / the S146 drift (anchoring on a lossy
    handoff line and reciting a stale blueprint instead of the settled decision).

    It does two things:

    1. LOADS THE SOURCE DECISIONS — resolves the directive's ``decision_ids`` to
       their live ``tracked_items`` records (id, status, title) and prints them, so
       the session builds on the source of record, NOT the compressed handoff line.
    2. AUDITS the plan against the directive via
       :func:`rag_kernel.schemas.audit_plan_against_directive` — ID-binding (cited
       decisions resolve and match the directive's pinned set) plus normalized-exact
       restatement. Deterministic, stdlib-only, zero-token; exit 1 on any finding.
    """
    from rag_kernel.drift_store import DriftStoreError, TrackedItemStore, load_hot
    from rag_kernel.schemas import audit_plan_against_directive

    rag_path = args.rag.resolve()
    if not rag_path.exists():
        print(f"Error: RAG file not found: {rag_path}", file=sys.stderr)
        return 1
    try:
        hot = load_hot(rag_path)
        store = TrackedItemStore.from_hot(hot)
    except DriftStoreError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    nsd = hot.get("next_session_directive")
    by_id = {it.id: it for it in store}

    # 1. Carry-forward loads the SOURCE decision — not the compressed line.
    print("Settled directive (decision-of-record):")
    if isinstance(nsd, dict):
        print(f"  {nsd.get('session')} -> for {nsd.get('for_session')}")
        print(f"  directive: {nsd.get('directive')}")
        dids = nsd.get("decision_ids") or []
        if dids:
            print("  source decisions:")
            for did in dids:
                it = by_id.get(did)
                if it is None:
                    print(f"    - {did}: (UNRESOLVED — no tracked_item)")
                else:
                    print(f"    - {it.id} [{it.status.value}] {it.title}")
    else:
        print("  (none persisted)")

    cited: list[str] = []
    if args.plan_decisions:
        cited = [s.strip() for s in args.plan_decisions.split(",") if s.strip()]

    ok, findings = audit_plan_against_directive(
        args.plan, cited, nsd, list(by_id.keys())
    )
    print()
    if ok:
        print(
            "intent-audit: OK — plan is faithful to the settled directive "
            "(ID-binding + normalized-exact restatement)."
        )
        return 0
    print(
        "intent-audit: FAIL — plan does not honor the settled directive:",
        file=sys.stderr,
    )
    for f in findings:
        print(f"  - {f}", file=sys.stderr)
    return 1


def cmd_render(args: argparse.Namespace) -> int:
    """Render legacy surfaces from the canonical tracked_items array (DRIFT-ELIM inc 4).

    Default is a dry-run that PRINTS the requested render. ``--apply`` regenerates
    the legacy ``open_tasks`` + ``deferred_items`` arrays in the RAG file itself,
    atomically (tmp -> verify -> .bak -> rename), making them projections of the
    canonical array. Hand-editing those arrays afterwards is the drift the inc-5
    session auditor will catch.
    """
    from rag_kernel.drift_store import DriftStoreError, TrackedItemStore, load_hot
    from rag_kernel import drift_render

    rag_path = args.rag.resolve()
    if not rag_path.exists():
        print(f"Error: RAG file not found: {rag_path}", file=sys.stderr)
        return 1
    try:
        hot = load_hot(rag_path)
        store = TrackedItemStore.from_hot(hot)
    except DriftStoreError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    if args.apply:
        drift_render.apply_renders_file(rag_path)
        rendered = drift_render.render_all(store)
        print(
            f"Applied renders to {rag_path}: "
            f"{len(rendered['open_tasks'])} open_tasks, "
            f"{len(rendered['deferred_items'])} deferred_items, "
            f"{len(rendered['priority_actions'])} priority_actions "
            "(tracked_items untouched; .bak refreshed)."
        )
        return 0

    what = args.what
    if getattr(args, "json_output", False):
        payload = drift_render.render_all(store)
        if what != "all":
            key = "backlog" if what in ("backlog", "error_log") else what
            payload = {key: payload[key]} if key in payload else payload
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    if what in ("open_tasks", "all"):
        print("# open_tasks (render)")
        for line in drift_render.render_open_tasks(store):
            print(f"  {line}")
        print()
    if what in ("deferred_items", "all"):
        print("# deferred_items (render)")
        for obj in drift_render.render_deferred_items(store):
            print(f"  {obj['id']}: {obj['title']} [{obj['status']}]")
        print()
    if what in ("priority_actions", "all"):
        print("# priority_actions (render — ACTIVE P1 only)")
        lines = drift_render.render_priority_actions(store)
        for line in lines:
            print(f"  {line}")
        if not lines:
            print("  (P1 clear — no active P1 item)")
        print()
    if what in ("backlog", "all"):
        print("# Rule 12 backlog (render)")
        print(drift_render.render_backlog_markdown(store))
        print()
    if what == "error_log":
        print(drift_render.render_error_log_backlog(store))
    return 0


def _drift_gate_ok(rag_path: Path) -> "bool | None":
    """Best-effort live drift-gate check for the report verb.

    Two evidence paths, in order of strength:

    1. SOURCE PATH (dev worktree): recompute the SHA-256 of the formal ``.tla``
       source and compare it to ``SOURCE_SHA256`` baked into ``generated_guards``.
       True iff they match, False iff they differ.
    2. BAKED-PROVENANCE PATH (deployed package): a deploy ships NO ``formal/`` dir
       by design (governance_runtime / Rule 19 — only the generated artifact + its
       provenance is deployed), so the ``.tla`` cannot be recomputed. Fall back to
       ``generated_guards.verify_self()``, which re-hashes the module's own guard
       tables against the baked ``GUARDS_SELF_SHA256``. True iff the guards are
       provably intact, False iff hand-edited post-generation.

    Returns None only if neither path yields evidence (no baseline AND no
    self-verify machinery) — a genuinely-unknown gate that honestly reads AMBER.
    """
    import hashlib

    try:
        from rag_kernel import generated_guards
    except Exception:
        return None
    baseline = getattr(generated_guards, "SOURCE_SHA256", None)
    if baseline:
        project_root = rag_path.parent.parent
        for cand in (
            project_root / "formal" / "RAGKernel.tla",
            rag_path.parent / "formal" / "RAGKernel.tla",
            Path(__file__).resolve().parent.parent / "formal" / "RAGKernel.tla",
        ):
            try:
                if cand.exists():
                    sha = hashlib.sha256(cand.read_bytes()).hexdigest()
                    return sha == baseline
            except OSError:
                continue
    # No reachable .tla source: self-verify from baked provenance (deployed case).
    verify_self = getattr(generated_guards, "verify_self", None)
    if callable(verify_self):
        try:
            return bool(verify_self())
        except Exception:
            return None
    return None


# ---------------------------------------------------------------------------
# REPORT-RENDER-ATTEST (E-062, recurrence of E-060) — report provenance guard.
# ---------------------------------------------------------------------------
#
# Rule 12 ("the report is a DETERMINISTIC RENDER, never re-prosed") was behavioral
# only, so the in-chat / on-demand report kept getting hand-authored (E-060 S136,
# E-062 S149). This mechanizes it: `report` appends a `report-attest: sha256(body)`
# token, and `report --verify` recomputes it and fails loud on mismatch/absence.
# A hand-typed or summarized report cannot carry a matching token, so re-prosing
# becomes machine-detectable. Zero-token, stdlib-only, deterministic.

_REPORT_ATTEST_SEP = "\n\n---\nreport-attest: "


def _normalize_report_body(body: str) -> str:
    """Deterministic normalization for attestation: strip trailing whitespace per
    line and drop trailing blank lines. Tolerates paste-time trailing-whitespace /
    newline drift WITHOUT tolerating any content change."""
    lines = [ln.rstrip() for ln in body.splitlines()]
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines)


def _report_attest_token(body: str) -> str:
    import hashlib
    digest = hashlib.sha256(_normalize_report_body(body).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def _append_report_attest(body: str) -> str:
    token = _report_attest_token(body)
    return (
        f"{body}{_REPORT_ATTEST_SEP}{token}\n"
        "(deterministic render — `rag_kernel report`; a re-prosed copy fails "
        "`rag_kernel report --verify <file>`)"
    )


def _verify_report_attest(text: str) -> "tuple[bool, str]":
    """Return (ok, detail). ok iff ``text`` carries a report-attest token that
    matches a recompute over its own body. A missing token => INVALID: a re-prosed
    report has no valid token."""
    if _REPORT_ATTEST_SEP not in text:
        return False, (
            "no report-attest token — not a kernel render "
            "(re-prosed / hand-authored)"
        )
    body, _, rest = text.rpartition(_REPORT_ATTEST_SEP)
    claimed = rest.splitlines()[0].strip() if rest.strip() else ""
    recomputed = _report_attest_token(body)
    if claimed == recomputed:
        return True, f"AUTHENTIC — verbatim kernel render ({recomputed})"
    return False, (
        "body does not match its report-attest token "
        f"(claimed {claimed or 'MISSING'}, recomputed {recomputed}) — re-prosed or altered"
    )


def cmd_report(args: argparse.Namespace) -> int:
    """Render the 7-section canonical status report deterministically (REPORT-VERB).

    Rule 12 requires the transfer/close status report to be a DETERMINISTIC RENDER
    of the RAG canonical fields, never hand-authored. This gathers the facts under
    the S136 sourcing discipline — STRUCTURED (meta / tracked_items / ledger),
    LIVE-COMPUTED (health, drift gate, git HEAD, .bak parity, bytes), and EXPLICIT
    external args (context %, tests, released?, claims?) — then calls the pure
    ``drift_render.render_status_report`` projector. Nothing is scraped from
    ``current_status`` prose; an unknown fact renders ``n/a`` and can only pull the
    verdict toward AMBER, never to a false GREEN (Rule 14).
    """
    from rag_kernel.drift_store import DriftStoreError

    # REPORT-RENDER-ATTEST (E-062) — verify mode: no RAG or --session needed; check
    # a rendered/pasted report carries a matching report-attest token. Fails loud
    # on a re-prosed / hand-authored / altered copy.
    verify_path = getattr(args, "verify", None)
    if verify_path is not None:
        try:
            text = Path(verify_path).read_text(encoding="utf-8")
        except OSError as e:
            print(f"Error: cannot read {verify_path}: {e}", file=sys.stderr)
            return 1
        ok, detail = _verify_report_attest(text)
        print(("report --verify: OK — " if ok else "report --verify: FAIL — ") + detail)
        return 0 if ok else 1

    if not getattr(args, "session", None):
        print("Error: --session is required (unless --verify).", file=sys.stderr)
        return 1

    rag_path = args.rag.resolve()
    if not rag_path.exists():
        print(f"Error: RAG file not found: {rag_path}", file=sys.stderr)
        return 1
    try:
        report = _build_report_text(rag_path, args)
    except DriftStoreError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    print(report)
    return 0


def _build_report_text(rag_path: Path, ns: argparse.Namespace) -> str:
    """Gather the report facts and render the 7-section canonical status report.

    Shared by the ``report`` verb and the ``session-end`` close (S139 WIRE-CLOSE),
    so the close renders the SAME deterministic artifact rather than trusting a
    hand-authored one. Facts: STRUCTURED (meta / tracked_items / ledger),
    LIVE-COMPUTED (health, drift gate, git HEAD, .bak parity, bytes), and EXPLICIT
    external scalars read off ``ns`` (context %, tests, released?, claims?). An
    unknown fact renders ``n/a`` and can only pull the verdict toward AMBER, never
    a false GREEN (Rule 14). Reads only; never mutates the RAG.
    """
    import importlib

    import rag_kernel
    from rag_kernel.drift_store import TrackedItemStore, load_hot
    from rag_kernel import drift_render, generated_guards

    hot = load_hot(rag_path)
    store = TrackedItemStore.from_hot(hot)

    meta = hot.get("meta", {}) if isinstance(hot, dict) else {}
    ledger = hot.get("inference_ledger", []) if isinstance(hot, dict) else []
    version = getattr(rag_kernel, "__version__", None)

    # -- live-computed facts (self-skipping under --no-live) --
    health = None
    health_ok = None
    drift_ok = None
    git_head = getattr(ns, "git_head", None)
    if not getattr(ns, "no_live", False):
        modules = list(rag_kernel._KERNEL_MODULES)
        passed = 0
        for mod_name in modules:
            try:
                importlib.import_module(mod_name)
                passed += 1
            except Exception:
                pass
        health = f"{passed}/{len(modules)}"
        health_ok = passed == len(modules)
        drift_ok = _drift_gate_ok(rag_path)
        if git_head is None:
            git_head = _resolve_git_head(rag_path)

    drift_sha = getattr(generated_guards, "SOURCE_SHA256", None)
    if drift_sha:
        drift_sha = drift_sha[:12]  # short form for the glance table

    # -- .bak parity + bytes (structured, from disk) --
    bak_path = rag_path.with_name(rag_path.name + ".bak")
    bak_parity = None
    try:
        if bak_path.exists():
            bak_parity = bak_path.read_bytes() == rag_path.read_bytes()
    except OSError:
        bak_parity = None
    try:
        rag_bytes = rag_path.stat().st_size
    except OSError:
        rag_bytes = None

    # -- the test gate: MEASURED first, typed flag only as an explicit override --
    #
    # REPORT-TESTS-GATE-UNMEASURED (S186, root-caused S188): this cell used to be
    # whatever the agent typed into --tests, so S184/S185 sealed `n/a` and S186/S187
    # sealed numbers nothing had checked. Now the stamp written by `tests --run` is the
    # authority; it carries the runtime + git HEAD it was measured against, so it
    # decays to STALE by itself when the code moves. --tests still wins when given,
    # because an operator override must remain possible — but it is no longer the
    # DEFAULT source, and an absent stamp renders "UNMEASURED", never a stale pass.
    from rag_kernel import test_gate as _test_gate

    tests = getattr(ns, "tests", None)
    tests_ok = None if tests is None else (not getattr(ns, "tests_failing", False))
    if tests is None:
        _stamp = _test_gate.read_stamp(hot)
        tests_ok, tests, _ = _test_gate.verdict(
            _stamp, live_head=git_head, live_runtime=version
        )

    body = drift_render.render_status_report(
        store,
        session=ns.session,
        meta=meta,
        ledger=ledger,
        version=version,
        milestone=getattr(ns, "milestone", None),
        tests=tests,
        tests_ok=tests_ok,
        health=health,
        health_ok=health_ok,
        drift_sha=drift_sha,
        drift_ok=drift_ok,
        released=getattr(ns, "released", None),
        release_ref=getattr(ns, "release_ref", None),
        claims_ok=getattr(ns, "claims_ok", None),
        context_pct=getattr(ns, "context_pct", None),
        git_head=git_head,
        rag_bytes=rag_bytes,
        bak_parity=bak_parity,
        handoff=getattr(ns, "handoff", None),
    )
    return _append_report_attest(body)


def cmd_note(args: argparse.Namespace) -> int:
    """Refresh a tracked item's note through the guarded API (DRIFT-ELIM inc 5, INS-038).

    A note is metadata, not the canonical status authority, so this never changes
    ``status`` and appends no history event. Routes through ``drift_store.set_note_in_file``
    (atomic, .bak-refreshed); hand-editing the note in tracked_items is the drift
    the auditor catches. Fails loud (writes nothing) on an unknown id.
    """
    from rag_kernel.drift_store import (
        DriftStoreError,
        TrackedItemStore,
        load_hot,
        set_note_in_file,
    )

    rag_path = args.rag.resolve()
    if not rag_path.exists():
        print(f"Error: RAG file not found: {rag_path}", file=sys.stderr)
        return 1
    try:
        store = TrackedItemStore.from_hot(load_hot(rag_path))
        if args.item_id not in store:
            print(f"Error: no tracked item with id {args.item_id!r}", file=sys.stderr)
            return 1
        if args.dry_run:
            current = store.get(args.item_id)
            print(
                f"[dry-run] would set note on {args.item_id} "
                f"(status {current.status.value}, unchanged): {args.note!r}"
            )
            return 0
        set_note_in_file(rag_path, args.item_id, args.note, session=args.session)
    except DriftStoreError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    print(f"Note updated on {args.item_id} (status untouched; .bak refreshed).")
    return 0


def cmd_cite(args: argparse.Namespace) -> int:
    """Attach evidence to a tracked item without moving its status.

    EVIDENCE-AMENDMENT (S191). ``resolve --artifact`` can only cite evidence AT
    the moment of resolution, and RESOLVED is terminal, so the 131 already-
    resolved items that cite nothing had no reachable remedy — the audit failed
    a check the ledger forbade fixing. This verb closes that gap and nothing
    else: it never changes status, so a closed item still never resurfaces.

    Every cited path MUST exist, exactly as ``resolve --artifact`` requires. An
    evidence verb that accepted unresolvable paths would launder the very
    problem it exists to fix.
    """
    from rag_kernel.drift_store import (
        DriftStoreError,
        TrackedItemStore,
        cite_in_file,
        load_hot,
    )

    rag_path = args.rag.resolve()
    if not rag_path.exists():
        print(f"Error: RAG file not found: {rag_path}", file=sys.stderr)
        return 1
    rag_dir = rag_path.parent
    missing = [a for a in args.artifact if not (rag_dir / a).exists()]
    if missing:
        print(
            "Error: cited artifact(s) do not exist, nothing written: "
            + ", ".join(missing),
            file=sys.stderr,
        )
        return 1
    try:
        store = TrackedItemStore.from_hot(load_hot(rag_path))
        if args.item_id not in store:
            print(f"Error: no tracked item with id {args.item_id!r}", file=sys.stderr)
            return 1
        current = store.get(args.item_id)
        already = {a for ev in current.history for a in ev.artifacts}
        fresh = [a for a in args.artifact if a not in already]
        if not fresh:
            print(
                f"{args.item_id}: already cites {len(args.artifact)} of "
                f"{len(args.artifact)} given artifact(s) — nothing to add."
            )
            return 0
        if args.dry_run:
            print(
                f"[dry-run] would cite {fresh} on {args.item_id} "
                f"(status {current.status.value}, unchanged)"
            )
            return 0
        cite_in_file(
            rag_path, args.item_id, fresh,
            session=args.session, reason=args.reason,
        )
    except DriftStoreError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    print(
        f"{args.item_id}: cited {len(fresh)} artifact(s) "
        f"(status {current.status.value}, untouched; .bak refreshed)."
    )
    return 0


def cmd_priority(args: argparse.Namespace) -> int:
    """Set a tracked item's Rule 21 priority_group through the guarded API.

    The priority bucket is metadata, not the canonical status authority, so this
    never changes ``status`` and appends no history event. Routes through
    ``drift_store.set_priority_in_file`` (atomic, .bak-refreshed); hand-editing
    priority_group in tracked_items is the drift the auditor forbids. Fails loud
    (writes nothing) on an unknown id or a bucket outside P1..P5 / "".
    """
    from rag_kernel.drift_control import ALLOWED_PRIORITY_GROUPS
    from rag_kernel.drift_store import (
        DriftStoreError,
        TrackedItemStore,
        load_hot,
        set_priority_in_file,
    )

    rag_path = args.rag.resolve()
    if not rag_path.exists():
        print(f"Error: RAG file not found: {rag_path}", file=sys.stderr)
        return 1
    if args.priority_group not in ALLOWED_PRIORITY_GROUPS:
        print(
            f"Error: unknown priority_group {args.priority_group!r} "
            f"(allowed: {sorted(ALLOWED_PRIORITY_GROUPS)})",
            file=sys.stderr,
        )
        return 1
    try:
        store = TrackedItemStore.from_hot(load_hot(rag_path))
        if args.item_id not in store:
            print(f"Error: no tracked item with id {args.item_id!r}", file=sys.stderr)
            return 1
        # SEMANTIC-PRECONDITION-GATE (S190, P1-D) — before the dry-run branch, so
        # a dry run reports the decision the real write would make.
        if _refuse_terminal_write("priority", store.get(args.item_id),
                                  getattr(args, "cite", None), store):
            return 1
        if args.dry_run:
            current = store.get(args.item_id)
            shown = args.priority_group or "(cleared)"
            print(
                f"[dry-run] would set priority_group on {args.item_id} "
                f"(status {current.status.value}, unchanged): {shown}"
            )
            return 0
        set_priority_in_file(
            rag_path, args.item_id, args.priority_group, session=args.session
        )
    except DriftStoreError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    shown = args.priority_group or "(cleared)"
    print(f"priority_group set on {args.item_id}: {shown} (status untouched; .bak refreshed).")
    return 0


def cmd_dedup_sessions(args: argparse.Namespace) -> int:
    """Repair duplicate-bootstrap rows in sessions_recent (KA-2 increment B).

    The repair half of the KA-2 invariant: where ``audit`` FAILS LOUD on two
    sessions_recent rows sharing a checkpoint timestamp, this verb removes the
    phantom duplicate(s) through the guarded, atomic ``drift_store`` path (tmp ->
    verify -> .bak parity -> rename), keeping one row per timestamp. Detection and
    repair share one predicate (``sessions_recent_duplicate_pairs``), so this fixes
    exactly what the auditor flags. No-op (writes nothing) when the ledger is clean.
    """
    from rag_kernel.drift_store import (
        DriftStoreError,
        dedup_sessions_recent,
        dedup_sessions_recent_file,
        load_hot,
        sessions_recent_duplicate_pairs,
    )

    rag_path = args.rag.resolve()
    if not rag_path.exists():
        print(f"Error: RAG file not found: {rag_path}", file=sys.stderr)
        return 1
    try:
        hot = load_hot(rag_path)
        sr = hot.get("sessions_recent")
        pairs = sessions_recent_duplicate_pairs(sr if isinstance(sr, list) else [])
        if not pairs:
            print("sessions_recent: no duplicate-bootstrap rows; nothing to repair.")
            return 0
        if args.dry_run:
            import copy
            _, removed = dedup_sessions_recent(copy.deepcopy(hot), keep=args.keep)
            print(f"[dry-run] would remove {len(removed)} duplicate row(s) (keep={args.keep}):")
            for r in removed:
                print(f"    - {r.get('id', '?')} @ {r.get('d', '?')}")
            return 0
        _, removed = dedup_sessions_recent_file(rag_path, keep=args.keep)
    except DriftStoreError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    print(f"sessions_recent deduped: removed {len(removed)} row(s) (keep={args.keep}); .bak refreshed.")
    for r in removed:
        print(f"    - {r.get('id', '?')} @ {r.get('d', '?')}")
    return 0


def _resolve_git_head(rag_path: Path) -> "str | None":
    """Best-effort short git HEAD for the current_status freshness guard (E-043).

    Resolves the git worktree from the RAG's own pointers
    (``current_status.git_worktree_path`` joined to the project root, derived both
    from the RAG file location and from ``meta.root_project``) and runs
    ``git -C <dir> rev-parse --short HEAD``. Returns ``None`` on ANY failure — no
    git, not a repo, bad/foreign path — so the freshness guard simply skips the
    HEAD sub-check instead of breaking the audit. A deployed project that is not a
    git repo (or whose recorded path belongs to another OS) is audited cleanly.
    """
    import json
    import subprocess

    try:
        hot = json.loads(rag_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        hot = {}
    meta = hot.get("meta", {}) if isinstance(hot, dict) else {}
    cs = hot.get("current_status", {}) if isinstance(hot, dict) else {}
    root = meta.get("root_project") if isinstance(meta, dict) else None
    wt = cs.get("git_worktree_path") if isinstance(cs, dict) else None
    wt_norm = str(wt).replace("\\", "/").rstrip("/") if wt else None

    project_root = rag_path.parent.parent  # RAG/RAG_MASTER.json -> project root
    candidates: list[Path] = []
    if wt_norm:
        candidates.append(project_root / wt_norm)            # WSL/native via RAG location
        if root:
            candidates.append(Path(str(root).replace("\\", "/")) / wt_norm)  # recorded host path
    candidates.append(project_root)                          # RAG lives inside the repo
    candidates.append(rag_path.parent)

    for d in candidates:
        try:
            if not d.exists():
                continue
            r = subprocess.run(
                ["git", "-C", str(d), "rev-parse", "--short", "HEAD"],
                capture_output=True, text=True, timeout=5,
            )
        except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
            return None  # git absent -> no point trying further candidates
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    return None


def cmd_audit(args: argparse.Namespace) -> int:
    """Run the fail-loud session auditor over the RAG (DRIFT-ELIM inc 5 + E-043).

    Asserts the rendered legacy arrays match the canonical tracked_items array
    (E-040 regression), supersede refs resolve, no active item's note contradicts
    its status (INS-038), the current_status narrative's version/HEAD match the
    live authorities (E-043), and no Cowork-memory side stores exist in the project
    root (Rule 13). Exit 0 if clean, 1 if any ERROR (or any finding under
    ``--strict``).
    """
    from rag_kernel import drift_audit
    from rag_kernel.drift_store import DriftStoreError

    rag_path = args.rag.resolve()
    if not rag_path.exists():
        print(f"Error: RAG file not found: {rag_path}", file=sys.stderr)
        return 1
    git_head = getattr(args, "git_head", None) or _resolve_git_head(rag_path)
    try:
        report = drift_audit.audit_file(
            rag_path,
            scan_root=args.scan_root,
            error_log_path=getattr(args, "error_log", None),
            docs_root=getattr(args, "docs_root", None),
            git_head=git_head,
        )
    except DriftStoreError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    if getattr(args, "json_output", False):
        print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
    else:
        print(report.summary())

    clean = report.is_clean(strict=args.strict)
    return 0 if clean else 1


def _session_checkpoint_gate(rag_path: Path, session_id: str) -> tuple[bool, str]:
    """KA-4 close gate: is ``session_id`` safe to close (i.e. checkpointed)?

    The ``ran-but-never-checkpointed`` governance freeze (eBay S4) happened
    because an agent ended a session on ``configure``/``audit`` (or a scratch
    script) without ever running ``checkpoint``. A checkpoint stamps
    ``meta.written_by_session`` with the session id (and appends a
    ``sessions_recent`` row); the absence of that stamp is the freeze signature.

    Returns ``(ok, reason)``. ``ok`` is True iff the RAG shows a checkpoint by
    this exact session — the precise inverse of the freeze condition. The
    programmatic ``KernelApp.close()`` already force-checkpoints on close
    (ENH-006); this guards the standalone CLI ``session close`` path, which the
    CLI-driven eBay deploy used to freeze on.
    """
    import json as _json

    if not rag_path.exists():
        return False, f"RAG_MASTER.json not found at {rag_path} — cannot confirm a checkpoint"
    try:
        with open(rag_path, "r", encoding="utf-8") as f:
            rag = _json.load(f)
    except (OSError, ValueError) as exc:
        return False, f"RAG_MASTER.json unreadable ({exc}) — cannot confirm a checkpoint"

    meta = rag.get("meta") or {}
    written_by = meta.get("written_by_session")
    if written_by == session_id:
        return True, "checkpoint present (meta.written_by_session matches)"
    return False, (
        f"no checkpoint by this session "
        f"(meta.written_by_session={written_by!r}, expected {session_id!r})"
    )


def cmd_session(args: argparse.Namespace) -> int:
    """Start or close a session logger via CLI.

    Wraps SessionLogger.open() and .close() so LLM orchestrators can
    manage sessions via a single CLI command instead of inline Python.
    """
    from rag_kernel.session_logger import SessionLogger

    action = args.session_action
    if action is None:
        print("Usage: rag_kernel session {start|close} <session_id> [--rag-dir .]")
        return 1

    session_id = args.session_id
    rag_dir = args.rag_dir.resolve()

    logger = SessionLogger(session_id, log_dir=rag_dir)

    if action == "start":
        logger.open()
        print(f"Session {session_id} started.")
        print(f"Log file: {logger.log_path}")
        # Also verify RAG_MASTER.json exists in the directory
        rag_path = rag_dir / "RAG_MASTER.json"
        if rag_path.exists():
            import json
            with open(rag_path, "r", encoding="utf-8") as f:
                rag = json.load(f)
            state = rag.get("state_machine_status", "UNKNOWN")
            print(f"RAG state: {state}")
        else:
            print(f"WARNING: RAG_MASTER.json not found at {rag_dir}")
        return 0
    elif action == "close":
        # KA-4 — checkpoint-to-close enforcement. Refuse to close a *started*
        # session (one that produced a log) unless it was checkpointed first.
        # This is the code-level guard that the S89 prose-only guide fix could
        # not provide and that the eBay S4 ran-but-never-checkpointed freeze
        # proved necessary. A no-op close (no log file) stays a harmless no-op.
        if logger.log_path.exists():
            rag_path = rag_dir / "RAG_MASTER.json"
            gate_ok, reason = _session_checkpoint_gate(rag_path, session_id)
            force = getattr(args, "force", False)
            if not gate_ok and not force:
                print(
                    f"ERROR: refusing to close session {session_id} — {reason}",
                    file=sys.stderr,
                )
                print(
                    "  A session must be checkpointed before it can be closed "
                    "(prevents the ran-but-never-checkpointed governance freeze, KA-4).",
                    file=sys.stderr,
                )
                print(
                    f'  Run:  rag_kernel checkpoint --rag "{rag_path}" '
                    f'--session {session_id} --summary "..."',
                    file=sys.stderr,
                )
                print(
                    "  To close anyway (UNSAFE — leaves governance state stale), pass --force.",
                    file=sys.stderr,
                )
                return 1
            if not gate_ok and force:
                print(
                    f"WARNING: closing session {session_id} WITHOUT a checkpoint "
                    f"(--force) — {reason}",
                    file=sys.stderr,
                )
                print(
                    "  Governance state (written_by_session / last_checkpoint_seq) is left "
                    "stale. KA-4 override used; this should be rare and deliberate.",
                    file=sys.stderr,
                )
            # Attach to resume the sequence WITHOUT a spurious second session_start
            # (FIX-12 / U4), then write the session_end marker.
            logger.attach()
            logger.close()
            print(f"Session {session_id} closed.")
            print(f"Log file: {logger.log_path}")
        else:
            print(f"WARNING: No log file found for session {session_id} at {logger.log_path}")
            print("Nothing to close.")
        return 0
    else:
        print(f"Unknown session action: {action}")
        return 1


def _reconcile_checkpoint_render_parity(rag: dict, *, apply: bool) -> list[str]:
    """KA-CKPT-PARITY-GATE (E-049): reconcile the legacy ``open_tasks`` /
    ``deferred_items`` renders against the canonical ``tracked_items`` at seal.

    ``tracked_items`` is the sole status authority (inc4); the legacy arrays are
    a pure render of it. Any tracked_item-mutating verb (note/resolve/defer/…)
    run between the last ``render --apply`` and a ``checkpoint`` would otherwise
    seal a STALE render, which post-seal ``audit --strict`` flags as an
    E-040-family ``render_parity`` ERROR — this is exactly E-049. Re-rendering
    here makes render-parity hold BY CONSTRUCTION at every seal, collapsing the
    fragile verb→render→checkpoint sequence to verb→checkpoint.

    Guards on the migrated architecture: acts ONLY when ``tracked_items`` is
    present, so an un-migrated/legacy RAG (whose legacy arrays are authored, not
    rendered) is never silently wiped. Returns the list of arrays that were (or,
    when ``apply=False``, would be) corrected, so the caller can surface a
    visible note — the seal is never a silent mutation. With ``apply=True`` the
    ``rag`` dict is updated in place.
    """
    if "tracked_items" not in rag:
        return []  # un-migrated/legacy RAG: nothing canonical to render from
    from rag_kernel import drift_render
    from rag_kernel.drift_store import TrackedItemStore

    store = TrackedItemStore.from_hot(rag)
    corrected: list[str] = []
    if "open_tasks" in rag:
        expected = drift_render.render_open_tasks(store)
        if rag["open_tasks"] != expected:
            corrected.append(f"open_tasks ({len(expected)})")
            if apply:
                rag["open_tasks"] = expected
    if "deferred_items" in rag:
        expected_d = drift_render.render_deferred_items(store)
        if rag["deferred_items"] != expected_d:
            corrected.append(f"deferred_items ({len(expected_d)})")
            if apply:
                rag["deferred_items"] = expected_d
    if "priority_actions" in rag:
        # S187 PRIORITY-ACTIONS-STALE-SNAPSHOT: priority_actions is a render too,
        # so the seal must reconcile it or the next boot briefs a stale agenda.
        expected_p = drift_render.render_priority_actions(store)
        if rag["priority_actions"] != expected_p:
            corrected.append(f"priority_actions ({len(expected_p)})")
            if apply:
                rag["priority_actions"] = expected_p
    return corrected


def cmd_checkpoint(args: argparse.Namespace) -> int:
    """Merge a session summary into RAG_MASTER.json atomically.

    Updates:
    - sessions_recent: appends {id, d, s} entry
    - meta.last_updated_utc: current timestamp
    - meta.written_by_session: session ID
    - meta.last_checkpoint_seq: incremented
    - state_machine_status: if --status provided
    - open_tasks: if --tasks provided (replaces)
    """
    import json
    from datetime import datetime, timezone

    rag_path = args.rag.resolve()
    if not rag_path.exists():
        print(f"Error: RAG file not found: {rag_path}", file=sys.stderr)
        return 1

    with open(rag_path, "r", encoding="utf-8") as f:
        rag = json.load(f)

    session_id = args.session
    summary = args.summary
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    # KA-18 (E-044/E-045) — session-start ORDERING GUARD. A checkpoint must not
    # seal before the mechanized `session-start` has opened this session's log:
    # sealing first is the recurring slip that banked state with NO observability
    # record (tripping the KA-7 observability-coherence auditor) and left
    # current_status stale post-commit. When enforcement is on — the CLI default;
    # programmatic callers (the session-end ritual, unit tests) opt in — refuse
    # fail-loud unless the session log exists, pointing the operator at the
    # mechanized session-start. Bypass explicitly with --no-require-session-log.
    if getattr(args, "require_session_log", False):
        from rag_kernel.session_logger import SessionLogger
        _log_path = SessionLogger(session_id, log_dir=rag_path.parent).log_path
        if not _log_path.exists():
            msg = (
                f"no open session log for {session_id} at {_log_path.name} — run "
                "mechanized `session-start` FIRST (KA-18 ordering guard, "
                "E-044/E-045). Bypass with --no-require-session-log if intentional."
            )
            if getattr(args, "dry_run", False):
                print(f"  [DRY RUN] would refuse: {msg}")
            else:
                print(f"ERROR: {msg}", file=sys.stderr)
                return 1

    # Update sessions_recent
    sessions = rag.get("sessions_recent", [])
    sessions.append({
        "id": session_id,
        "d": now,
        "s": summary,
    })
    # Keep only last 5 sessions in HOT
    if len(sessions) > 5:
        sessions = sessions[-5:]
    rag["sessions_recent"] = sessions

    # Update meta
    rag["meta"]["last_updated_utc"] = now
    rag["meta"]["written_by_session"] = session_id
    checkpoint_seq = rag["meta"].get("last_checkpoint_seq", 0) + 1
    rag["meta"]["last_checkpoint_seq"] = checkpoint_seq

    # Optional: update state
    if args.status:
        # STATE-MACHINE-STATUS-INVALID (S187): --status was a free-text passthrough
        # straight into canonical state, which is how "COMPLETE" — a value no
        # transition produces — got written and then survived every audit. Detecting
        # it downstream is not enough; refuse it at the write site.
        from rag_kernel.spec_parser import VALID_STATE_MACHINE_STATUS
        if args.status not in VALID_STATE_MACHINE_STATUS:
            legal = ", ".join(sorted(s for s in VALID_STATE_MACHINE_STATUS if s))
            print(
                f"Error: {args.status!r} is not a legal state_machine_status — "
                f"the machine admits only {legal}. Refusing to write a non-state "
                "into canonical state.",
                file=sys.stderr,
            )
            return 1
        old_state = rag.get("state_machine_status", "UNKNOWN")
        rag["state_machine_status"] = args.status
        print(f"State: {old_state} -> {args.status}")

    # Optional: replace open_tasks
    if args.tasks:
        try:
            tasks_list = json.loads(args.tasks)
            if isinstance(tasks_list, list):
                rag["open_tasks"] = tasks_list
                print(f"Open tasks updated: {len(tasks_list)} items")
        except json.JSONDecodeError as e:
            print(f"WARNING: --tasks is not valid JSON: {e}", file=sys.stderr)

    # KA-16 — fold the ERROR_LOG append INTO the governed checkpoint call. The
    # operator/agent PROPOSES the entry text (--error-log-entry); the kernel
    # appends it idempotently (a hidden `<!-- close-log-id: ID -->` marker makes
    # a resumed/retried checkpoint a no-op, never a double-append). Doing it here
    # — one atomic governed call — retires the fragile multi-Edit ERROR_LOG hand
    # edit that stranded the eBay S4 close. The append happens BEFORE the RAG
    # atomic write so a failed append aborts the checkpoint with the seq still
    # un-incremented on disk (the RAG write is the atomic commit point).
    error_log_entry = getattr(args, "error_log_entry", None)
    error_log_id = getattr(args, "error_log_id", None)
    error_log_path = getattr(args, "error_log_path", None)
    el_path = (
        Path(error_log_path).resolve()
        if error_log_path
        else rag_path.parent / "ERROR_LOG.md"
    )

    # KA-INTENT-FIDELITY inc1 — persist a STATED handoff as a structured,
    # gate-checkable ``next_session_directive`` (decision-of-record), VERBATIM.
    # Root cause closed (E-055 / S146): a close STATED a next-session directive
    # but it lived only in the ephemeral close report (--handoff -> section 7), so
    # a later "directive banked" claim had no discrete field backing it. Folding
    # the write into this atomic checkpoint keeps HOT/.bak parity; an invalid
    # record aborts BEFORE any side effect (ERROR_LOG fold / RAG write), leaving
    # the seq un-incremented. The session-end gate (_drive_close) independently
    # re-verifies persistence + verbatim match before the seal.
    _handoff = getattr(args, "handoff", None)
    _directive_record: "dict | None" = None
    _directive_errors: list[str] = []
    if isinstance(_handoff, str) and _handoff.strip():
        from rag_kernel.schemas import validate_next_session_directive
        _directive_record = {
            "session": session_id,
            "for_session": _next_session_id(session_id),
            "directive": _handoff,
            "authored_utc": now,
        }
        ok_d, _directive_errors = validate_next_session_directive(_directive_record)
        if not ok_d:
            _directive_record = None

    if args.dry_run:
        print(f"\n[DRY RUN] Would update {rag_path}:")
        print(f"  Session: {session_id}")
        print(f"  Summary: {summary[:80]}...")
        print(f"  Checkpoint seq: {checkpoint_seq}")
        stale_preview = _reconcile_checkpoint_render_parity(rag, apply=False)
        if stale_preview:
            print(
                "  render-parity: would re-render stale "
                f"{', '.join(stale_preview)} from tracked_items before seal "
                "(KA-CKPT-PARITY-GATE / E-049)"
            )
        if error_log_entry:
            eid = error_log_id or f"{session_id}-checkpoint"
            already = _error_log_has_id(el_path, eid)
            print(
                f"  ERROR_LOG: would {'SKIP (id already present)' if already else 'append'} "
                f"entry id='{eid}' -> {el_path}"
            )
        if isinstance(_handoff, str) and _handoff.strip():
            if _directive_errors:
                print(
                    "  next_session_directive: would REFUSE — invalid record: "
                    + "; ".join(_directive_errors)
                )
            else:
                print(
                    "  next_session_directive: would persist verbatim for "
                    f"{_directive_record['for_session']} "
                    "(KA-INTENT-FIDELITY inc1)"
                )
        return 0

    # 0. KA-INTENT-FIDELITY inc1 — a stated handoff that failed validation aborts
    # the checkpoint fail-loud BEFORE any side effect (no ERROR_LOG append, no RAG
    # write); a valid one is folded into the atomic write below.
    if isinstance(_handoff, str) and _handoff.strip():
        if _directive_errors:
            print(
                "ERROR: next_session_directive invalid — aborting checkpoint "
                "before any write (seq un-incremented): "
                + "; ".join(_directive_errors),
                file=sys.stderr,
            )
            return 1
        rag["next_session_directive"] = _directive_record

    # 1. ERROR_LOG fold (idempotent) — must succeed before the RAG commit.
    if error_log_entry:
        eid = error_log_id or f"{session_id}-checkpoint"
        try:
            appended = _append_error_log(el_path, error_log_entry, eid)
        except OSError as exc:
            print(
                f"ERROR: ERROR_LOG append failed ({exc}) — aborting checkpoint "
                "before the RAG write (seq left un-incremented).",
                file=sys.stderr,
            )
            return 1
        print(
            f"  ERROR_LOG: {'appended' if appended else 'skipped (id already present)'} "
            f"entry id='{eid}'"
        )

    # 1b. KA-CKPT-PARITY-GATE (E-049) — re-render the legacy open_tasks /
    # deferred_items from canonical tracked_items so this seal is render-parity
    # clean BY CONSTRUCTION. Without it, a tracked_item-mutating verb
    # (note/resolve/defer/…) run before this checkpoint would seal a stale render
    # that post-seal `audit --strict` flags as a render_parity ERROR.
    #
    # Scoped to the MIGRATED architecture (tracked_items present). An un-migrated
    # RAG carries authored, non-rendered legacy arrays: it is out of scope for
    # E-049 and must be neither wiped by the reconcile nor tripped by the
    # defensive assertion below (whose parity check would otherwise read an
    # authored open_tasks against an empty render). Both are gated together so
    # they can never disagree.
    if "tracked_items" in rag:
        corrected = _reconcile_checkpoint_render_parity(rag, apply=True)
        if corrected:
            print(
                "  render-parity: re-rendered stale "
                f"{', '.join(corrected)} from tracked_items before seal "
                "(KA-CKPT-PARITY-GATE / E-049)"
            )
        # Defensive fail-loud: after reconcile the sealed dict MUST be
        # parity-clean. A non-empty result here means a render-logic bug, not
        # stale operator state — abort BEFORE the atomic write rather than seal
        # a divergent RAG.
        from rag_kernel.drift_audit import check_render_parity
        residual = check_render_parity(rag)
        if residual:
            print(
                "ERROR: checkpoint render-parity still stale after reconcile — "
                "aborting seal (no write): "
                + "; ".join(f.detail for f in residual),
                file=sys.stderr,
            )
            return 1

    # 2. Atomic write via persistence module.
    # mirror_bak=True refreshes RAG_MASTER.json.bak to a byte-identical copy of
    # HOT after the commit, enforcing the FIX-4 / K6 parity-mirror .bak contract
    # for this canonical session-close write — matching api.checkpoint do_full.
    # Without it the standalone CLI `checkpoint` left .bak one seq behind (E-045),
    # which audit.check_bak_parity correctly fails loud on unless a later
    # mirroring write (render --apply) happened to follow (FIX-8).
    try:
        from rag_kernel.persistence import atomic_write_json
        atomic_write_json(rag_path, rag, mirror_bak=True)
    except ImportError:
        # Fallback: direct write if persistence not available
        with open(rag_path, "w", encoding="utf-8") as f:
            json.dump(rag, f, indent=2, ensure_ascii=False)

    print(f"Checkpoint complete:")
    print(f"  Session: {session_id}")
    print(f"  Checkpoint seq: {checkpoint_seq}")
    print(f"  RAG updated: {rag_path}")

    return 0


def _session_seq(sid: "str | None") -> "int | None":
    """Trailing-integer sequence of a session id (``S171`` -> 171); None if absent."""
    import re
    if not sid:
        return None
    m = re.search(r"(\d+)$", str(sid))
    return int(m.group(1)) if m else None


def _last_sealed_session(rag_path: Path, rag_dir: Path) -> "str | None":
    """The most recent session PROVEN sealed: its ``session_close`` marker reached
    ``transfer_ready`` AND its canonical transfer-surface report exists on disk
    (Rule 23 — ``AUDIT_CANONICAL_REPORT_<sid>.md``). Returns the sealed session id,
    else None (the latest close is not a completed, surfaced seal).
    """
    marker = _read_close_marker(rag_path)
    if not isinstance(marker, dict) or not marker.get("transfer_ready", False):
        return None
    sealed = marker.get("session")
    if not sealed:
        return None
    if not _close_report_artifact_path(rag_dir, sealed).exists():
        return None
    return sealed


def _unsealed_prior_session(
    rag_path: Path, rag_dir: Path, new_sid: "str | None"
) -> "str | None":
    """CLOSE-SEAL-ENFORCE (KA-21): the highest PRIOR session that RAN (has a
    ``session_log_*.jsonl`` on disk) but was never sealed. Returns that session id,
    else None.

    Catches the S157 class — a session that never ran ``session-end``, leaving zero
    close events and no ``AUDIT_CANONICAL_REPORT`` — which the single ``session_close``
    marker (holding only the LAST completed close) structurally cannot reveal. The
    check is gated on the close protocol being in use (a ``session_close`` marker
    exists): a legacy/un-migrated RAG that never adopted the sealed close closes
    byte-for-byte as before (back-compat, mirroring KA-13's undeclared skip).

    A ran session ``S`` counts as unsealed iff ``session_log_S<seq>.jsonl`` exists
    with ``seq`` strictly greater than the last sealed session's seq, excluding
    ``new_sid`` itself.
    """
    import re
    if not isinstance(_read_close_marker(rag_path), dict):
        return None  # close protocol not in use — legacy RAG, do not retro-enforce
    sealed_seq = _session_seq(_last_sealed_session(rag_path, rag_dir))
    if sealed_seq is None:
        return None  # last close not a completed seal — step 3 handles aborts
    new_seq = _session_seq(new_sid)
    worst, worst_seq = None, sealed_seq
    for p in rag_dir.glob("session_log_*.jsonl"):
        m = re.search(r"session_log_(.+)\.jsonl$", p.name)
        if not m:
            continue
        cand_seq = _session_seq(m.group(1))
        if cand_seq is None:
            continue
        if new_seq is not None and cand_seq == new_seq:
            continue  # the session being started now
        if cand_seq > worst_seq:
            worst, worst_seq = m.group(1), cand_seq
    return worst


# ---------------------------------------------------------------------------
# S192 — THE INTERVAL GUARDS (E-123, E-124, E-125, E-126)
# ---------------------------------------------------------------------------
#
# The operator's diagnosis, which is the correct one: when state fails to reach
# the next session there are exactly two causes — either the agent disregarded a
# rule the RAG already carried, or the RAG could not carry the fact at all. Every
# probe below is one of those two, converted into a refusal.
#
#   DISREGARDED  meta.test_gate held "STALE" in canonical state the whole time.
#                grand_audit axis 2 printed it. Nothing at boot ever asked, so an
#                agent who did not feel like asking simply did not. -> _probe_test_gate
#                is now wired into the boot gate, where it exits non-zero. A rule
#                that returns an exit code cannot be disregarded.
#
#   UNCARRIED    three findings (E-111/E-114/E-115) lived as prose in ERROR_LOG.md
#                and as ids in source comments, with no tracked item behind them.
#                Prose does not transfer; only tracked items do. -> _probe_orphan_enums
#                makes an uncarried finding a boot-blocking defect, so the next
#                session cannot start on top of a fact the RAG never learned.
#
#   UNCARRIED    the RAG had NO representation whatsoever of "the kernel that is
#                running is not the kernel that is committed" — the E-109/E-123
#                defect class — because the deployed tree (RAG/rag_kernel) and the
#                git worktree are two separate copies. -> _probe_worktree_clean and
#                _probe_deploy_parity give that fact a home and a gate.
#
# All four are ASSERTED, never auto-repairable: re-measuring a suite, banking an
# id, committing a tree and copying a kernel are all real acts with real content.
# Auto-performing them would be exactly the self-concealing repair GATE-AUTO-RECONCILE
# was written to forbid.

def _declares_git_deployment(rag_path: Path) -> bool:
    """Does this RAG declare a root that is ACTUALLY a git-backed kernel tree?

    The probes below must fail CLOSED — an unresolvable probe has to refuse, not
    shrug — but "fail closed on everything" would make every fixture RAG in the
    suite unbootable, and a guard that has to be disabled to get work done is a
    guard that gets disabled. So the probes need a predicate for "is there
    anything here to be answerable for", and getting that predicate wrong in
    either direction is its own defect:

      too NARROW  the guard passes on the deployment it was written for. That is
                  the fail-open hole this whole block exists to close.
      too BROAD   the guard refuses on trees that were never making the claim.
                  Two versions of this predicate were wrong that way before this
                  one. Both keyed off ``meta.reconciliation_docs_root`` being
                  declared — but that field names the docs tree the CLOSE
                  reconciles against, which is not the same claim as "a kernel is
                  deployed here", and every KA-13 fixture sets it to a bare path
                  with no kernel anywhere near it. A guard that cries wolf on
                  legitimate state gets suppressed, and then it is not a guard.

    The question these probes actually need answered is narrower and physical: is
    there a kernel deployed at this RAG whose provenance I am supposed to be able
    to prove? A deployment has its kernel package sitting beside RAG_MASTER.json —
    ``RAG/rag_kernel/`` next to ``RAG/RAG_MASTER.json``. That is what executes.
    If it is there, its repository MUST be reachable, and a probe that cannot
    reach it has to refuse. If it is not there, this RAG is not a deployment of
    this kernel and there is genuinely nothing to prove.

    Note what this does NOT rest on: a declaration. The earlier versions asked the
    RAG to describe itself and then trusted the description, which is the same
    shape as trusting a stamp without checking the head. This one looks.
    """
    try:
        deployed = Path(rag_path).resolve().parent / "rag_kernel"
        return deployed.is_dir() and any(deployed.glob("__main__.py"))
    except OSError:  # noqa: BLE001 — an unreadable RAG is reported by gate step 1
        return False


def _live_kernel_head(rag_path: Path) -> "str | None":
    """Short HEAD of the repo that actually holds the kernel suite.

    Deliberately NOT ``_resolve_git_head``. That helper answers a different
    question — it walks ``current_status.git_worktree_path``, which is the tree
    the FRESHNESS guard (E-043) is about. The interval probes are about the tree
    the SUITE was measured in, which ``test_gate.resolve_repo_root`` derives from
    ``meta.reconciliation_docs_root``. On this deployment the two happen to name
    the same directory, and nothing anywhere enforced that they must. Two probes
    grading "the kernel" against two different repositories is the same category
    of defect as a green number attached to code nobody ran, so all four interval
    probes are pinned to one tree here, with ``_resolve_git_head`` as the fallback
    for deployments that declare no suite root.
    """
    import subprocess

    repo = _kernel_repo_root(rag_path)
    if repo is not None:
        try:
            r = subprocess.run(
                ["git", "-C", str(repo), "rev-parse", "--short", "HEAD"],
                capture_output=True, text=True, timeout=15, check=False,
            )
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            pass
    return _resolve_git_head(rag_path)


def _probe_test_gate(rag_path: Path, *, git_head: "str | None" = None) -> "str | None":
    """Is meta.test_gate measured, green, AND measured at the live HEAD?

    Returns a finding string when it is not, else None. This is the same
    ``test_gate.verdict`` the close already obeys (CLOSE-TESTGATE-STALE-BLOCKS,
    E-115); the only defect was that the BOOT never consulted it. S191 told the
    operator the S192 boot would refuse on a stale gate. It would not have —
    the check lived solely in grand_audit axis 2, which the boot does not run.
    That promise is what this function makes true.
    """
    rag_path = Path(rag_path).resolve()
    # Same predicate as the other three, for the same reason: this probe asks
    # whether the kernel deployed at this RAG was proven at the commit it ships
    # from. Where no kernel is deployed there is no such kernel, and inventing a
    # finding about it would be the too-BROAD failure described above. (The
    # close's own CLOSE-TESTGATE-STALE-BLOCKS, E-115, is a separate and
    # unconditional gate — this does not weaken it.)
    if not _declares_git_deployment(rag_path):
        return None
    try:
        from rag_kernel import test_gate as _tg
        from rag_kernel.drift_store import load_hot as _load_hot

        hot = _load_hot(rag_path)
        live_head = git_head or _live_kernel_head(rag_path)
        # FAIL CLOSED on an unresolvable HEAD. `verdict` compares only when it is
        # GIVEN a live head — `if live_head and head and ...` — so a None head
        # makes a stale green stamp grade as True. That is the same fail-open
        # shape as the original defect, one layer down, and it is how a relative
        # rag_path silently disarmed all four of these probes on first run.
        if not live_head:
            return (
                "test gate: live git HEAD is unresolvable, so the stamp cannot be "
                "graded against the running code — refusing rather than grading a "
                "measurement against nothing"
            )
        ok, cell, why = _tg.verdict(
            _tg.read_stamp(hot),
            live_head=live_head,
            live_runtime=(hot.get("meta") or {}).get("runtime_version"),
        )
    except Exception as exc:  # noqa: BLE001 — a gate that cannot measure must not pass
        return f"test gate: probe raised {exc} — cannot certify the inherited kernel"
    if ok is True:
        return None
    return (
        f"test gate: {cell} ({why}) — the inherited kernel was never proven at the "
        "commit it ships from. Re-measure it: `rag_kernel tests --run --session <SID>`"
    )


def _probe_orphan_enums(rag_path: Path, rag_dir: "Path | None") -> "str | None":
    """Does ERROR_LOG.md cite E-numbers that no tracked item backs?

    An id written in prose and never banked is a fabricated identifier: the next
    session reads the citation, finds nothing behind it, and cannot recover what
    was meant. This is the RAG failing to carry a fact it was told, which is the
    operator's second failure mode, and it is why S191 handed S192 three ids
    (E-111, E-114, E-115) with no ledger entry.
    """
    import json as _json
    import re as _re

    rag_path = Path(rag_path).resolve()
    rag_dir = Path(rag_dir).resolve() if rag_dir is not None else rag_path.parent
    log = rag_dir / "ERROR_LOG.md"
    if not log.exists():
        return None
    try:
        txt = log.read_text(encoding="utf-8", errors="replace")
        with open(rag_path, "r", encoding="utf-8-sig") as fh:
            rag = _json.load(fh)
    except (OSError, ValueError) as exc:
        return f"orphan enums: cannot read ({exc})"

    known: set[str] = set()

    def _walk(node) -> None:
        if isinstance(node, dict):
            ident = node.get("id")
            if isinstance(ident, str) and "status" in node:
                known.add(ident)
            for val in node.values():
                _walk(val)
        elif isinstance(node, list):
            for val in node:
                _walk(val)

    _walk(rag)
    cited = sorted(set(_re.findall(r"\bE-\d{3}\b", txt)))
    orphans = [e for e in cited if e not in known]
    if not orphans:
        return None
    return (
        f"orphan enums: ERROR_LOG.md cites {len(orphans)} of {len(cited)} E-numbers "
        f"with no tracked item behind them: {', '.join(orphans[:8])}"
        f"{' ...' if len(orphans) > 8 else ''} — a cited id that was never banked is "
        "a fabricated identifier. Bank each one: `rag_kernel add <id> \"<title>\" "
        "--kind ERROR --session <SID> --note \"...\"`"
    )


def _kernel_repo_root(rag_path: Path) -> "Path | None":
    """Resolve the git worktree that holds the kernel suite, or None."""
    try:
        from rag_kernel import test_gate as _tg
        from rag_kernel.drift_store import load_hot as _load_hot

        root = _tg.resolve_repo_root(rag_path, _load_hot(rag_path))
        return Path(root) if root else None
    except Exception:  # noqa: BLE001 — resolution failure is reported by the caller
        return None


def _probe_worktree_clean(rag_path: Path) -> "str | None":
    """Is the kernel worktree free of uncommitted changes RIGHT NOW?

    E-123 in one sentence: S191 measured the gate, sealed, and only then noticed
    ``M rag_kernel/__main__.py``. The running kernel existed in no commit, so the
    green number described code nobody could retrieve. ``git status --porcelain``
    is the cheapest possible probe and it was never once run before a claim of
    done.
    """
    import subprocess

    rag_path = Path(rag_path).resolve()
    repo = _kernel_repo_root(rag_path)
    if repo is None:
        if _declares_git_deployment(rag_path):
            return (
                "worktree: a kernel is deployed beside this RAG but its git repo "
                "could not be resolved — cannot prove the running kernel is "
                "committed, so this refuses instead of passing"
            )
        return None  # nothing declared — nothing to prove
    if not (repo / ".git").exists():
        return None  # resolved, and genuinely not a git deployment
    try:
        out = subprocess.run(
            ["git", "status", "--porcelain"], cwd=str(repo),
            capture_output=True, text=True, timeout=60, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return f"worktree: cannot run git status in {repo} ({exc})"
    if out.returncode != 0:
        return f"worktree: git status failed in {repo} ({out.stderr.strip()[:160]})"
    dirty = [ln for ln in out.stdout.splitlines() if ln.strip()]
    if not dirty:
        return None
    return (
        f"worktree: {len(dirty)} uncommitted change(s) in {repo.name} — "
        f"{'; '.join(x.strip()[:60] for x in dirty[:4])}"
        f"{' ...' if len(dirty) > 4 else ''} — the running kernel exists in no "
        "commit (E-109/E-123). Commit and push before this state is inherited."
    )


def _probe_deploy_parity(rag_path: Path) -> "str | None":
    """Is the DEPLOYED kernel byte-identical to the TESTED one?

    REUSE, NOT REWRITE (Rule 25). This probe originally reimplemented the
    comparison and should not have: ``drift_audit.check_kernel_copy_lockstep``
    has done exactly this since S188 (KERNEL-COPY-LOCKSTEP-UNGATED), and does it
    strictly better — ``rglob`` across subpackages where the rewrite globbed only
    top-level ``*.py``, and ``__pycache__`` excluded because bytecode differs
    legitimately per interpreter. `reuse-check` returned CLEAR for "deployed
    kernel divergence" because it searches the baked-ASSET registry, not the
    kernel's own functions, so the registry answered a narrower question than the
    one asked. The lesson is banked; this delegates.

    What is genuinely new here is not the comparison but WHEN it runs. The
    lockstep check reaches the boot through ``audit_file`` (gate step 2) and the
    close through its audit step — both of which happen well BEFORE
    ``transfer_ready`` is written. Running it again as the last act before the
    seal is the interval, which is the whole subject of E-123.
    """
    rag_path = Path(rag_path).resolve()
    repo = _kernel_repo_root(rag_path)
    if repo is None and _declares_git_deployment(rag_path):
        return (
            "deploy parity: a kernel is deployed beside this RAG but its git "
            "repo could not be resolved — cannot prove the deployed kernel "
            "matches the committed one"
        )
    try:
        from rag_kernel import drift_audit as _da
        from rag_kernel.drift_store import load_hot as _load_hot

        findings = _da.check_kernel_copy_lockstep(
            _load_hot(rag_path), rag_path.parent.parent, rag_path.parent
        )
    except Exception as exc:  # noqa: BLE001 — a probe that cannot compare must not pass
        return f"deploy parity: lockstep check raised {exc}"
    if not findings:
        return None
    details = [getattr(f, "detail", str(f)) for f in findings]
    return (
        f"deploy parity: {len(details)} kernel-copy lockstep violation(s) — "
        + "; ".join(d[:110] for d in details[:3])
        + (" ..." if len(details) > 3 else "")
        + " — the kernel that runs is not the kernel that is tested and committed."
    )


def _interval_probes(
    rag_path: Path, *, rag_dir: "Path | None" = None, git_head: "str | None" = None,
) -> list[str]:
    """Run all four S192 interval probes and return the findings, in report order."""
    return [
        f for f in (
            _probe_test_gate(rag_path, git_head=git_head),
            _probe_orphan_enums(rag_path, rag_dir),
            _probe_worktree_clean(rag_path),
            _probe_deploy_parity(rag_path),
        ) if f
    ]


def _carry_forward_gate(
    rag_path: Path, *, strict: bool = False, git_head: "str | None" = None,
    rag_dir: "Path | None" = None, new_sid: "str | None" = None,
) -> tuple[bool, list[str]]:
    """KA-6 session-START gate: is the INHERITED RAG coherent and safe to build on?

    The precise inverse of the KA-4 close gate. KA-4 stops a session *ending*
    without a checkpoint; this stops a session *beginning* work on a RAG that the
    prior session left incoherent or unbanked — the upstream half of the eBay
    S2/S4 governance freeze. It runs, as code, the two fail-loud checks the
    canonical carry-forward verification otherwise performs by hand every session
    start:

      1. verify — HOT<->COLD self-version coherence + no surviving
                  ``<SPEC_VERSION>`` placeholder (SpecParser.verify_coherence).
      2. audit  — renders == canonical tracked_items (E-040), supersede refs
                  resolve, notes don't contradict status (INS-038), ``.bak``
                  parity (FIX-8), current_status freshness vs live HEAD (E-043),
                  and no Cowork-memory side stores (Rule 13).

    Returns ``(ok, findings)``. ``ok`` is True iff BOTH gates are clean. Disk /
    parse / audit faults surface as findings (fail-loud) — the gate never raises
    and never returns a silent green on an unreadable RAG.
    """
    import json as _json
    from rag_kernel.spec_parser import SpecParser
    from rag_kernel import drift_audit
    from rag_kernel.drift_store import DriftStoreError

    findings: list[str] = []
    if not rag_path.exists():
        return False, [f"RAG_MASTER.json not found at {rag_path}"]

    # 1. verify — HOT<->COLD coherence (utf-8-sig tolerates a COLD BOM).
    try:
        def _load(p: Path) -> dict:
            with open(p, "r", encoding="utf-8-sig") as f:
                return _json.load(f)

        rag = _load(rag_path)
        cold_path = rag_path.parent / "RAG_COLD.json"
        cold = _load(cold_path) if cold_path.exists() else None
        for fnd in SpecParser.verify_coherence(rag, cold, ""):
            findings.append(f"verify: {fnd}")
    except (OSError, ValueError) as exc:
        findings.append(f"verify: RAG/COLD unreadable ({exc})")

    # 2. audit — fail-loud session auditor (renders, refs, notes, .bak parity,
    #    freshness, side stores). Defaults match a bare ``audit`` (scan_root=True,
    #    docs_root=None — repo-doc reconciliation is a close-time concern).
    try:
        head = git_head or _resolve_git_head(rag_path)
        report = drift_audit.audit_file(rag_path, git_head=head)
        if not report.is_clean(strict=strict):
            findings.append("audit: " + report.summary().replace("\n", " | "))
    except (DriftStoreError, OSError, ValueError, KeyError) as exc:
        findings.append(f"audit: {exc}")

    # 3. KA-16 — incomplete-close detection. If the inherited RAG carries a
    #    session_close marker that never reached transfer_ready, the prior
    #    session banked state but its close aborted (the eBay S4 stranding).
    #    Refuse to build forward until it is resumed (independent safe read; an
    #    unreadable RAG is already surfaced by step 1).
    try:
        import json as _json_kc
        with open(rag_path, "r", encoding="utf-8-sig") as f:
            _marker = _json_kc.load(f).get("session_close")
        if isinstance(_marker, dict) and not _marker.get("transfer_ready", False):
            findings.append(
                f"incomplete close: session {_marker.get('session')} left at phase "
                f"{_marker.get('phase')} (transfer_ready=false) — run "
                "`session-resume` before starting a new session"
            )
    except (OSError, ValueError):
        pass

    # 4. CLOSE-SEAL-ENFORCE (S172, KA-21). Step 3 catches a close that STARTED and
    #    aborted (marker transfer_ready=false); this catches the complementary hole
    #    — a prior session that never ran session-end AT ALL, so no close event and
    #    no AUDIT_CANONICAL_REPORT ever existed (the S157 gap, independently
    #    reproduced as the eBay S14 CLOSE-GAP — UNIVERSAL per Rule 15). Refuse to
    #    build a new session forward over an unsealed predecessor.
    if rag_dir is not None:
        try:
            unsealed = _unsealed_prior_session(rag_path, rag_dir, new_sid)
            if unsealed:
                findings.append(
                    f"unsealed prior session: {unsealed} ran (session_log present) but "
                    "was never sealed — no COMPLETE session_close and no "
                    f"AUDIT_CANONICAL_REPORT_{unsealed}.md. Seal it first: "
                    "`session-resume` (if a close was interrupted) or `session-end "
                    f"--session {unsealed}` — or pass --force to start anyway (UNSAFE)."
                )
        except (OSError, ValueError):
            pass

    # 5. BOOT-INTERVAL-GUARDS (S192, E-123/E-124/E-125/E-126). Steps 1-4 all ask
    #    "is the inherited RECORD coherent?". None of them asks "is the inherited
    #    KERNEL the one that was measured?" — and that is the question every
    #    boot-time disaster in this project has turned on. S190 shipped a kernel
    #    its suite had never seen and cost S191 its entire boot; S191 sealed with
    #    an uncommitted __main__.py and cost the operator his trust. In both cases
    #    the fact was sitting in canonical state, visible to grand_audit, and no
    #    gate on the startup path asked for it.
    #
    #    All four are ASSERTED — see _NEVER_REPAIRABLE_MARKERS. Re-measuring a
    #    suite, banking an id, committing a tree and syncing a deployment are acts
    #    with content; performing them silently at boot would convert a refusal
    #    into a cover-up.
    findings.extend(_interval_probes(rag_path, rag_dir=rag_dir, git_head=git_head))

    return (not findings), findings


# ---------------------------------------------------------------------------
# GATE-AUTO-RECONCILE (S184) — repair DERIVED state, never ASSERTED state
# ---------------------------------------------------------------------------
#
# Origin: the operator, after a birth in which every single carry-forward refusal
# was mechanically repairable and every one of them was nonetheless handed to him
# as a command to paste. "I want to say hello and expect it to run smooth."
#
# The classifier below is the whole design. A finding is repairable ONLY if the
# repair is a pure function of canonical state — regenerate the derived artifact
# and no fact is lost, because canonical already held it. Everything else is a
# claim, and a claim can only be settled by a decision.
#
# REPAIRABLE (derived):
#   map_coverage / boot-map drift  -> reseal the baseline from the live tree
#   render_parity                  -> re-render the legacy arrays from tracked_items
#   current_status_freshness       -> re-stamp the git-head token
#   bak / .bak parity              -> re-mirror the backup from HOT
#
# NEVER REPAIRABLE (asserted) — deliberately enumerated so the list cannot grow
# by accident:
#   verify / spec coherence     a version skew or a surviving placeholder
#   asset_registry              a registered file's content changed; refreshing
#                               the checksum would erase the only signal that it did
#   note_status_contradiction   a semantic disagreement between a human sentence
#                               and a machine status
#   side stores                 a second source of truth; Rule 13
#   incomplete close            the predecessor's close aborted; `session-resume`
#                               is a real recovery with real state to reconcile
#   unsealed prior session      sealing it needs a SUMMARY and a HANDOFF that only
#                               the session that did the work can write. Auto-sealing
#                               would forge a close. This is the single most
#                               important entry in this list.
#   test gate                   (S192) re-running the suite is a 3-minute measurement
#                               whose whole value is that a human saw the number.
#                               A boot that quietly re-stamped it would restore the
#                               exact condition E-115 exists to prevent.
#   orphan enums                banking an id requires saying what it MEANS. Only
#                               the session that wrote the prose knows; a generated
#                               placeholder would launder the gap instead of closing it.
#   worktree                    committing on the agent's behalf writes an unreviewed
#                               commit message into permanent history.
#   deploy parity               the kernel that runs and the kernel that is committed
#                               have diverged; picking a winner automatically is a
#                               coin-flip over which code is real.
_REPAIRABLE_MARKERS = (
    "map_coverage",
    "boot-map",
    "render_parity",
    "current_status_freshness",
    "bak_parity",
    ".bak",
)
_NEVER_REPAIRABLE_MARKERS = (
    "verify:",
    "asset_registry",
    "note_status_contradiction",
    "side store",
    "incomplete close",
    "unsealed prior session",
    "spec_completeness",
    "record_coverage",
    # S192 interval guards — see the block comment above _carry_forward_gate.
    "test gate:",
    "orphan enums:",
    "worktree:",
    "deploy parity:",
)


def _finding_is_repairable(finding: str) -> bool:
    """Classify one gate finding. Fail CLOSED: unknown text is never repairable."""
    low = finding.lower()
    if any(m.lower() in low for m in _NEVER_REPAIRABLE_MARKERS):
        return False
    return any(m.lower() in low for m in _REPAIRABLE_MARKERS)


def _auto_reconcile_gate(
    rag_path: Path, rag_dir: Path, sid: str, findings: list[str], *,
    strict: bool = False, git_head: "str | None" = None,
) -> "tuple[list[str], bool, list[str]]":
    """Repair the derived-state findings, then RE-RUN the gate once and return it.

    Returns ``(repairs, ok, findings)``. The gate is re-run rather than assumed
    clean — a repair that did not actually fix its finding must surface as a
    refusal, not as an optimistic green. One pass only: a repair that needs a
    second pass to hold is not a repair, it is a loop.
    """
    repairs: list[str] = []

    # Only act when at least one finding is repairable AND none is asserted. A
    # mixed batch is refused wholesale: repairing half of a broken state and then
    # reporting the remainder invites acting on a partially-reconciled RAG.
    if not any(_finding_is_repairable(f) for f in findings):
        return repairs, False, findings
    if any(not _finding_is_repairable(f) for f in findings):
        return repairs, False, findings

    joined = " ".join(findings).lower()

    # 1. render_parity — re-render the legacy arrays from canonical tracked_items.
    if "render_parity" in joined:
        try:
            rc = cmd_render(argparse.Namespace(rag=rag_path, apply=True, what=None))
            repairs.append(
                "render_parity: re-rendered legacy open_tasks/deferred_items/"
                f"priority_actions from canonical tracked_items (rc={rc})"
            )
        except Exception as exc:  # noqa: BLE001 — a failed repair must not crash the boot
            repairs.append(f"render_parity: repair FAILED ({exc})")

    # 2. current_status_freshness — re-stamp the git-head token only.
    if "current_status_freshness" in joined:
        try:
            rc = cmd_refresh_current_status(argparse.Namespace(
                rag=rag_path, session=sid, version=None, git_head=git_head,
                tests=None, strict=False, dry_run=False,
            ))
            repairs.append(
                f"current_status_freshness: re-stamped github_repo HEAD from the live "
                f"worktree (rc={rc})"
            )
        except Exception as exc:  # noqa: BLE001
            repairs.append(f"current_status_freshness: repair FAILED ({exc})")

    # 3. map_coverage / boot-map drift — reseal the baseline from the live tree.
    #    This is the S183 field failure: files authored AFTER a close left the map
    #    stale and blocked the successor. Resealing loses nothing; the map IS the
    #    derived artifact.
    if "map_coverage" in joined or "boot-map" in joined:
        try:
            rc = cmd_bootmap(argparse.Namespace(
                rag=rag_path, root=None, refresh=True, session=sid,
                json_output=False,
            ))
            repairs.append(f"map_coverage: resealed the domain boot-map baseline (rc={rc})")
        except Exception as exc:  # noqa: BLE001
            repairs.append(f"map_coverage: repair FAILED ({exc})")

    # Re-run the gate. Its verdict, not ours, decides whether the boot proceeds.
    ok2, findings2 = _carry_forward_gate(
        rag_path, strict=strict, git_head=git_head, rag_dir=rag_dir, new_sid=sid,
    )
    return repairs, ok2, findings2


# ---------------------------------------------------------------------------
# KA-14 — session-start rule-load attestation gate
# ---------------------------------------------------------------------------
#
# The fresh-deploy root cause (eBay S0/S105 field audit): the HOT operating_protocol
# rule bodies live on disk in the RAG, but a fresh agent never actually loaded them
# into working cognition — it ran the ritual and proceeded blind to its own rules.
# A gate that merely PRINTS the rules cannot prove they were ingested. KA-14 makes
# rule-load a two-phase, token-attested handshake:
#
#   BOOT -> RULES_LOADED(attested) -> READY
#
#   phase 1  session-start <sid>           : carry-forward gate -> gc -> RENDER the
#                                            compact rule digest into context, write
#                                            a rule_load marker (attested=false) and a
#                                            digest token; the logger is NOT opened.
#   phase 2  session-start <sid> --attest T: verify T == the LIVE digest token, flip
#                                            attested=true, open the logger (READY).
#
# The token is the digest's fingerprint: an agent cannot produce it without having
# received the rendered digest, so READY is unreachable without the rules in context.
# ML lens: a compact digest (rule key + one-line summary), not the full bodies, keeps
# the token cost low. CS lens: no new TLA+ state (RULES_LOADED is a runtime marker
# phase, like KA-16's session_close) so the drift gate is unchanged; the token check
# is deterministic and fail-loud. LLM proposes the attestation; the system decides on
# a byte-exact token match; the marker persists the decision.

_RULE_SUMMARY_LIMIT = 110


def _rule_summary(value, limit: int = _RULE_SUMMARY_LIMIT) -> str:
    """One-line summary of an operating_protocol rule value (str or dict)."""
    if isinstance(value, dict):
        keys = list(value.keys())
        shown = ", ".join(keys[:4])
        more = "…" if len(keys) > 4 else ""
        return f"[{len(keys)} sub-rules: {shown}{more}]"
    s = " ".join(str(value).split())
    dot = s.find(". ")
    if 0 < dot <= limit:
        return s[: dot + 1]
    return (s[:limit] + "…") if len(s) > limit else s


def _compute_rule_digest(rag: dict) -> "tuple[list[tuple[str, str]], str]":
    """Project operating_protocol into (lines, token).

    ``lines`` is an ordered [(rule_key, one_line_summary)] list; ``token`` is the
    first 12 hex of sha256 over the canonical ``key|summary`` serialization — a
    deterministic fingerprint of the exact digest the agent is shown.
    """
    import hashlib

    op = rag.get("operating_protocol", {})
    lines: list[tuple[str, str]] = (
        [(k, _rule_summary(v)) for k, v in op.items()] if isinstance(op, dict) else []
    )
    canon = "\n".join(f"{k}|{summ}" for k, summ in lines)
    token = hashlib.sha256(canon.encode("utf-8")).hexdigest()[:12]
    return lines, token


def _render_rule_digest(lines: "list[tuple[str, str]]") -> str:
    if not lines:
        return "  (no operating_protocol rules in this RAG)"
    return "\n".join(f"  - {k}: {summ}" for k, summ in lines)


# ---------------------------------------------------------------------------
# KA-20 — BOOT-GUARD-FIRST-ACTION (S172)
# ---------------------------------------------------------------------------
#
# Root cause (E-071/072/073/075/076, five consecutive fresh boots): at "hello" a
# cold-booting agent reads RAG_MASTER.json via the PERMANENTLY-BANNED Cowork
# sandbox to brief the operator, BEFORE the governed ritual. A rule in the RAG
# cannot bind — the agent breaks it while loading it. The kernel cannot observe a
# sandbox read from inside, so BOOT-GUARD does not claim to (increment-status
# honesty). It removes the TRIGGER and records PROOF:
#
#   (1) session-start renders a deterministic BOOT-STATE BRIEFING — the exact
#       state facts (ledger OPEN/overdue, next_session_directive, backlog counts)
#       the agent would otherwise open the RAG to get — so there is no reason to
#       read RAG_MASTER.json directly at all;
#   (2) it writes a ``boot_guard`` first-action marker (the governed boot ran);
#   (3) it prints an explicit E-071-class notice naming the sandbox read as a
#       violation.
#
# The load-bearing PREVENTION is out-of-band, in the Project Instructions (which
# load before any file read): step 1 must mandate `rag_kernel session-start` as
# the first action and forbid a direct RAG read. Kernel + PI together = the
# "kill the trigger + fail loud" design settled S172.

_BOOT_GUARD_NOTICE = (
    "[BOOT-GUARD] The briefing above is the CANONICAL boot state — it is complete.\n"
    "  Do NOT read RAG_MASTER.json directly (sandbox or any transport) to brief\n"
    "  state: a direct/sandbox read of the canonical RAG at boot is an E-071-class\n"
    "  tool_hierarchy violation. All governed reads go through this command / tmux."
)


def _render_agent_frame(rag: dict, *, rag_dir: "str | None" = None) -> str:
    """BOOT-RENDER-POV-ROLES (S176) — render the agent's OPERATING FRAME at boot.

    Root cause (S176): the boot path rendered 40 operating_protocol rules and the
    state briefing, but never rendered ``pov_roles`` / ``pov_mandate``. The dual-POV
    identity therefore lived in the RAG *unread*, reaching the agent only through
    operator-owned Project Instructions prose — a single point of failure on text
    the operator has to maintain by hand. Same failure family as E-071: the state
    exists, but has no delivery path at boot.

    Renders three things the agent otherwise re-learns (or fails to re-learn) every
    session, all sourced FROM the RAG so nothing is duplicated into code:

      (1) pov_roles + pov_mandate — who the agent reasons as, and how strictly;
      (2) the process-discipline tail of ``token_economy`` (Rule 17) plus the
          E-081 detached-execution discipline — the rules most often loaded and
          then ignored under time pressure;
      (3) the baked-asset registry count (REUSE-REGISTRY-GUARD / Rule 25), so
          prior hardening is DISCOVERABLE instead of re-derived by grep.
    """
    roles = rag.get("pov_roles", []) or []
    mand = rag.get("pov_mandate", {}) or {}
    count = mand.get("count", len(roles))
    mode = str(mand.get("mode", "strict"))
    out = [
        f"[BOOT-FRAME] Operating frame — POV mandate: {count} role(s), mode {mode.upper()}:"
    ]
    if roles:
        for i, r in enumerate(roles, 1):
            out.append(f"  ROLE {i}: {r}")
        if mode.lower() == "strict":
            out.append(
                "  STRICT: reason EVERY deliverable from ALL the roles above, not one."
            )
    else:
        out.append("  (no pov_roles in this RAG — identity is UNDEFINED, fail loud)")

    out.append("  PROCESS DISCIPLINE (loaded every boot because it is the most re-learned):")
    out.append("    - Long jobs run DETACHED to a file; check ONCE after a single long")
    out.append("      wait. NEVER poll a running command (E-081).")
    out.append("    - THE WAIT IS A VERB, NOT A JUDGEMENT CALL (WAIT-PRIMITIVE, S180):")
    out.append("        rag_kernel wait-for <file> --timeout N --contains DONE --emit 20")
    out.append("      It blocks server-side and returns the tail in ONE round-trip.")
    out.append("      Use it in a SECOND pane (Rule 27) while the job runs in the first.")
    out.append("      There is now no reason to poll and no reason to reach for the")
    out.append("      banned Cowork sandbox as a sleep timer (E-082b/E-086/E-089/E-090).")
    out.append("    - Two consecutive uninformative/malformed emissions toward one goal")
    out.append("      = STOP, diagnose, report with options (Rule 17 / circuit_breaker).")
    out.append("    - Bounded emissions only: pipe verbose output to a file, read back a")
    out.append("      capped slice (tail/head/grep/wc). Tool round-trips are the")
    out.append("      operator's metered resource, not free.")

    n_assets = None
    try:
        ctx_dir = rag_dir or "."
        ctx_path = os.path.join(ctx_dir, "RAG_CONTEXT.json")
        if os.path.isfile(ctx_path):
            with open(ctx_path, "r", encoding="utf-8") as fh:
                ctx = json.load(fh)
            part = (ctx.get("baked_assets") or {}) if isinstance(ctx, dict) else {}
            n_assets = len(part.get("assets", []) or [])
    except Exception:
        n_assets = None
    if n_assets is None:
        out.append("  BAKED ASSETS: registry unreadable — run `rag_kernel reuse-check`.")
    elif n_assets == 0:
        out.append(
            "  BAKED ASSETS: registry EMPTY — nothing prior is discoverable. Register"
        )
        out.append(
            "    kernel-owned assets with `rag_kernel register-asset` (Rule 25)."
        )
    else:
        out.append(
            f"  BAKED ASSETS: {n_assets} registered — run `rag_kernel reuse-check` BEFORE"
        )
        out.append("    authoring anything new (REUSE-BEFORE-REWRITE, Rule 25).")
    return "\n".join(out)


def _render_boot_briefing(rag: dict, *, current_sid: "str | None" = None) -> str:
    """Deterministic boot-state briefing from the RAG — every state fact the agent
    needs at boot, so it never has to open RAG_MASTER.json itself (KA-20).

    Renders: inference_ledger OPEN count (+ overdue OPEN items >2 sessions old per
    inference_ledger_protocol step 4), the next_session_directive, and
    priority_actions / open_tasks / deferred_items counts.
    """
    led = rag.get("inference_ledger", []) or []
    open_items = [x for x in led if isinstance(x, dict) and x.get("disposition") == "OPEN"]
    cur_seq = _session_seq(current_sid)
    overdue = 0
    if cur_seq is not None:
        for x in open_items:
            s = _session_seq(x.get("session"))
            if s is not None and (cur_seq - s) > 2:
                overdue += 1
    pa = rag.get("priority_actions", []) or []
    ot = rag.get("open_tasks", []) or []
    di = rag.get("deferred_items", []) or []
    nsd = rag.get("next_session_directive")

    lines: list[str] = []
    overdue_txt = f" ({overdue} OVERDUE >2 sessions)" if overdue else ""
    lines.append(
        f"  inference_ledger: {len(open_items)} OPEN of {len(led)} total{overdue_txt}"
    )
    if isinstance(nsd, dict):
        # DIRECTIVE-NO-TRUNCATE (S184). This was clipped to 300 chars with an
        # ellipsis, which made the boot briefing the ONLY place a directive could
        # be read and even there only its first paragraph. The directive is the
        # highest-leverage artifact a session produces — E-095 was a directive
        # DEFECT, and a successor cannot honour, or contradict, text it cannot
        # see. token_economy governs verbose diagnostics, not the one field the
        # next session is required to obey; a bounded emission that drops the
        # binding instruction is not economy, it is data loss with a nice name.
        # Wrapped for readability, never shortened.
        directive = " ".join(str(nsd.get("directive", "")).split())
        lines.append(
            f"  next_session_directive: for {nsd.get('for_session', '?')} "
            f"(by {nsd.get('session', '?')}) — FULL TEXT, verbatim:"
        )
        if directive:
            import textwrap as _tw
            for _ln in _tw.wrap(directive, width=96) or [directive]:
                lines.append(f"    {_ln}")
        else:
            lines.append("    (none)")
    else:
        lines.append("  next_session_directive: (none)")
    lines.append(
        f"  backlog: priority_actions={len(pa)}, open_tasks={len(ot)}, "
        f"deferred_items={len(di)}"
    )
    # S187 PRIORITY-ACTIONS-STALE-SNAPSHOT: priority_actions is now a render of the
    # ACTIVE P1 set, so the boot can brief the live agenda by id instead of a count
    # over a frozen prose blob. Ids only — the titles live one `render` call away.
    #
    # INSPECTED-COUNT-DISCLOSURE (S195, E-130 prevention, PLAN-FEASIBILITY-GATE).
    # The agenda is now enumerated from ``tracked_items`` — the canonical array —
    # rather than parsed out of the persisted ``priority_actions`` projection, and
    # the briefing STATES THE SET IT WALKED: how many items it inspected, out of
    # how many tracked, over which kinds. A count with no denominator is what let
    # a truncated view be mistaken for the whole set (E-130), and a check that
    # reports only its verdict is what let E-129 and E-131 pass while blind. Every
    # completeness surface in this kernel now reports what it looked at.
    _live_all: list = []
    _p1_ids: list[str] = []
    _n_tracked = 0
    try:
        from rag_kernel import drift_render as _dr
        from rag_kernel.drift_store import TRACKED_ITEMS_KEY, TrackedItemStore

        _n_tracked = len(rag.get(TRACKED_ITEMS_KEY, []) or [])
        _store = TrackedItemStore.from_hot(rag)
        _live_all = [it for it in _store if it.status in _dr.ACTIVE_STATUSES]
        _p1_ids = [
            it.id
            for it in _live_all
            if it.priority_group == "P1" and it.kind in _dr.AGENDA_KINDS
        ]
    except Exception:  # pragma: no cover - briefing must never fail the boot
        _p1_ids = [
            str(x).split(" [", 1)[0].strip()
            for x in pa
            if isinstance(x, str) and " [P1 · " in x
        ]
    if _live_all:
        _kinds = sorted({it.kind.value for it in _live_all})
        lines.append(
            f"  INSPECTED: {len(_live_all)} live item(s) (OPEN+IN_PROGRESS) of "
            f"{_n_tracked} tracked, kinds {'/'.join(_kinds)} — the set every count "
            f"below was taken over."
        )
    if _p1_ids:
        import textwrap as _tw2
        lines.append(
            f"  P1 (live, enumerated from tracked_items.priority_group) — "
            f"{len(_p1_ids)}:"
        )
        for _ln in _tw2.wrap(", ".join(_p1_ids), width=92):
            lines.append(f"    {_ln}")
        # Agenda-vs-projection disagreement is a render_parity defect the boot must
        # not swallow: the successor reads THIS text, not the audit log.
        _pa_ids = {
            str(x).split(" [", 1)[0].strip()
            for x in pa
            if isinstance(x, str) and " [P1 · " in x
        }
        _missing = sorted(set(_p1_ids) - _pa_ids)
        if _missing:
            lines.append(
                f"  WARNING: persisted priority_actions omits {len(_missing)} live "
                f"P1 item(s) — {', '.join(_missing)}. Run `rag_kernel render --apply`."
            )
    return "\n".join(lines)


def _read_top_level_field(rag_path: Path, key: str):
    try:
        with open(rag_path, "r", encoding="utf-8-sig") as f:
            return json.load(f).get(key)
    except (OSError, ValueError):
        return None


def _write_top_level_field(rag_path: Path, key: str, value) -> None:
    """Persist a top-level RAG field, preserving ``.bak`` byte-parity (FIX-8)."""
    with open(rag_path, "r", encoding="utf-8") as f:
        rag = json.load(f)
    rag[key] = value
    try:
        from rag_kernel.persistence import atomic_write_json
        atomic_write_json(rag_path, rag, mirror_bak=True)
    except ImportError:
        with open(rag_path, "w", encoding="utf-8") as f:
            json.dump(rag, f, indent=2, ensure_ascii=False)


def _session_start_attest(
    rag_path: Path, rag_dir: Path, sid: str, token: str
) -> int:
    """KA-14 phase 2 — verify the digest token, then open the logger (READY)."""
    marker = _read_top_level_field(rag_path, "rule_load")
    if not isinstance(marker, dict):
        print(
            "ERROR: no rule_load marker — run `session-start <sid>` (phase 1) first "
            "to render the rule digest.",
            file=sys.stderr,
        )
        return 1
    if marker.get("session") != sid:
        print(
            f"ERROR: rule_load marker is for session {marker.get('session')!r}, "
            f"not {sid!r} — run phase 1 for {sid!r} first.",
            file=sys.stderr,
        )
        return 1
    try:
        with open(rag_path, "r", encoding="utf-8-sig") as f:
            rag = json.load(f)
    except (OSError, ValueError) as exc:
        print(f"ERROR: RAG unreadable ({exc}).", file=sys.stderr)
        return 1
    _, current_token = _compute_rule_digest(rag)
    if token != current_token:
        print(
            "ERROR: attestation token mismatch — the rule digest changed or the token "
            "is wrong. Re-run `session-start` (phase 1) to load the CURRENT digest, "
            "then attest the freshly-printed token.",
            file=sys.stderr,
        )
        return 1

    attested = dict(marker)
    attested["attested"] = True
    attested["attested_utc"] = _utcnow_iso()
    _write_top_level_field(rag_path, "rule_load", attested)

    from rag_kernel.session_logger import SessionLogger

    logger = SessionLogger(sid, log_dir=rag_dir)
    logger.open()
    print("Rule-load attested: token matches the live operating_protocol digest.")
    print(f"  Session {sid} READY. Log file: {logger.log_path}")
    return 0


class _BootTee:
    """BOOT-LOG-TEE (S184) — mirror phase-1 stdout to a file, unconditionally.

    Field cause (clone ERR-S3-DUP-SESSION-START): the transport returned "output
    could not be captured" on a healthy pane three times, so the agent could not
    read the attestation token — and RE-RAN `session-start`, a state-touching
    governed verb, to recover it. The token was recoverable the whole time; only
    the *channel* had failed.

    A governed verb whose only copy of its output is the terminal makes re-running
    it the cheapest recovery. So there is always a second copy: every phase-1 boot
    writes its full transcript to ``<rag_dir>/.boot/session_start_<SID>.log`` and
    prints that path FIRST, before anything that could be lost. Recovery becomes a
    read, never a re-execution.

    Best-effort by construction: if the log cannot be opened the boot proceeds
    un-teed rather than failing. A diagnostic aid must never become a new gate.
    """

    def __init__(self, stream, path: "Path | None"):
        self._stream = stream
        self._fh = None
        if path is not None:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                self._fh = open(path, "w", encoding="utf-8")
            except OSError:
                self._fh = None

    def write(self, data):
        n = self._stream.write(data)
        if self._fh is not None:
            try:
                self._fh.write(data)
            except OSError:
                pass
        return n

    def flush(self):
        self._stream.flush()
        if self._fh is not None:
            try:
                self._fh.flush()
            except OSError:
                pass

    def isatty(self):
        return getattr(self._stream, "isatty", lambda: False)()

    def close(self):
        if self._fh is not None:
            try:
                self._fh.close()
            except OSError:
                pass
            self._fh = None


def cmd_session_start(args: argparse.Namespace) -> int:
    """KA-6 + KA-14 — machine-enforced, rule-load-attested session-START ritual.

    Phase 1 (no ``--attest``): carry-forward gate (fail-loud unless ``--force``) ->
    gc dry-run (report-before-delete) -> render the operating_protocol rule digest
    into context + write a ``rule_load`` marker (attested=false) + print a digest
    token. The logger is NOT opened — the session is not yet READY.

    Phase 2 (``--attest <token>``): verify the token against the live digest, flip
    the marker to attested=true, and open the logger (READY).

    ``--no-attest-gate`` restores the legacy one-shot open (UNSAFE; CI/tests only).
    Collapsing the steps into one command removes the hand-scripted-ritual surface
    where a step gets skipped (eBay S2/S4); the attestation gate closes the
    fresh-deploy unloaded-rules hole (eBay S0/S105).
    """
    rag_path = args.rag.resolve()
    rag_dir = rag_path.parent
    sid = args.session_id

    # AUTO-SID-DERIVE (BOOT-PROSE-TO-SCRIPT / zero-read boot): when the agent
    # supplies no session id, the kernel derives it as the increment of
    # meta.written_by_session — the same governed value the PI used to make the
    # agent read out by hand. Both phase 1 and phase 2 derive identically because
    # written_by_session only advances at checkpoint (session-end), so the derived
    # id is stable across the attestation handshake within one session. An explicit
    # id still overrides. This removes the SID-derivation prose (and its
    # sandbox-read bait) from the Project Instructions entirely.
    if sid is None:
        sid = _derive_next_sid(rag_path)
        if sid is None:
            print(
                "ERROR: could not AUTO-SID-DERIVE the next session id — the RAG is "
                "unreadable, or meta.written_by_session is unset, or it is not a "
                "canonical session id (alphabetic prefix + counter, max 9 "
                "digits — a 10/13-digit epoch is refused). AUTO-SID-DERIVE REFUSES to "
                "increment a malformed id (PHANTOM-SESSION-ID, S198): a derived "
                "successor inherits the malformation and then looks governed. "
                "Inspect it with `rag_kernel meta --get written_by_session`. "
                "Otherwise pass an explicit session id (e.g. `session-start S1`).",
                file=sys.stderr,
            )
            return 1
        print(f"[AUTO-SID] Derived next session id {sid} from meta.written_by_session.")

    # Phase 2 — attestation handshake (no gate/gc/render; the session was already
    # vetted in phase 1, this only verifies the token and opens the logger).
    if getattr(args, "attest", None) is not None:
        return _session_start_attest(rag_path, rag_dir, sid, args.attest)

    # BOOT-LOG-TEE (S184) — open the transcript mirror and announce it FIRST, so a
    # transport that drops the rest of this output still leaves the agent a path to
    # read instead of a verb to re-run.
    _boot_log = rag_dir / ".boot" / f"session_start_{sid}.log"
    _tee = _BootTee(sys.stdout, _boot_log)
    _real_stdout = sys.stdout
    sys.stdout = _tee
    try:
        print(f"[BOOT-LOG] Full transcript of this phase-1 boot: {_boot_log}")
        print(
            "  If this output is truncated or the transport drops it, READ THAT FILE. "
            "Do NOT re-run session-start to recover the token (ERR-S3-DUP-SESSION-START)."
        )
        return _session_start_phase1(args, rag_path, rag_dir, sid)
    finally:
        sys.stdout = _real_stdout
        _tee.close()


def _session_start_phase1(
    args: argparse.Namespace, rag_path: Path, rag_dir: Path, sid: str
) -> int:
    """Phase-1 body, extracted S184 so the BOOT-LOG-TEE can wrap it wholesale."""
    # 0. OPERATING FRAME — rendered BEFORE the gate, deliberately.
    #
    # S176 defect found by testing the S176 fix: the frame was rendered at step 3c,
    # AFTER the carry-forward gate. A refusing gate therefore returned early and the
    # agent got NO roles and NO process discipline — precisely the moment it is most
    # likely to improvise on broken state. Identity and execution discipline must not
    # be conditional on the state being clean, so they render first, unconditionally.
    # The frame is read-only and best-effort: it must never itself block a boot.
    try:
        with open(rag_path, "r", encoding="utf-8") as _fh:
            _frame_rag = json.load(_fh)
    except Exception:
        _frame_rag = {}
    print(_render_agent_frame(_frame_rag, rag_dir=rag_dir))

    # 1. Carry-forward gate (fail-loud).
    ok, findings = _carry_forward_gate(
        rag_path, strict=args.strict, git_head=getattr(args, "git_head", None),
        rag_dir=rag_dir, new_sid=sid,
    )
    print("[1/4] Carry-forward gate:")
    if not ok and not getattr(args, "no_auto_reconcile", False):
        # GATE-AUTO-RECONCILE (S184). A gate that refuses at a HUMAN for a failure
        # the kernel can repair itself is not a safety control, it is a chore. The
        # split is not "safe vs unsafe" — it is DERIVED vs ASSERTED state:
        #
        #   DERIVED   regenerable from canonical with zero information loss —
        #             the boot-map baseline, the legacy render arrays, the
        #             current_status git-head snapshot. Repairing these cannot
        #             destroy a fact, because canonical already holds it.
        #   ASSERTED  a claim someone made — spec coherence, asset checksums,
        #             note/status agreement, an unsealed predecessor's missing
        #             summary. Repairing these would FABRICATE or ERASE a fact.
        #
        # Derived failures are repaired. Asserted failures stay fail-loud, forever.
        # And nothing is silent: every repair is named, with its before/after, so
        # self-healing never becomes self-concealing.
        repairs, ok, findings = _auto_reconcile_gate(
            rag_path, rag_dir, sid, findings,
            strict=args.strict, git_head=getattr(args, "git_head", None),
        )
        if repairs:
            print(f"  AUTO-RECONCILED — {len(repairs)} derived-state repair(s):")
            for rep in repairs:
                print(f"    · {rep}")
    if ok:
        print("  OK — inherited RAG coherent (verify + audit clean).")
    else:
        for fnd in findings:
            print(f"  FAIL — {fnd}", file=sys.stderr)
        if not getattr(args, "force", False):
            print(
                f"ERROR: refusing to start session {sid} — "
                "inherited state is not carry-forward clean.",
                file=sys.stderr,
            )
            print(
                "  These findings are ASSERTED state: repairing them automatically "
                "would fabricate or erase a fact, so they need a decision.",
                file=sys.stderr,
            )
            print(
                f"  Full boot transcript: {rag_dir / '.boot' / ('session_start_' + sid + '.log')}",
                file=sys.stderr,
            )
            return 1
        print(
            "WARNING: starting despite a failed carry-forward gate (--force).",
            file=sys.stderr,
        )

    # 2. gc dry-run (report-before-delete) + domain boot-map (ROOT-FILE-MANIFEST
    #    S168): the same root walk feeds the deterministic map, diffed against the
    #    sealed baseline and returned into context so the agent is never boot-blind.
    if not getattr(args, "no_gc", False):
        print("[2/4] GC (dry-run):")
        cmd_gc(argparse.Namespace(path=_boot_gc_root(args, rag_dir), dry_run=True))
    else:
        print("[2/4] GC: skipped (--no-gc).")
    try:
        from rag_kernel import bootmap
        # BOOTMAP-BOOTROOT-FIX (S170, E-074): the domain boot-map baseline is
        # ALWAYS sealed against the project root (refresh_baseline(rag_dir.parent,
        # ...)) and audited against p.parent.parent, so boot_root MUST be that same
        # project root regardless of --gc-path or CWD. The prior form keyed boot_root
        # off --gc-path (default Path(".") = CWD, always truthy -> the else branch
        # was dead): run per governance_runtime from RAG/, it walked RAG/ and diffed
        # RAG-relative paths against the project-root-keyed baseline, yielding a
        # spurious full +N/-M turnover. --gc-path governs ONLY the GC scan (above),
        # never the boot-map root.
        boot_root = rag_dir.parent
        print(f"[2/4] {bootmap.session_start_line(boot_root, rag_dir)}")
    except Exception as exc:  # boot-map is advisory at start; never block the open
        print(f"[2/4] Domain map: unavailable ({exc}).", file=sys.stderr)

    # GRAND-AUDIT-AT-BOOT (S190, P2) — prove the transports THIS session before
    # the session is allowed to believe anything else. Axis 1 is the audit's own
    # first law; a FAIL here means every measurement below it is unsafe, so the
    # boot REFUSES rather than opening a session that cannot be trusted.
    if not getattr(args, "no_boot_audit", False):
        _state, _lines = _boot_axis1_audit(rag_dir)
        if _state == "FAIL":
            print(
                "ERROR: GRAND-AUDIT-AT-BOOT — axis 1 (TOOL FITNESS) FAILED; "
                "refusing to open the session.\n"
                + "".join(f"  - {ln}\n" for ln in _lines)
                + "  Nothing measured below a broken transport is trustworthy "
                  "(the auditor's own L1). Repair the tool, or start with "
                  "--no-boot-audit and say so in the close.",
                file=sys.stderr,
            )
            return 1
        if _state == "UNKNOWN":
            print(f"[2/4] Boot audit (axis 1): UNKNOWN — {_lines[0] if _lines else ''} "
                  "(advisory: a missing or unfinished auditor is not a defect).",
                  file=sys.stderr)
        else:
            print(f"[2/4] Boot audit (axis 1): PASS — {_lines[0] if _lines else 'tools fit'}")
    else:
        print("[2/4] Boot audit (axis 1): SKIPPED (--no-boot-audit).")

    # 3a. Legacy one-shot bypass (UNSAFE) — kept for CI and emergencies.
    if getattr(args, "no_attest_gate", False):
        from rag_kernel.session_logger import SessionLogger

        print("[3/4] Rule-load gate: SKIPPED (--no-attest-gate, UNSAFE).")
        print("[4/4] Open logger:")
        logger = SessionLogger(sid, log_dir=rag_dir)
        logger.open()
        print(f"  Session {sid} started (UNGATED — rules not attested).")
        print(f"  Log file: {logger.log_path}")
        return 0

    # 3b. Render the rule digest into context + record the rule_load marker.
    try:
        with open(rag_path, "r", encoding="utf-8-sig") as f:
            rag = json.load(f)
    except (OSError, ValueError) as exc:
        print(f"ERROR: RAG unreadable for the rule digest ({exc}).", file=sys.stderr)
        return 1
    lines, token = _compute_rule_digest(rag)
    print(
        f"[3/4] Rule digest ({len(lines)} operating_protocol rules) — "
        "LOAD these into working context:"
    )
    print(_render_rule_digest(lines))
    _write_top_level_field(
        rag_path,
        "rule_load",
        {
            "session": sid,
            "attested": False,
            "token": token,
            "rule_count": len(lines),
            "started_utc": _utcnow_iso(),
            "attested_utc": None,
        },
    )

    # 3c. BOOT-GUARD (KA-20) — render the canonical boot-state briefing so the
    #     agent has every state fact WITHOUT reading RAG_MASTER.json directly (the
    #     E-071-class trigger), record the first-action boot-proof marker, and
    #     print the E-071-class notice.
    print("[BOOT-GUARD] Boot-state briefing (canonical — no direct RAG read needed):")
    print(_render_boot_briefing(rag, current_sid=sid))
    print(_BOOT_GUARD_NOTICE)
    _write_top_level_field(
        rag_path,
        "boot_guard",
        {
            "session": sid,
            "first_action_utc": _utcnow_iso(),
            "briefing_rendered": True,
            "source": "rag_kernel session-start (governed boot path)",
        },
    )

    # 4. Attestation required — the logger is deliberately NOT opened here.
    print("[4/4] Attestation REQUIRED (logger NOT opened — session not yet READY):")
    print(f"  Confirm you loaded the {len(lines)} rules above by re-running:")
    print(f"    session-start {sid} --attest {token}")
    return 0


# ---------------------------------------------------------------------------
# KA-16 — atomic, resumable session close
# ---------------------------------------------------------------------------
#
# The eBay S4 freeze was a NON-ATOMIC close: state was banked (seq advanced) but
# the close ritual then aborted, so the operator was stranded — state saved, no
# handoff, and nothing on disk said the close was unfinished. KA-16 makes the
# close a deterministic forward-progress transaction tracked by a single
# top-level ``session_close`` marker:
#
#   phase:  CHECKPOINTED -> CLOSED -> COMPLETE
#   transfer_ready: flips True ONLY at COMPLETE — i.e. after checkpoint +
#                   ERROR_LOG fold + logger close + audit have all passed.
#
# Every phase transition is an atomic, ``.bak``-mirrored write, so an interrupted
# close leaves a resumable record. ``session-resume`` (and the session-start
# carry-forward gate) read the one cheap ``transfer_ready`` field to tell a clean
# handoff from a stranded one — no log re-derivation. The ERROR_LOG append is
# folded INTO the governed checkpoint call (idempotent), retiring the fragile
# multi-Edit that failed at eBay S4.

CLOSE_PHASES = ("CHECKPOINTED", "CLOSED", "SURFACE_PENDING", "COMPLETE")


def _utcnow_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _error_log_has_id(path: Path, entry_id: str) -> bool:
    """True iff a prior close-fold for ``entry_id`` is already in ERROR_LOG.md."""
    marker = f"<!-- close-log-id: {entry_id} -->"
    try:
        return marker in path.read_text(encoding="utf-8")
    except OSError:
        return False


def _append_error_log(path: Path, text: str, entry_id: str) -> bool:
    """Idempotently append a close ERROR_LOG entry. Returns True if appended,
    False if an entry with this id was already present (resume/retry no-op).
    """
    marker = f"<!-- close-log-id: {entry_id} -->"
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    if marker in existing:
        return False
    block = f"\n{text.rstrip()}\n{marker}\n"
    with open(path, "a", encoding="utf-8") as f:
        f.write(block)
    return True


def _next_session_id(sid: str) -> str:
    """Best-effort NEXT session id for a directive's ``for_session``.

    Increments a trailing integer run (``S148`` -> ``S149``), preserving prefix
    and zero-pad width. A sid with no trailing digits gets a ``-next`` suffix so
    the directive still validates (non-empty str). Deterministic, stdlib-only.
    """
    import re
    m = re.search(r"(\d+)$", sid or "")
    if not m:
        return f"{sid}-next" if sid else "next"
    digits = m.group(1)
    nxt = str(int(digits) + 1).zfill(len(digits))
    return sid[: m.start(1)] + nxt


#: The canonical session-id shape: an alphabetic prefix followed by a bounded
#: counter. PHANTOM-SESSION-ID-S1786488555313 (S198): a millisecond timestamp
#: used as a fallback session id satisfies ``\d+$`` and so incremented cleanly
#: into ``S1786488555314`` — the derivation TOLERATED a malformed id instead of
#: refusing one, and the phantom propagated into a session log and three WAL
#: entries before the close audit complained.
#:
#: THE DIGIT BOUND IS THE PART THAT BITES, not the prefix. The ledger item asked
#: for the literal ``S<n>`` shape; that is deliberately widened here, because
#: this kernel ships to clones and at least one of them numbers its sessions
#: with a different prefix (``SESS0099``). Refusing those would fix a phantom in
#: this deployment by breaking derivation in every other one. What the phantom
#: actually needed was a LENGTH ceiling: nine digits admits any counter a real
#: project will reach (a billion sessions) while excluding both epoch forms that
#: fallback code reaches for — seconds are 10 digits, milliseconds 13. The
#: prefix must be alphabetic, so a bare timestamp with no prefix is refused too.
_SESSION_ID_RE = re.compile(r"^[A-Za-z]{1,8}\d{1,9}$")


def _derive_next_sid(rag_path: Path) -> "str | None":
    """AUTO-SID-DERIVE: the next session id = increment of meta.written_by_session.

    Reads ONLY through the kernel (the governed path — this is not the banned
    Cowork-sandbox read) and reuses ``_next_session_id`` so the increment rule is
    single-sourced. Returns ``None`` when the RAG is unreadable, when
    ``written_by_session`` is unset/empty, or when it does not match
    ``_SESSION_ID_RE`` (an alphabetic prefix plus a bounded counter) — in every
    one of those cases the caller must require an explicit id rather than guess.
    Deterministic, stdlib-only.

    SHAPE REFUSAL (PHANTOM-SESSION-ID-S1786488555313, S198): deriving from a
    malformed id is worse than refusing, because the derived id inherits the
    malformation and looks governed. ``S1786488555313`` — a millisecond
    timestamp — sat in ``meta.written_by_session`` at the S197 boot and this
    function happily produced a successor from it. Refusing is the only safe
    behaviour: an operator can always pass an explicit id, but nobody can undo a
    phantom id that has already been stamped into a session log and the WAL.
    """
    try:
        with open(rag_path, "r", encoding="utf-8-sig") as f:
            rag = json.load(f)
    except (OSError, ValueError):
        return None
    written_by = ((rag.get("meta") or {}).get("written_by_session") or "").strip()
    if not written_by:
        return None
    if not _SESSION_ID_RE.match(written_by):
        return None
    return _next_session_id(written_by)


def _build_close_marker(
    sid: str, phase: str, steps: dict, started_utc: str,
    completed_utc: "str | None", *, transfer_ready: bool = False,
    conduct: "list[str] | None" = None, conduct_measured: bool = False,
    conduct_accepted: "str | None" = None,
) -> dict:
    return {
        "session": sid,
        "phase": phase,
        "transfer_ready": transfer_ready,
        "started_utc": started_utc,
        "completed_utc": completed_utc,
        # FORENSICS-AS-GATE (S190): the conduct record travels with the seal, so a
        # successor can see what was accepted and on whose word, not just that the
        # marker said COMPLETE.
        "conduct": {
            "measured": bool(conduct_measured),
            "findings": list(conduct or []),
            "accepted_reason": conduct_accepted,
        },
        "steps": {
            "checkpoint": bool(steps.get("checkpoint")),
            "error_log": bool(steps.get("error_log")),
            "logger_close": bool(steps.get("logger_close")),
            "audit": bool(steps.get("audit")),
            "report_rendered": bool(steps.get("report_rendered")),
            "report_presented": bool(steps.get("report_presented")),
        },
    }


def _read_close_marker(rag_path: Path) -> "dict | None":
    import json as _json
    try:
        with open(rag_path, "r", encoding="utf-8-sig") as f:
            return _json.load(f).get("session_close")
    except (OSError, ValueError):
        return None


def _write_close_marker(rag_path: Path, marker: dict) -> None:
    """Persist the ``session_close`` marker, preserving ``.bak`` byte-parity.

    The marker write reuses the FIX-8 parity-mirror contract (mirror_bak=True) so
    that every phase transition keeps HOT == ``.bak`` — otherwise the close-time
    audit's parity check would fail on the intermediate-phase write.
    """
    import json as _json
    with open(rag_path, "r", encoding="utf-8") as f:
        rag = _json.load(f)
    rag["session_close"] = marker
    try:
        from rag_kernel.persistence import atomic_write_json
        atomic_write_json(rag_path, rag, mirror_bak=True)
    except ImportError:
        with open(rag_path, "w", encoding="utf-8") as f:
            _json.dump(rag, f, indent=2, ensure_ascii=False)


def _resolve_close_docs_root(
    rag_path: Path, args: argparse.Namespace
) -> "str | None":
    """KA-13: resolve the docs_root for the close-time Rule 11 reconciliation.

    Precedence (highest first), with a back-compatible skip when nothing is
    declared so an un-migrated RAG closes byte-for-byte as it does today:

      1. ``--no-reconcile``          -> None (explicit opt-out; skip the pass).
      2. ``--docs-root PATH``        -> that path (per-invocation override).
      3. ``meta.reconciliation_docs_root`` -> the project's declared surface root.
      4. (undeclared)                -> None (skip; identical to prior behaviour).

    A declared/override path may be absolute or relative; a relative path is
    resolved against the project root (``RAG/RAG_MASTER.json`` -> its grandparent,
    where ``RAG/`` and the published docs live), and ``~`` is expanded. The path
    is NOT required to exist here — :func:`reconciliation_surfaces` /
    :func:`audit_hot` already emit a WARNING for a missing surface, so a stale
    declaration fails loud through the audit rather than silently here.
    """
    if getattr(args, "no_reconcile", False):
        return None
    declared = getattr(args, "docs_root", None)
    if not declared:
        try:
            with open(rag_path, "r", encoding="utf-8") as f:
                meta = (json.load(f).get("meta") or {})
            candidate = meta.get("reconciliation_docs_root")
            if isinstance(candidate, str) and candidate.strip():
                declared = candidate.strip()
        except (OSError, ValueError):
            declared = None
    if not declared:
        return None
    dr = Path(declared).expanduser()
    if not dr.is_absolute():
        dr = (rag_path.resolve().parent.parent / dr)
    return str(dr)


def _close_report_ns(sid: str, args: argparse.Namespace) -> argparse.Namespace:
    """Build the namespace ``_build_report_text`` consumes for the close report.

    Keyed on the resolved ``sid`` (so a resume with no --session still renders a
    correct heading) and forwarding whatever report scalars the close was given;
    anything unset stays None -> renders n/a -> honestly pulls the verdict to AMBER.
    """
    return argparse.Namespace(
        session=sid,
        git_head=getattr(args, "git_head", None),
        no_live=False,
        tests=getattr(args, "tests", None),
        tests_failing=getattr(args, "tests_failing", False),
        released=getattr(args, "released", None),
        release_ref=getattr(args, "release_ref", None),
        claims_ok=getattr(args, "claims_ok", None),
        context_pct=getattr(args, "context_pct", None),
        milestone=getattr(args, "milestone", None),
        handoff=getattr(args, "handoff", None),
        no_report=getattr(args, "no_report", False),
        # CLOSE-STEP-ERRLOG gate (S188): the explicit "nothing to bank" declaration,
        # forwarded so the seal can tell a clean session from a silent one.
        no_errors=getattr(args, "no_errors", False),
        force=getattr(args, "force", False),
        # FORENSICS-AS-GATE (S190): the conduct declaration, forwarded so the
        # close can tell a declared burst from an unnoticed one.
        accept_conduct=getattr(args, "accept_conduct", None),
    )


# AUDIT-XFER-SURFACE-ATTEST (S154, F1) — the operator-facing transfer surface.
# The close machine-renders the attested canonical report (S139 WIRE-CLOSE); the
# helpers below PERSIST that exact text to a deterministic file so the agent
# presents the FILE verbatim instead of retyping it into chat (the S152 relay
# hole, where a bare-count paraphrase was pasted with a hand-appended token). The
# written text already carries its report-attest token, so a presented copy is
# re-checkable with `rag_kernel report --verify <file>`.
#: SESSION-DELTA-RITUAL (S199): per-session measured debit/credit, written beside
#: the canonical report at every close and by every clone, because the ritual
#: travels with the kernel rather than with whoever remembers to do it.
SESSION_DELTA_PREFIX = "SESSION_DELTA_"


def _emit_session_delta(rag_path: "Path", sid: str):
    """Write SESSION_DELTA_<sid>.md and seed the next session's baseline.

    Returns the path, or None when the delta could not be produced. Deliberately
    total: every failure mode here (unreadable store, missing git, no registry)
    degrades to None and the close continues. The delta is a REPORT — refusing a
    seal over it would make the seal hostage to a reporting convenience.
    """
    try:
        from rag_kernel import session_delta as _sd
        rag_dir = Path(rag_path).parent
        hot = json.loads(Path(rag_path).read_text(encoding="utf-8-sig"))
        counters = _sd.collect_counters(
            hot, rag_dir=rag_dir, project_root=rag_dir.parent,
            repo_root=_guess_repo_root(rag_dir),
        )
        delta = _sd.compute(hot, sid, baseline=_sd.load_baseline(rag_dir),
                            counters_after=counters)
        out = rag_dir / f"{SESSION_DELTA_PREFIX}{sid}.md"
        out.write_text(_sd.render(delta) + "\n", encoding="utf-8")
        _sd.save_baseline(
            rag_dir, sid, counters,
            item_ids=[str(i.get("id", "")) for i in (hot.get("tracked_items") or [])
                      if isinstance(i, dict)],
        )
        return out
    except Exception as ex:  # noqa: BLE001 — reporting must never fail a seal
        print(f"  (session-delta skipped: {ex})", file=sys.stderr)
        return None


CLOSE_REPORT_PREFIX = "AUDIT_CANONICAL_REPORT_"
CLOSE_REPORT_EXT = ".md"


def _close_report_artifact_path(rag_dir: Path, sid: str) -> Path:
    """Deterministic transfer-surface file for a session's canonical close report."""
    return rag_dir / f"{CLOSE_REPORT_PREFIX}{sid}{CLOSE_REPORT_EXT}"


def _write_close_report_artifact(rag_dir: Path, sid: str, report_text: str) -> Path:
    """Persist the attested close report to the deterministic transfer-surface file.

    Written atomically (tmp -> os.replace) with no ``.bak`` mirror — this is a
    DERIVED artifact, not canonical RAG state (which alone owns the parity-mirror
    contract). Returns the path so the close can point the operator/agent at the
    file to present VERBATIM (AUDIT-XFER-SURFACE-ATTEST / F1).
    """
    path = _close_report_artifact_path(rag_dir, sid)
    data = report_text if report_text.endswith("\n") else report_text + "\n"
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(data, encoding="utf-8")
    os.replace(tmp, path)
    return path


def _drive_close(
    rag_path: Path, rag_dir: Path, sid: str, *, summary: "str | None",
    tasks, status, strict: bool, git_head: "str | None",
    error_log_entry: "str | None", error_log_id: "str | None",
    error_log_path: "str | None", report_rendered: bool,
    marker: "dict | None", resuming: bool, docs_root: "str | None" = None,
    report_args: "argparse.Namespace | None" = None,
) -> int:
    """Run the close transaction forward from whatever step is incomplete.

    Shared by ``session-end`` (marker=None, fresh close) and ``session-resume``
    (marker=the interrupted record). Each completed step is persisted to the
    ``session_close`` marker BEFORE the next begins, so any abort is resumable.
    ``transfer_ready`` is set only after ALL four steps pass.
    """
    steps = dict(marker.get("steps", {})) if marker else {}
    started = (marker.get("started_utc") if marker else None) or _utcnow_iso()
    # KA-INTENT-FIDELITY inc1 — the stated handoff for this close (if any). The
    # checkpoint step persists it verbatim into next_session_directive; the gate
    # below re-verifies before the seal is allowed to proceed.
    handoff = getattr(report_args, "handoff", None) if report_args is not None else None

    # HANDOFF-CLAIMS-GATE (E-132, S199) — BEFORE the checkpoint persists this
    # text into next_session_directive, where it becomes the only thing the
    # successor loads at boot. The item's charge is exact: the handoff asserts
    # backlog facts and NOTHING checks them against tracked_items. Two recorded
    # instances: S197 stated a remedy that measurement had already ruled out,
    # and S197's chat summary and stored directive carried different P1
    # orderings with only the stored one rendered at boot. A wrong number in
    # here is not a typo — it is the successor's starting context.
    if handoff and not getattr(report_args, "handoff_claims_unchecked", False):
        try:
            from rag_kernel import session_delta as _sd
            _hot_now = json.loads(Path(rag_path).read_text(encoding="utf-8-sig"))
            _counters = _sd.collect_counters(
                _hot_now, rag_dir=rag_dir, project_root=Path(rag_dir).parent,
                repo_root=_guess_repo_root(Path(rag_dir)),
                audit_errors=None, audit_warnings=None,
            )
            _problems = _sd.check_handoff_claims(handoff, _counters)
        except Exception as ex:  # noqa: BLE001 — an unmeasurable gate must not brick a close
            print(f"  (handoff-claims gate skipped: {ex})", file=sys.stderr)
            _problems = []
        if _problems:
            print("ERROR: HANDOFF-CLAIMS-GATE (E-132) — the handoff states numbers "
                  "that disagree with the measured state:", file=sys.stderr)
            for p in _problems:
                print(f"  - {p}", file=sys.stderr)
            print("Correct the handoff, or pass --handoff-claims-unchecked if the "
                  "number is deliberately about something else (say which, in the "
                  "handoff). Nothing has been banked.", file=sys.stderr)
            return 1

    # Step 1/4 — checkpoint (+ idempotent ERROR_LOG fold).
    if not steps.get("checkpoint"):
        print("[1/4] Checkpoint (+ERROR_LOG fold):")
        rc = cmd_checkpoint(argparse.Namespace(
            rag=rag_path, session=sid, summary=summary, tasks=tasks,
            status=status, dry_run=False, error_log_entry=error_log_entry,
            error_log_id=error_log_id, error_log_path=error_log_path,
            handoff=handoff,
        ))
        if rc != 0:
            print(
                "ERROR: checkpoint failed — aborting before close/audit "
                "(no marker written; nothing banked).",
                file=sys.stderr,
            )
            return rc
        steps["checkpoint"] = True
        steps["error_log"] = bool(error_log_entry)
        if report_rendered:
            steps["report_rendered"] = True
        _write_close_marker(
            rag_path, _build_close_marker(sid, "CHECKPOINTED", steps, started, None)
        )
    else:
        print("[1/4] Checkpoint: already banked (resuming).")
        # CLOSE-RESUME-CAN-BANK-ERRLOG (S191, E-122). The ERROR_LOG fold lived
        # INSIDE the checkpoint step, and `steps["error_log"]` was only ever set
        # there. So a close that checkpointed without an entry could never
        # satisfy the CLOSE-STEP-ERRLOG gate on resume: the gate demanded an
        # entry, and the only code path that could bank one was skipped as
        # "already banked". S191 hit exactly that and the close became
        # unresumable — a resumable close that cannot be resumed is not
        # resumable. The fold is idempotent by design (it carries its own
        # id marker), so running it here is safe and is the whole point.
        if error_log_entry and not steps.get("error_log"):
            print("[1b] ERROR_LOG: banking the entry supplied on resume.")
            rc = cmd_checkpoint(argparse.Namespace(
                rag=rag_path, session=sid, summary=summary, tasks=tasks,
                status=status, dry_run=False, error_log_entry=error_log_entry,
                error_log_id=error_log_id, error_log_path=error_log_path,
                handoff=handoff,
            ))
            if rc != 0:
                print("ERROR: ERROR_LOG fold failed on resume — nothing banked.",
                      file=sys.stderr)
                return rc
            steps["error_log"] = True
            _write_close_marker(
                rag_path, _build_close_marker(sid, "CHECKPOINTED", steps, started, None)
            )

    # Step 1b — KA-INTENT-FIDELITY inc1 SEAL GATE. If this close STATED a handoff,
    # refuse to advance toward transfer_ready unless it was persisted VERBATIM as
    # the structured next_session_directive. An independent re-read + normalized-
    # exact match is the fail-loud guard for the E-055/S146 "directive stated but
    # never persisted (or persisted lossily)" class — the very failure this
    # feature exists to end. On a miss the marker stays CHECKPOINTED (resumable)
    # and transfer_ready is never set.
    if isinstance(handoff, str) and handoff.strip():
        from rag_kernel.schemas import directive_matches
        try:
            with open(rag_path, "r", encoding="utf-8-sig") as _f:
                _nsd = json.load(_f).get("next_session_directive")
        except (OSError, ValueError) as _exc:
            print(
                f"ERROR: intent-fidelity gate could not read RAG ({_exc}) — "
                "aborting close (marker CHECKPOINTED, resumable).",
                file=sys.stderr,
            )
            return 1
        _stored = _nsd.get("directive") if isinstance(_nsd, dict) else None
        if not directive_matches(handoff, _stored):
            print(
                "ERROR: intent-fidelity gate FAILED — the stated handoff is not "
                "persisted verbatim in next_session_directive; refusing to seal "
                "(marker CHECKPOINTED, resumable). This is the E-055/S146 guard "
                "(KA-INTENT-FIDELITY inc1).",
                file=sys.stderr,
            )
            return 1
        print(
            "[1b] intent-fidelity: next_session_directive persisted + "
            "verbatim-matched (KA-INTENT-FIDELITY inc1)."
        )

    # Step 1c REMOVED S186 (SEAL-BOOTMAP-ORDER-GAP): the domain boot-map
    # used to be resealed HERE, mid-ritual -- before the logger close and
    # before the report artifact were written. Everything produced after it
    # therefore landed OUTSIDE the sealed baseline, and the next boot opened
    # on a coverage gap. The reseal now runs LAST, after the transfer marker.

    # Step 2/4 — close the session logger (KA-4 gate satisfied by step 1).
    if not steps.get("logger_close"):
        print("[2/4] Close logger:")
        rc = cmd_session(argparse.Namespace(
            session_action="close", session_id=sid, rag_dir=rag_dir, force=False,
        ))
        if rc != 0:
            print(
                "ERROR: session close failed — marker left CHECKPOINTED "
                "(resume with `session-resume`).",
                file=sys.stderr,
            )
            return rc
        steps["logger_close"] = True
        _write_close_marker(
            rag_path, _build_close_marker(sid, "CLOSED", steps, started, None)
        )
    else:
        print("[2/4] Close logger: already closed (resuming).")

    # Step 3/4 — fail-loud audit. transfer_ready stays False if this is red.
    # KA-13: the Rule 11 published-doc reconciliation now runs at close when a
    # docs_root is resolved (declared meta.reconciliation_docs_root / --docs-root),
    # and stays skipped (docs_root=None) for an un-migrated RAG (back-compat).
    if not steps.get("audit"):
        if docs_root:
            print(f"[3/4] Audit (reconciling published docs under {docs_root}):")
        else:
            print("[3/4] Audit:")
        rc = cmd_audit(argparse.Namespace(
            rag=rag_path, strict=strict, scan_root=True, error_log=None,
            docs_root=docs_root, git_head=git_head, json_output=False,
        ))
        if rc != 0:
            print(
                "ERROR: post-close audit FAILED — governance state not clean; "
                "transfer_ready NOT set, marker left CLOSED (resumable).",
                file=sys.stderr,
            )
            return rc
        steps["audit"] = True
    else:
        print("[3/4] Audit: already green (resuming).")

    # Step 4/4 — commit completion. Marker write is pure data and touches no
    # audited invariant, so flipping it after a green audit cannot un-clean the
    # RAG (validate-then-commit-the-flag).
    #
    # S139 WIRE-CLOSE: the close now MACHINE-RENDERS the deterministic canonical
    # report from the just-checkpointed RAG as the mandated close artifact, so it
    # can never be hand-authored (the S136 close-drift root cause). Rendering IS
    # the attestation — report_rendered is set because the machine produced it.
    close_report = None
    close_report_path = None
    if report_args is not None and not getattr(report_args, "no_report", False):
        try:
            close_report = _build_report_text(rag_path, report_args)
            steps["report_rendered"] = True
            # AUDIT-XFER-SURFACE-ATTEST (F1) — persist the attested report to the
            # deterministic transfer-surface file so the agent presents the FILE
            # verbatim, never a retype. A write hiccup must not strand a green
            # close, so it is caught separately from the render.
            try:
                close_report_path = _write_close_report_artifact(
                    rag_dir, sid, close_report
                )
            except OSError as exc:
                print(f"  WARN: could not write close-report artifact: {exc}",
                      file=sys.stderr)
        except Exception as exc:  # a render hiccup must never strand a green close
            print(f"  WARN: could not machine-render close report: {exc}",
                  file=sys.stderr)
    if report_rendered:
        steps["report_rendered"] = True

    # ------------------------------------------------------------------
    # XFER-PRESENT-GATE (S178) — bind the seal to EMISSION, not to render.
    #
    # S177 sealed COMPLETE / transfer_ready=true while the operator had never
    # seen the canonical report: `report_rendered` was honestly True (the
    # machine DID produce the file) and nothing downstream cared whether the
    # bytes ever reached anyone. Rule 23 said "present the file verbatim" and
    # was rendered at every close — a behavioural instruction the seal did not
    # check. Rendering a discipline is not enforcing it.
    #
    # The fix is mechanical and costs no extra command: before the seal flips,
    # the close RE-READS the persisted artifact from disk and writes it to
    # stdout in full. `report_presented` records that emission, and
    # transfer_ready is unreachable without it. A close can no longer declare
    # itself transferable while its report sits unseen on disk.
    #
    # The report is emitted LAST, after every seal line, so the common
    # `| tail -N` idiom captures the REPORT rather than the instruction to go
    # read it — the exact truncation that hid it in S177.
    # ------------------------------------------------------------------
    if close_report_path is not None and not steps.get("report_presented"):
        # Marker parks at SURFACE_PENDING first: if emission dies mid-write the
        # close is resumable and provably un-sealed, never silently COMPLETE.
        _write_close_marker(
            rag_path,
            _build_close_marker(sid, "SURFACE_PENDING", steps, started, None),
        )
        try:
            _artifact = close_report_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            print(
                f"ERROR: XFER-PRESENT-GATE — could not re-read the canonical "
                f"report artifact ({exc}); transfer_ready NOT set (marker "
                f"SURFACE_PENDING, resumable).",
                file=sys.stderr,
            )
            return 1
        # AUTHENTICITY, not volume. The artifact on disk must match the render
        # this close just produced, byte for byte. A readable-but-drifted file
        # is a WORSE failure than a missing one — it hands over a plausible lie.
        if close_report is not None and _artifact.rstrip("\n") != close_report.rstrip("\n"):
            print(
                "ERROR: XFER-PRESENT-GATE — the persisted report artifact does "
                "NOT match the render produced by this close; transfer_ready "
                "NOT set (marker SURFACE_PENDING, resumable).",
                file=sys.stderr,
            )
            return 1
        _digest = hashlib.sha256(_artifact.encode("utf-8")).hexdigest()
        _lines = _artifact.count("\n")
        print("")
        print("=== AUDIT-XFER-SURFACE-ATTEST — canonical close report ===")
        print(f"  file   : {close_report_path}")
        print(f"  sha256 : {_digest}")
        print(f"  lines  : {_lines}")
        print(
            f"  verify : rag_kernel report --verify {close_report_path.name}"
        )
        print(
            "  PRESENT THIS FILE to the operator as a link/reference. Do NOT "
            "retype, paraphrase, or echo its body into the transcript — the "
            "file IS the transfer surface (token_economy / Rule 17)."
        )
        steps["report_presented"] = True
        close_report = None  # pointer emitted; never echo the body below

    # SESSION-DELTA-RITUAL (S199) — the second artifact every close owes.
    # The canonical report says what the deployment IS; the delta says what THIS
    # session CHANGED, measured. It was hand-written for 198 sessions and the
    # hand-written numbers drifted (S198 wrote "107 session logs" over a measured
    # 108). Emitted as a pointer, like the report, and never echoed: the file is
    # the surface (Rule 17). Never fatal — a close that refuses because git is
    # missing teaches people to skip the ritual, and a skipped ritual is how the
    # hand-written version survived this long.
    delta_path = _emit_session_delta(rag_path, sid)
    if delta_path is not None:
        print("")
        print("=== SESSION-DELTA-RITUAL — measured debit/credit for this session ===")
        print(f"  file   : {delta_path}")
        print(f"  re-run : rag_kernel session-delta --session {sid}")
        print("  PRESENT THIS FILE alongside the canonical report. The baseline "
              "for the NEXT session's delta was recorded by this same call.")
        steps["session_delta_emitted"] = True

    if close_report_path is not None and not steps.get("report_presented"):
        print(
            "ERROR: XFER-PRESENT-GATE — canonical report not emitted; "
            "transfer_ready NOT set (marker SURFACE_PENDING, resumable). "
            "Re-run `session-resume` to complete the seal.",
            file=sys.stderr,
        )
        return 1

    # ------------------------------------------------------------------
    # CLOSE-STEP-ERRLOG-UNENFORCED (S186; root-caused S188).
    #
    # `steps["error_log"]` has been RECORDED since S139 and CHECKED by nothing.
    # S184–S187 each sealed with it False. The S188 audit found the consequence:
    # ERROR_LOG.md's last write is 2026-07-29 (E-096, S183), while S187 named two
    # of its own errors to the operator in prose and banked neither. A step that
    # is recorded and never gates is not a step, it is a comment.
    #
    # REFUSE-BY-DEFAULT, in the house style: a close either banks an ERROR_LOG
    # entry or DECLARES there was nothing to bank (`--no-errors`). Absence of a
    # declaration is not permission. The declaration is cheap and honest; what is
    # no longer available is saying nothing at all.
    # ------------------------------------------------------------------
    if not steps.get("error_log") and not getattr(report_args, "no_errors", False) \
            and not getattr(report_args, "force", False):
        print(
            "ERROR: CLOSE-STEP-ERRLOG gate — this close banked no ERROR_LOG entry "
            "and did not declare that there was nothing to bank. transfer_ready "
            "NOT set (marker SURFACE_PENDING, resumable).\n"
            "  bank one : session-end --error-log-entry '<E-nnn (Snnn): ...>' "
            "--error-log-id E-nnn\n"
            "  or declare: session-end --no-errors   (asserts this session "
            "produced no error worth a record)\n"
            "  This gate exists because S184-S187 all sealed with error_log=false "
            "while ERROR_LOG.md went unwritten for four sessions "
            "(CLOSE-STEP-ERRLOG-UNENFORCED / E-088 recurrence).",
            file=sys.stderr,
        )
        _write_close_marker(
            rag_path,
            _build_close_marker(sid, "SURFACE_PENDING", steps, started, None),
        )
        return 1
    if not steps.get("error_log"):
        print("[3b] ERROR_LOG: none banked — DECLARED clean by --no-errors.")

    _surface = None
    if close_report_path is not None:
        try:
            _surface = Path(close_report_path).read_text(encoding="utf-8")
        except OSError:
            _surface = None
    _drift = _report_state_drift(rag_path, _surface)
    if _drift:
        print(
            f"ERROR: SEAL-REPORT-STALE-SURFACE -- {_drift}. transfer_ready "
            "NOT set (marker SURFACE_PENDING, resumable). Re-render the "
            "report against current state and re-run session-resume.",
            file=sys.stderr,
        )
        return 1

    # ------------------------------------------------------------------
    # CLOSE-TESTGATE-STALE-BLOCKS (S191, E-115) — the seal refuses an
    # unproven kernel.
    #
    # S190 ran the suite, THEN committed, and never re-measured. The stamp
    # said 2,509 green @ e8fbb96 while the kernel shipped at 9d68bf0, and the
    # commit in between carried `_boot_axis1_audit` with no `import
    # subprocess`. Every S191 boot died with a NameError before it could
    # render the operating frame. The grand audit DID catch it — "test gate
    # GREEN and current :: STALE (measured @ e8fbb96, live @ 9d68bf0)" — but
    # that check only reports, and the close never consults it, so the
    # finding cost a whole session anyway.
    #
    # A detector nobody consults is not a guard. `verdict` is already
    # tri-state and already compares the stamp against the LIVE head, so the
    # seal simply has to obey it: only True (measured, green, current) may
    # seal. None (UNMEASURED or STALE) and False (red) both refuse, with the
    # resumable marker shape the other close gates use.
    # ------------------------------------------------------------------
    try:
        from rag_kernel import test_gate as _tg
        from rag_kernel.drift_store import load_hot as _load_hot

        _hot = _load_hot(rag_path)
        _tg_ok, _tg_cell, _tg_why = _tg.verdict(
            _tg.read_stamp(_hot),
            live_head=_resolve_git_head(rag_path),
            live_runtime=(_hot.get("meta") or {}).get("runtime_version"),
        )
    except Exception as exc:  # noqa: BLE001 — a gate that cannot measure must not pass
        _tg_ok, _tg_cell, _tg_why = None, "n/a", f"test-gate probe raised {exc}"
    if _tg_ok is not True:
        print(
            f"ERROR: CLOSE-TESTGATE-STALE-BLOCKS -- test gate is {_tg_cell} "
            f"({_tg_why}). transfer_ready NOT set (marker SURFACE_PENDING, "
            "resumable). Run `rag_kernel tests --run` against the CURRENT "
            "tree, then re-run session-resume. A kernel that was never "
            "measured at the commit it ships from is the S190 defect that "
            "cost S191 its boot.",
            file=sys.stderr,
        )
        _write_close_marker(
            rag_path,
            _build_close_marker(sid, "SURFACE_PENDING", steps, started, None),
        )
        return 1

    # ------------------------------------------------------------------
    # FORENSICS-AS-GATE (S190, P1-A) — the conduct facts now BLOCK the seal.
    #
    # Until now this ran AFTER the transfer marker, and two comments there
    # declared it advisory by design — it was allowed to print and forbidden to
    # act. (The phrases are quoted in E-106 and in the S189 audit, not here: a
    # gate that repeats them in its own source is what the wiring axis looks
    # for.) S188 sealed GREEN while its own forensics printed two polling
    # bursts and 1h09 of silence; S189 would have been refused three times over.
    # The evidence was produced every session and thrown away every session.
    # It now runs BEFORE the marker, and a close carrying repeat bursts,
    # undeclared failed calls, or excess silent gaps REFUSES unless the operator
    # DECLARES it with --accept-conduct <reason>, which is recorded in the
    # marker. Un-measurable conduct is blocking too (L2: UNKNOWN is not a pass).
    # ------------------------------------------------------------------
    _accept_conduct = getattr(report_args, "accept_conduct", None)
    _conduct: "list[str]" = []
    _conduct_measured = False
    try:
        from rag_kernel import session_forensics as _sf
        from rag_kernel.session_logger import LOG_FILE_PREFIX, LOG_FILE_EXT
        _log = rag_dir / f"{LOG_FILE_PREFIX}{sid}{LOG_FILE_EXT}"
        if _log.exists():
            _f = _sf.analyze_file(_log)
            print("")
            print(_sf.render_text(_f))
            print("")
            _conduct = _sf.conduct_findings(_f)
            _conduct_measured = True
        else:
            _conduct = [f"session log absent ({_log.name}) — conduct not measurable"]
    except Exception as _sf_exc:  # noqa: BLE001 — a probe that failed is not a pass
        _conduct = [f"forensics could not run: {_sf_exc}"]

    if _conduct and not _accept_conduct:
        print(
            "ERROR: FORENSICS-AS-GATE — this close is REFUSED on its own conduct "
            "record. transfer_ready NOT set (marker SURFACE_PENDING, resumable).\n"
            + "".join(f"  - {c}\n" for c in _conduct)
            + "  Fix the conduct, or DECLARE it:\n"
            "    session-end --accept-conduct '<why these numbers are acceptable>'\n"
            "  The declaration is recorded in the close marker and travels with "
            "the seal. Advisory forensics is how S188 sealed green over two "
            "polling bursts and 1h09 of silence (FORENSICS-ADVISORY-ONLY, S189).",
            file=sys.stderr,
        )
        _write_close_marker(
            rag_path,
            _build_close_marker(sid, "SURFACE_PENDING", steps, started, None,
                                conduct=_conduct, conduct_measured=_conduct_measured),
        )
        return 1
    if _conduct:
        print("[4/4] CONDUCT DECLARED (gate overridden and recorded):")
        for _c in _conduct:
            print(f"  - {_c}")
        print(f"  reason: {_accept_conduct}")
    else:
        print("[4/4] Conduct gate: clean — no bursts, no undeclared failures, "
              "gaps within allowance.")

    # ------------------------------------------------------------------
    # SEAL-INTERVAL-RECHECK (S192, E-123) — the LAST thing before the seal.
    #
    # This is the guard E-115 could not be. E-115 evaluates the test gate at the
    # top of the close and is correct at that instant; it says nothing about the
    # interval between that instant and this line. S191 spent an entire session
    # fixing "the running kernel exists in no commit", passed E-115, then edited
    # __main__.py while the close was still running and sealed COMPLETE over an
    # uncommitted kernel. The gate did not fail. The gate simply was not looking
    # at the moment that mattered.
    #
    # So the probes run AGAIN here, with nothing between them and the write:
    # test gate still green at the live HEAD, worktree still clean, deployed
    # kernel still identical to the committed one, no E-number left unbanked.
    # A seal now means those four facts were true at the instant it was taken —
    # which is the only reading of "sealed" that survives being handed to
    # somebody else.
    #
    # There is deliberately no --force here. Everything reachable by --force is
    # reachable by fixing the tree, and a seal is the one artifact the next
    # session cannot re-derive.
    # ------------------------------------------------------------------
    _interval = _interval_probes(rag_path, rag_dir=rag_dir, git_head=git_head)
    if _interval:
        print(
            "ERROR: SEAL-INTERVAL-RECHECK — the tree moved during this close. "
            "transfer_ready NOT set (marker SURFACE_PENDING, resumable).\n"
            + "".join(f"  - {f}\n" for f in _interval)
            + "  These were re-measured with nothing between them and the seal. "
            "E-115 proves the kernel at the moment the close BEGINS; this proves "
            "it at the moment the close ENDS. S191 passed the first and failed "
            "the second, and sealed anyway (E-123).\n"
            "  Fix each finding, then re-run `session-resume`.",
            file=sys.stderr,
        )
        _write_close_marker(
            rag_path,
            _build_close_marker(sid, "SURFACE_PENDING", steps, started, None,
                                conduct=_conduct, conduct_measured=_conduct_measured),
        )
        return 1
    print("[4/4] Seal-interval recheck: clean — gate green at live HEAD, worktree "
          "committed, deployment in parity, no orphaned E-numbers.")

    _write_close_marker(
        rag_path,
        _build_close_marker(
            sid, "COMPLETE", steps, started, _utcnow_iso(), transfer_ready=True,
            conduct=_conduct, conduct_measured=_conduct_measured,
            conduct_accepted=_accept_conduct,
        ),
    )
    print("[4/4] Transfer marker: transfer_ready=true (phase COMPLETE).")

    # Step 5/5 (S186, SEAL-BOOTMAP-ORDER-GAP) -- reseal the domain boot-map
    # LAST, once every artifact this close produces exists on disk: the
    # session log, the canonical report and the transfer marker. Sealing
    # earlier is what handed a successor a coverage gap on an otherwise
    # clean close.
    #
    # BOOTMAP-ADVISORY-ENDED (S190). This step used to WARN and carry on, under
    # "a map hiccup must not strand a green seal" — the same sentence that kept
    # forensics toothless until this session. It was the last advisory-only
    # governance module the wiring axis could name. A close whose map did not
    # reseal hands the successor a coverage gap, which is exactly the defect
    # this step exists to prevent, so it now REFUSES. Nothing is stranded: the
    # marker goes back to SURFACE_PENDING and `session-resume` finishes the
    # close once the map can be written.
    try:
        from rag_kernel import bootmap
        _bm_path = bootmap.refresh_baseline(rag_dir.parent, rag_dir, sid)
        bootmap.ensure_meta_pointer(rag_path)
        print(f"[5/5] Domain map resealed LAST: {_bm_path.name} (+.bak parity).")
    except Exception as _bm_exc:
        print(
            f"ERROR: SEAL-BOOTMAP-ORDER-GAP — could not reseal the domain "
            f"boot-map: {_bm_exc}. transfer_ready WITHDRAWN (marker "
            "SURFACE_PENDING, resumable). A successor booting on a stale map is "
            "boot-blind over every file this session touched; fix the map and "
            "run `session-resume`.",
            file=sys.stderr,
        )
        _write_close_marker(
            rag_path,
            _build_close_marker(sid, "SURFACE_PENDING", steps, started, None,
                                conduct=_conduct, conduct_measured=_conduct_measured,
                                conduct_accepted=_accept_conduct),
        )
        return 1
    # SELF-DIAGNOSIS-UNSOURCED (S188) — the session's CONDUCT facts were rendered
    # ABOVE, before the transfer marker, because since S190 they gate the seal
    # rather than decorate it (FORENSICS-AS-GATE). Do not move them back down.

    verb = "resumed and completed" if resuming else "ended cleanly"
    print(
        f"Session {sid} {verb}: checkpoint + ERROR_LOG + close + audit all green; "
        "transfer_ready set."
    )
    if close_report is not None:
        print("")
        print("=== Canonical status report (Rule 12 — machine-rendered at close) ===")
        print(close_report)
        if close_report_path is not None:
            print("")
            print(
                "AUDIT-XFER-SURFACE-ATTEST — the report above was written VERBATIM to:"
            )
            print(f"  {close_report_path}")
            print(
                "PRESENT THAT FILE to the operator verbatim (do NOT retype or "
                "paraphrase it into chat); confirm authenticity with "
                f"`rag_kernel report --verify {close_report_path.name}`."
            )
    elif not steps.get("report_rendered"):
        print(
            "  NOTE: close report not machine-rendered (--no-report) and not "
            "attested — render it in chat (Rule 12).",
            file=sys.stderr,
        )
    return 0


# ---------------------------------------------------------------------------
# CLOSE-DOUBLE-SEAL (S187, found by the S188 forensic audit)
# ---------------------------------------------------------------------------
#
# session_log_S187.jsonl carries TWO session_end records — seq 63 at 15:27:18Z and
# seq 72 at 15:31:02Z — with eight canonical mutations between them (un-add ×2,
# add ×2, note, bootmap, render, audit). The first seal therefore attested a state
# that then changed underneath it, which is the same class of lie as a stale report
# surface: the artifact is authentic and the state it describes is gone.
#
# SEAL-REPORT-STALE-SURFACE catches the report drifting from state. Nothing caught
# STATE drifting after the seal. This does: once a session is sealed COMPLETE with
# transfer_ready=true, a mutating verb naming THAT SAME session is refused. The
# repair is named in the refusal — either start the next session (the normal path)
# or reopen this one through `session-resume`, which un-sets transfer_ready and
# makes the close resumable again.
#
# Read-only verbs are untouched: inspecting a sealed session must always be free.

#: Verbs that write canonical state and are therefore refused after a seal.
_SEAL_GUARDED_VERBS = frozenset({
    "add", "un-add", "resolve", "start", "defer", "reopen", "discard", "supersede",
    "note", "cite", "priority", "add-rule", "update-rule", "refresh-current-status",
    "prune-current-status", "meta", "register-asset", "decide", "ingest",
    "checkpoint", "migrate", "transplant", "birth-adopt", "dedup-sessions",
    "errlog-migrate",
})


def _refuse_mutation_after_seal(command: str, args: argparse.Namespace):
    """Refuse a canonical write aimed at an already-sealed session. -> ``None`` | rc.

    Returns ``None`` when the call may proceed (the overwhelmingly common case), or
    an exit code when it must not. Deliberately fail-OPEN on any inability to read
    the marker: a guard that cannot read state must not become an outage, and every
    downstream verb still has its own guards.
    """
    if command not in _SEAL_GUARDED_VERBS:
        return None
    session = getattr(args, "session", None)
    if not session:
        return None
    rag = getattr(args, "rag", None)
    if rag is None:
        return None
    try:
        hot = json.loads(Path(rag).read_text(encoding="utf-8-sig"))
        marker = hot.get("session_close")
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(marker, dict):
        return None
    if not marker.get("transfer_ready", False):
        return None
    if str(marker.get("session_id") or marker.get("session") or "") != str(session):
        return None

    print(
        f"ERROR: CLOSE-DOUBLE-SEAL guard — session {session} is already sealed "
        f"COMPLETE (transfer_ready=true, sealed "
        f"{marker.get('completed_utc') or marker.get('ended_utc') or 'earlier'}). "
        f"Refusing `{command}`: a write after the seal makes the sealed report and "
        f"the sealed boot-map describe a state that no longer exists — the S187 "
        f"double-close defect.\n"
        f"  repair: run the next session (`session-start`) and bank it there, or "
        f"re-open this close with `session-resume` if the seal was premature.",
        file=sys.stderr,
    )
    return 1


def _report_state_drift(rag_path, report_text: str):
    """Return a drift message if the rendered report no longer describes reality.

    SEAL-REPORT-STALE-SURFACE (S186). A close artifact is a transfer surface
    only while it describes the state at the moment of sealing. S185 sealed a
    report naming seq 284 while the live RAG was at seq 285, and the child
    deployment reproduced the same defect the same day, which makes it a defect
    in the ritual ORDER rather than a local slip. The report is rendered before
    the transfer marker is written, so anything that moves state in between
    turns the surface into a plausible lie -- strictly worse than a missing one.
    """
    import json as _j
    import re as _re
    m = _re.search(r"seq\s+(\d+)", report_text or "")
    if not m:
        return None
    try:
        live = _j.loads(Path(rag_path).read_text(encoding="utf-8"))
        live_seq = (live.get("meta") or {}).get("last_checkpoint_seq")
    except Exception:
        return None
    if live_seq is None or int(m.group(1)) == int(live_seq):
        return None
    return (
        f"report asserts seq {m.group(1)} but the live RAG is at seq {live_seq}; "
        f"the state moved after the report was rendered"
    )


def cmd_session_end(args: argparse.Namespace) -> int:
    """KA-16 — machine-enforced, ATOMIC, RESUMABLE session-END ritual.

    Runs the close as a forward-progress transaction (checkpoint(+ERROR_LOG fold)
    -> close logger -> audit -> commit transfer_ready) tracked by the
    ``session_close`` marker. Any step's non-zero exit aborts the rest and leaves
    a resumable marker — a session can never end half-ritualed AND silently
    (the eBay S4 stranding is structurally unreachable, and what remains IS
    resumable via ``session-resume``).
    """
    rag_path = args.rag.resolve()
    rag_dir = rag_path.parent
    sid = args.session

    marker = _read_close_marker(rag_path)
    # A DIFFERENT prior session left an unfinished close — resume that first.
    if (
        isinstance(marker, dict)
        and not marker.get("transfer_ready", False)
        and marker.get("session") not in (None, sid)
    ):
        print(
            f"ERROR: an incomplete close for session {marker.get('session')} "
            f"(phase {marker.get('phase')}) is pending — resume it before ending {sid}.",
            file=sys.stderr,
        )
        print(f'  Run:  rag_kernel session-resume --rag "{rag_path}"', file=sys.stderr)
        return 1
    # Reuse the marker only if it is THIS session's own interrupted close.
    active = (
        marker
        if (
            isinstance(marker, dict)
            and marker.get("session") == sid
            and not marker.get("transfer_ready", False)
        )
        else None
    )
    return _drive_close(
        rag_path, rag_dir, sid, summary=args.summary, tasks=args.tasks,
        status=args.status, strict=args.strict,
        git_head=getattr(args, "git_head", None),
        error_log_entry=getattr(args, "error_log_entry", None),
        error_log_id=getattr(args, "error_log_id", None),
        error_log_path=getattr(args, "error_log_path", None),
        report_rendered=getattr(args, "report_rendered", False),
        marker=active, resuming=False,
        docs_root=_resolve_close_docs_root(rag_path, args),
        report_args=_close_report_ns(sid, args),
    )


def cmd_session_resume(args: argparse.Namespace) -> int:
    """KA-16 — detect and RESUME an interrupted session close.

    Reads the ``session_close`` marker; if it is incomplete (transfer_ready
    False) it drives the remaining steps to COMPLETE. A no-op (exit 0) when there
    is nothing to resume. If the close was interrupted before the checkpoint ever
    landed, ``--summary`` is required to bank the session.
    """
    rag_path = args.rag.resolve()
    rag_dir = rag_path.parent
    marker = _read_close_marker(rag_path)

    if not isinstance(marker, dict) or marker.get("transfer_ready", False):
        why = "no session_close marker" if not isinstance(marker, dict) else (
            "last close is COMPLETE (transfer_ready=true)"
        )
        print(f"No incomplete close to resume — {why}.")
        return 0

    sid = args.session or marker.get("session")
    if not sid:
        print(
            "ERROR: marker carries no session id and --session was not given.",
            file=sys.stderr,
        )
        return 1

    steps = marker.get("steps", {})
    if not steps.get("checkpoint") and not args.summary:
        print(
            f"ERROR: the close for {sid} was interrupted BEFORE checkpoint; "
            "re-run `session-resume` with --summary to bank it.",
            file=sys.stderr,
        )
        return 1

    print(
        f"Resuming incomplete close for {sid} "
        f"(phase {marker.get('phase')}, transfer_ready=false)."
    )
    return _drive_close(
        rag_path, rag_dir, sid, summary=args.summary,
        tasks=getattr(args, "tasks", None), status=getattr(args, "status", None),
        strict=args.strict, git_head=getattr(args, "git_head", None),
        error_log_entry=getattr(args, "error_log_entry", None),
        error_log_id=getattr(args, "error_log_id", None),
        error_log_path=getattr(args, "error_log_path", None),
        report_rendered=getattr(args, "report_rendered", False),
        marker=marker, resuming=True,
        docs_root=_resolve_close_docs_root(rag_path, args),
        report_args=_close_report_ns(sid, args),
    )


#: An ERROR_LOG entry number. The same vocabulary the continuity axis counts with,
#: deliberately identical so the migration and the auditor can never disagree.
_ERRLOG_NUM = re.compile(r"\bE-\d{3}\b")


def parse_errlog(text: str) -> "list[tuple[str, str]]":
    """Every E-number in an ERROR_LOG.md, with the line that introduces it.

    ERRLOG-MIGRATION (S190, P2). The ledger's continuity axis requires every
    ERROR_LOG entry to exist as a tracked item; 106 of 106 were orphaned, so the
    error record and the canonical item store had no edge between them at all.
    (S188 reported 44 — it counted headings of one shape and stopped.)

    Returns ``[(e_number, title)]`` in first-appearance order, deduplicated. The
    title is the introducing line stripped of markdown furniture; when an
    E-number first appears mid-prose the title is that sentence, which is still
    a truer record than a synthesized one.
    """
    seen: "dict[str, str]" = {}
    for raw in (text or "").splitlines():
        # EVERY E-number on the line, not just the first: a line that renumbers
        # one error in terms of another carries two, and taking one of them is
        # how a count of 106 becomes a count of 103.
        nums = _ERRLOG_NUM.findall(raw)
        if not nums:
            continue
        title = raw.strip().lstrip("#").strip().strip("*_ ").replace("`", "")
        for num in nums:
            if num not in seen:
                seen[num] = title[:180] or num
    return list(seen.items())


def cmd_acceptance(args: argparse.Namespace) -> int:
    """Boot-readiness acceptance check for every deployment (P3 wiring, S190).

    ``scripts/acceptance_check.py`` has existed since S178 and answered the one
    question ``audit`` cannot — *would a successor session actually start?* — by
    driving verify / audit / boot-map coverage / identity / seal / a real
    read-only ``session-start`` against the kernel and every registered
    deployment. Nothing referenced it, so the S189 census counted it abandoned
    at 11 days old and the P3 instruction was: wire it or delete it.

    It is wired. The script stays the single authority (no second copy to
    drift); this verb is the edge that makes it reachable, and callable from a
    close or a CI job the same way every other governed check is.
    """
    script = Path(args.script) if args.script else (args.rag.resolve().parent
                                                    / "scripts" / "acceptance_check.py")
    if not script.exists():
        print(f"Error: acceptance checker not found: {script}", file=sys.stderr)
        return 1
    try:
        r = subprocess.run([sys.executable, str(script)], text=True,
                           timeout=args.timeout, cwd=str(script.parent.parent))
    except subprocess.TimeoutExpired:
        print(f"Error: acceptance check did not finish within {args.timeout}s "
              "— no conclusion (L1: an unfinished probe is not a failure).",
              file=sys.stderr)
        return 1
    return r.returncode


def cmd_errlog_migrate(args: argparse.Namespace) -> int:
    """Fold every ERROR_LOG.md entry into tracked_items as ``kind=ERROR``.

    ONE atomic write for the whole migration, not one governed call per record:
    a hundred sequential CLI writes would be a hundred chances to half-finish,
    and would read as a polling burst in the session's own forensics.

    Status is RESOLVED and the note says exactly what that asserts — that the
    incident is RECORDED, not that any fix was re-verified. Overstating it here
    would be the same disease as the 131 evidence-free RESOLVED items this
    session is closing. Re-running is idempotent (existing ids are skipped).
    """
    from rag_kernel.drift_store import DriftStoreError, add_items_file, load_hot

    rag_path = args.rag.resolve()
    if not rag_path.exists():
        print(f"Error: RAG file not found: {rag_path}", file=sys.stderr)
        return 1
    log_path = Path(args.error_log) if args.error_log else rag_path.parent / "ERROR_LOG.md"
    if not log_path.exists():
        print(f"Error: ERROR_LOG not found: {log_path}", file=sys.stderr)
        return 1

    entries = parse_errlog(log_path.read_text(encoding="utf-8", errors="replace"))
    try:
        hot = load_hot(rag_path)
    except DriftStoreError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    existing = {str(it.get("id")) for it in (hot.get("tracked_items") or [])}
    missing = [(n, t) for n, t in entries if n not in existing]

    print(f"ERROR_LOG: {log_path}")
    print(f"  E-numbers found : {len(entries)}")
    print(f"  already tracked : {len(entries) - len(missing)}")
    print(f"  to migrate      : {len(missing)}")
    if not missing:
        print("  Nothing to do — the error record and the item store agree.")
        return 0
    for n, t in missing[:10]:
        print(f"    {n}  {t[:96]}")
    if len(missing) > 10:
        print(f"    … {len(missing) - 10} more")
    if args.dry_run:
        print("  [DRY RUN] nothing written.")
        return 0

    specs = [{
        "id": n,
        "title": t,
        "status": "RESOLVED",
        "kind": "ERROR",
        "session": args.session,
        "note": f"record migrated from {log_path.name} (S190); RESOLVED asserts "
                "the incident is RECORDED, not that a fix was re-verified",
    } for n, t in missing]
    try:
        add_items_file(rag_path, specs, allow_existing=True)
    except DriftStoreError as e:
        print(f"Error: {e} — nothing written.", file=sys.stderr)
        return 1
    print(f"  Migrated {len(specs)} ERROR_LOG entries into tracked_items "
          "(kind=ERROR, one atomic write, .bak refreshed).")
    return 0


def _boot_axis1_audit(rag_dir: Path, timeout: int = 240) -> "tuple[str, list[str]]":
    """Run the grand audit's axis 1 (TOOL FITNESS) as a boot gate.

    GRAND-AUDIT-AT-BOOT (S190, P2). Axis 1 is the audit's own first law: nothing
    measured below it is trustworthy until the transports are proven to work THIS
    session. It is also the only axis cheap enough to run at every boot, so the
    gate selects it with ``--only 1`` rather than taxing the operator with all
    eleven.

    Returns ``(state, lines)`` where state is:
      ``"OK"``      — axis 1 ran, no FAIL;
      ``"FAIL"``    — axis 1 ran and found defects (lines name them): REFUSE;
      ``"UNKNOWN"`` — the probe itself did not complete, or the script is absent.
                      Advisory: a missing auditor must not brick the project, and
                      an unfinished probe may not produce a finding (L1).
    """
    import subprocess

    script = Path(rag_dir) / "scripts" / "grand_audit.py"
    if not script.exists():
        return "UNKNOWN", [f"auditor not found at {script}"]
    try:
        r = subprocess.run(
            [sys.executable, str(script), "--only", "1", "--fast",
             "--root", str(Path(rag_dir).resolve().parent)],
            capture_output=True, text=True, timeout=timeout, cwd=str(rag_dir),
        )
    except subprocess.TimeoutExpired:
        return "UNKNOWN", [f"axis-1 probe did not finish within {timeout}s"]
    except Exception as exc:  # noqa: BLE001 — an auditor crash is not a verdict
        return "UNKNOWN", [f"axis-1 probe raised {exc}"]
    out = (r.stdout or "") + (r.stderr or "")
    # The report lists defects under "FAIL detail:" and unfinished probes under
    # "UNKNOWN detail:". Only the first section refuses a boot (L1: an
    # unfinished probe is not a defect).
    section, fails = None, []
    for ln in out.splitlines():
        s = ln.strip()
        if s.startswith("FAIL detail"):
            section = "FAIL"; continue
        if s.startswith("UNKNOWN detail"):
            section = "UNKNOWN"; continue
        if section == "FAIL" and s.startswith("[1-") and "::" in s:
            fails.append(s)
    if fails:
        return "FAIL", fails
    if "1-TOOLS" not in out:
        return "UNKNOWN", ["axis-1 probe produced no rows"]
    return "OK", [ln.strip() for ln in out.splitlines() if "RESULT:" in ln]


def _boot_gc_root(args: argparse.Namespace, rag_dir: Path) -> Path:
    """The root the boot-time GC sweep walks. ONE authority, so it can be probed.

    GC-BOOTROOT-FIX (S190, P1-B). The sweep used to inherit ``--gc-path``'s
    default of ``Path(".")``, i.e. the CWD, and the sanctioned boot runs from
    ``RAG/``. So the collector spent its whole life scanning the RAG directory
    while the project root above it accumulated 283 MB of TLC state and 17
    abandoned files. S189 measured it: gc(root)=3 items vs gc(RAG)=1.

    The default is now the project root — the parent of the RAG directory, the
    same root ``bootmap`` already seals against (BOOTMAP-BOOTROOT-FIX, S170).
    An explicit ``--gc-path`` still wins, so the override survives.
    """
    override = getattr(args, "gc_path", None)
    return Path(override).resolve() if override else Path(rag_dir).resolve().parent


def cmd_gc(args: argparse.Namespace) -> int:
    """Garbage collector — scan and clean temp artifacts within project root.

    Targets (COLLECTED — removed on a non-dry run):
    - __pycache__/ directories and .pyc files
    - .tmp files
    - Orphaned single-digit/short numeric files at project root (stdout captures)
    - .bat files (Desktop Commander artifacts)
    - TLC model-checking state directories under formal/states/ (S190: 283 MB of
      regenerable metadata that no prior sweep had a word for), skipping any dir
      touched in the last two hours so a live run is never collected
    - .boot/*.log transcripts older than BOOT_LOG_AGE_DAYS

    Targets (SEEN — reported, never deleted; P3: archive, never blind-delete):
    - zero-byte files (S189 planted zero.log and the collector did not see it)
    - one-off session-stamped scripts, e.g. ``foo_s188_probe.py``

    Always reports before deleting. In --dry-run mode, reports only. The report is
    the point: a collector that cannot NAME a thing cannot be said to know it
    exists, which is how 17 files reached 96 days old inside a governed project.
    """
    import re

    project_root = args.path.resolve()
    dry_run = args.dry_run

    #: A boot transcript older than this is history, not context.
    BOOT_LOG_AGE_DAYS = 30
    #: A TLC state dir touched more recently than this may belong to a live run.
    TLC_ACTIVE_SECONDS = 2 * 3600
    #: Zero-byte files that MEAN zero bytes.
    ZERO_BYTE_KEEP = {"__init__.py", ".gitkeep", ".keep", "py.typed", ".gitignore"}
    #: A one-off: `<something>_s188_<something>.py`, the shape P3 census counted.
    ONE_OFF_SCRIPT = re.compile(r"_s\d{2,4}(?:_|\.)", re.I)
    now = time.time()

    print(f"RAG Runtime Kernel - Garbage Collector")
    print(f"Scanning: {project_root}")
    if dry_run:
        print("[DRY RUN] No files will be deleted.\n")
    else:
        print()

    findings: dict[str, list[str]] = {
        "pycache_dirs": [],
        "pyc_files": [],
        "tmp_files": [],
        "orphan_files": [],
        "bat_files": [],
        "tlc_state_dirs": [],
        "aged_boot_logs": [],
    }
    # SEEN-NOT-COLLECTED: named in the report, never removed by this verb.
    review: dict[str, list[str]] = {
        "zero_byte_files": [],
        "one_off_scripts": [],
    }

    for dirpath, dirnames, filenames in os.walk(project_root):
        rel = os.path.relpath(dirpath, project_root)

        # Skip .venv, .git, node_modules
        skip_dirs = {".venv", ".git", "node_modules", ".playwright-mcp"}
        # An archive is a decision already taken (P3, S190): its contents were
        # examined, recorded in a manifest and moved out of the live tree. Re-
        # flagging them every sweep would train the reader to ignore the report.
        dirnames[:] = [d for d in dirnames
                       if d not in skip_dirs and not d.startswith("_ARCHIVE")]

        # __pycache__ directories
        if os.path.basename(dirpath) == "__pycache__":
            findings["pycache_dirs"].append(rel)
            continue

        # TLC state directories: formal/states/<run>/ — regenerable metadata, and
        # the single largest thing this collector never had a word for.
        if os.path.basename(os.path.dirname(dirpath)) == "states" \
                and "formal" in rel.replace("\\", "/").split("/"):
            try:
                idle = now - os.path.getmtime(dirpath)
            except OSError:
                idle = 0
            if idle > TLC_ACTIVE_SECONDS:
                findings["tlc_state_dirs"].append(rel)
                dirnames[:] = []
                continue

        for fname in filenames:
            fpath = os.path.join(dirpath, fname)
            rel_file = os.path.relpath(fpath, project_root)

            # .pyc files outside __pycache__
            if fname.endswith(".pyc"):
                findings["pyc_files"].append(rel_file)

            # .tmp files
            elif fname.endswith(".tmp"):
                findings["tmp_files"].append(rel_file)

            # .bat files (Desktop Commander artifacts) at project root only
            elif fname.endswith(".bat") and dirpath == str(project_root):
                findings["bat_files"].append(rel_file)

            # aged boot transcripts
            elif os.path.basename(dirpath) == ".boot" and fname.endswith(".log"):
                try:
                    age_days = (now - os.path.getmtime(fpath)) / 86400.0
                except OSError:
                    age_days = 0.0
                if age_days > BOOT_LOG_AGE_DAYS:
                    findings["aged_boot_logs"].append(
                        f"{rel_file} ({age_days:.0f}d)")

            # SEEN, not collected
            if fname.endswith(".py") and ONE_OFF_SCRIPT.search(fname):
                review["one_off_scripts"].append(rel_file)
            try:
                if os.path.getsize(fpath) == 0 and fname not in ZERO_BYTE_KEEP:
                    review["zero_byte_files"].append(rel_file)
            except OSError:
                pass

    # Orphaned numeric files at project root (stdout captures from wsl-exec)
    for fname in os.listdir(project_root):
        fpath = os.path.join(project_root, fname)
        if os.path.isfile(fpath) and re.match(r"^\d{1,3}$", fname):
            # Check if it's small (<1KB) — likely stdout capture
            try:
                size = os.path.getsize(fpath)
                if size < 1024:
                    findings["orphan_files"].append(fname)
            except OSError:
                pass

    # Report
    total = sum(len(v) for v in findings.values()) + sum(len(v) for v in review.values())
    if total == 0:
        print("  No garbage found. Project is clean.")
        print(f"\n  Total: {total} items")
        return 0

    if findings["pycache_dirs"]:
        print(f"  __pycache__ directories ({len(findings['pycache_dirs'])}):")
        for d in findings["pycache_dirs"]:
            print(f"    {d}/")

    if findings["pyc_files"]:
        print(f"  .pyc files ({len(findings['pyc_files'])}):")
        for f in findings["pyc_files"]:
            print(f"    {f}")

    if findings["tmp_files"]:
        print(f"  .tmp files ({len(findings['tmp_files'])}):")
        for f in findings["tmp_files"]:
            print(f"    {f}")

    if findings["orphan_files"]:
        print(f"  Orphaned stdout captures ({len(findings['orphan_files'])}):")
        for f in findings["orphan_files"]:
            print(f"    {f}")

    if findings["bat_files"]:
        print(f"  .bat artifacts ({len(findings['bat_files'])}):")
        for f in findings["bat_files"]:
            print(f"    {f}")

    if findings["tlc_state_dirs"]:
        print(f"  TLC state directories ({len(findings['tlc_state_dirs'])}):")
        for d in findings["tlc_state_dirs"]:
            print(f"    {d}/")

    if findings["aged_boot_logs"]:
        print(f"  Aged boot transcripts >{BOOT_LOG_AGE_DAYS}d "
              f"({len(findings['aged_boot_logs'])}):")
        for f in findings["aged_boot_logs"]:
            print(f"    {f}")

    seen = sum(len(v) for v in review.values())
    if seen:
        print(f"\n  SEEN, NOT COLLECTED ({seen}) — reported for a decision, never "
              "deleted by this verb:")
        if review["zero_byte_files"]:
            print(f"  Zero-byte files ({len(review['zero_byte_files'])}):")
            for f in review["zero_byte_files"]:
                print(f"    {f}")
        if review["one_off_scripts"]:
            print(f"  One-off session-stamped scripts ({len(review['one_off_scripts'])}):")
            for f in review["one_off_scripts"]:
                print(f"    {f}")

    print(f"\n  Total: {total} items")

    if dry_run:
        print("\n  [DRY RUN] Run without --dry-run to delete.")
        return 0

    # Delete — collected classes only. The review classes above are deliberately
    # absent from this loop: a collector that deletes what it merely suspects is
    # how evidence disappears (P3: archive, never blind-delete).
    deleted = 0

    for d in findings["pycache_dirs"] + findings["tlc_state_dirs"]:
        full = os.path.join(project_root, d)
        try:
            shutil.rmtree(full)
            deleted += 1
        except OSError as e:
            print(f"  WARNING: Could not delete {d}: {e}")

    for category in ["pyc_files", "tmp_files", "orphan_files", "bat_files",
                     "aged_boot_logs"]:
        for f in findings[category]:
            full = os.path.join(project_root, f.split(" (")[0])
            try:
                os.remove(full)
                deleted += 1
            except OSError as e:
                print(f"  WARNING: Could not delete {f}: {e}")

    if seen:
        print(f"  Left in place for a decision: {seen} seen-not-collected items.")
    print(f"\n  Deleted: {deleted} items")
    return 0


def build_env_audit(project_root: Path) -> dict:
    """Probe the environment and return the audit dict (no printing).

    Extracted from :func:`cmd_audit_env` so the ``doctor`` preflight can reuse the
    EXACT same enumeration — one env-probe authority, no second copy to drift
    (the DRIFT-ELIM principle applied to the CLI itself).

    Satisfies: INS-017 (environment audit protocol, kernel-enforced).
    """
    import subprocess

    audit: dict = {
        "python_versions": [],
        "pip_variants": [],
        "package_managers": [],
        "tooling": [],
        "project_env": {},
        "platform": {},
    }

    # --- Platform info ---
    import platform as plat
    audit["platform"] = {
        "system": plat.system(),
        "release": plat.release(),
        "machine": plat.machine(),
        "python_default": plat.python_version(),
        "python_path": sys.executable,
    }

    # --- Discover Python versions ---
    python_candidates = [
        ("python3", "python3"),
        ("python", "python"),
        ("python3.12", "python3.12"),
        ("python3.13", "python3.13"),
        ("python3.14", "python3.14"),
        ("python3.11", "python3.11"),
        ("python3.10", "python3.10"),
    ]

    # Also check common absolute paths
    absolute_candidates = [
        ("/usr/bin/python3", "system-python3"),
        ("/usr/bin/python", "system-python"),
    ]
    # Windows paths
    for ver in ["314", "313", "312", "311", "310"]:
        winpath = f"C:\\Python{ver}\\python.exe"
        wslpath = f"/mnt/c/Python{ver}/python.exe"
        absolute_candidates.append((wslpath, f"windows-python-{ver}"))

    seen_versions: set[str] = set()

    def probe_python(cmd: str, label: str) -> dict | None:
        try:
            result = subprocess.run(
                [cmd, "--version"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                version_str = result.stdout.strip() or result.stderr.strip()
                version_str = version_str.replace("Python ", "")
                if version_str in seen_versions:
                    return None
                seen_versions.add(version_str)
                # Check if pip works
                pip_check = subprocess.run(
                    [cmd, "-m", "pip", "--version"],
                    capture_output=True, text=True, timeout=10,
                )
                pip_works = pip_check.returncode == 0
                pip_version = ""
                if pip_works:
                    pip_out = pip_check.stdout.strip()
                    # Parse "pip 24.0 from ..."
                    parts = pip_out.split()
                    if len(parts) >= 2:
                        pip_version = parts[1]

                return {
                    "command": cmd,
                    "label": label,
                    "version": version_str,
                    "pip_works": pip_works,
                    "pip_version": pip_version,
                    "path": shutil.which(cmd) or cmd,
                }
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            pass
        return None

    for cmd, label in python_candidates:
        info = probe_python(cmd, label)
        if info:
            audit["python_versions"].append(info)

    for cmd, label in absolute_candidates:
        info = probe_python(cmd, label)
        if info:
            audit["python_versions"].append(info)

    # --- Discover pip variants ---
    pip_candidates = ["pip3", "pip", "pip3.12", "pip3.13", "pip3.14"]
    seen_pips: set[str] = set()

    for cmd in pip_candidates:
        try:
            result = subprocess.run(
                [cmd, "--version"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                out = result.stdout.strip()
                if out not in seen_pips:
                    seen_pips.add(out)
                    audit["pip_variants"].append({
                        "command": cmd,
                        "info": out,
                        "path": shutil.which(cmd) or cmd,
                    })
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            pass

    # --- Package managers ---
    pkg_mgrs = [
        ("uv", "uv --version"),
        ("pipx", "pipx --version"),
        ("conda", "conda --version"),
        ("npm", "npm --version"),
        ("node", "node --version"),
    ]

    for name, cmd in pkg_mgrs:
        try:
            parts = cmd.split()
            result = subprocess.run(
                parts, capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                version = (result.stdout.strip() or result.stderr.strip()).split("\n")[0]
                audit["package_managers"].append({
                    "name": name,
                    "version": version,
                    "path": shutil.which(name) or name,
                })
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            pass

    # --- Fetch / VCS / shell tooling (INS-045) ---
    # Bootstrap must deterministically know fetch (curl/wget), VCS (git/gh),
    # and shell (jq/PowerShell) tooling — not just Python/Node — so a new
    # project never rediscovers these live (the eBay S0 thrash, F-19).
    # Each canonical tool is recorded with a present flag so the audit reports
    # both what exists AND what is missing.
    tooling_probes = [
        ("curl", ["curl", "--version"]),
        ("wget", ["wget", "--version"]),
        ("git", ["git", "--version"]),
        ("gh", ["gh", "--version"]),
        ("jq", ["jq", "--version"]),
        ("pwsh", ["pwsh", "--version"]),
        ("powershell.exe", ["powershell.exe", "-NoProfile", "-Command",
                            "$PSVersionTable.PSVersion.ToString()"]),
    ]

    for name, cmd_args in tooling_probes:
        entry = {"name": name, "present": False, "version": "", "path": shutil.which(name)}
        try:
            result = subprocess.run(
                cmd_args, capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                version = (result.stdout.strip() or result.stderr.strip()).split("\n")[0]
                entry["present"] = True
                entry["version"] = version
                if entry["path"] is None:
                    entry["path"] = name
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            pass
        audit["tooling"].append(entry)

    # --- Project environment ---
    req_path = project_root / "requirements.txt"
    venv_path = project_root / ".venv"
    audit["project_env"] = {
        "requirements_txt": str(req_path) if req_path.exists() else None,
        "virtualenv": str(venv_path) if venv_path.exists() else None,
        "project_root": str(project_root),
    }

    if req_path.exists():
        try:
            with open(req_path, "r", encoding="utf-8") as f:
                lines = [l.strip() for l in f if l.strip() and not l.startswith("#")]
            audit["project_env"]["requirements_count"] = len(lines)
            audit["project_env"]["requirements_packages"] = lines
        except OSError:
            pass

    return audit


def cmd_audit_env(args: argparse.Namespace) -> int:
    """Audit environment: enumerate Python versions, pip variants, package
    managers, fetch/VCS/shell tooling, and project deps. Renders build_env_audit.

    Satisfies: INS-017 (environment audit protocol, kernel-enforced).
    """
    import json as json_mod

    project_root = args.path.resolve()
    json_output = getattr(args, "json_output", False)
    audit = build_env_audit(project_root)

    # --- Output ---
    if json_output:
        print(json_mod.dumps(audit, indent=2))
    else:
        print("RAG Runtime Kernel - Environment Audit")
        print("=" * 50)

        print(f"\nPlatform: {audit['platform']['system']} {audit['platform']['release']} ({audit['platform']['machine']})")
        print(f"Default Python: {audit['platform']['python_default']} ({audit['platform']['python_path']})")

        print(f"\nPython versions found ({len(audit['python_versions'])}):")
        if audit["python_versions"]:
            for p in audit["python_versions"]:
                pip_status = f"pip {p['pip_version']}" if p["pip_works"] else "pip BROKEN"
                print(f"  {p['version']:12s}  {p['path']:40s}  [{pip_status}]")
        else:
            print("  None found!")

        if audit["pip_variants"]:
            print(f"\nStandalone pip variants ({len(audit['pip_variants'])}):")
            for p in audit["pip_variants"]:
                print(f"  {p['command']:12s}  {p['path']}")

        if audit["package_managers"]:
            print(f"\nPackage managers ({len(audit['package_managers'])}):")
            for p in audit["package_managers"]:
                print(f"  {p['name']:12s}  {p['version']:20s}  {p['path']}")

        present_tools = [t for t in audit["tooling"] if t["present"]]
        print(f"\nFetch/VCS/shell tooling ({len(present_tools)}/{len(audit['tooling'])} present):")
        for t in audit["tooling"]:
            if t["present"]:
                print(f"  {t['name']:14s}  {t['version']:24s}  {t['path'] or ''}")
            else:
                print(f"  {t['name']:14s}  NOT FOUND")

        print(f"\nProject environment ({project_root}):")
        if audit["project_env"]["requirements_txt"]:
            count = audit["project_env"].get("requirements_count", "?")
            print(f"  requirements.txt: YES ({count} packages)")
        else:
            print("  requirements.txt: NOT FOUND")
        if audit["project_env"]["virtualenv"]:
            print(f"  virtualenv: {audit['project_env']['virtualenv']}")
        else:
            print("  virtualenv: NOT FOUND")

        # Recommendations
        print("\n" + "=" * 50)
        working_pythons = [p for p in audit["python_versions"] if p["pip_works"]]
        broken_pythons = [p for p in audit["python_versions"] if not p["pip_works"]]

        if working_pythons:
            best = working_pythons[0]
            print(f"RECOMMENDED: Use {best['command']} ({best['version']}) at {best['path']}")
        elif audit["python_versions"]:
            print("WARNING: All detected Python versions have broken pip!")
            print("  Manual intervention required.")
        else:
            print("WARNING: No Python installations detected!")

        if broken_pythons:
            for p in broken_pythons:
                print(f"KNOWN ISSUE: {p['command']} ({p['version']}) has broken pip — do NOT use for installs")

    return 0


# ---------------------------------------------------------------------------
# doctor / preflight + guarded ADD verb (ENV-NORM increment 1)
# ---------------------------------------------------------------------------

# The proven anti-mangling shell pattern (E-036 / E-042): never inline-chain a
# composed command through a sanitizing transport — write it to a script file in
# the project tree and run THAT verbatim under a real shell (tmux-mcp). This is
# what `doctor --emit-runner` drops next to the project so the recommended runner
# is always one file away instead of a remembered convention.
_RUNNER_TEMPLATE = """#!/usr/bin/env bash
# rag_kernel run-in-project helper (emitted by `rag_kernel doctor --emit-runner`).
# WHY: composed shell (&&, ;, |, $(), 2>&1) is mangled by sanitizing transports
# (wsl-exec strips operators and leaves an orphan `1` file). The structural fix is
# to put the commands in THIS file and execute it verbatim under a real shell.
#   usage:  bash run_in_project.sh
set -euo pipefail
cd "$(dirname "$0")"
# --- put your composed commands below this line ---
"""


def diagnose_index_lock(
    lock_exists: bool,
    git_running: bool,
    lock_age_seconds,
    *,
    stale_after: float = 60.0,
) -> dict:
    """Pure, fail-closed decision: is a ``.git/index.lock`` safe to clear?

    A lock is STALE (clearable) only when nothing currently holds it: no git
    process is running AND the lock has aged past ``stale_after``. A running git
    process means the lock is LIVE — never touch it. If the age cannot be read,
    refuse (fail-closed). Deterministic and side-effect-free so it is unit-tested
    without real processes or files.

    Returns ``{present, verdict, clearable, reason}`` with verdict in
    {absent, live, stale, fresh, unknown}.
    """
    if not lock_exists:
        return {"present": False, "verdict": "absent", "clearable": False,
                "reason": "no .git/index.lock present"}
    if git_running:
        return {"present": True, "verdict": "live", "clearable": False,
                "reason": "a git process is running — lock is LIVE, do not clear"}
    if lock_age_seconds is None:
        return {"present": True, "verdict": "unknown", "clearable": False,
                "reason": "cannot determine lock age — refusing to clear (fail-closed)"}
    if lock_age_seconds >= stale_after:
        return {"present": True, "verdict": "stale", "clearable": True,
                "reason": (f"no git running and lock aged {lock_age_seconds:.0f}s "
                           f">= {stale_after:.0f}s — safe to clear")}
    return {"present": True, "verdict": "fresh", "clearable": False,
            "reason": (f"no git running but lock only {lock_age_seconds:.0f}s old "
                       f"(< {stale_after:.0f}s) — refusing (could be a live op)")}


def _git_process_running() -> bool:
    """Best-effort, stdlib-only check for a running ``git`` process (POSIX + Win)."""
    import subprocess
    try:
        r = subprocess.run(["pgrep", "-x", "git"], capture_output=True, text=True, timeout=5)
        return r.returncode == 0
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        pass
    try:
        r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq git.exe"],
                           capture_output=True, text=True, timeout=5)
        return "git.exe" in r.stdout.lower()
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return False


def _doctor_recover(rag_path: Path, do_fix: bool) -> dict:
    """RECOVERY ADVISOR — the scripted form of the PI's recovery_protocol.

    Assesses a corrupt/unreadable HOT and stages the ordered recovery path
    ``.bak -> COLD + WAL -> rebuild``. Assessment is READ-ONLY; ``do_fix`` performs
    ONLY the safe, common-case ``.bak`` restore, reusing the same persistence
    primitives (``verify_hashes`` / ``atomic_write_json``) as :meth:`api.KernelAPI.recover`
    — no second copy of the restore rule (Rule 13). COLD/WAL/rebuild are reported
    as the ordered next options and never silently reconstructed ("offer rebuild
    options"). Fail-safe: any read error degrades to a recommendation, never a raise.
    """
    from rag_kernel.persistence import verify_hashes, atomic_write_json
    import json as _json

    rp = Path(rag_path)
    bak = rp.with_suffix(rp.suffix + ".bak")
    cold = rp.parent / "RAG_COLD.json"
    wal = rp.parent / (rp.stem + ".wal")  # best-effort WAL sidecar
    out: dict = {
        "hot_ok": False, "bak": {}, "cold": {}, "wal": {},
        "action": None, "recommended": [],
    }

    # HOT
    hot = None
    try:
        hot = _json.loads(rp.read_text(encoding="utf-8-sig"))
        he = verify_hashes(hot)
        out["hot_ok"] = not he
        if he:
            out["hot_hash_errors"] = he
    except (OSError, ValueError) as e:
        out["hot_error"] = str(e)

    # .bak
    bak_obj = None
    if bak.exists():
        try:
            bak_obj = _json.loads(bak.read_text(encoding="utf-8-sig"))
            be = verify_hashes(bak_obj)
            out["bak"] = {"present": True, "valid": not be}
            if be:
                out["bak"]["hash_errors"] = be
        except (OSError, ValueError) as e:
            out["bak"] = {"present": True, "valid": False, "error": str(e)}
    else:
        out["bak"] = {"present": False, "valid": False}

    out["cold"] = {"present": cold.exists()}
    out["wal"] = {"present": wal.exists()}

    # No recovery needed.
    if out["hot_ok"]:
        out["action"] = "none"
        out["recommended"] = ["HOT is readable and hash-clean — no recovery needed."]
        return out

    # Stage 1 — .bak (the safe common case).
    if out["bak"].get("valid") and bak_obj is not None:
        if do_fix:
            atomic_write_json(rp, bak_obj, mirror_bak=True)
            out["action"] = "bak_restored"
            out["recommended"].append("Restored HOT from a valid .bak (hash-verified).")
        else:
            out["action"] = "bak_available"
            out["recommended"].append(
                "Stage 1: .bak is valid — restore with `doctor --recover --fix`."
            )
        return out

    # Stage 2/3 — COLD + WAL, then rebuild (offered, never auto-run).
    out["action"] = "manual"
    out["recommended"].append("Stage 1: .bak missing or invalid — cannot auto-restore.")
    if out["cold"]["present"]:
        out["recommended"].append(
            "Stage 2: RAG_COLD.json present — rehydrate HOT from COLD, then replay the WAL."
        )
    else:
        out["recommended"].append("Stage 2: no RAG_COLD.json — COLD rehydrate unavailable.")
    out["recommended"].append(
        "Stage 2: WAL " + ("present — replay outstanding entries after COLD hydrate."
                           if out["wal"]["present"] else "absent — nothing to replay.")
    )
    out["recommended"].append(
        "Stage 3 (offer): if no snapshot is viable, rebuild from the INIT spec "
        "via `rag_kernel init <spec>` and re-apply session state."
    )
    return out


def cmd_doctor(args: argparse.Namespace) -> int:
    """Preflight the environment + repo before real work (ENV-NORM increment 1).

    Three deterministic, fail-closed checks:
      1. ENV   — best working Python, broken-pip flags, fetch/VCS/shell tooling
                 (renders :func:`build_env_audit` — same authority as audit-env).
      2. LOCK  — detects a stale ``.git/index.lock`` and, only with ``--fix`` and
                 only when :func:`diagnose_index_lock` proves it clearable (no git
                 running AND aged), clears it. A LIVE lock is never touched. This
                 turns the recurring stale-lock waste (E-042 / S61 / S62) into an
                 enforced check instead of a remembered manual cleanup.
      3. SHELL — prints the prescribed first move (tmux-mcp primary). With
                 ``--rag`` the pointer is rendered from the RAG's
                 ``session_start_shell_rule`` (no second copy of the rule, Rule 13).

    Exit 0 when nothing blocks; non-zero when a blocking issue is found and not
    fixed. ``--emit-runner PATH`` writes the script-file runner template and exits.
    """
    import json as json_mod
    import time
    import rag_kernel

    project_root = args.path.resolve()

    if getattr(args, "emit_runner", None):
        dest = Path(args.emit_runner).resolve()
        dest.write_text(_RUNNER_TEMPLATE, encoding="utf-8")
        try:
            dest.chmod(0o755)
        except OSError:
            pass
        print(f"runner written: {dest}")
        return 0

    # RECOVERY ADVISOR (BOOT-PROSE-TO-SCRIPT): one governed verb absorbs the
    # Project Instructions' ".bak -> COLD + WAL -> rebuild" recovery prose, so the
    # PI's RECOVERY EXCEPTION collapses to a single pointer. Covers the common
    # "kernel intact, RAG corrupt" case (the kernel can still run `doctor`); the
    # truly-kernel-unreachable case still falls back to raw .bak/COLD/WAL reads.
    if getattr(args, "recover", False):
        rag_path = getattr(args, "rag", None) or (project_root / "RAG_MASTER.json")
        rec = _doctor_recover(Path(rag_path), do_fix=getattr(args, "fix", False))
        if getattr(args, "json_output", False):
            print(json_mod.dumps(rec, indent=2))
        else:
            print("RAG Runtime Kernel - doctor --recover")
            print("=" * 50)
            print(f"HOT      : {'OK (readable, hash-clean)' if rec['hot_ok'] else 'UNREADABLE/CORRUPT'}")
            print(f".bak     : present={rec['bak'].get('present')} valid={rec['bak'].get('valid')}")
            print(f"COLD     : present={rec['cold'].get('present')} (RAG_COLD.json)")
            print(f"WAL      : present={rec['wal'].get('present')}")
            print(f"ACTION   : {rec['action']}")
            for line in rec["recommended"]:
                print(f"  -> {line}")
        # 0 when nothing is wrong or a restore succeeded; 1 when recovery is
        # needed and was not (or could not be) applied — fail-loud for callers.
        return 0 if rec["action"] in ("none", "bak_restored") else 1

    report: dict = {"env": {}, "lock": {}, "shell": {}, "blocking": []}

    # 1. ENV (same authority as audit-env)
    audit = build_env_audit(project_root)
    working = [p for p in audit["python_versions"] if p["pip_works"]]
    broken = [p for p in audit["python_versions"] if not p["pip_works"]]
    tools_present = {t["name"]: t["present"] for t in audit["tooling"]}
    report["env"] = {
        "best_python": (working[0]["command"] + " " + working[0]["version"]) if working else None,
        "broken_pip": [p["command"] + " " + p["version"] for p in broken],
        "tooling_present": [n for n, p in tools_present.items() if p],
        "tooling_missing": [n for n, p in tools_present.items() if not p],
    }
    # KA-17: classify the running interpreter against the supported window.
    py_status, py_running = rag_kernel.python_support_status()
    report["env"]["running_python"] = py_running
    report["env"]["python_support"] = py_status
    report["env"]["supported_python"] = list(rag_kernel.SUPPORTED_PYTHON)
    if py_status == "below_floor":
        report["blocking"].append(
            f"running Python {py_running} is below the supported floor "
            f"{'.'.join(map(str, rag_kernel.SUPPORTED_PYTHON_MIN))}")
    if not working:
        report["blocking"].append("no Python with a working pip")
    if not tools_present.get("git", False):
        report["blocking"].append("git not found")

    # 2. LOCK
    lock_path = project_root / ".git" / "index.lock"
    lock_exists = lock_path.exists()
    age = None
    if lock_exists:
        try:
            age = max(0.0, time.time() - lock_path.stat().st_mtime)
        except OSError:
            age = None
    git_running = _git_process_running() if lock_exists else False
    diag = diagnose_index_lock(lock_exists, git_running, age, stale_after=args.stale_after)
    report["lock"] = diag
    if lock_exists and getattr(args, "fix", False) and diag["clearable"]:
        try:
            lock_path.unlink()
            report["lock"]["cleared"] = True
            report["lock"]["reason"] += "  [CLEARED]"
        except OSError as ex:
            report["lock"]["cleared"] = False
            report["blocking"].append(f"index.lock present and unlink failed: {ex}")
    elif lock_exists and diag["verdict"] == "live":
        report["blocking"].append("git index.lock is LIVE (a git op is running)")
    elif lock_exists and diag["verdict"] == "stale" and not getattr(args, "fix", False):
        report["lock"]["hint"] = "re-run with --fix to clear"

    # 3. SHELL policy first move (render from RAG when given)
    ssr = None
    rag_path = getattr(args, "rag", None)
    if rag_path:
        try:
            hot = json_mod.loads(Path(rag_path).read_text(encoding="utf-8"))
            ssr = hot.get("operating_protocol", {}).get("session_start_shell_rule")
        except (OSError, ValueError):
            ssr = None
    report["shell"] = {
        "first_move": ("First shell/git/test action via tmux-mcp (PRIMARY). "
                       "wsl-exec = atomic single commands only. Cowork sandbox BANNED."),
        "rag_rule_present": bool(ssr),
    }

    # --- Output ---
    if getattr(args, "json_output", False):
        print(json_mod.dumps(report, indent=2))
        return 1 if report["blocking"] else 0

    print("RAG Runtime Kernel - doctor (preflight)")
    print("=" * 50)
    e = report["env"]
    print(f"ENV   best python : {e['best_python'] or 'NONE (blocking)'}")
    _sup = {"ok": "ok", "below_floor": "BELOW FLOOR (blocking)",
            "above_ceiling": "above tested ceiling (warn)"}.get(
        e.get("python_support", "ok"), e.get("python_support", "ok"))
    print(f"      running py  : {e.get('running_python', '?')} "
          f"[{_sup}] | supported {'/'.join(e.get('supported_python', []))}")
    if e["broken_pip"]:
        print(f"      broken pip  : {', '.join(e['broken_pip'])}")
    print(f"      tooling     : present={','.join(e['tooling_present']) or '-'} "
          f"| missing={','.join(e['tooling_missing']) or '-'}")
    lk = report["lock"]
    print(f"LOCK  {lk['verdict']:8s}: {lk['reason']}")
    if lk.get("hint"):
        print(f"      hint        : {lk['hint']}")
    print(f"SHELL first move : {report['shell']['first_move']}")
    if ssr:
        print("      (rendered from RAG operating_protocol.session_start_shell_rule)")
    print("=" * 50)
    if report["blocking"]:
        for b in report["blocking"]:
            print(f"BLOCKING: {b}")
        return 1
    print("OK: preflight clean.")
    return 0


def cmd_add(args: argparse.Namespace) -> int:
    """Add a NEW canonical tracked item through the guarded, atomic store API.

    Closes the long-flagged no-ADD-verb gap: the lifecycle verbs only TRANSITION
    existing items and ``migrate_backlog`` refuses a non-empty array, so there was
    no CLI path to introduce a brand-new tracked item without hand-editing JSON —
    the exact drift the project forbids (E-037 / E-040). This wires
    ``drift_store.add_items_file``: one validated spec -> unique-id invariant ->
    atomic write (tmp -> verify -> .bak -> rename). A duplicate id fails LOUD and
    writes nothing.
    """
    from rag_kernel.drift_control import ItemKind, ItemStatus, ItemValidationError
    from rag_kernel.drift_store import (
        DriftStoreError,
        DuplicateItemError,
        TrackedItemStore,
        add_items_file,
        load_hot,
    )

    rag_path = args.rag.resolve()
    if not rag_path.exists():
        print(f"Error: RAG file not found: {rag_path}", file=sys.stderr)
        return 1

    try:
        status = ItemStatus(args.status.upper())
    except ValueError:
        print(f"Error: unknown status {args.status!r}; valid: "
              f"{[s.value for s in ItemStatus]}", file=sys.stderr)
        return 1
    try:
        kind = ItemKind(args.kind.upper())
    except ValueError:
        print(f"Error: unknown kind {args.kind!r}; valid: "
              f"{[k.value for k in ItemKind]}", file=sys.stderr)
        return 1

    spec: dict = {
        "id": args.item_id,
        "title": args.title,
        "status": status,
        "kind": kind,
        "session": args.session,
        "note": args.note,
    }
    if status == ItemStatus.SUPERSEDED:
        if not getattr(args, "by", None):
            print("Error: adding at status SUPERSEDED requires --by", file=sys.stderr)
            return 1
        spec["superseded_by"] = args.by

    try:
        store = TrackedItemStore.from_hot(load_hot(rag_path))
    except DriftStoreError as ex:
        print(f"Error: {ex}", file=sys.stderr)
        return 1
    if args.item_id in store:
        print(f"Error: id {args.item_id!r} already exists "
              f"(add is fail-loud on duplicates)", file=sys.stderr)
        return 1

    if args.dry_run:
        print(f"[DRY RUN] would add {args.item_id} "
              f"[{status.value}/{kind.value}] (no write)")
        return 0

    try:
        add_items_file(rag_path, [spec])
    except (DuplicateItemError, DriftStoreError, ItemValidationError) as ex:
        print(f"Error: {ex}", file=sys.stderr)
        return 1

    print(f"added {args.item_id}: {status.value} {kind.value}  "
          f"{args.title!r}  [session {args.session}]")
    return 0


def cmd_unadd(args: argparse.Namespace) -> int:
    """Un-add a PRISTINE mis-added tracked item — the guarded, atomic inverse of add.

    Closes the KA-CUTOVER-GATE recovery gap: before this, a mis-``add`` (wrong
    id / kind / status) could only be discarded or superseded, never removed — so
    a mis-kinded ERROR/INFERENCE item latched the record-coverage cutover gate ON
    with no way to clear it. This wires ``drift_store.remove_item_file``: load ->
    pristine-only guard (empty history) -> atomic write (tmp -> verify -> .bak ->
    rename). An unknown id, or a transitioned (real, historied) item, fails LOUD
    and writes nothing.
    """
    from rag_kernel.drift_store import (
        DriftStoreError,
        TrackedItemStore,
        UnknownItemError,
        load_hot,
        remove_item_file,
    )

    rag_path = args.rag.resolve()
    if not rag_path.exists():
        print(f"Error: RAG file not found: {rag_path}", file=sys.stderr)
        return 1

    try:
        store = TrackedItemStore.from_hot(load_hot(rag_path))
    except DriftStoreError as ex:
        print(f"Error: {ex}", file=sys.stderr)
        return 1
    if args.item_id not in store:
        print(f"Error: no tracked item with id {args.item_id!r}", file=sys.stderr)
        return 1
    item = store.get(args.item_id)
    if item.history:
        print(f"Error: cannot un-add {args.item_id!r}: it carries "
              f"{len(item.history)} lifecycle event(s) and is a real tracked item "
              f"— un-add is only for a pristine mis-add. Use discard/supersede.",
              file=sys.stderr)
        return 1

    if args.dry_run:
        print(f"[DRY RUN] would un-add {args.item_id} "
              f"[{item.status.value}/{item.kind.value}] (no write)")
        return 0

    try:
        remove_item_file(rag_path, args.item_id)
    except (UnknownItemError, DriftStoreError) as ex:
        print(f"Error: {ex}", file=sys.stderr)
        return 1

    print(f"un-added {args.item_id}: was {item.status.value} {item.kind.value}  "
          f"{item.title!r}  [session {args.session}]")
    return 0


def cmd_add_rule(args: argparse.Namespace) -> int:
    """Append a NEW operating_protocol rule through the guarded, atomic store (FIX-5/P3).

    Closes the no-add-rule-verb gap: operating_protocol rules (e.g. the STRICT-OBEY
    operator directive) were previously introduced by hand-editing RAG_MASTER.json
    — the manual-JSON drift the project forbids (E-037 / E-039). This wires
    ``drift_store.add_operating_protocol_rule_file``: validate -> fail-loud on an
    existing key (unless ``--allow-overwrite``) -> atomic write (tmp -> verify ->
    .bak parity -> rename). The rule text may come from the positional argument or,
    for long rules, ``--value-file``.
    """
    from rag_kernel.drift_store import (
        DriftStoreError,
        DuplicateItemError,
        OPERATING_PROTOCOL_KEY,
        add_operating_protocol_rule_file,
        load_hot,
    )

    rag_path = args.rag.resolve()
    if not rag_path.exists():
        print(f"Error: RAG file not found: {rag_path}", file=sys.stderr)
        return 1

    # Rule text: --value-file takes precedence over the positional value.
    if args.value_file is not None:
        if not args.value_file.exists():
            print(f"Error: value file not found: {args.value_file}", file=sys.stderr)
            return 1
        value = args.value_file.read_text(encoding="utf-8").strip()
    elif args.value is not None:
        value = args.value
    else:
        print("Error: provide the rule text as the positional value or via --value-file",
              file=sys.stderr)
        return 1
    if not value.strip():
        print("Error: rule value is empty", file=sys.stderr)
        return 1

    try:
        hot = load_hot(rag_path)
    except DriftStoreError as ex:
        print(f"Error: {ex}", file=sys.stderr)
        return 1
    op = hot.get(OPERATING_PROTOCOL_KEY)
    exists = isinstance(op, dict) and args.key in op
    if exists and not args.allow_overwrite:
        print(f"Error: operating_protocol already has rule {args.key!r} "
              f"(add-rule is fail-loud; pass --allow-overwrite to replace)", file=sys.stderr)
        return 1

    if args.dry_run:
        verb = "replace" if exists else "add"
        print(f"[DRY RUN] would {verb} operating_protocol rule {args.key!r} "
              f"({len(value)} chars) (no write)")
        return 0

    try:
        add_operating_protocol_rule_file(
            rag_path, args.key, value, allow_overwrite=args.allow_overwrite)
    except (DuplicateItemError, DriftStoreError) as ex:
        print(f"Error: {ex}", file=sys.stderr)
        return 1

    action = "replaced" if exists else "added"
    print(f"{action} operating_protocol rule {args.key!r} ({len(value)} chars) "
          f"[session {args.session}]")
    return 0


def cmd_update_rule(args: argparse.Namespace) -> int:
    """Re-set an EXISTING operating_protocol rule — string OR structured (dict/JSON) —
    or a single sub-key of a dict-valued rule, through the guarded atomic store
    (UPDATE-RULE-VERB).

    Closes the gap left by ``add-rule``, whose value is string-only and whose default
    is ADD: this verb's default is UPDATE (the target must already exist; pass
    ``--create`` to add). With ``--json`` the value is parsed as JSON, so structured
    rules like ``tool_hierarchy`` can be re-set wholesale or — with ``--subkey`` —
    trimmed one sub-entry at a time. Same write contract as add-rule: validate ->
    ``set_operating_protocol_rule_file`` -> atomic write (tmp -> verify -> .bak
    parity -> rename).
    """
    import json as _json
    from rag_kernel.drift_store import (
        DriftStoreError,
        OPERATING_PROTOCOL_KEY,
        load_hot,
        set_operating_protocol_rule_file,
    )

    rag_path = args.rag.resolve()
    if not rag_path.exists():
        print(f"Error: RAG file not found: {rag_path}", file=sys.stderr)
        return 1

    # Raw value: --value-file takes precedence over the positional.
    if args.value_file is not None:
        if not args.value_file.exists():
            print(f"Error: value file not found: {args.value_file}", file=sys.stderr)
            return 1
        raw = args.value_file.read_text(encoding="utf-8")
    elif args.value is not None:
        raw = args.value
    else:
        print("Error: provide the value as the positional arg or via --value-file",
              file=sys.stderr)
        return 1

    # Parse: JSON when --json, else a stripped string.
    if args.as_json:
        try:
            value = _json.loads(raw)
        except _json.JSONDecodeError as ex:
            print(f"Error: --json given but value is not valid JSON: {ex}", file=sys.stderr)
            return 1
    else:
        value = raw.strip()
        if not value:
            print("Error: rule value is empty", file=sys.stderr)
            return 1

    # Pre-flight existence/type checks for clear messaging + an accurate dry-run.
    try:
        hot = load_hot(rag_path)
    except DriftStoreError as ex:
        print(f"Error: {ex}", file=sys.stderr)
        return 1
    op = hot.get(OPERATING_PROTOCOL_KEY)
    if not isinstance(op, dict):
        print(f"Error: {OPERATING_PROTOCOL_KEY!r} is not a JSON object", file=sys.stderr)
        return 1

    key_exists = args.key in op
    if args.subkey is None:
        if not key_exists and not args.create:
            print(f"Error: operating_protocol has no rule {args.key!r} to update "
                  f"(pass --create to add, or use add-rule)", file=sys.stderr)
            return 1
        target_desc = f"rule {args.key!r}"
        action = "update" if key_exists else "create"
    else:
        if not key_exists:
            print(f"Error: operating_protocol has no rule {args.key!r}; "
                  f"cannot set sub-key {args.subkey!r}", file=sys.stderr)
            return 1
        if not isinstance(op[args.key], dict):
            print(f"Error: rule {args.key!r} is {type(op[args.key]).__name__}, not a JSON "
                  f"object; --subkey requires a dict-valued rule", file=sys.stderr)
            return 1
        sub_exists = args.subkey in op[args.key]
        if not sub_exists and not args.create:
            print(f"Error: rule {args.key!r} has no sub-key {args.subkey!r} "
                  f"(pass --create to add)", file=sys.stderr)
            return 1
        target_desc = f"rule {args.key!r} sub-key {args.subkey!r}"
        action = "update" if sub_exists else "create"

    kind = "json" if args.as_json else "string"
    if args.dry_run:
        print(f"[DRY RUN] would {action} operating_protocol {target_desc} "
              f"({kind} value) (no write)")
        return 0

    try:
        set_operating_protocol_rule_file(
            rag_path, args.key, value, subkey=args.subkey, create=args.create)
    except DriftStoreError as ex:
        print(f"Error: {ex}", file=sys.stderr)
        return 1

    print(f"{action}d operating_protocol {target_desc} ({kind} value) "
          f"[session {args.session}]")
    return 0


def cmd_refresh_current_status(args: argparse.Namespace) -> int:
    """Re-stamp the denormalized machine-facts in ``current_status`` (KA-CS-REFRESH).

    The governed, atomic REPAIR half of the E-043 freshness guard. A mid-session dev
    commit bumps ``rag_kernel.__version__`` / moves git HEAD, leaving the
    ``current_status`` narrative stale with no reconciliation path — so the
    session-end freshness audit fails and the only prior fix was a forbidden hand-edit
    (the S116 + S127 incidents). This verb refreshes the runtime-version token
    (``current_status.rag_kernel_version`` <- live ``rag_kernel.__version__``) and the
    published git HEAD (``current_status.github_repo``'s "LATEST COMMIT <sha>" <- the
    live worktree HEAD), and OPTIONALLY the ``unit_tests`` count (``--tests``, never
    fabricated). Same write contract as update-rule: load -> plan via the shared guard
    regexes -> atomic write (tmp -> verify -> .bak parity -> rename). Idempotent — a
    no-op (no write, .bak untouched) when already fresh. Exit 0 on success/no-op, 1 on
    error (incl. a --strict missing-token failure).
    """
    from rag_kernel.drift_store import (
        CurrentStatusRefreshError,
        DriftStoreError,
        refresh_current_status_file,
    )

    rag_path = args.rag.resolve()
    if not rag_path.exists():
        print(f"Error: RAG file not found: {rag_path}", file=sys.stderr)
        return 1

    # Live authorities: version from the imported runtime, HEAD from the worktree
    # (reusing the auditor's own resolver so detect and repair agree on the source).
    version = args.version
    if version is None:
        try:
            import rag_kernel
            version = getattr(rag_kernel, "__version__", None)
        except Exception:
            version = None
    git_head = args.git_head or _resolve_git_head(rag_path)
    tests = f"{args.tests:,}" if args.tests is not None else None

    if not any([version, git_head, tests]):
        print("Error: nothing to refresh — could not resolve a live version, git "
              "HEAD, or --tests count", file=sys.stderr)
        return 1

    try:
        changes, wrote = refresh_current_status_file(
            rag_path,
            version=version,
            git_head=git_head,
            tests=tests,
            strict=args.strict,
            dry_run=args.dry_run,
        )
    except (CurrentStatusRefreshError, DriftStoreError) as ex:
        print(f"Error: {ex}", file=sys.stderr)
        return 1

    # Line-by-line render of every planned token (STRICT-OBEY rendering discipline —
    # never collapse to a bare count).
    header = (
        "[DRY RUN] current_status would refresh" if args.dry_run
        else ("current_status refreshed" if wrote else "current_status already fresh")
    )
    print(f"{header} [session {args.session}]:")
    for c in changes:
        if c["action"] == "updated":
            print(f"  {c['field']} ({c['kind']}): {c['old']} -> {c['new']}")
        elif c["action"] == "unchanged":
            print(f"  {c['field']} ({c['kind']}): {c['new']} (already fresh)")
        else:  # skipped
            print(f"  {c['field']} ({c['kind']}): skipped (no target token; pass "
                  f"--strict to fail loud)")
    if wrote:
        print("  .bak refreshed to byte-parity (HOT == BAK).")
    return 0


def cmd_meta(args: argparse.Namespace) -> int:
    """Read or SET a declared ``meta.*`` scalar — the governed close of META-SETTER-GAP.

    ``refresh-current-status`` re-stamps ``current_status`` tokens and
    ``prune-current-status`` removes archived keys, but neither can touch ``meta``.
    So a wrong ``meta.written_by_session`` or a drifted ``meta.policy_version`` had no
    governed repair at all — only the hand edit ``tool_contract`` forbids. This is that
    path, and it is deliberately narrow: REFUSE-BY-DEFAULT over a declared allowlist,
    containers refused by name with a pointer to the verb that owns them, typed
    coercion that fails loud, and a no-op write when the value is already correct.
    """
    from rag_kernel.meta_setter import (
        CONTAINER_KEYS,
        SETTABLE,
        MetaSetterError,
        get_meta_scalar,
        set_meta_scalar_file,
    )
    from rag_kernel.drift_store import DriftStoreError, load_hot

    rag_path = args.rag.resolve()
    if not rag_path.exists():
        print(f"Error: RAG file not found: {rag_path}", file=sys.stderr)
        return 1

    try:
        hot = load_hot(rag_path)
    except DriftStoreError as ex:
        print(f"Error: {ex}", file=sys.stderr)
        return 1

    if args.get_key:
        val = get_meta_scalar(hot, args.get_key)
        if val is None and args.get_key not in (hot.get("meta") or {}):
            print(f"Error: meta.{args.get_key} is not present", file=sys.stderr)
            return 1
        print(val)
        return 0

    if args.list_keys or not args.set_kv:
        print(f"Declared settable meta scalars ({len(SETTABLE)}):")
        for k, typ in sorted(SETTABLE.items()):
            print(f"  {k} <{typ.__name__}> = {get_meta_scalar(hot, k)!r}")
        print(f"\nRefused containers ({len(CONTAINER_KEYS)}) — each has its own verb:")
        for k, why in sorted(CONTAINER_KEYS.items()):
            print(f"  {k}: {why}")
        if not args.set_kv:
            print("\nSet one with:  rag_kernel meta --set KEY=VALUE --session S<n>")
        return 0

    if "=" not in args.set_kv:
        print("Error: --set expects KEY=VALUE", file=sys.stderr)
        return 1
    key, _, raw = args.set_kv.partition("=")
    key, raw = key.strip(), raw.strip()

    try:
        old, new, wrote = set_meta_scalar_file(
            rag_path, key, raw, session=args.session or "", dry_run=args.dry_run
        )
    except (MetaSetterError, DriftStoreError) as ex:
        print(f"Error: {ex}", file=sys.stderr)
        return 1

    if args.dry_run:
        print(f"[DRY RUN] meta.{key}: {old!r} -> {new!r} [session {args.session}]")
    elif wrote:
        print(f"meta.{key}: {old!r} -> {new!r} [session {args.session}]")
        print("  .bak refreshed to byte-parity (HOT == BAK).")
    else:
        print(f"meta.{key} already {new!r} — no write (HOT == BAK preserved).")
    return 0


def cmd_tests(args: argparse.Namespace) -> int:
    """Measure the suite and stamp it, or grade the stamp (REPORT-TESTS-GATE-UNMEASURED).

    The seal used to repeat whatever number the agent typed into ``--tests``. S184 and
    S185 sealed with ``n/a``; nothing checked. Here the count is produced by an actual
    run and stored with the runtime version and git HEAD it was measured against, so
    "green" decays into "STALE" by itself the moment the code moves — which is the only
    way a cached pass stops lying.
    """
    import json as _json

    from rag_kernel import test_gate
    from rag_kernel.drift_store import DriftStoreError, load_hot

    rag_path = args.rag.resolve()
    if not rag_path.exists():
        print(f"Error: RAG file not found: {rag_path}", file=sys.stderr)
        return 1

    try:
        hot = load_hot(rag_path)
    except DriftStoreError as ex:
        print(f"Error: {ex}", file=sys.stderr)
        return 1

    live_runtime = None
    try:
        import rag_kernel as _rk
        live_runtime = getattr(_rk, "__version__", None)
    except Exception:
        pass
    live_head = _resolve_git_head(rag_path)

    if args.run:
        if not args.session:
            print("Error: --session is required with --run: a measurement must be "
                  "attributable", file=sys.stderr)
            return 1
        try:
            repo = (args.repo.resolve() if args.repo
                    else test_gate.resolve_repo_root(rag_path, hot))
            print(f"tests: measuring {repo} (timeout {args.timeout}s) …", flush=True)
            result = test_gate.run_suite(repo, timeout=args.timeout)
            stamp, wrote = test_gate.set_test_gate_file(
                rag_path, result, session=args.session,
                runtime=live_runtime, git_head=live_head,
            )
        except (test_gate.TestGateError, DriftStoreError) as ex:
            print(f"Error: {ex}", file=sys.stderr)
            return 1
        ok, cell, reason = test_gate.verdict(
            stamp, live_head=live_head, live_runtime=live_runtime
        )
        if args.as_json:
            print(_json.dumps({"stamp": stamp, "ok": ok, "cell": cell}, indent=2))
        else:
            print(f"  {result['summary_line']}")
            print(f"  stamped meta.test_gate: {cell}")
            print(f"  measured against runtime {live_runtime} @ {(live_head or '?')[:7]}")
            if wrote:
                print("  .bak refreshed to byte-parity (HOT == BAK).")
        return 0 if ok else 1

    stamp = test_gate.read_stamp(hot)

    if args.show:
        if args.as_json:
            print(_json.dumps(stamp, indent=2))
        elif stamp is None:
            print("meta.test_gate: (unstamped)")
        else:
            for k in sorted(stamp):
                print(f"  {k} = {stamp[k]!r}")
        return 0 if stamp else 1

    ok, cell, reason = test_gate.verdict(
        stamp, live_head=live_head, live_runtime=live_runtime
    )
    if args.as_json:
        print(_json.dumps({"ok": ok, "cell": cell, "reason": reason,
                           "stamp": stamp}, indent=2))
    else:
        state = "GREEN" if ok else ("RED" if ok is False else "UNVERIFIED")
        print(f"test gate: {state} — {cell}")
        print(f"  {reason}")
        if ok is None and stamp is None:
            print("  repair: rag_kernel tests --run --session S<n>")
    return 0 if ok else 1


def cmd_forensics(args: argparse.Namespace) -> int:
    """Render a session's conduct from its log (SELF-DIAGNOSIS-UNSOURCED, S188).

    S187 explained a four-hour session by naming a five-second event. It had the log
    open and did not read it. This verb is the answer to "why did that take so long":
    the denominators, the failures with their REAL cost, the silent gaps that
    actually held the time, the repeat bursts that shadow polling, and whether the
    session sealed more than once. It measures conduct, not intent.
    """
    import json as _json

    from rag_kernel import session_forensics as sf
    from rag_kernel.session_logger import LOG_FILE_PREFIX, LOG_FILE_EXT

    if args.log:
        log_path = args.log.resolve()
    else:
        rag_dir = args.rag.resolve().parent
        if args.session_id:
            log_path = rag_dir / f"{LOG_FILE_PREFIX}{args.session_id}{LOG_FILE_EXT}"
        else:
            logs = sorted(rag_dir.glob(f"{LOG_FILE_PREFIX}*{LOG_FILE_EXT}"),
                          key=lambda p: p.stat().st_mtime)
            if not logs:
                print(f"Error: no session logs under {rag_dir}", file=sys.stderr)
                return 1
            log_path = logs[-1]

    try:
        facts = sf.analyze_file(log_path)
    except sf.ForensicsError as ex:
        print(f"Error: {ex}", file=sys.stderr)
        return 1

    if args.as_json:
        print(_json.dumps({
            "session_id": facts.session_id,
            "wall_seconds": facts.wall_seconds,
            "invocations": facts.invocations,
            "failures": facts.failures,
            "failure_seconds": facts.failure_seconds,
            "gaps": facts.gaps,
            "gap_seconds": facts.gap_seconds,
            "gap_share": facts.gap_share,
            "bursts": facts.bursts,
            "session_ends": facts.session_ends,
            "double_sealed": facts.double_sealed,
            "mutations_after_first_end": facts.mutations_after_first_end,
        }, indent=2))
    else:
        print(sf.render_text(facts))
    return 0


def cmd_prune_current_status(args: argparse.Namespace) -> int:
    """Remove ARCHIVED session-stamped keys from current_status (META-SETTER-GAP residue).

    ``refresh-current-status`` re-stamps machine-fact TOKENS; it has no way to remove
    a KEY. So the 22 ``next_session_directive_S<n>`` snapshots, ``session_finding_
    S77_E045`` and ``fv_phase3_S35`` sat on the live status surface with no governed
    path to clear them — the exact "requires a hand edit that tool_contract forbids"
    shape META-SETTER-GAP names. This is that path.

    The archived predicate is single-sourced in ``drift_store`` and shared with the
    auditor, so the verb can only ever remove what the auditor flags; naming a live
    field REFUSES. Same write contract as its siblings: load -> select -> atomic
    write (tmp -> verify -> .bak parity -> rename). Idempotent; a no-op when clean.
    """
    from rag_kernel.drift_store import (
        CurrentStatusRefreshError,
        DriftStoreError,
        archived_current_status_keys,
        load_hot,
        prune_current_status_file,
    )

    rag_path = args.rag.resolve()
    if not rag_path.exists():
        print(f"Error: RAG file not found: {rag_path}", file=sys.stderr)
        return 1

    try:
        if args.list:
            stale = archived_current_status_keys(load_hot(rag_path))
            print(f"current_status archived keys ({len(stale)}):")
            for k in stale:
                print(f"  {k}")
            if not stale:
                print("  (none — the live status surface is clean)")
            return 0
        removed, wrote = prune_current_status_file(
            rag_path,
            keys=args.keys or None,
            dry_run=args.dry_run,
        )
    except (CurrentStatusRefreshError, DriftStoreError) as ex:
        print(f"Error: {ex}", file=sys.stderr)
        return 1

    header = (
        "[DRY RUN] current_status would prune" if args.dry_run
        else ("current_status pruned" if wrote else "current_status already clean")
    )
    print(f"{header} [session {args.session}] — {len(removed)} archived key(s):")
    for k in removed:
        print(f"  - {k}")
    if not removed:
        print("  (none — no session-stamped key remains)")
    if wrote:
        print("  .bak refreshed to byte-parity (HOT == BAK).")
    return 0


def cmd_migrate(args: argparse.Namespace) -> int:
    """Governed schema/version migration of a DEPLOYMENT's RAG (KA-SCHEMA-MIGRATE).

    The kernel is deployed onto other projects; redeploying the pinned package does
    not move a deploy's ``meta.schema_version`` / ``meta.policy_version``, so new
    code silently drives an old-shaped RAG and the only prior remedy was a forbidden
    hand-edit of another project's canonical state. This verb reads the TARGET's own
    meta, resolves a declared ladder path, and applies it atomically (tmp -> .bak
    parity -> rename). Direction is never assumed: a target AHEAD of this kernel is
    refused, not downgraded (at S159 the eBay clone ran policy_version 3.2.7 against
    this kernel's then-current 3.2.6; the S160 self-adoption of v3.2.7 closed that
    gap). Idempotent — already-current is a no-op with no write. Exit 0 on
    success/no-op, 1 on any fail-loud condition.
    """
    from rag_kernel.schema_migrate import SchemaMigrateError, migrate_file

    try:
        plan, wrote = migrate_file(
            args.rag,
            session=args.session,
            spec_version=args.spec_version,
            dry_run=args.dry_run,
        )
    except SchemaMigrateError as ex:
        print(f"Error: {ex}", file=sys.stderr)
        return 1

    if plan.is_noop:
        header = "migrate: already current — no change"
    elif args.dry_run:
        header = "[DRY RUN] migrate would apply"
    else:
        header = "migrate applied"
    print(f"{header} [session {args.session}]:")

    # Line-by-line render of every planned change (STRICT-OBEY rendering discipline —
    # never collapse a plan into a bare count).
    print(f"  schema_version: {plan.schema_from} -> {plan.schema_to}"
          f"{' (unchanged)' if not plan.steps else ''}")
    for step in plan.steps:
        print(f"    step {step.from_version} -> {step.to_version}: {step.description}")
    for note in plan.notes:
        print(f"      - {note}")
    if plan.policy_action == "advanced":
        print(f"  policy_version: {plan.policy_from} -> {plan.policy_to}")
    elif plan.policy_action == "ahead-preserved":
        print(f"  policy_version: {plan.policy_from} is AHEAD of this kernel's "
              f"{plan.policy_to} — PRESERVED (never downgraded)")
    elif plan.policy_action == "absent":
        print("  policy_version: absent on target — left absent (not fabricated)")
    else:
        print(f"  policy_version: {plan.policy_from} (already current)")
    if plan.init_prompt_to:
        how = ("paired to advancing policy_version"
               if plan.init_prompt_action == "paired-on-advance"
               else "repaired to policy_version (pointer was stale)")
        print(f"  rag_files.init_prompt: {plan.init_prompt_from} -> "
              f"{plan.init_prompt_to} ({how})")
    if plan.cold_action == "repaired":
        if plan.cold_version_to is not None:
            print(f"  COLD init_prompt_reference.version: {plan.cold_version_from} -> "
                  f"{plan.cold_version_to} (repaired to policy_version)")
        if plan.cold_filename_to is not None:
            print(f"  COLD init_prompt_reference.filename: {plan.cold_filename_from} -> "
                  f"{plan.cold_filename_to} (repaired to policy_version)")
    print("  rag_version / tracked_items / operating_protocol: untouched "
          "(project-owned state)")
    if wrote:
        print("  written atomically; .bak refreshed to byte-parity (HOT == BAK).")
    return 0


def cmd_transplant(args: argparse.Namespace) -> int:
    """Governed scaffold transplant (TRANSPLANT-CLASSIFY-AUTHORITY).

    Moves ONLY the first class of divergence — universal governance rules the target
    is MISSING — from a source kernel into a target deployment, leaving the target's
    own project-specific rules invisible and untouched. Classification is Authority A
    (operator-ratified S160): a rule is universal iff its key appears in the named INIT
    spec. Additive-only; a universal rule the target has locally amended is a fail-loud
    collision, never an overwrite; a target ahead of the spec is refused; idempotent;
    atomic (FIX-4 .bak parity) with a meta.transplants audit entry. Exit 0 on
    success/no-op, 1 on any fail-loud condition (unknown spec, collision, target ahead,
    source incomplete).
    """
    from rag_kernel.transplant import (
        TransplantError,
        transplant_file,
    )

    try:
        plan, wrote = transplant_file(
            args.rag,
            args.source,
            args.spec,
            session=args.session,
            dry_run=args.dry_run,
        )
    except TransplantError as ex:
        print(f"Error: {ex}", file=sys.stderr)
        return 1

    if plan.is_noop and not args.dry_run:
        header = "transplant: target already carries every universal rule — no change"
    elif args.dry_run:
        header = "[DRY RUN] transplant would apply"
    else:
        header = "transplant applied"
    print(f"{header} [session {args.session}]:")
    print(f"  authority: A (spec-derived) — spec v{plan.spec_version} "
          f"({Path(args.spec).name})")
    print(f"  source kernel: {plan.source_version}   target: {plan.target_version}")

    # Line-by-line render of every planned change (STRICT-OBEY — never a bare count).
    if plan.additions:
        print(f"  additions ({len(plan.additions)}) — universal rules missing from target:")
        for key, _ in plan.additions:
            print(f"    + {key}")
    else:
        print("  additions (0): none — nothing to add")
    if plan.collisions:
        print(f"  COLLISIONS ({len(plan.collisions)}) — universal rules the target has "
              f"locally amended (FAIL-LOUD, nothing written):")
        for key, _t, _s in plan.collisions:
            print(f"    ! {key} — target content differs from source; overwrite forbidden")
    print(f"  project-specific rules: invisible — untouched "
          f"({len(plan.present_identical)} universal already-identical, skipped)")

    if plan.collisions:
        # Contract §2/§4: a collision is a fail-loud condition (exit 1) whether or not
        # this was a dry run. A real run already raised before any write; a dry run
        # rendered the collisions above and now signals the same fail-loud via exit code.
        print("  RESULT: HALT — resolve collisions by operator ruling, then re-run.",
              file=sys.stderr)
        return 1
    if wrote:
        print("  written atomically; .bak refreshed to byte-parity (HOT == BAK); "
              "meta.transplants stamped.")
    return 0


def cmd_birth_adopt(args: argparse.Namespace) -> int:
    """Governed value-adoption between deployments (BIRTH-ADOPT-VERB, S181).

    Where ``transplant`` moves rules a target is MISSING and halts fail-loud on
    every key present with differing content, this verb moves the CONTENT of
    keys both sides already have — the case that leaves a clone frozen. It never
    guesses direction: the INIT spec is a third reference point (a side holding
    verbatim spec text has never hardened that key), provenance breaks the
    remaining ties, and a genuine tie is REFUSED with the keys named.

    Exit 0 on success/no-op, 1 on any fail-loud condition (unknown spec,
    undecidable divergence without a ruling, stale target provenance).
    """
    from rag_kernel.birth_adopt import (
        ADOPTABLE,
        AdoptError,
        Direction,
        adopt_file,
        adoption_complete,
        render_diff,
    )

    decisions: dict[str, str] = {}
    for raw in (args.decisions or []):
        if "=" not in raw:
            print(f"Error: --decide expects KEY=source|target, got {raw!r}",
                  file=sys.stderr)
            return 1
        key, _, side = raw.partition("=")
        side = side.strip().lower()
        if side not in ("source", "target"):
            print(f"Error: --decide side must be 'source' or 'target', got {side!r}",
                  file=sys.stderr)
            return 1
        decisions[key.strip()] = side

    try:
        diff, result, wrote = adopt_file(
            args.rag,
            args.source,
            args.spec,
            session=args.session,
            mode=args.mode,
            keys=args.keys,
            decisions=decisions,
            dry_run=args.dry_run,
            force=args.force,
        )
    except AdoptError as ex:
        print(f"Error: {ex}", file=sys.stderr)
        return 1

    print(render_diff(diff, limit=args.limit))
    print()

    if args.mode == "diff":
        # The mandatory first mode: report both directions and stop. A diff that
        # finds undecidables has done its job — it surfaced them before a write.
        undecided = diff.undecidable
        if undecided:
            print(f"  {len(undecided)} key(s) UNDECIDABLE — neither side matches the "
                  f"spec and provenance is absent or tied. Rule a side with "
                  f"`--decide KEY=source|target` before adopting:")
            for key in undecided:
                print(f"    ? {key}")
        back = diff.by(Direction.TARGET_TO_SOURCE, Direction.ADD_TO_SOURCE)
        if back:
            print(f"  {len(back)} key(s) where the TARGET is ahead — back-flow "
                  f"candidates this verb will NOT move (run it in reverse to adopt "
                  f"them here):")
            for entry in back:
                print(f"    < {entry.key}")
        return 0

    assert result is not None
    header = "[DRY RUN] would apply" if args.dry_run else (
        "applied" if wrote else "no change"
    )
    print(f"birth-adopt {args.mode}: {header} [session {args.session}]")
    if result.applied:
        print(f"  applied ({len(result.applied)}):")
        for key, direction in result.applied:
            print(f"    > {key}  [{direction.value}]")
    else:
        print("  applied (0): nothing to move")
    if result.refused:
        print(f"  refused ({len(result.refused)}):")
        for key, why in result.refused:
            print(f"    ! {key} — {why}")

    # GATE-OR-HOPE: state the decidable exit predicate, do not merely assert done.
    if args.mode == "adopt" and not args.dry_run:
        remaining = [e for e in diff.entries
                     if e.direction in ADOPTABLE and e.key not in
                     {k for k, _ in result.applied}]
        ok, verdict = adoption_complete(diff) if not result.applied else (
            not remaining,
            "adoption COMPLETE: no adoptable move remains, none undecidable"
            if not remaining else
            f"adoption INCOMPLETE: {len(remaining)} adoptable move(s) remain",
        )
        print(f"  exit predicate: {verdict}")
        if not ok:
            return 1
    if wrote:
        print("  written atomically; .bak refreshed to byte-parity (HOT == BAK); "
              "meta.rule_provenance + meta.init_spec stamped.")
    return 0


def cmd_measured(args: argparse.Namespace) -> int:
    """Inspect / emit MEASURED provenance stamps (RUNBOOK-TABLE-NO-INVARIANT, S187).

    ``--stamp`` prints the stamp a re-measuring session should paste into its
    document (it does NOT write: the session that re-measured is the only party
    entitled to say what it measured). Default lists every stamp found under the
    scanned roots and whether the live world has outrun it — the same predicate the
    auditor uses, so `measured` and `audit` can never disagree.
    """
    import json as _json

    from rag_kernel.drift_audit import canonical_facts
    from rag_kernel.measured import format_stamp, scan_measurements, stale_measurements

    version, _mc, _sha = canonical_facts()
    try:
        from rag_kernel import __spec_version__ as live_spec
    except Exception:  # noqa: BLE001
        live_spec = ""

    if args.stamp:
        print(format_stamp(session=args.session or "S?", runtime=version or "",
                           spec=live_spec or ""))
        return 0

    # RESOLVE before deriving roots: a relative --rag ("RAG_MASTER.json") has
    # Path.parent == "." , so the unresolved form scanned the CWD and silently
    # reported zero stamps while `audit` found them. Found by running both.
    _rag_abs = Path(args.rag).resolve()
    _default_roots = [_rag_abs.parent.parent, _rag_abs.parent]
    roots = [Path(r).resolve() for r in (args.roots or _default_roots)]
    docs: list[Path] = []
    seen: set[Path] = set()
    for r in roots:
        try:
            for d in sorted(r.glob("*.md")):
                if d not in seen:
                    seen.add(d)
                    docs.append(d)
        except OSError:
            continue

    found = []
    for d in docs:
        try:
            found.extend(scan_measurements(d.read_text(encoding="utf-8",
                                                       errors="replace"), path=str(d)))
        except OSError:
            continue
    stale = {(m.path, m.line): reasons
             for m, reasons in stale_measurements(docs, live_runtime=version or "",
                                                  live_spec=live_spec or "")}

    if args.as_json:
        print(_json.dumps([{
            "path": m.path, "line": m.line, "session": m.session,
            "runtime": m.runtime, "spec": m.spec,
            "stale": (m.path, m.line) in stale,
            "reasons": stale.get((m.path, m.line), []),
        } for m in found], indent=2))
        return 1 if stale else 0

    print(f"MEASURED stamps found: {len(found)}   stale: {len(stale)}   "
          f"(live runtime {version}, spec {live_spec})")
    for m in found:
        reasons = stale.get((m.path, m.line))
        mark = "STALE" if reasons else "ok   "
        print(f"  [{mark}] {Path(m.path).name}:{m.line} "
              f"session={m.session or '—'} runtime={m.runtime or '—'} spec={m.spec or '—'}")
        for r in reasons or []:
            print(f"           {r}")
    if not found:
        print("  (no document under the scanned roots carries a MEASURED stamp)")
    return 1 if stale else 0


def cmd_list_kinds(args: argparse.Namespace) -> int:
    """Print the INGEST kinds this deployment declares (INGEST-KIND-UNVALIDATED).

    The enumerable half of the ingest contract. ``ingest`` now REFUSES any kind
    outside this set; a sender who cannot read the set has no way to comply and
    will invent one — which is exactly how a parent handoff declaring four
    fabricated kinds landed as a satisfied exit predicate. Rendered from the same
    ``KINDS`` / ``DESTINATION`` data the refusal enforces, so the published surface
    and the gate cannot drift apart. Read-only; always exit 0.
    """
    import json as _json

    from rag_kernel.ingest import declared_kinds

    rows = declared_kinds()
    if args.as_json:
        print(_json.dumps(rows, indent=2))
        return 0
    print(f"INGEST kinds declared by this deployment ({len(rows)}):")
    for r in rows:
        print(f"  {r['kind']:<12} -> {r['destination']}")
    print()
    print("Declare a claim with:  INGEST: <KIND> <id> — <text>")
    print("Any other kind is REFUSED — a sender cannot introduce a kind the")
    print("receiver does not define (HANDOFF-PRESCRIPTION-BAN).")
    return 0


def cmd_ingest(args: argparse.Namespace) -> int:
    """Document ingestion with a decidable exit predicate (B4, S181).

    Read-only: it plans, renders, and states whether the deployment already
    answers what the document answers. It never writes — the landing records it
    names are created through their own governed verbs (add / add-rule /
    register-asset), so ingestion cannot become a second write path into
    canonical state. Exit 0 when ingestion is COMPLETE, 1 while it is not.
    """
    import json as _json

    from rag_kernel.ingest import (
        IngestError,
        ingest_complete,
        plan_ingest,
        render_plan,
    )

    try:
        rag = _json.loads(Path(args.rag).read_text(encoding="utf-8"))
    except (OSError, ValueError) as ex:
        print(f"Error: cannot read RAG {args.rag}: {ex}", file=sys.stderr)
        return 1

    rag_dir = Path(args.rag_dir) if args.rag_dir else Path(args.rag).parent
    context = None
    ctx_path = rag_dir / "RAG_CONTEXT.json"
    if ctx_path.exists():
        try:
            context = _json.loads(ctx_path.read_text(encoding="utf-8"))
        except ValueError:
            context = None

    try:
        plan = plan_ingest(args.document, rag, context=context)
    except IngestError as ex:
        print(f"Error: {ex}", file=sys.stderr)
        return 1

    ok, verdict = ingest_complete(plan, rag, context=context)

    if args.as_json:
        print(_json.dumps({
            "document": plan.document,
            "counts": plan.counts,
            "routes": [
                {"id": r.claim.id, "kind": r.claim.kind,
                 "destination": r.destination, "action": r.action,
                 "landing_id": r.landing_id, "basis": r.basis,
                 "explicit": r.claim.explicit}
                for r in plan.routes
            ],
            "unrouted": [c.id for c in plan.unrouted],
            "complete": ok,
            "verdict": verdict,
        }, indent=2))
        return 0 if ok else 1

    print(render_plan(plan, limit=args.limit))
    print()
    print(f"  exit predicate: {verdict}")
    if not ok:
        print("  Land the missing records through their governed verbs "
              "(add / add-rule / register-asset), then re-run.")
    return 0 if ok else 1


def cmd_decide(args: argparse.Namespace) -> int:
    """Record an operator ruling as governed state (B5, S181)."""
    import json as _json

    from rag_kernel.decision_ledger import DecisionError, record_decision
    from rag_kernel.persistence import atomic_write_json

    rag_path = Path(args.rag)
    try:
        rag = _json.loads(rag_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as ex:
        print(f"Error: cannot read RAG {rag_path}: {ex}", file=sys.stderr)
        return 1

    try:
        decision = record_decision(
            rag,
            session=args.session,
            question=args.question,
            options=args.options or [],
            chosen=args.chosen,
            rationale=args.rationale,
            binds=args.binds,
            supersedes=args.supersedes,
        )
    except DecisionError as ex:
        print(f"Error: {ex}", file=sys.stderr)
        return 1

    prefix = "[DRY RUN] would record" if args.dry_run else "recorded"
    print(f"{prefix} {decision.id} [session {decision.session}]")
    print(f"  Q: {decision.question}")
    for opt in decision.options:
        print(f"   {'>' if opt == decision.chosen else ' '} {opt}")
    if decision.rationale:
        print(f"  why: {decision.rationale}")
    if decision.binds:
        print(f"  binds: {', '.join(decision.binds)}")
    if decision.supersedes:
        print(f"  supersedes: {decision.supersedes}")
    if args.dry_run:
        return 0
    atomic_write_json(rag_path, rag, mirror_bak=True, guard_side_stores=True)
    print("  written atomically; .bak refreshed to byte-parity (HOT == BAK).")
    return 0


def cmd_decisions(args: argparse.Namespace) -> int:
    """Render the decision ledger (read-only)."""
    import json as _json

    from rag_kernel.decision_ledger import decisions_for, render_ledger

    try:
        rag = _json.loads(Path(args.rag).read_text(encoding="utf-8"))
    except (OSError, ValueError) as ex:
        print(f"Error: cannot read RAG {args.rag}: {ex}", file=sys.stderr)
        return 1

    if args.item:
        found = decisions_for(rag, args.item)
        print(f"decision ledger — {len(found)} ruling(s) binding {args.item}")
        for rec in found:
            print(f"\n  {rec.get('id')} [{rec.get('session')}] "
                  f"chose: {rec.get('chosen')}")
            print(f"    Q: {str(rec.get('question'))[:160]}")
        return 0

    print(render_ledger(rag, limit=args.limit, live_only=args.live))
    return 0


def _resolve_context_dir(rag_dir: Path) -> Path:
    """Shared with cmd_context (KA-CTX-RAGFLAG): operators habitually pass a FILE to
    a --rag*-style flag; the context store lives in a DIRECTORY. When the path is (or
    names) a file, use its containing directory instead of crashing at mkdir."""
    d = rag_dir.resolve()
    if d.is_file() or d.suffix.lower() == ".json":
        print(f"note: registry store lives in a directory, but --rag-dir named a file "
              f"({d.name}); using its directory {d.parent} instead.", file=sys.stderr)
        d = d.parent
    return d


def cmd_register_asset(args: argparse.Namespace) -> int:
    """Register a baked asset into the sanctioned baked_assets partition (REUSE-REGISTRY-GUARD).

    Additive + idempotent through the lean-RAG RAG_CONTEXT.json store: re-registering
    the same id with identical content is a no-op, a rebound id or a duplicate path is
    fail-loud (exit 1). The file being registered must exist (its sha256 is the record).
    """
    from rag_kernel.asset_registry import (
        AssetRegistryError, PARTITION_NAME, deregister_asset, normalize_path,
        register_asset,
    )

    rag_dir = _resolve_context_dir(args.rag_dir)
    project_root = args.project_root.resolve() if args.project_root else rag_dir.parent
    asset_id = args.asset_id or normalize_path(args.path, project_root)

    # ASSET-DEREGISTER-BEFORE-MOVE (S191): retiring is the counterpart of
    # registering. Without it, archiving a registered file — the sanctioned
    # way to retire something — left the registry pointing at a path that no
    # longer exists, so the only way to keep the audit clean was to leave junk
    # in the live tree.
    if getattr(args, "deregister", False):
        try:
            removed, action = deregister_asset(
                rag_dir, asset_id=asset_id, session=args.session,
                reason=getattr(args, "purpose", "") or "", dry_run=args.dry_run,
            )
        except AssetRegistryError as ex:
            print(f"Error: {ex}", file=sys.stderr)
            return 1
        print(f"register-asset: {action} [session {args.session}]:")
        print(f"  id:      {removed.get('asset_id')}")
        print(f"  path:    {removed.get('path')}")
        print(f"  sha256:  {str(removed.get('sha256'))[:16]}…")
        print("  -> the record is retired; the file itself was NOT touched.")
        return 0

    if not (args.purpose or "").strip():
        print("Error: --purpose is required when registering an asset "
              "(only --deregister may omit it)", file=sys.stderr)
        return 1

    try:
        rec, action = register_asset(
            rag_dir, asset_id=asset_id, path=args.path, purpose=args.purpose,
            session=args.session, project_root=project_root, dry_run=args.dry_run,
            update=getattr(args, "update", False),
        )
    except AssetRegistryError as ex:
        print(f"Error: {ex}", file=sys.stderr)
        return 1

    if args.dry_run:
        head = "[DRY RUN] would register" if action != "updated" else "[DRY RUN] would update"
    elif action == "idempotent":
        head = "already registered (idempotent -- no write)"
    elif action == "updated":
        head = "UPDATED in place"
    else:
        head = "registered"
    print(f"register-asset: {head} [session {args.session}]:")
    print(f"  id:      {rec.asset_id}")
    print(f"  path:    {rec.path}")
    print(f"  purpose: {rec.purpose}")
    print(f"  sha256:  {rec.sha256}")
    if rec.supersedes:
        prior = rec.supersedes[-1]
        print(f"  supersedes: sha256 {str(prior.get('sha256', ''))[:12]}… "
              f"registered {prior.get('registered_utc')} by session {prior.get('session')} "
              f"({len(rec.supersedes)} prior revision(s) retained)")
    if action == "created" and not args.dry_run:
        print(f"  -> appended to RAG_CONTEXT.json[{PARTITION_NAME}] (non-loaded store; no .bak).")
    if action == "updated" and not args.dry_run:
        print(f"  -> record rewritten in RAG_CONTEXT.json[{PARTITION_NAME}]; the id and its "
              f"path are unchanged, only the content hash and its lineage.")
    return 0


def cmd_hook_guard(args: argparse.Namespace) -> int:
    """HOOK-ENFORCEMENT-LAYER (S195): evaluate one hook payload, or self-test.

    Two modes, one purpose — make a process rule refusable instead of remembered:

    ``--gate <name>``   read a Claude Code hook payload on stdin and emit the
                        hook-contract verdict. Wired from ``.claude/settings.json``;
                        never invoked by hand in normal operation.
    ``--selftest``      drive every gate through a known-bad payload and assert
                        the refusal. This exists because the layer's ONE hope is
                        its own liveness: a hook that silently stopped running
                        looks exactly like a session with no violations.
    """
    if args.selftest:
        failures, lines = selftest(state_dir=args.state_dir)
        for line in lines:
            print(line)
        if failures:
            print(f"\nHOOK-ENFORCEMENT-LAYER: {failures} gate(s) NOT gating.",
                  file=sys.stderr)
            return 1
        print("\nHOOK-ENFORCEMENT-LAYER: every gate refused its known-bad payload.")
        return 0
    if not args.gate:
        print("Error: --gate is required (or use --selftest); known gates: "
              + ", ".join(hook_guard.GATES), file=sys.stderr)
        return 2
    if args.gate not in hook_guard.GATES:
        print(f"Error: unknown gate {args.gate!r}; known gates: "
              + ", ".join(hook_guard.GATES), file=sys.stderr)
        return 2
    return run_gate(args.gate, sys.stdin.read(), state_dir=args.state_dir,
                    project_root=args.project_root)


def cmd_status(args: argparse.Namespace) -> int:
    """OPERATOR-ONE-NUMBER — one line, one exit code, run by the operator.

    Prints the verdict and nothing else unless asked. The whole point is that it
    is shorter than any report an agent could write about it.
    """
    from rag_kernel import operator_status

    verdict = operator_status.compose(args.rag)
    if getattr(args, "json_output", False):
        print(json.dumps({
            "green": verdict.green,
            "headline": verdict.headline(),
            "terms": [{"name": t.name, "ok": t.ok, "detail": t.detail}
                      for t in verdict.terms],
        }, indent=2))
        return verdict.exit_code()
    print(verdict.headline())
    if getattr(args, "verbose", False):
        for term in verdict.terms:
            print(term.render())
    return verdict.exit_code()


def cmd_reuse_check(args: argparse.Namespace) -> int:
    """Pre-write reuse guard (REUSE-REGISTRY-GUARD): report baked assets already
    covering a path/purpose. Fail-loud (exit 1) on a hit so the caller reuses instead
    of re-authoring; exit 0 when nothing is baked yet. Never writes."""
    import json as _json
    from rag_kernel.asset_registry import AssetRegistryError, reuse_check

    rag_dir = _resolve_context_dir(args.rag_dir)
    project_root = args.project_root.resolve() if args.project_root else rag_dir.parent

    # RULE-25 REPAIR (S190, P2). Rule 25 commands "run `rag_kernel reuse-check`
    # BEFORE authoring anything new"; the bare invocation the rule names answered
    # `Error: reuse_check needs at least one of path or purpose`, so the S189
    # audit concluded the verb did not exist at all. It does. A bare call now
    # RENDERS THE REGISTRY — the one thing an author about to write something
    # wants to see — and is silent about failure, because nothing was claimed.
    if not args.path and not args.purpose:
        from rag_kernel.asset_registry import list_assets
        try:
            assets = list_assets(rag_dir)
        except AssetRegistryError as ex:
            print(f"Error: {ex}", file=sys.stderr)
            return 1
        print(f"reuse-check: {len(assets)} baked asset(s) registered. Narrow with "
              "--path <candidate> or --purpose <what you are about to write>.")
        for a in assets:
            purpose = getattr(a, "purpose", "") or ""
            print(f"  - {getattr(a, 'path', '') or getattr(a, 'asset_id', '')}"
                  + (f"  :: {purpose[:90]}" if purpose else ""))
        return 0

    try:
        hits = reuse_check(
            rag_dir, path=args.path, purpose=args.purpose, project_root=project_root
        )
    except AssetRegistryError as ex:
        print(f"Error: {ex}", file=sys.stderr)
        return 1

    if getattr(args, "json_output", False):
        print(_json.dumps({"ok": not hits, "hits": [h.to_dict() for h in hits]}, indent=2))
        return 1 if hits else 0

    fleet_hits = []
    if getattr(args, "fleet", False) and args.purpose:
        from rag_kernel.inventory import fleet_reuse_check
        fleet_hits = fleet_reuse_check(rag_dir, args.purpose)

    if not hits and not fleet_hits:
        crit = args.path if args.path is not None else args.purpose
        suffix = "" if getattr(args, "fleet", False) else \
            " (local only — add --fleet to search sibling deployments)"
        print(f"reuse-check: CLEAR -- no baked asset covers {str(crit)!r}; "
              f"safe to author.{suffix}")
        return 0

    if not hits and fleet_hits:
        print(f"reuse-check: FLEET REUSE -- {len(fleet_hits)} artifact(s) in SIBLING "
              f"deployments already cover this. Read them before authoring:")
        for h in fleet_hits[:20]:
            mark = "" if h.registered else "  [unregistered at home]"
            print(f"  - [{h.deployment}] {h.rel}{mark}"
                  + (f"  purpose: {h.purpose}" if h.purpose else ""))
        return 1
    print(f"reuse-check: REUSE -- {len(hits)} baked asset(s) already cover this "
          f"(do NOT rewrite; reuse):")
    for h in hits:
        print(f"  - {h.asset_id}  [{h.path}]  purpose: {h.purpose}  sha256:{h.sha256[:12]}")
    return 1


def cmd_session_delta(args: argparse.Namespace) -> int:
    """SESSION-DELTA-RITUAL: emit the measured debit/credit report for a session.

    Read-only unless ``--save-baseline`` is given. Exit 0 always: an absent
    baseline or an unmeasurable counter is reported IN the document, not as a
    failure — a close that refuses because git was missing would teach people to
    skip the ritual, which is how the hand-written version survived this long.
    """
    import json as _json
    from typing import Optional as _Optional  # noqa: F401 — used by the helper below
    from rag_kernel import session_delta as sd

    rag_path = Path(args.rag).resolve()
    try:
        hot = _json.loads(rag_path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as ex:
        print(f"session-delta: cannot read {rag_path}: {ex}", file=sys.stderr)
        return 1
    rag_dir = rag_path.parent
    session = args.session or (hot.get("meta") or {}).get("written_by_session") or ""
    if not session:
        print("session-delta: no --session and meta.written_by_session is empty",
              file=sys.stderr)
        return 1

    repo = Path(args.repo).resolve() if args.repo else _guess_repo_root(rag_dir)
    counters = sd.collect_counters(
        hot, rag_dir=rag_dir, project_root=rag_dir.parent, repo_root=repo,
        audit_errors=args.audit_errors, audit_warnings=args.audit_warnings,
    )
    delta = sd.compute(hot, session, baseline=sd.load_baseline(rag_dir),
                       counters_after=counters)

    text = _json.dumps(delta.to_dict(), indent=2, ensure_ascii=False) \
        if args.json_output else sd.render(delta)
    print(text)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
        print(f"\nsession-delta: written to {args.out}")
    if args.save_baseline:
        sd.save_baseline(
            rag_dir, session, counters,
            item_ids=[str(i.get("id", "")) for i in (hot.get("tracked_items") or [])
                      if isinstance(i, dict)],
        )
        print(f"session-delta: baseline recorded for {session} "
              f"(the next session diffs against it)")
    return 0


def _guess_repo_root(rag_dir: Path):
    """Best-effort git worktree for this deployment.

    This project keeps its source in a SIBLING worktree, not under the RAG, so a
    check that only looked at the RAG dir would report `not measured` forever on
    the deployment it was written for.
    """
    candidates = [rag_dir, rag_dir.parent]
    wt = rag_dir.parent / "GIT WORKTREES"
    if wt.is_dir():
        candidates.extend(sorted(p for p in wt.iterdir() if p.is_dir()))
    for c in candidates:
        if (c / ".git").exists():
            return c
    return None


def cmd_inventory(args: argparse.Namespace) -> int:
    """FLEET-INVENTORY: scan / backfill / fleet.

    Exit 0 on success. ``scan`` exits 1 when reusable work is unregistered —
    fail-loud, because an unregistered asset is capability that ``reuse-check``
    cannot see, which is how it gets rewritten.
    """
    import json as _json
    from rag_kernel import inventory as inv

    rag_dir = _resolve_context_dir(args.rag_dir)
    root = args.root.resolve() if args.root else Path(rag_dir).parent

    if args.mode == "scan":
        report = inv.scan(root)
        pending = inv.unregistered(root, rag_dir)
        if args.json_output:
            payload = report.to_dict(limit=args.limit)
            payload["unregistered"] = [f.to_dict() for f in pending[:args.limit]]
            print(_json.dumps(payload, indent=2))
        else:
            print(report.render(limit=args.limit))
            if pending:
                print(f"\n  UNREGISTERED reusable work: {len(pending)} "
                      f"— invisible to reuse-check, therefore liable to be rewritten:")
                for f in pending[:args.limit]:
                    print(f"    [{f.cls}] {f.rel}")
                if len(pending) > args.limit:
                    print(f"    … {len(pending) - args.limit} more")
                print("  Fix: `rag_kernel inventory backfill --session <SID>`")
            else:
                print("\n  All reusable work is registered.")
        return 1 if pending else 0

    if args.mode == "backfill":
        if not args.session:
            print("inventory backfill: --session is required (audit trail)",
                  file=sys.stderr)
            return 2
        from rag_kernel.asset_registry import AssetRegistryError, register_asset
        pending = inv.unregistered(root, rag_dir)
        if not pending:
            print("inventory backfill: nothing to do — registry already complete.")
            return 0
        if args.dry_run:
            print(f"[DRY RUN] would register {len(pending)} asset(s):")
            for f in pending[:args.limit]:
                print(f"  [{f.cls}] {f.rel}")
            if len(pending) > args.limit:
                print(f"  … {len(pending) - args.limit} more")
            return 0
        done = failed = 0
        for f in pending:
            try:
                register_asset(
                    rag_dir,
                    asset_id=f.rel,
                    path=Path(root) / f.rel,
                    purpose=(f"BACKFILLED {args.session}: {f.cls} "
                             f"{Path(f.rel).stem.replace('_', ' ')} — purpose not yet "
                             f"described; refine with register-asset when its intent "
                             f"is confirmed."),
                    session=args.session, project_root=root)
                done += 1
            except AssetRegistryError as ex:
                failed += 1
                print(f"  SKIP {f.rel}: {ex}", file=sys.stderr)
        print(f"inventory backfill: registered {done}, skipped {failed}, "
              f"of {len(pending)} candidate(s).")
        return 0 if failed == 0 else 1

    # -- fleet -------------------------------------------------------------- #
    deployments: list[dict] = []
    for spec in (args.deployment or []):
        name, _, path = spec.partition("=")
        if not path:
            print(f"inventory fleet: expected NAME=ROOT, got {spec!r}", file=sys.stderr)
            return 2
        p = Path(path).resolve()
        deployments.append({
            "name": name,
            "root": str(p),
            "rag_dir": str(p / "RAG") if (p / "RAG").is_dir() else str(p),
        })
    if not deployments:
        deployments = inv.fleet_config(rag_dir)
    if not deployments:
        print("inventory fleet: no deployments given and no `fleet` context "
              "partition declared. Pass --deployment NAME=ROOT.", file=sys.stderr)
        return 2

    entries = inv.fleet_scan(deployments)
    unregistered_n = sum(1 for e in entries if not e.registered)
    if args.dry_run or args.json_output:
        payload = {
            "deployments": [d["name"] for d in deployments],
            "entries": len(entries), "unregistered": unregistered_n,
            "sample": [e.to_dict() for e in entries[:args.limit]],
        }
        print(_json.dumps(payload, indent=2))
        return 0

    from rag_kernel.cold_manager import ProjectContextManager
    mgr = ProjectContextManager.default(Path(rag_dir))
    mgr.path.parent.mkdir(parents=True, exist_ok=True)
    mgr.update_partition(inv.FLEET_PARTITION, {
        "_protocol": ("FLEET-INVENTORY. The reusable surface of every declared sibling "
                      "deployment. Search it with `reuse-check --fleet` BEFORE authoring: "
                      "a sibling's unregistered script is still prior art. Regenerate "
                      "with `rag_kernel inventory fleet`."),
        "entries": [e.to_dict() for e in entries],
    })
    print(f"inventory fleet: {len(entries)} reusable artifact(s) across "
          f"{len(deployments)} deployment(s) -> RAG_CONTEXT.json[{inv.FLEET_PARTITION}]")
    print(f"  {unregistered_n} of them are UNREGISTERED in their own deployment "
          f"— visible here, invisible at home.")
    for e in entries[:args.limit]:
        print(f"    [{e.deployment}] {e.rel}")
    if len(entries) > args.limit:
        print(f"    … {len(entries) - args.limit} more")
    return 0


def cmd_wait_for(args: argparse.Namespace) -> int:
    """WAIT-PRIMITIVE: block on a sentinel, return a bounded tail, fail loud.

    The sanctioned answer to "the job is detached, now what". Reads nothing but
    the sentinel file, writes nothing at all, and imports no kernel state -- so
    it is usable before a RAG exists, which is precisely when a clone-birth
    runbook needs to wait on a long init.
    """
    import json as _json
    from rag_kernel.wait_primitive import EXIT_USAGE, WaitError, wait_for

    try:
        result = wait_for(
            args.path,
            args.timeout,
            poll_ms=args.poll_ms,
            contains=args.contains,
            emit_lines=args.emit_lines,
        )
    except WaitError as ex:
        print(f"wait-for: usage error: {ex}", file=sys.stderr)
        return EXIT_USAGE
    except KeyboardInterrupt:
        # An interrupted wait is NOT a completed job. Say so, loudly.
        print("wait-for: INTERRUPTED -- the job was not observed to finish.",
              file=sys.stderr)
        return EXIT_USAGE

    if getattr(args, "json_output", False):
        print(_json.dumps(result.to_dict(), indent=2))
    else:
        print(result.render(), file=sys.stdout if result.ok else sys.stderr)
    return result.exit_code


def cmd_verify(args: argparse.Namespace) -> int:
    """Deterministic post-init coherence gate (FIX-2, K4/K8).

    Loads the HOT RAG and its COLD sidecar and asserts the self-version is
    consistent: HOT ``policy_version`` == COLD ``init_prompt_reference.version``,
    matching ``init_prompt`` filenames, and no surviving ``<SPEC_VERSION>``
    token. With ``--spec`` it also asserts both equal the spec's own version.
    Zero LLM, zero tokens. Exit non-zero on any finding (fail-loud gate).
    """
    import json
    from rag_kernel.spec_parser import SpecParser

    rag_path = args.rag
    if not rag_path.exists():
        print(f"Error: RAG not found: {rag_path}", file=sys.stderr)
        return 2
    cold_path = args.cold or (rag_path.parent / "RAG_COLD.json")

    def _load(p: Path) -> dict:
        # utf-8-sig tolerates a BOM (production COLD files carry one).
        with open(p, "r", encoding="utf-8-sig") as f:
            return json.load(f)

    rag = _load(rag_path)
    cold = _load(cold_path) if cold_path.exists() else None

    spec_version = ""
    if args.spec is not None:
        if not args.spec.exists():
            print(f"Error: spec not found: {args.spec}", file=sys.stderr)
            return 2
        with open(args.spec, "r", encoding="utf-8") as f:
            spec_version = SpecParser()._extract_version(f.readlines())

    findings = SpecParser.verify_coherence(rag, cold, spec_version)

    if getattr(args, "json_output", False):
        print(json.dumps({
            "ok": not findings,
            "rag": str(rag_path),
            "cold": str(cold_path) if cold else None,
            "spec_version": spec_version or None,
            "findings": findings,
        }, indent=2))
    else:
        print(f"verify: {rag_path}")
        print(f"  COLD: {cold_path if cold else '(none)'}")
        if spec_version:
            print(f"  spec version: {spec_version}")
        if findings:
            print(f"  FAIL — {len(findings)} finding(s):")
            for fnd in findings:
                print(f"    - {fnd}")
        else:
            print("  OK — HOT↔COLD self-version coherent, no placeholders.")

    return 1 if findings else 0


def cmd_context(args: argparse.Namespace) -> int:
    """Read/write the sanctioned project-context store (FIX-11 inc2 / U3).

    A thin, governed CLI over ``rag_kernel.cold_manager.ProjectContextManager`` —
    the sanctioned, NON-LOADED, lazy/partitioned/atomic ``RAG_CONTEXT.json`` store
    inc1 introduced. It gives operators a path to land project-specific context
    WITHOUT hand-editing JSON (the E-037/E-040 drift the project forbids) and
    WITHOUT the transient ``*_context.json`` side store the auditor flags (the eBay
    U3 contradiction, S80). Writes delegate to ``update_partition`` ->
    ``atomic_write_json`` (COLD-style: deliberately NO ``.bak`` mirror — the
    FIX-11 contract, distinct from the HOT FIX-4/K6 parity rule); reads lazy-load a
    single partition so an unread store costs zero boot tokens.

    Sub-actions: ``set`` (create/replace a partition), ``get`` (lazy-load + print),
    ``list`` (partitions + loaded state + token budget). Unknown ids / bad JSON
    fail LOUD (exit 1) and write nothing.
    """
    from rag_kernel.cold_manager import (
        ColdFileError,
        PartitionNotFoundError,
        ProjectContextManager,
        estimate_tokens,
    )

    action = getattr(args, "context_action", None)
    if action is None:
        print("Usage: rag_kernel context {set|get|list} [--rag-dir DIR]", file=sys.stderr)
        return 1

    rag_dir = args.rag_dir.resolve()
    # KA-CTX-RAGFLAG: operators habitually pass `--rag <RAG_MASTER.json>` (a FILE),
    # as every other verb takes. argparse prefix-matches `--rag` -> `--rag-dir`
    # (the only `--rag*` option here), so rag_dir can land on the RAG file itself;
    # building `<file>/RAG_CONTEXT.json` then crashes FileExistsError at mkdir.
    # Robustness (Rule 15 lane A): when the path is — or clearly names — a file,
    # use its containing directory instead of crashing.
    if rag_dir.is_file() or rag_dir.suffix.lower() == ".json":
        print(
            f"note: context store lives in a directory, but --rag-dir named a file "
            f"({rag_dir.name}); using its directory {rag_dir.parent} instead.",
            file=sys.stderr,
        )
        rag_dir = rag_dir.parent
    mgr = ProjectContextManager.default(rag_dir)

    if action == "set":
        # Resolve the JSON value: --value-file takes precedence over positional.
        if args.value_file is not None:
            if not args.value_file.exists():
                print(f"Error: value file not found: {args.value_file}", file=sys.stderr)
                return 1
            raw = args.value_file.read_text(encoding="utf-8")
        elif args.value is not None:
            raw = args.value
        else:
            print("Error: provide the value as JSON (positional) or via --value-file",
                  file=sys.stderr)
            return 1
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as e:
            print(f"Error: value is not valid JSON: {e}", file=sys.stderr)
            return 1

        existed = mgr.has_partition(args.partition)
        verb = "replace" if existed else "create"
        if args.dry_run:
            print(f"[DRY RUN] would {verb} partition {args.partition!r} "
                  f"(~{estimate_tokens(value)} tokens) in {mgr.path} (no write)")
            return 0
        # atomic_write_json does not mkdir its parent (cf. cmd_configure); ensure
        # the RAG dir exists so a first-write into a fresh deploy succeeds.
        mgr.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            mgr.update_partition(args.partition, value)
        except ColdFileError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1
        print(f"context {verb}d {args.partition!r} in {mgr.path} "
              f"(~{estimate_tokens(value)} tokens; no .bak — sanctioned non-loaded store).")
        return 0

    if action == "get":
        try:
            value = mgr.get(args.partition)
        except (PartitionNotFoundError, ColdFileError) as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1
        if getattr(args, "json_output", False):
            print(json.dumps(value, ensure_ascii=False))
        else:
            print(f"# {args.partition} ({mgr.path.name})")
            print(json.dumps(value, indent=2, ensure_ascii=False))
        return 0

    if action == "list":
        try:
            summary = mgr.summary()
        except ColdFileError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1
        if getattr(args, "json_output", False):
            print(json.dumps(summary, indent=2, ensure_ascii=False))
            return 0
        if not summary["partition_names"]:
            print(f"(no project-context partitions in {mgr.path})")
            return 0
        print(f"{summary['total_partitions']} partition(s) in {mgr.path} "
              f"({summary['loaded_partitions']} loaded, "
              f"~{summary['estimated_tokens']} tokens loaded):")
        loaded = set(summary["loaded_names"])
        for name in summary["partition_names"]:
            mark = "loaded" if name in loaded else "on-disk"
            print(f"  {name:<24} [{mark}]")
        return 0

    print(f"Unknown context action: {action}", file=sys.stderr)
    return 1


# ---------------------------------------------------------------------------
# Bootstrap session-log instrumentation (FIX-12 / U4)
# ---------------------------------------------------------------------------

# Verbs excluded from the central bootstrap-log wrapper:
#   session    — manages its own session_start / session_end lifecycle markers
#   serve, mcp — long-lived servers; nothing to bracket around
#   session-start / session-end — KA-6 rituals that themselves open/close the
#                                 logger; the wrapper must not touch it mid-ritual
_NO_BOOTSTRAP_LOG = frozenset(
    {"session", "session-start", "session-end", "session-resume", "serve", "mcp"}
)


def _active_session_log(rag_dir: Path) -> "Path | None":
    """Most-recently-modified bootstrap session log in ``rag_dir``, or None.

    Identifies the session a short-lived CLI process should append its real
    events to (FIX-12 / U4). Returns None when no bootstrap log is active — in
    which case CLI instrumentation is a silent no-op, preserving prior behaviour
    when no session has been started.
    """
    from rag_kernel.session_logger import LOG_FILE_PREFIX, LOG_FILE_EXT

    try:
        logs = [
            p
            for p in rag_dir.glob(f"{LOG_FILE_PREFIX}*{LOG_FILE_EXT}")
            if p.is_file()
        ]
    except OSError:
        return None
    if not logs:
        return None
    try:
        return max(logs, key=lambda p: p.stat().st_mtime)
    except OSError:
        return None


def _rag_dir_for(args: argparse.Namespace) -> Path:
    """Best-effort RAG directory for the current command (for bootstrap logging)."""
    rag = getattr(args, "rag", None)
    if rag:
        return Path(rag).resolve().parent
    rag_dir = getattr(args, "rag_dir", None)
    if rag_dir:
        return Path(rag_dir).resolve()
    return _default_rag_path().resolve().parent


def _dispatch_with_bootstrap_log(
    command: str, handler, args: argparse.Namespace
) -> int:
    """Run a CLI handler, appending a real ``tool_invocation`` event to the
    active bootstrap session log (FIX-12 / U4, comprehensive scope).

    Every instrumented verb — read-only (audit / verify / health / items / …)
    and mutating alike — records its command, exit status, and duration, so a
    deploy's ``session_log_<sid>.jsonl`` is a faithful, non-empty observability
    artifact instead of bare start/end markers.

    Observability must NEVER break the command: any logging failure is swallowed
    and the handler's own return code (or exception) is what propagates.
    """
    if command in _NO_BOOTSTRAP_LOG:
        return handler(args)

    rag_dir = _rag_dir_for(args)
    start = time.monotonic()
    rc: "int | None" = None
    exc: "BaseException | None" = None
    try:
        rc = handler(args)
        return rc
    except SystemExit as e:
        rc = e.code if isinstance(e.code, int) else 1
        exc = e
        raise
    except BaseException as e:  # noqa: BLE001 — re-raised below; logging is best-effort
        exc = e
        raise
    finally:
        try:
            duration_ms = (time.monotonic() - start) * 1000
            log_path = _active_session_log(rag_dir)
            if log_path is not None:
                from rag_kernel.session_logger import (
                    SessionLogger,
                    LOG_FILE_PREFIX,
                    LOG_FILE_EXT,
                )

                sid = (
                    log_path.name[len(LOG_FILE_PREFIX): -len(LOG_FILE_EXT)]
                    or "unknown"
                )
                real_error = exc is not None and not isinstance(exc, SystemExit)
                success = (not real_error) and rc in (0, None)
                extra: dict = {}
                if real_error:
                    extra["error_type"] = type(exc).__name__
                # FORENSICS-CALLER-ATTRIBUTION (S191, E-111). The auditor drives
                # the kernel through the same CLI the agent uses, so its own
                # probes (gc x4, audit x8) landed in the session log as agent
                # conduct: a clean session read as bursty and failing, and the
                # conduct gate would have taught the agent to declare its way
                # past someone else's calls. Attribution is stamped at the
                # source by the process that spawns the call, never inferred
                # from the verb name — a heuristic on verb names would also
                # excuse the agent's own gc/audit polling, which is real.
                extra["caller"] = os.environ.get(CALLER_ENV, CALLER_AGENT).strip() or CALLER_AGENT
                # FORENSICS-BURST-TARGET (S191, E-117). Burst detection counted
                # a verb repeating inside a window and called it polling. It
                # cannot be: `cite` x22 over 22 DISTINCT items is the scripted
                # batch PY-SCRIPT-MANDATE asks for, while `audit` x5 against the
                # same state is the violation. Without the target the two are
                # indistinguishable, so the check flagged correct work. Logging
                # what the call ACTED ON makes the distinction measurable.
                _tgt = next(
                    (getattr(args, a) for a in ("item_id", "asset_id", "rule_id", "path")
                     if isinstance(getattr(args, a, None), str)),
                    None,
                )
                if _tgt:
                    extra["target"] = _tgt
                logger = SessionLogger(
                    sid, log_dir=rag_dir, log_filename=log_path.name
                )
                logger.attach()
                logger.tool_invocation(
                    tool="cli",
                    command=command,
                    result=(f"exit {rc}" if rc is not None else "ok"),
                    success=success,
                    duration_ms=duration_ms,
                    **extra,
                )
                logger.detach()
        except Exception:
            pass  # never let observability break the command


def _cmd_run_detach_await(args: argparse.Namespace) -> int:
    """RUN-DETACH-AWAIT (S185) — thin CLI shim over rag_kernel.detach_run."""
    from rag_kernel.detach_run import cmd_run as _run
    return _run(args)


def cmd_deployment(args: argparse.Namespace) -> int:
    """Read or set meta.deployments fields through the guarded atomic store."""
    from rag_kernel.deployment_registry import (
        DeploymentRegistryError, load_deployments, set_deployment_field_in_file,
    )
    from rag_kernel.drift_store import load_hot

    rag_path = args.rag or _default_rag_path()
    try:
        if args.list or not args.field:
            deps = load_deployments(load_hot(rag_path))
            if not deps:
                print("deployment: registry is EMPTY -- every destination is refused.")
                return 0
            for key in sorted(deps):
                rec = deps[key]
                print(f"{key}")
                for k in sorted(rec):
                    print(f"    {k}: {rec[k]}")
            return 0
        if not (args.key and args.value is not None and args.session):
            print("Error: --key, --field, --value and --session are all required to set.",
                  file=sys.stderr)
            return 2
        set_deployment_field_in_file(
            rag_path, args.key, args.field, args.value, session=args.session,
        )
    except DeploymentRegistryError as ex:
        print(f"Error: {ex}", file=sys.stderr)
        return 1
    print(f"deployment {args.key}: {args.field} set [session {args.session}]")
    print("  written atomically; .bak refreshed to byte-parity.")
    return 0


def cmd_push_check(args: argparse.Namespace) -> int:
    """Refuse a push to an undeclared or mismatched destination."""
    from rag_kernel.deployment_registry import (
        DeploymentRegistryError, check_push_destination,
    )

    rag_path = args.rag or _default_rag_path()
    try:
        result = check_push_destination(
            rag_path, args.deployment, args.root, remote=args.remote,
        )
    except DeploymentRegistryError as ex:
        print(f"REFUSED: {ex}", file=sys.stderr)
        return 1
    print(f"push-check: {result.message}")
    print(f"  declared: {result.declared}")
    return 0


def cmd_adopt_preflight(args: argparse.Namespace) -> int:
    """Enumerate what a redeploy would delete in the target, and refuse."""
    from rag_kernel.adopt_preflight import PreflightError, assert_safe_to_adopt

    try:
        div = assert_safe_to_adopt(
            args.target, args.source, accept_local_loss=args.accept_local_loss,
        )
    except PreflightError as ex:
        print(f"{ex}", file=sys.stderr)
        return 1
    if div.clean:
        print("adopt-preflight: SAFE -- " + div.render())
    else:
        print("adopt-preflight: PROCEEDING UNDER --accept-local-loss; deleting:")
        print(div.render())
    return 0


def cmd_bootmap(args: argparse.Namespace) -> int:
    """ROOT-FILE-MANIFEST (S168) — deterministic domain boot-map verb.

    Default (read-only): walk the project root, diff against the sealed baseline,
    and print the ``Domain map: N files; since S<last>: ...`` line. ``--refresh``
    reseals the persisted baseline (session-end semantics) with ``.bak`` parity and
    sets the one-line ``meta.rag_files`` pointer.
    """
    from rag_kernel import bootmap

    rag_path = args.rag.resolve()
    rag_dir = rag_path.parent
    root = (args.root.resolve() if getattr(args, "root", None) else rag_path.parent.parent)

    if getattr(args, "refresh", False):
        sid = args.session or "manual"
        path = bootmap.refresh_baseline(root, rag_dir, sid)
        wrote_ptr = bootmap.ensure_meta_pointer(rag_path)
        manifest = bootmap.read_manifest(rag_dir) or {}
        if getattr(args, "json_output", False):
            print(json.dumps({"resealed": str(path), "count": manifest.get("count"),
                              "session": sid, "meta_pointer_written": wrote_ptr}, indent=2))
        else:
            print(f"Domain map resealed: {manifest.get('count', '?')} files "
                  f"-> {path.name} (+.bak parity){'; meta pointer set' if wrote_ptr else ''}.")
        return 0

    prior = bootmap.read_manifest(rag_dir)
    current = bootmap.build_manifest(root, session=(prior or {}).get("session", "?"))
    if getattr(args, "json_output", False):
        print(json.dumps({"boot_line": bootmap.boot_line(prior, current),
                          "diff": bootmap.diff_maps(prior, current),
                          "count": current["count"]}, indent=2))
    else:
        print(bootmap.boot_line(prior, current))
    return 0


#: Options whose VALUE may legitimately begin with a dash. argparse cannot tell
#: ``--contains --attest`` (a token to search for) from ``--contains`` followed
#: by an unknown flag, and errors with "expected one argument".
#:
#: WAITFOR-LEADING-DASH-ARGV (S198): that is not a cosmetic parsing wart. The
#: single most useful thing to wait for at boot is the attestation line, whose
#: token IS ``--attest``; the wait verb is the sanctioned replacement for
#: polling, so a token it cannot express pushes the agent straight back to the
#: banned behaviour. Hit live at the S198 boot, which then polled.
_DASH_VALUE_OPTIONS = ("--contains",)


def _fold_dash_values(argv: "list[str] | None") -> "list[str] | None":
    """Rewrite ``--contains -x`` as ``--contains=-x`` before argparse sees it.

    The ``=`` form always worked; nobody knew, because the failure mode is an
    argparse usage error that reads like the user's fault. Folding it here means
    the obvious spelling works and the documented one still does. Pure, total,
    and a no-op for every argv that does not contain the pattern.
    """
    if not argv:
        return argv
    out: list[str] = []
    i = 0
    while i < len(argv):
        tok = argv[i]
        if (
            tok in _DASH_VALUE_OPTIONS
            and i + 1 < len(argv)
            and argv[i + 1].startswith("-")
        ):
            out.append(f"{tok}={argv[i + 1]}")
            i += 2
            continue
        out.append(tok)
        i += 1
    return out


def _force_utf8_console() -> None:
    """Make stdout/stderr UTF-8 before any verb can print (S201).

    MEASURED S201: `rag_kernel items` — a MANDATED read path for state, and one
    of the two verbs boot rule 1 names — died mid-render with

        UnicodeEncodeError: 'charmap' codec can't encode character '\\u26a0'

    because the Windows console defaults to cp1252 and a tracked_item title
    contains a warning sign. The canonical way to read state was unusable on the
    only platform this project is deployed to, and every session so far worked
    around it by hand or never hit that item.

    The rule this belongs to: the kernel decides its own output encoding. Leaving
    it to the host console makes rendering a property of the operator's terminal
    settings, which nothing in this repo can audit.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass                      # a redirected/closed stream is not fatal


def main(argv: list[str] | None = None) -> int:
    _force_utf8_console()
    parser = build_parser()
    if argv is None:
        argv = sys.argv[1:]
    args = parser.parse_args(_fold_dash_values(argv))
    if args.command is None:
        parser.print_help()
        return 1
    commands = {
        "init": cmd_init, "configure": cmd_configure, "health": cmd_health,
        "serve": cmd_serve, "mcp": cmd_mcp, "session": cmd_session,
        "session-start": cmd_session_start, "session-end": cmd_session_end,
        "session-resume": cmd_session_resume,
        "checkpoint": cmd_checkpoint, "gc": cmd_gc, "audit-env": cmd_audit_env,
        "graph": cmd_graph,
        "resolve": cmd_item_transition, "defer": cmd_item_transition,
        "reopen": cmd_item_transition, "start": cmd_item_transition,
        "discard": cmd_item_transition, "supersede": cmd_item_transition,
        "items": cmd_items,
        "intent-audit": cmd_intent_audit,
        "render": cmd_render,
        "report": cmd_report,
        "note": cmd_note,
        "cite": cmd_cite,
        "priority": cmd_priority,
        "dedup-sessions": cmd_dedup_sessions,
        "audit": cmd_audit,
        "doctor": cmd_doctor,
        "add": cmd_add, "errlog-migrate": cmd_errlog_migrate,
        "acceptance": cmd_acceptance,
        "un-add": cmd_unadd,
        "add-rule": cmd_add_rule,
        "update-rule": cmd_update_rule,
        "refresh-current-status": cmd_refresh_current_status,
        "prune-current-status": cmd_prune_current_status,
        "meta": cmd_meta,
        "tests": cmd_tests,
        "forensics": cmd_forensics,
        "migrate": cmd_migrate,
        "transplant": cmd_transplant,
        "birth-adopt": cmd_birth_adopt,
        "ingest": cmd_ingest,
        "list-kinds": cmd_list_kinds,
        "measured": cmd_measured,
        "decide": cmd_decide,
        "decisions": cmd_decisions,
        "register-asset": cmd_register_asset,
        "status": cmd_status,
        "reuse-check": cmd_reuse_check,
        "session-delta": cmd_session_delta,
        "hook-guard": cmd_hook_guard,
        "inventory": cmd_inventory,
        "run": _cmd_run_detach_await,
        "wait-for": cmd_wait_for,
        "verify": cmd_verify,
        "context": cmd_context,
        "deployment": cmd_deployment,
        "push-check": cmd_push_check,
        "adopt-preflight": cmd_adopt_preflight,
        "bootmap": cmd_bootmap,
    }
    rc = _refuse_mutation_after_seal(args.command, args)
    if rc is not None:
        return rc
    return _dispatch_with_bootstrap_log(
        args.command, commands[args.command], args
    )


if __name__ == "__main__":
    sys.exit(main())
