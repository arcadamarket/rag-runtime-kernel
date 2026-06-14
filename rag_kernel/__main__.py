"""CLI entry point for the RAG Runtime Kernel.

Usage:
    python -m rag_kernel init --spec path/to/INIT_v3.1.8.md [--output RAG/] [--root-project ...]
    python -m rag_kernel health [--path .]
    python -m rag_kernel serve --project ~/my-project/RAG [--port 7437] [--host 127.0.0.1]
    python -m rag_kernel mcp --project ~/my-project/RAG
    python -m rag_kernel configure --rag RAG/RAG_MASTER.json --context project_context.json
    python -m rag_kernel session start S1 [--rag-dir RAG/]
    python -m rag_kernel session close S1 [--rag-dir RAG/]
    python -m rag_kernel checkpoint --rag RAG/RAG_MASTER.json --session S1 --summary "..."
    python -m rag_kernel gc [--path .] [--dry-run]
    python -m rag_kernel audit-env [--path .] [--json]
    python -m rag_kernel graph run spec.json [--project .] [--schedule levels]
    python -m rag_kernel resolve <item-id> --session S50 [--rag RAG/RAG_MASTER.json] [--reason "..."]
    python -m rag_kernel defer <item-id> --session S50 [--reason "..."]
    python -m rag_kernel items [--status OPEN] [--kind TASK] [--json]

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
    "configure": "Merge project-specific context into existing RAG",
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
    "verify": "Deterministic post-init HOT↔COLD self-version coherence gate (FIX-2)"
  },
  "use_when": "Any CLI invocation of rag_kernel"
}
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

from rag_kernel.api import DEFAULT_PORT, KernelApp, create_server
from rag_kernel.mcp_transport import MCPServer

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
             "(reconciles README.md / CHANGELOG.md / docs/ROADMAP.md vs the canonical facts).",
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

    errors = sp.validate_rag(rag)
    if errors:
        print(f"\nValidation issues ({len(errors)}):")
        for e in errors:
            print(f"  - {e}")

    # Auto-ready: transition BOOTING -> READY if init succeeded with no errors
    if getattr(args, "auto_ready", False) and not errors:
        rag["state_machine_status"] = "READY"
        print("\n--auto-ready: state_machine_status set to READY.")

    if not args.dry_run:
        output_dir = args.output or Path("RAG")
        hot_path = output_dir / "RAG_MASTER.json"
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
        written = sp.write_rag(updated_rag, rag_path)
        print(f"\nRAG_MASTER.json updated: {written}")
        print("Done. Zero tokens consumed.")
    else:
        print("\n[DRY RUN] No files written.")
        # Show diff summary
        diff_keys = [k for k in context_data if k in existing_rag]
        new_keys = [k for k in context_data if k not in existing_rag]
        if diff_keys:
            print(f"  Would update: {diff_keys}")
        if new_keys:
            print(f"  Would add: {new_keys}")

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
        # Re-open to resume sequence, then close
        if logger.log_path.exists():
            logger.open()
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

    if args.dry_run:
        print(f"\n[DRY RUN] Would update {rag_path}:")
        print(f"  Session: {session_id}")
        print(f"  Summary: {summary[:80]}...")
        print(f"  Checkpoint seq: {checkpoint_seq}")
        return 0

    # Atomic write via persistence module
    try:
        from rag_kernel.persistence import atomic_write_json
        atomic_write_json(rag_path, rag)
    except ImportError:
        # Fallback: direct write if persistence not available
        with open(rag_path, "w", encoding="utf-8") as f:
            json.dump(rag, f, indent=2, ensure_ascii=False)

    print(f"Checkpoint complete:")
    print(f"  Session: {session_id}")
    print(f"  Checkpoint seq: {checkpoint_seq}")
    print(f"  RAG updated: {rag_path}")

    return 0


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


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 1
    commands = {
        "init": cmd_init, "configure": cmd_configure, "health": cmd_health,
        "serve": cmd_serve, "mcp": cmd_mcp, "session": cmd_session,
        "checkpoint": cmd_checkpoint, "gc": cmd_gc, "audit-env": cmd_audit_env,
        "graph": cmd_graph,
        "resolve": cmd_item_transition, "defer": cmd_item_transition,
        "reopen": cmd_item_transition, "start": cmd_item_transition,
        "discard": cmd_item_transition, "supersede": cmd_item_transition,
        "items": cmd_items,
        "render": cmd_render,
        "note": cmd_note,
        "audit": cmd_audit,
        "doctor": cmd_doctor,
        "add": cmd_add,
        "add-rule": cmd_add_rule,
        "verify": cmd_verify,
    }
    return commands[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
