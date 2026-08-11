"""MCP stdio transport for the RAG Runtime Kernel.

Implements the Model Context Protocol (MCP) over stdio, enabling
Claude Desktop (and other MCP clients) to interact with the kernel
via JSON-RPC messages on stdin/stdout.

Each kernel API endpoint is exposed as an MCP tool:
- rag_boot, rag_status, rag_hot, rag_cold, rag_propose,
  rag_commit, rag_reject, rag_checkpoint, rag_wal,
  rag_recover, rag_close, rag_graph_run, rag_wait

Protocol: JSON-RPC 2.0 over newline-delimited JSON on stdio.
Messages are framed as: Content-Length: N\r\n\r\n{json}

Design doc reference: v3.2_ARCHITECTURE_DESIGN.md §9.2

AGENT-SIDE-WAIT-GAP (S197)
--------------------------
``wait_primitive`` gave the SHELL a blocking read.  The AGENT had none: every
agent-facing wait went through ``mcp__tmux-mcp__get-command-result``, which is
poll-based, so the no-polling discipline (E-081 -> E-116 -> E-128, three
sessions, one defect) was impossible to obey with the tools available.  S195
answered that with a hook that REFUSES a second poll -- a gate over a missing
capability, which is the ``GATE-OR-HOPE`` failure mode in miniature: it can stop
the wrong behaviour but cannot supply the right one.

``rag_wait`` supplies it.  It is the same ``wait_primitive.wait_for`` state
machine, reachable from the agent's own tool layer, blocking inside the server
process for zero agent round-trips.  With it, the poll hook becomes a backstop
instead of the only control -- which is what PLAN_S193_PROCESS_FIX P1-C means by
"fix the transport, not the agent".

Two deliberate properties, both load-bearing:

* STATELESS / PRE-BOOT.  ``rag_wait`` dispatches straight to ``wait_primitive``
  and never touches ``KernelApp``.  It therefore answers before ``rag_boot``, on
  a freshly inited clone, with no RAG on disk -- exactly when a birth runbook
  must wait on a long init.  Routing it through the app would have made the one
  case it exists for the one case it could not serve.
* TIMEOUT IS ``isError``.  A wait that expired is not a wait that succeeded.
  The generic handler below cannot express this (``wait_for`` returns TIMEOUT,
  it does not raise), so ``rag_wait`` builds its own MCP envelope.

Known capability limit, stated rather than papered over (PLAN-FEASIBILITY-GATE):
the MCP CLIENT's own request timeout bounds this tool from outside the kernel.
A ``timeout_s`` beyond the client's ceiling fails at the client no matter what
the server does.  ``_CLIENT_TIMEOUT_HINT_S`` is the threshold past which the
result carries an explicit warning naming ``MCP_TIMEOUT``; the kernel cannot
raise the client's limit, so it names it instead.

@rag-kernel-manifest
{
  "module": "rag_kernel.mcp_transport",
  "capability": "mcp_server",
  "description": "MCP stdio transport — primary interface for Claude Desktop",
  "exports": ["MCPServer"],
  "tools": [
    "rag_boot", "rag_status", "rag_hot", "rag_cold", "rag_propose",
    "rag_commit", "rag_reject", "rag_checkpoint", "rag_wal",
    "rag_recover", "rag_close", "rag_graph_run", "rag_wait"
  ],
  "use_when": "Running kernel as MCP server for Claude Desktop integration",
  "never_bypass": false
}
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Optional, TextIO

from rag_kernel.api import KernelApp
from rag_kernel.wait_primitive import WaitError, wait_for


# ---------------------------------------------------------------------------
# MCP Protocol Constants
# ---------------------------------------------------------------------------

JSONRPC_VERSION = "2.0"
MCP_PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "rag-kernel"
SERVER_VERSION = "0.1.0"

# -- rag_wait bounds (transport-layer, distinct from wait_primitive's own) ----
# wait_primitive caps timeout at 24h because a shell wait is bounded only by the
# job. An agent-facing wait is bounded by the CONTEXT it reports into, so the
# transport applies the tighter pair of limits: a ceiling on how long the agent
# may block, and a ceiling on how many lines come back.
_WAIT_MAX_TIMEOUT_S = 3600
_WAIT_MAX_EMIT_LINES = 200
# Past this, warn that the MCP client's own request timeout, not the kernel, is
# the binding constraint. Claude Code's default is 30s unless MCP_TIMEOUT is set.
_CLIENT_TIMEOUT_HINT_S = 30


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
    {
        "name": "rag_graph_run",
        "description": (
            "Execute a Graph Orchestrator DAG through the kernel's single "
            "serialized propose->validate->commit pipeline (v4.0). The kernel "
            "stays sole writer; every node is committed and checkpointed in "
            "deterministic order."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "nodes": {
                    "type": "array",
                    "description": "DAG nodes; each {id, deps?, action, payload?}.",
                    "items": {"type": "object"},
                },
                "schedule": {
                    "type": "string",
                    "description": "'sequential' (default) or 'levels'.",
                },
                "stop_on_failure": {
                    "type": "boolean",
                    "description": "Halt remaining branches on first node failure.",
                },
                "rollback_on_failure": {
                    "type": "boolean",
                    "description": "Transactional: undo the whole run on any failure.",
                },
            },
            "required": ["nodes"],
        },
    },
    {
        "name": "rag_wait",
        "description": (
            "BLOCKING wait for a detached job (AGENT-SIDE-WAIT-GAP, S197). "
            "Blocks inside the server until 'path' exists and — if 'contains' "
            "is given — holds that token, then returns a bounded tail. Costs "
            "ONE round-trip and zero polls. Use this instead of re-querying "
            "mcp__tmux-mcp__get-command-result, which is polling (E-081) and "
            "is refused by the hook layer. Prefer 'contains' with a completion "
            "token the job writes LAST ('echo DONE >> log'): shell redirection "
            "creates the file before the job writes, so bare existence races "
            "the writer. Stateless — valid before rag_boot and on a clone with "
            "no RAG. A TIMEOUT is returned as an ERROR, never as success."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Sentinel file to watch. Its parent need not exist yet.",
                },
                "timeout_s": {
                    "type": "number",
                    "description": (
                        f"Hard upper bound in seconds, monotonic. Max "
                        f"{_WAIT_MAX_TIMEOUT_S}. NOTE: the MCP client's own "
                        f"request timeout also applies and is typically lower "
                        f"(~{_CLIENT_TIMEOUT_HINT_S}s unless MCP_TIMEOUT is set)."
                    ),
                },
                "contains": {
                    "type": "string",
                    "description": "Completion token the file must contain. Strongly preferred over bare existence.",
                },
                "emit_lines": {
                    "type": "integer",
                    "description": f"Lines of tail to return on success (0-{_WAIT_MAX_EMIT_LINES}, default 20).",
                },
                "poll_ms": {
                    "type": "integer",
                    "description": "Internal stat interval, default 250. Costs no round-trips; this is the machine polling, not the agent.",
                },
            },
            "required": ["path", "timeout_s"],
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

        # rag_wait builds its own envelope: a TIMEOUT is a legitimate return
        # value of wait_for, not an exception, so the generic try/except below
        # would report an expired wait as a SUCCESS. It also must not touch
        # self.app — see the AGENT-SIDE-WAIT-GAP note in the module docstring.
        if tool_name == "rag_wait":
            return self._handle_rag_wait(arguments)

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
            "rag_graph_run": lambda args: self.app.run_graph(
                args["nodes"],
                schedule=args.get("schedule", "sequential"),
                stop_on_failure=args.get("stop_on_failure", False),
                rollback_on_failure=args.get("rollback_on_failure", False),
            ),
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

    @staticmethod
    def _wait_envelope(payload: dict, *, is_error: bool) -> dict:
        """Wrap a rag_wait payload in the MCP content envelope."""
        result = {
            "content": [
                {"type": "text", "text": json.dumps(payload, indent=2, ensure_ascii=False)}
            ],
        }
        if is_error:
            result["isError"] = True
        return result

    def _handle_rag_wait(self, args: dict) -> dict:
        """Blocking agent-side wait — the AGENT-SIDE-WAIT-GAP fix.

        Deliberately does NOT go through ``self.app``: the wait must answer
        before boot, on a deployment with no RAG on disk. Delegates the whole
        state machine to the baked WAIT-PRIMITIVE asset (Rule 25, reuse before
        rewrite); everything here is argument bounding and envelope shaping.
        """
        # -- bound the arguments the transport owns ---------------------------
        emit_lines = args.get("emit_lines", 20)
        try:
            emit_lines = int(emit_lines)
        except (TypeError, ValueError):
            return self._wait_envelope(
                {"error": f"emit_lines must be an integer, got {emit_lines!r}"},
                is_error=True,
            )
        if emit_lines > _WAIT_MAX_EMIT_LINES:
            emit_lines = _WAIT_MAX_EMIT_LINES

        timeout_s = args.get("timeout_s")
        try:
            timeout_probe = float(timeout_s)
        except (TypeError, ValueError):
            return self._wait_envelope(
                {"error": f"timeout_s must be a number, got {timeout_s!r}"},
                is_error=True,
            )
        if timeout_probe > _WAIT_MAX_TIMEOUT_S:
            return self._wait_envelope(
                {
                    "error": (
                        f"timeout_s {timeout_probe:.0f}s exceeds the agent-facing "
                        f"ceiling of {_WAIT_MAX_TIMEOUT_S}s. A wait that long is a "
                        f"hung job, not a slow one — surface it instead of blocking on it."
                    )
                },
                is_error=True,
            )

        # -- run the state machine -------------------------------------------
        try:
            result = wait_for(
                args.get("path"),
                timeout_probe,
                contains=args.get("contains"),
                emit_lines=emit_lines,
                poll_ms=args.get("poll_ms", 250),
            )
        except WaitError as e:
            # Malformed request, not a wait outcome. EXIT_USAGE equivalent.
            return self._wait_envelope({"error": f"rag_wait usage: {e}"}, is_error=True)

        payload = result.to_dict()
        payload["render"] = result.render()

        if args.get("contains") is None:
            payload["advisory"] = (
                "Waited on bare existence. Shell redirection creates the file "
                "before the job writes to it, so this can report completion "
                "against an empty file. Prefer contains=<token written last>."
            )
        if timeout_probe > _CLIENT_TIMEOUT_HINT_S:
            payload["client_timeout_note"] = (
                f"timeout_s {timeout_probe:.0f}s exceeds the typical MCP client "
                f"request timeout (~{_CLIENT_TIMEOUT_HINT_S}s). The kernel cannot "
                f"raise that limit; set MCP_TIMEOUT on the client or this call "
                f"will be cut off from outside regardless of the wait's outcome."
            )

        # FAIL-LOUD: an expired wait is an error, never a quiet success.
        return self._wait_envelope(payload, is_error=not result.ok)

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
