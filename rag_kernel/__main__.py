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
    "render": "Render legacy open_tasks/deferred_items/backlog/ERROR_LOG from tracked_items; --apply rewrites the legacy arrays atomically (DRIFT-ELIM increment 4)",
    "note": "Refresh a tracked item's one-line note through the guarded API (status untouched) — DRIFT-ELIM increment 5 (INS-038)",
    "audit": "Fail-loud session auditor: renders match canonical, supersede refs resolve, notes don't contradict status, no side stores — DRIFT-ELIM increment 5",
    "add": "Add a NEW canonical tracked item through the guarded atomic store (fail-loud on duplicate id)",
    "add-rule": "Append a NEW operating_protocol rule through the guarded atomic store (FIX-5/P3, fail-loud on existing key)",
    "update-rule": "Re-set an EXISTING operating_protocol rule (string or dict/JSON value) or one sub-key of a dict rule through the guarded atomic store (UPDATE-RULE-VERB, fail-loud on a missing target unless --create)",
    "verify": "Deterministic post-init HOT↔COLD self-version coherence gate (FIX-2)",
    "context": "Read/write the sanctioned, non-loaded RAG_CONTEXT.json project-context store (set|get|list) — FIX-11 inc2 / U3"
  },
  "use_when": "Any CLI invocation of rag_kernel"
}
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

