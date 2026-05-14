"""Tests for the RAG Runtime Kernel MCP stdio transport.

Coverage targets:
- Tool definitions completeness
- Initialize handshake
- tools/list response
- tools/call dispatch for each tool
- Error handling (unknown tool, missing params)
- JSON-RPC framing (success, error)
- Ping
- Notification handling (no response)
- Message I/O (Content-Length and newline-delimited)
"""

import io
import json

import pytest

from rag_kernel.api import KernelApp
from rag_kernel.mcp_transport import (
    JSONRPC_VERSION,
    MCP_PROTOCOL_VERSION,
    MCPServer,
    SERVER_NAME,
    SERVER_VERSION,
    TOOLS,
)


# ===== Fixtures =====

SAMPLE_HOT = {
    "meta": {"session_id": "S8", "state_hash": "", "last_checkpoint_seq": 0},
    "current_status": {"phase": "idle"},
}


@pytest.fixture
def project_dir(tmp_path):
    d = tmp_path / "RAG"
    d.mkdir()
    (d / "RAG_MASTER.json").write_text(json.dumps(SAMPLE_HOT), encoding="utf-8")
    (d / "RAG_COLD.json").write_text(
        json.dumps({"meta": {"type": "RAG_COLD"}, "inventory": {"files": []}}),
        encoding="utf-8",
    )
    return d


@pytest.fixture
def app(project_dir):
    return KernelApp(project_dir, session_id="MCP-TEST")


@pytest.fixture
def booted_app(app):
    app.boot()
    return app


@pytest.fixture
def server(booted_app):
    """MCPServer with in-memory streams."""
    return MCPServer(booted_app, input_stream=io.StringIO(), output_stream=io.StringIO())


def call(server, method, params=None, msg_id=1):
    """Helper: send a JSON-RPC message and get the response."""
    message = {"jsonrpc": JSONRPC_VERSION, "id": msg_id, "method": method}
    if params:
        message["params"] = params
    return server.handle_message(message)


# ===== Tool definitions =====

class TestToolDefinitions:
    def test_all_tools_have_names(self):
        for tool in TOOLS:
            assert "name" in tool
            assert tool["name"].startswith("rag_")

    def test_all_tools_have_descriptions(self):
        for tool in TOOLS:
            assert "description" in tool
            assert len(tool["description"]) > 10

    def test_all_tools_have_input_schema(self):
        for tool in TOOLS:
            assert "inputSchema" in tool
            assert tool["inputSchema"]["type"] == "object"

    def test_tool_count(self):
        assert len(TOOLS) == 11

    def test_expected_tools_present(self):
        names = {t["name"] for t in TOOLS}
        expected = {
            "rag_boot", "rag_status", "rag_hot", "rag_cold",
            "rag_propose", "rag_commit", "rag_reject",
            "rag_checkpoint", "rag_wal", "rag_recover", "rag_close",
        }
        assert names == expected


# ===== Initialize =====

class TestInitialize:
    def test_initialize(self, server):
        resp = call(server, "initialize")
        assert resp["jsonrpc"] == JSONRPC_VERSION
        result = resp["result"]
        assert result["protocolVersion"] == MCP_PROTOCOL_VERSION
        assert result["serverInfo"]["name"] == SERVER_NAME
        assert result["serverInfo"]["version"] == SERVER_VERSION
        assert "tools" in result["capabilities"]


# ===== tools/list =====

class TestToolsList:
    def test_tools_list(self, server):
        resp = call(server, "tools/list")
        tools = resp["result"]["tools"]
        assert len(tools) == 11
        names = {t["name"] for t in tools}
        assert "rag_status" in names


# ===== tools/call =====

