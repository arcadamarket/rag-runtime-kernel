"""MCP stdio transport for the RAG Runtime Kernel.

Implements the Model Context Protocol (MCP) over stdio, enabling
Claude Desktop (and other MCP clients) to interact with the kernel
via JSON-RPC messages on stdin/stdout.

Each kernel API endpoint is exposed as an MCP tool:
- rag_boot, rag_status, rag_hot, rag_cold, rag_propose,
  rag_commit, rag_reject, rag_checkpoint, rag_wal,
  rag_recover, rag_close

Protocol: JSON-RPC 2.0 over newline-delimited JSON on stdio.
Messages are framed as: Content-Length: N\r\n\r\n{json}

Design doc reference: v3.2_ARCHITECTURE_DESIGN.md §9.2
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Optional, TextIO

from rag_kernel.api import KernelApp


# ---------------------------------------------------------------------------
# MCP Protocol Constants
# ---------------------------------------------------------------------------

JSONRPC_VERSION = "2.0"
MCP_PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "rag-kernel"
SERVER_VERSION = "0.1.0"


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "name": "rag_boot",
        "description": "Initialize the kernel session. Loads HOT, verifies hashes, opens WAL, acquires lock.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "rag_status",
        "description": "Get current kernel status: state, session_id, seq, available transitions.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "rag_hot",
        "description": "Get current HOT (RAG_MASTER) contents.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "rag_cold",
        "description": "Get COLD data. Optionally specify a partition name.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "partition": {
                    "type": "string",
                    "description": "Partition name (e.g., 'documents_inventory'). Omit for full COLD.",
                },
            },
        },
    },
    {
        "name": "rag_propose",
        "description": "Submit a mutation proposal. Requires 'action' and 'payload' fields.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "The mutation action (e.g., 'update_status').",
                },
                "payload": {
                    "type": "object",
                    "description": "The data to write.",
                },
            },
            "required": ["action", "payload"],
        },
    },
    {
        "name": "rag_commit",
        "description": "Commit a validated proposal by its ID.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "proposal_id": {
                    "type": "string",
                    "description": "The proposal ID to commit.",
                },
            },
            "required": ["proposal_id"],
        },
    },
    {
        "name": "rag_reject",
        "description": "Reject a proposal by its ID.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "proposal_id": {
                    "type": "string",
                    "description": "The proposal ID to reject.",
                },
            },
            "required": ["proposal_id"],
        },
    },
    {
        "name": "rag_checkpoint",
        "description": "Save current state with backup rotation and hash recompute.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "rag_wal",
        "description": "Get WAL entries. Optionally filter by 'since' sequence number.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "since": {
                    "type": "integer",
                    "description": "Only return entries with seq > since.",
                    "default": 0,
                },
            },
        },
    },
    {
        "name": "rag_recover",
        "description": "Attempt recovery from .bak file.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "rag_close",
        "description": "Close the session: checkpoint, flush WAL, release lock.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
]


# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------

class MCPServer:
    """MCP stdio server that bridges JSON-RPC to KernelApp.

    Reads JSON-RPC messages from stdin, dispatches to the appropriate
    KernelApp method, and writes responses to stdout.

    Usage:
        app = KernelApp(Path("RAG"))
        server = MCPServer(app)
        server.run()  # blocks, reading stdin until EOF
    """

    def __init__(
        self,
        app: KernelApp,
        input_stream: Optional[TextIO] = None,
        output_stream: Optional[TextIO] = None,
    ) -> None:
        self.app = app
        self._in = input_stream or sys.stdin
        self._out = output_stream or sys.stdout
        self._initialized = False

    def run(self) -> None:
        """Main loop: read messages, dispatch, respond."""
        while True:
            message = self._read_message()
            if message is None:
                break  # EOF

            response = self._dispatch(message)
            if response is not None:
                self._write_message(response)

    def handle_message(self, message: dict) -> Optional[dict]:
        """Handle a single message and return the response (or None for notifications)."""
        return self._dispatch(message)

    # -- Message I/O --------------------------------------------------------

    def _read_message(self) -> Optional[dict]:
        """Read a JSON-RPC message from stdin.

        Supports two framing modes:
        1. Content-Length header (MCP standard)
        2. Newline-delimited JSON (fallback)
        """
        try:
            # Try Content-Length framing first
            line = self._in.readline()
            if not line:
                return None  # EOF

            line = line.strip()

            # Content-Length header
            if line.lower().startswith("content-length:"):
                length = int(line.split(":")[1].strip())
                # Read blank line
                self._in.readline()
                # Read body
                body = self._in.read(length)
                return json.loads(body)

            # Newline-delimited JSON fallback
            if line:
                return json.loads(line)

            return None

        except (json.JSONDecodeError, ValueError, OSError):
            return None

    def _write_message(self, message: dict) -> None:
        """Write a JSON-RPC message to stdout with Content-Length framing."""
        body = json.dumps(message, separators=(",", ":"), ensure_ascii=False)
        header = f"Content-Length: {len(body.encode('utf-8'))}\r\n\r\n"
        self._out.write(header)
        self._out.write(body)
        self._out.flush()

    # -- Dispatch -----------------------------------------------------------

    def _dispatch(self, message: dict) -> Optional[dict]:
        """Route a JSON-RPC message to the appropriate handler."""
        method = message.get("method", "")
        msg_id = message.get("id")
        params = message.get("params", {})

        # Notifications (no id) don't get responses
        if msg_id is None and method.startswith("notifications/"):
            return None

        handlers = {
            "initialize": self._handle_initialize,
            "tools/list": self._handle_tools_list,
            "tools/call": self._handle_tools_call,
            "ping": self._handle_ping,
        }

        handler = handlers.get(method)
        if handler:
            try:
                result = handler(params)
                return self._success(msg_id, result)
            except Exception as e:
                return self._error(msg_id, -32603, str(e))
        else:
            # Unknown method
            if msg_id is not None:
                return self._error(msg_id, -32601, f"Method not found: {method}")
            return None

    # -- Protocol handlers --------------------------------------------------

    def _handle_initialize(self, params: dict) -> dict:
        """Handle MCP initialize request."""
        self._initialized = True
        return {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "capabilities": {
                "tools": {},
            },
            "serverInfo": {
                "name": SERVER_NAME,
                "version": SERVER_VERSION,
            },
        }

    def _handle_tools_list(self, params: dict) -> dict:
        """Return the list of available tools."""
        return {"tools": TOOLS}

    def _handle_tools_call(self, params: dict) -> dict:
        """Dispatch a tool call to the appropriate KernelApp method."""
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})

        tool_handlers = {
            "rag_boot": lambda args: self.app.boot(),
            "rag_status": lambda args: self.app.status(),
            "rag_hot": lambda args: self.app.get_hot(),
            "rag_cold": lambda args: self.app.get_cold(args.get("partition")),
            "rag_propose": lambda args: self.app.propose(args),
            "rag_commit": lambda args: self.app.commit(args["proposal_id"]),
            "rag_reject": lambda args: self.app.reject(args["proposal_id"]),
            "rag_checkpoint": lambda args: self.app.checkpoint(),
            "rag_wal": lambda args: self.app.get_wal(since=args.get("since", 0)),
            "rag_recover": lambda args: self.app.recover(),
            "rag_close": lambda args: self.app.close(),
        }

        handler = tool_handlers.get(tool_name)
        if not handler:
            return {
                "content": [
                    {"type": "text", "text": json.dumps({"error": f"Unknown tool: {tool_name}"})}
                ],
                "isError": True,
            }

        try:
            result = handler(arguments)
            return {
                "content": [
                    {"type": "text", "text": json.dumps(result, indent=2, ensure_ascii=False)}
                ],
            }
        except Exception as e:
            return {
                "content": [
                    {"type": "text", "text": json.dumps({"error": str(e)})}
                ],
                "isError": True,
            }

    def _handle_ping(self, params: dict) -> dict:
        """Handle ping request."""
        return {}

    # -- JSON-RPC helpers ---------------------------------------------------

    @staticmethod
    def _success(msg_id: Any, result: Any) -> dict:
        return {
            "jsonrpc": JSONRPC_VERSION,
            "id": msg_id,
            "result": result,
        }

    @staticmethod
    def _error(msg_id: Any, code: int, message: str) -> dict:
        return {
            "jsonrpc": JSONRPC_VERSION,
            "id": msg_id,
            "error": {"code": code, "message": message},
        }

    def __repr__(self) -> str:
        return (
            f"MCPServer(app={self.app!r}, "
            f"initialized={self._initialized})"
        )
