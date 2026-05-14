"""CLI entry point for the RAG Runtime Kernel.

Usage:
    python -m rag_kernel serve --project ~/my-project/RAG [--port 7437] [--host 127.0.0.1]
    python -m rag_kernel mcp --project ~/my-project/RAG

Commands:
    serve   Start the HTTP API server (for GPT Web / direct access).
    mcp     Start the MCP stdio server (for Claude Desktop).

Design doc reference: v3.2_ARCHITECTURE_DESIGN.md §9
Satisfies: M-026 (CLI entry point)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from rag_kernel.api import DEFAULT_PORT, KernelApp, create_server
from rag_kernel.mcp_transport import MCPServer


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="rag_kernel",
        description="RAG Runtime Kernel — OS-level runtime bridge for LLM memory persistence.",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # -- serve --
    serve_parser = subparsers.add_parser(
        "serve",
        help="Start the HTTP API server.",
    )
    serve_parser.add_argument(
        "--project",
        type=Path,
        required=True,
        help="Path to the RAG project directory (containing RAG_MASTER.json).",
    )
    serve_parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"Port to listen on (default: {DEFAULT_PORT}).",
    )
    serve_parser.add_argument(
        "--host",
        type=str,
        default="127.0.0.1",
        help="Host to bind to (default: 127.0.0.1).",
    )
    serve_parser.add_argument(
        "--session-id",
        type=str,
        default=None,
        help="Session identifier (auto-generated if omitted).",
    )

    # -- mcp --
    mcp_parser = subparsers.add_parser(
        "mcp",
        help="Start the MCP stdio server (for Claude Desktop).",
    )
    mcp_parser.add_argument(
        "--project",
        type=Path,
        required=True,
        help="Path to the RAG project directory.",
    )
    mcp_parser.add_argument(
        "--session-id",
        type=str,
        default=None,
        help="Session identifier (auto-generated if omitted).",
    )

    return parser


def cmd_serve(args: argparse.Namespace) -> int:
    """Run the HTTP API server."""
    project = args.project.resolve()
    if not project.exists():
        print(f"Error: Project directory does not exist: {project}", file=sys.stderr)
        return 1

    server = create_server(
        project,
        host=args.host,
        port=args.port,
        session_id=args.session_id,
    )

    # Boot the kernel
    result = server.app.boot()
    if result["status"] != "OK":
        print(f"Boot failed: {result}", file=sys.stderr)
        if result["status"] == "RECOVERY":
            print("Kernel entered RECOVERY. Manual intervention may be needed.", file=sys.stderr)

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
    """Run the MCP stdio server."""
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
    """Main entry point."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 1

    commands = {
        "serve": cmd_serve,
        "mcp": cmd_mcp,
    }

    return commands[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