class TestToolsCall:
    def test_call_status(self, server):
        resp = call(server, "tools/call", {"name": "rag_status", "arguments": {}})
        content = json.loads(resp["result"]["content"][0]["text"])
        assert content["state"] == "READY"

    def test_call_hot(self, server):
        resp = call(server, "tools/call", {"name": "rag_hot", "arguments": {}})
        content = json.loads(resp["result"]["content"][0]["text"])
        assert "meta" in content

    def test_call_cold_full(self, server):
        resp = call(server, "tools/call", {"name": "rag_cold", "arguments": {}})
        content = json.loads(resp["result"]["content"][0]["text"])
        assert "meta" in content

    def test_call_cold_partition(self, server):
        resp = call(server, "tools/call", {
            "name": "rag_cold",
            "arguments": {"partition": "inventory"},
        })
        content = json.loads(resp["result"]["content"][0]["text"])
        assert "files" in content

    def test_call_propose(self, server):
        resp = call(server, "tools/call", {
            "name": "rag_propose",
            "arguments": {"action": "test", "payload": {"x": 1}},
        })
        content = json.loads(resp["result"]["content"][0]["text"])
        assert content["valid"] is True
        assert "proposal_id" in content

    def test_call_commit(self, server):
        # First propose
        prop_resp = call(server, "tools/call", {
            "name": "rag_propose",
            "arguments": {"action": "test", "payload": {"test": True}},
        })
        prop_id = json.loads(prop_resp["result"]["content"][0]["text"])["proposal_id"]

        # Then commit
        resp = call(server, "tools/call", {
            "name": "rag_commit",
            "arguments": {"proposal_id": prop_id},
        })
        content = json.loads(resp["result"]["content"][0]["text"])
        assert content["committed"] is True

    def test_call_reject(self, server):
        prop_resp = call(server, "tools/call", {
            "name": "rag_propose",
            "arguments": {"action": "test", "payload": {}},
        })
        prop_id = json.loads(prop_resp["result"]["content"][0]["text"])["proposal_id"]

        resp = call(server, "tools/call", {
            "name": "rag_reject",
            "arguments": {"proposal_id": prop_id},
        })
        content = json.loads(resp["result"]["content"][0]["text"])
        assert content["rejected"] is True

    def test_call_checkpoint(self, server):
        resp = call(server, "tools/call", {"name": "rag_checkpoint", "arguments": {}})
        content = json.loads(resp["result"]["content"][0]["text"])
        assert content["checkpointed"] is True

    def test_call_wal(self, server):
        resp = call(server, "tools/call", {"name": "rag_wal", "arguments": {}})
        content = json.loads(resp["result"]["content"][0]["text"])
        assert isinstance(content, list)
        assert len(content) >= 1

    def test_call_wal_since(self, server):
        resp = call(server, "tools/call", {
            "name": "rag_wal",
            "arguments": {"since": 999999},
        })
        content = json.loads(resp["result"]["content"][0]["text"])
        assert content == []

    def test_call_recover(self, server):
        resp = call(server, "tools/call", {"name": "rag_recover", "arguments": {}})
        content = json.loads(resp["result"]["content"][0]["text"])
        # No bak file, so recovery fails
        assert content["recovered"] is False

    def test_call_unknown_tool(self, server):
        resp = call(server, "tools/call", {"name": "rag_nonexistent", "arguments": {}})
        result = resp["result"]
        assert result["isError"] is True
        content = json.loads(result["content"][0]["text"])
        assert "Unknown tool" in content["error"]

    def test_call_boot(self, server):
        # Already booted, but calling again should work
        resp = call(server, "tools/call", {"name": "rag_boot", "arguments": {}})
        content = json.loads(resp["result"]["content"][0]["text"])
        assert "status" in content


# ===== Error handling =====

class TestErrorHandling:
    def test_unknown_method(self, server):
        resp = call(server, "unknown/method")
        assert "error" in resp
        assert resp["error"]["code"] == -32601

    def test_notification_no_response(self, server):
        msg = {"jsonrpc": JSONRPC_VERSION, "method": "notifications/initialized"}
        resp = server.handle_message(msg)
        assert resp is None


# ===== Ping =====

class TestPing:
    def test_ping(self, server):
        resp = call(server, "ping")
        assert resp["result"] == {}


# ===== JSON-RPC framing =====

class TestJSONRPCFraming:
    def test_success_response_structure(self, server):
        resp = call(server, "ping", msg_id=42)
        assert resp["jsonrpc"] == JSONRPC_VERSION
        assert resp["id"] == 42
        assert "result" in resp

    def test_error_response_structure(self, server):
        resp = call(server, "bad_method", msg_id=99)
        assert resp["jsonrpc"] == JSONRPC_VERSION
        assert resp["id"] == 99
        assert "error" in resp
        assert "code" in resp["error"]
        assert "message" in resp["error"]


# ===== Message I/O =====

class TestMessageIO:
    def test_write_message(self):
        out = io.StringIO()
        server = MCPServer(None, output_stream=out)
        server._write_message({"test": "value"})
        output = out.getvalue()
        assert "Content-Length:" in output
        assert '"test"' in output

    def test_read_newline_delimited(self):
        msg = {"jsonrpc": "2.0", "method": "ping", "id": 1}
        inp = io.StringIO(json.dumps(msg) + "\n")
        server = MCPServer(None, input_stream=inp)
        result = server._read_message()
        assert result["method"] == "ping"

    def test_read_content_length(self):
        msg = {"jsonrpc": "2.0", "method": "ping", "id": 1}
        body = json.dumps(msg)
        framed = f"Content-Length: {len(body)}\r\n\r\n{body}"
        inp = io.StringIO(framed)
        server = MCPServer(None, input_stream=inp)
        result = server._read_message()
        assert result["method"] == "ping"

    def test_read_eof(self):
        inp = io.StringIO("")
        server = MCPServer(None, input_stream=inp)
        result = server._read_message()
        assert result is None

    def test_read_corrupt_json(self):
        inp = io.StringIO("{bad json\n")
        server = MCPServer(None, input_stream=inp)
        result = server._read_message()
        assert result is None


# ===== Repr =====

class TestRepr:
    def test_repr(self, server):
        r = repr(server)
        assert "MCPServer" in r
        assert "initialized=False" in r

    def test_repr_after_init(self, server):
        call(server, "initialize")
        r = repr(server)
        assert "initialized=True" in r