from rag_kernel.api import DEFAULT_PORT, KernelApp, create_server
from rag_kernel.mcp_transport import MCPServer

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
        "--context", type=Path, required=True,
        help="Path to context file (JSON or structured MD with rag-config blocks)",
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
    sstart_parser.add_argument("session_id", type=str, help="Session identifier to open (e.g., S92)")
    sstart_parser.add_argument(
        "--rag", type=Path, default=_default_rag_path(),
        help="Path to RAG_MASTER.json (default: RAG/RAG_MASTER.json)",
    )
    sstart_parser.add_argument(
        "--gc-path", type=Path, default=Path("."),
        help="Project root to scan in the gc dry-run (default: . — run from root_project)",
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
        help="Attest the canonical status report was rendered in chat (Rule 12).",
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
    # KA-16 — optional ERROR_LOG fold (idempotent) as part of the governed checkpoint.
    ckpt_parser.add_argument("--error-log-entry", type=str, default=None, help="Markdown ERROR_LOG entry to fold in (idempotent).")
    ckpt_parser.add_argument("--error-log-id", type=str, default=None, help="Unique id for the ERROR_LOG entry (default: <session>-checkpoint).")
    ckpt_parser.add_argument("--error-log-path", type=str, default=None, help="ERROR_LOG.md path (default: beside the RAG).")

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
        if _verb == "supersede":
            vp.add_argument("--by", type=str, required=True, help="id of the item that supersedes this one")

    # -- items (read-only render of tracked_items) --
    items_parser = subparsers.add_parser("items", help="List the canonical tracked_items array (read-only).")
    items_parser.add_argument("--rag", type=Path, default=_default_rag_path(), help="Path to RAG_MASTER.json")
    items_parser.add_argument("--status", type=str, default=None, help="Filter by status (e.g. OPEN, DEFERRED)")
    items_parser.add_argument("--kind", type=str, default=None, help="Filter by kind (e.g. TASK, MILESTONE)")
    items_parser.add_argument("--json", dest="json_output", action="store_true", help="Output as JSON instead of a table")

    # -- render (DRIFT-ELIM increment 4: project tracked_items into legacy surfaces) --
    render_parser = subparsers.add_parser(
        "render",
        help="Render legacy open_tasks/deferred_items/backlog/ERROR_LOG from the canonical tracked_items array.",
    )
    render_parser.add_argument(
        "--rag", type=Path, default=_default_rag_path(),
        help="Path to RAG_MASTER.json (default: RAG/RAG_MASTER.json)",
    )
    render_parser.add_argument(
        "--what", choices=["open_tasks", "deferred_items", "backlog", "error_log", "all"],
        default="all", help="Which render to emit (default: all)",
    )
    render_parser.add_argument(
        "--apply", action="store_true",
        help="Write the rendered open_tasks + deferred_items back into the RAG atomically (else dry-run/print only).",
    )
    render_parser.add_argument("--json", dest="json_output", action="store_true", help="Output as JSON instead of text")

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

    # Load context
    context_path = args.context.resolve()
    if not context_path.exists():
        print(f"Error: Context file not found: {context_path}", file=sys.stderr)
        return 1

    # --consume validation (FIX-11 inc3 / U3): refuse to delete a canonical or
    # sanctioned file BEFORE any merge, so misuse fails loud without mutating
    # state. The merge-input is meant to be a *transient* overlay; consuming the
    # RAG itself, its .bak, the COLD archive, or the sanctioned RAG_CONTEXT.json
    # store would destroy real state.
    if getattr(args, "consume", False):
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
    app = KernelApp(project, session_id=args.session_id)
    app.boot()
    server = MCPServer(app)
    try:
        server.run()
    except KeyboardInterrupt:
        pass
    finally:
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

    if args.dry_run:
        if not legal_status_transition(current.status, target):
            print(
                f"[DRY RUN] ILLEGAL: {args.item_id} {current.status.value} -> {target}",
                file=sys.stderr,
            )
            return 1
        print(f"[DRY RUN] {args.item_id}: {current.status.value} -> {target} (no write)")
        return 0

    try:
        transition_in_file(
            rag_path,
            args.item_id,
            target,
            session=args.session,
            reason=args.reason,
            superseded_by=superseded_by,
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
            f"{len(rendered['deferred_items'])} deferred_items "
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
    if what in ("backlog", "all"):
        print("# Rule 12 backlog (render)")
        print(drift_render.render_backlog_markdown(store))
        print()
    if what == "error_log":
        print(drift_render.render_error_log_backlog(store))
    return 0


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

    if args.dry_run:
        print(f"\n[DRY RUN] Would update {rag_path}:")
        print(f"  Session: {session_id}")
        print(f"  Summary: {summary[:80]}...")
        print(f"  Checkpoint seq: {checkpoint_seq}")
        if error_log_entry:
            eid = error_log_id or f"{session_id}-checkpoint"
            already = _error_log_has_id(el_path, eid)
            print(
                f"  ERROR_LOG: would {'SKIP (id already present)' if already else 'append'} "
                f"entry id='{eid}' -> {el_path}"
            )
        return 0

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


def _carry_forward_gate(
    rag_path: Path, *, strict: bool = False, git_head: "str | None" = None
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

    return (not findings), findings


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

    # Phase 2 — attestation handshake (no gate/gc/render; the session was already
    # vetted in phase 1, this only verifies the token and opens the logger).
    if getattr(args, "attest", None) is not None:
        return _session_start_attest(rag_path, rag_dir, sid, args.attest)

    # 1. Carry-forward gate (fail-loud).
    ok, findings = _carry_forward_gate(
        rag_path, strict=args.strict, git_head=getattr(args, "git_head", None)
    )
    print("[1/4] Carry-forward gate:")
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
                "  Reconcile the inherited RAG (or pass --force to start anyway, UNSAFE).",
                file=sys.stderr,
            )
            return 1
        print(
            "WARNING: starting despite a failed carry-forward gate (--force).",
            file=sys.stderr,
        )

    # 2. gc dry-run (report-before-delete).
    if not getattr(args, "no_gc", False):
        print("[2/4] GC (dry-run):")
        cmd_gc(argparse.Namespace(path=args.gc_path, dry_run=True))
    else:
        print("[2/4] GC: skipped (--no-gc).")

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

CLOSE_PHASES = ("CHECKPOINTED", "CLOSED", "COMPLETE")


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


def _build_close_marker(
    sid: str, phase: str, steps: dict, started_utc: str,
    completed_utc: "str | None", *, transfer_ready: bool = False,
) -> dict:
    return {
        "session": sid,
        "phase": phase,
        "transfer_ready": transfer_ready,
        "started_utc": started_utc,
        "completed_utc": completed_utc,
        "steps": {
            "checkpoint": bool(steps.get("checkpoint")),
            "error_log": bool(steps.get("error_log")),
            "logger_close": bool(steps.get("logger_close")),
            "audit": bool(steps.get("audit")),
            "report_rendered": bool(steps.get("report_rendered")),
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


def _drive_close(
    rag_path: Path, rag_dir: Path, sid: str, *, summary: "str | None",
    tasks, status, strict: bool, git_head: "str | None",
    error_log_entry: "str | None", error_log_id: "str | None",
    error_log_path: "str | None", report_rendered: bool,
    marker: "dict | None", resuming: bool,
) -> int:
    """Run the close transaction forward from whatever step is incomplete.

    Shared by ``session-end`` (marker=None, fresh close) and ``session-resume``
    (marker=the interrupted record). Each completed step is persisted to the
    ``session_close`` marker BEFORE the next begins, so any abort is resumable.
    ``transfer_ready`` is set only after ALL four steps pass.
    """
    steps = dict(marker.get("steps", {})) if marker else {}
    started = (marker.get("started_utc") if marker else None) or _utcnow_iso()

    # Step 1/4 — checkpoint (+ idempotent ERROR_LOG fold).
    if not steps.get("checkpoint"):
        print("[1/4] Checkpoint (+ERROR_LOG fold):")
        rc = cmd_checkpoint(argparse.Namespace(
            rag=rag_path, session=sid, summary=summary, tasks=tasks,
            status=status, dry_run=False, error_log_entry=error_log_entry,
            error_log_id=error_log_id, error_log_path=error_log_path,
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
    if not steps.get("audit"):
        print("[3/4] Audit:")
        rc = cmd_audit(argparse.Namespace(
            rag=rag_path, strict=strict, scan_root=True, error_log=None,
            docs_root=None, git_head=git_head, json_output=False,
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
    if report_rendered:
        steps["report_rendered"] = True
    _write_close_marker(
        rag_path,
        _build_close_marker(
            sid, "COMPLETE", steps, started, _utcnow_iso(), transfer_ready=True
        ),
    )
    print("[4/4] Transfer marker: transfer_ready=true (phase COMPLETE).")
    if not steps.get("report_rendered"):
        print(
            "  NOTE: report_rendered not attested — render the canonical status "
            "report in chat (Rule 12) and pass --report-rendered to record it.",
            file=sys.stderr,
        )
    verb = "resumed and completed" if resuming else "ended cleanly"
    print(
        f"Session {sid} {verb}: checkpoint + ERROR_LOG + close + audit all green; "
        "transfer_ready set."
    )
    return 0


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
    )


def cmd_gc(args: argparse.Namespace) -> int:
    """Garbage collector — scan and clean temp artifacts within project root.

    Targets:
    - __pycache__/ directories and .pyc files
    - .tmp files
    - Orphaned single-digit/short numeric files at project root (stdout captures)
    - .bat files (Desktop Commander artifacts)

    Always reports before deleting. In --dry-run mode, reports only.
    """
    import re

    project_root = args.path.resolve()
    dry_run = args.dry_run

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
    }

    for dirpath, dirnames, filenames in os.walk(project_root):
        rel = os.path.relpath(dirpath, project_root)

        # Skip .venv, .git, node_modules
        skip_dirs = {".venv", ".git", "node_modules", ".playwright-mcp"}
        dirnames[:] = [d for d in dirnames if d not in skip_dirs]

        # __pycache__ directories
        if os.path.basename(dirpath) == "__pycache__":
            findings["pycache_dirs"].append(rel)
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
    total = sum(len(v) for v in findings.values())
    if total == 0:
        print("  No garbage found. Project is clean.")
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

    print(f"\n  Total: {total} items")

    if dry_run:
        print("\n  [DRY RUN] Run without --dry-run to delete.")
        return 0

    # Delete
    deleted = 0

    for d in findings["pycache_dirs"]:
        full = os.path.join(project_root, d)
        try:
            shutil.rmtree(full)
            deleted += 1
        except OSError as e:
            print(f"  WARNING: Could not delete {d}: {e}")

    for category in ["pyc_files", "tmp_files", "orphan_files", "bat_files"]:
        for f in findings[category]:
            full = os.path.join(project_root, f)
            try:
                os.remove(full)
                deleted += 1
            except OSError as e:
                print(f"  WARNING: Could not delete {f}: {e}")

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


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
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
        "render": cmd_render,
        "note": cmd_note,
        "dedup-sessions": cmd_dedup_sessions,
        "audit": cmd_audit,
        "doctor": cmd_doctor,
        "add": cmd_add,
        "un-add": cmd_unadd,
        "add-rule": cmd_add_rule,
        "update-rule": cmd_update_rule,
        "verify": cmd_verify,
        "context": cmd_context,
    }
    return _dispatch_with_bootstrap_log(
        args.command, commands[args.command], args
    )


if __name__ == "__main__":
    sys.exit(main())
