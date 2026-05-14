"""Tests for the RAG Runtime Kernel CLI entry point.

Coverage targets:
- Argument parsing (serve, mcp, missing command)
- serve command (project validation, server creation)
- mcp command (project validation)
- main() return codes
"""

import json
import sys

import pytest

from rag_kernel.__main__ import build_parser, main


# ===== Fixtures =====

SAMPLE_HOT = {
    "meta": {"session_id": "S8", "state_hash": "", "last_checkpoint_seq": 0},
}


@pytest.fixture
def project_dir(tmp_path):
    d = tmp_path / "RAG"
    d.mkdir()
    (d / "RAG_MASTER.json").write_text(json.dumps(SAMPLE_HOT), encoding="utf-8")
    (d / "RAG_COLD.json").write_text('{"meta":{}}', encoding="utf-8")
    return d


# ===== Parser =====

class TestParser:
    def test_serve_command(self):
        parser = build_parser()
        args = parser.parse_args(["serve", "--project", "/tmp/RAG"])
        assert args.command == "serve"
        assert args.port == 7437
        assert args.host == "127.0.0.1"

    def test_serve_custom_port(self):
        parser = build_parser()
        args = parser.parse_args(["serve", "--project", "/tmp/RAG", "--port", "8080"])
        assert args.port == 8080

    def test_serve_custom_host(self):
        parser = build_parser()
        args = parser.parse_args(["serve", "--project", "/tmp/RAG", "--host", "0.0.0.0"])
        assert args.host == "0.0.0.0"

    def test_mcp_command(self):
        parser = build_parser()
        args = parser.parse_args(["mcp", "--project", "/tmp/RAG"])
        assert args.command == "mcp"

    def test_session_id(self):
        parser = build_parser()
        args = parser.parse_args(["serve", "--project", "/tmp/RAG", "--session-id", "S9"])
        assert args.session_id == "S9"

    def test_no_command(self):
        parser = build_parser()
        args = parser.parse_args([])
        assert args.command is None


# ===== main() =====

class TestMain:
    def test_no_command_returns_1(self):
        result = main([])
        assert result == 1

    def test_serve_missing_project(self, tmp_path):
        result = main(["serve", "--project", str(tmp_path / "nonexistent")])
        assert result == 1

    def test_mcp_missing_project(self, tmp_path):
        result = main(["mcp", "--project", str(tmp_path / "nonexistent")])
        assert result == 1

    def test_serve_requires_project(self):
        with pytest.raises(SystemExit):
            main(["serve"])

    def test_mcp_requires_project(self):
        with pytest.raises(SystemExit):
            main(["mcp"])
