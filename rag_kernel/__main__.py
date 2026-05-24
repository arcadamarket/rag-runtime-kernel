"""CLI entry point for the RAG Runtime Kernel.

Usage:
    python -m rag_kernel init --spec path/to/INIT_v3.1.8.md [--output RAG/] [--root-project ...]
    python -m rag_kernel health [--path .]
    python -m rag_kernel serve --project ~/my-project/RAG [--port 7437] [--host 127.0.0.1]
    python -m rag_kernel mcp --project ~/my-project/RAG
    python -m rag_kernel configure --rag RAG/RAG_MASTER.json --context project_context.json

Commands:
    init      Parse init prompt MD and create RAG_MASTER.json deterministically (zero tokens).
    configure Merge project-specific context into an existing RAG_MASTER.json.
    health    Verify all rag_kernel modules are importable and functional.
    serve     Start the HTTP API server (for GPT Web / direct access).
    mcp       Start the MCP stdio server (for Claude Desktop).

Design doc reference: v3.2_ARCHITECTURE_DESIGN.md section 9
Satisfies: M-026 (CLI entry point), V33-BOOTSTRAP (init command)

@rag-kernel-manifest
{
  "module": "rag_kernel.__main__",
  "capability": "cli",
  "description": "CLI entry point — dispatches init, health, serve, mcp, configure commands",
  "commands": {
    "init": "Parse init prompt MD → RAG_MASTER.json (zero tokens)",
    "health": "Verify all modules importable and functional",
    "serve": "Start HTTP API server",
    "mcp": "Start MCP stdio server",
    "configure": "Merge project-specific context into existing RAG"
  },
  "use_when": "Any CLI invocation of rag_kernel"
}
"""

from __future__ import annotations

import argparse
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

    if args.root_project:
        rag["meta"]["root_project"] = args.root_project
    if args.root_deliverables:
        rag["meta"]["root_deliverables"] = args.root_deliverables
    if args.root_rag:
        rag["meta"]["root_rag"] = args.root_rag
    if args.project_name:
        rag["meta"]["project_name"] = args.project_name

    errors = sp.validate_rag(rag)
    if errors:
        print(f"\nValidation issues ({len(errors)}):")
        for e in errors:
            print(f"  - {e}")

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


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 1
    commands = {"init": cmd_init, "configure": cmd_configure, "health": cmd_health, "serve": cmd_serve, "mcp": cmd_mcp}
    return commands[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
