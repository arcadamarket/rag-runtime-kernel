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

Commands:
    init       Parse init prompt MD and create RAG_MASTER.json deterministically (zero tokens).
    configure  Merge project-specific context into an existing RAG_MASTER.json.
    health     Verify all rag_kernel modules are importable and functional.
    serve      Start the HTTP API server (for GPT Web / direct access).
    mcp        Start the MCP stdio server (for Claude Desktop).
    session    Start or close a session logger (wraps SessionLogger open/close).
    checkpoint Merge session summary into RAG_MASTER.json atomically.
    gc         Garbage collector — clean __pycache__, .pyc, .tmp, orphaned files.

Design doc reference: v3.2_ARCHITECTURE_DESIGN.md section 9
Satisfies: M-026 (CLI entry point), V33-BOOTSTRAP (init command), ENH-008 (session/checkpoint/gc)

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
    "gc": "Garbage collector — clean temp files, pycache, orphans"
  },
  "use_when": "Any CLI invocation of rag_kernel"
}
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

from rag_kernel.api import DEFAULT_PORT, KernelApp, create_server
from rag_kernel.mcp_transport import MCPServer


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
        rag = result.merged
        cold = result.cold_template
    elif args.spec and not args.spec.exists():
        print(f"Error: Spec file not found: {args.spec}", file=sys.stderr)
        return 1
    else:
        print("No --spec provided. Creating void RAG with structural defaults.")
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
        print("\nDone. Zero tokens consumed.")
    else:
        print("\n[DRY RUN] No files written.")
        print(f"RAG preview ({len(json.dumps(rag))} bytes):")
        print(json.dumps(rag, indent=2)[:500] + "...")

    return 0 if not errors else 1


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
    project_path = str(args.path.resolve())
    if project_path not in sys.path:
        sys.path.insert(0, project_path)

    modules = [
        "rag_kernel.state_machine",
        "rag_kernel.persistence",
        "rag_kernel.schemas",
        "rag_kernel.concurrency",
        "rag_kernel.cold_manager",
        "rag_kernel.api",
        "rag_kernel.mcp_transport",
        "rag_kernel.spec_parser",
        "rag_kernel.session_logger",
        "rag_kernel.__main__",
    ]

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

    print(f"\nResult: {passed}/{total} modules OK.")
    return 0 if passed == total else 1


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


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 1
    commands = {
        "init": cmd_init, "configure": cmd_configure, "health": cmd_health,
        "serve": cmd_serve, "mcp": cmd_mcp, "session": cmd_session,
        "checkpoint": cmd_checkpoint, "gc": cmd_gc,
    }
    return commands[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
