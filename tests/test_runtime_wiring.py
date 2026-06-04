"""Tests for v4.0 runtime-wiring of the Graph Orchestrator.

Increments 1-7 built the orchestrator and registered it as a capability
module, but it was only *importable* — callers had to construct a
GraphExecutor in Python. This milestone wires it into the kernel's runtime
entry points so it is invokable through the runtime:

  * KernelApp.run_graph    — the canonical API entry (build DAG from a
                             JSON-serializable spec, drive it through the one
                             serialized propose->validate->commit pipeline).
  * `rag_kernel graph run` — the CLI command (boot app, run spec, print report).
  * rag_graph_run          — the MCP tool (same entry over JSON-RPC).

The wiring adds NO new state mutation, WAL event type, or schema: it reuses
GraphExecutor and the kernel's existing pipeline. These tests assert each
surface reaches the orchestrator, the report round-trips as JSON, and bad
input fails closed without mutating HOT.
"""

import json

import pytest

from rag_kernel.api import KernelApp
from rag_kernel.mcp_transport import MCPServer, TOOLS
from rag_kernel.__main__ import main


# ===== Fixtures =====

@pytest.fixture
def project_dir(tmp_path):
    d = tmp_path / "RAG"
    d.mkdir()
    (d / "RAG_MASTER.json").write_text(
        json.dumps({
            "meta": {"session_id": "S-WIRE", "state_hash": ""},
            "current_status": {"phase": "idle"},
        }),
        encoding="utf-8",
    )
    (d / "RAG_COLD.json").write_text(
        json.dumps({"meta": {"type": "RAG_COLD"}}),
        encoding="utf-8",
    )
    return d


@pytest.fixture
def booted(project_dir):
    app = KernelApp(project_dir, session_id="S-WIRE")
    app.boot()
    yield app
    app.close()


def chain_spec():
    """A linear 3-node spec, JSON-serializable (no Python objects)."""
    return [
        {"id": "a", "action": "update_status", "payload": {"k_a": "a"}},
        {"id": "b", "deps": ["a"], "action": "update_status", "payload": {"k_b": "b"}},
        {"id": "c", "deps": ["b"], "action": "update_status", "payload": {"k_c": "c"}},
    ]


# ===== KernelApp.run_graph (API surface) =====

class TestApiRunGraph:
    def test_sequential_chain_completes(self, booted):
        report = booted.run_graph(chain_spec())
        assert "error" not in report
        assert report["complete"] is True
        assert report["executed_order"] == ["a", "b", "c"]
        assert report["done"] == ["a", "b", "c"]
        assert report["schedule"] == "sequential"

    def test_levels_schedule(self, booted):
        # b and c both depend only on a -> same level, parallel-eligible.
        spec = [
            {"id": "a", "action": "update_status", "payload": {"x": 1}},
            {"id": "b", "deps": ["a"], "action": "update_status", "payload": {"x": 2}},
            {"id": "c", "deps": ["a"], "action": "update_status", "payload": {"x": 3}},
        ]
        report = booted.run_graph(spec, schedule="levels")
        assert report["complete"] is True
        assert report["schedule"] == "levels"
        # deterministic id order within the serialized commit pipeline
        assert report["executed_order"] == ["a", "b", "c"]
        assert [sorted(l) for l in report["levels_executed"]] == [["a"], ["b", "c"]]

    def test_report_is_json_serializable(self, booted):
        report = booted.run_graph(chain_spec())
        # Must round-trip — it crosses the CLI/MCP boundary as JSON.
        assert json.loads(json.dumps(report))["complete"] is True

    def test_unsupported_schedule_is_error_no_mutation(self, booted):
        before = json.dumps(booted.get_hot(), sort_keys=True)
        report = booted.run_graph(chain_spec(), schedule="process_levels")
        assert "error" in report
        assert "process_levels" in report["error"]
        assert json.dumps(booted.get_hot(), sort_keys=True) == before

    def test_unknown_schedule_is_error(self, booted):
        report = booted.run_graph(chain_spec(), schedule="bogus")
        assert "error" in report

    def test_bad_dag_spec_fails_closed(self, booted):
        before = json.dumps(booted.get_hot(), sort_keys=True)
        # dangling dependency -> DAGBuildError -> error dict, no mutation
        report = booted.run_graph(
            [{"id": "a", "deps": ["ghost"], "action": "update_status"}]
        )
        assert "error" in report
        assert json.dumps(booted.get_hot(), sort_keys=True) == before

    def test_cycle_spec_fails_closed(self, booted):
        report = booted.run_graph([
            {"id": "a", "deps": ["b"], "action": "update_status"},
            {"id": "b", "deps": ["a"], "action": "update_status"},
        ])
        assert "error" in report

    def test_failure_propagation(self, booted):
        # 'add_conflict' with an empty payload is rejected at validation, so the
        # node FAILS and its downstream closure is SKIPPED.
        spec = [
            {"id": "a", "action": "add_conflict", "payload": {}},
            {"id": "b", "deps": ["a"], "action": "update_status", "payload": {"k": 1}},
        ]
        report = booted.run_graph(spec)
        assert "a" in report["failed"]
        assert "b" in report["skipped"]

    def test_run_graph_blocked_outside_runtime_state(self, project_dir):
        # An un-booted app is not in a state where proposals are legal.
        app = KernelApp(project_dir, session_id="S-WIRE")
        report = app.run_graph(chain_spec())
        assert "error" in report
        assert "cannot run graph" in report["error"]


# ===== CLI: rag_kernel graph run (CLI surface) =====

class TestCliGraph:
    def test_graph_run_executes_and_prints_report(self, project_dir, tmp_path, capsys):
        spec_file = tmp_path / "spec.json"
        spec_file.write_text(json.dumps({"nodes": chain_spec()}), encoding="utf-8")
        rc = main([
            "graph", "run", str(spec_file),
            "--project", str(project_dir),
            "--session-id", "S-CLI",
        ])
        assert rc == 0
        out = json.loads(capsys.readouterr().out)
        assert out["complete"] is True
        assert out["done"] == ["a", "b", "c"]

    def test_graph_run_schedule_override(self, project_dir, tmp_path, capsys):
        spec_file = tmp_path / "spec.json"
        spec_file.write_text(
            json.dumps({"nodes": chain_spec(), "schedule": "sequential"}),
            encoding="utf-8",
        )
        rc = main([
            "graph", "run", str(spec_file),
            "--project", str(project_dir),
            "--schedule", "levels",
        ])
        assert rc == 0
        assert json.loads(capsys.readouterr().out)["schedule"] == "levels"

    def test_graph_run_missing_spec_returns_1(self, project_dir, tmp_path):
        rc = main([
            "graph", "run", str(tmp_path / "nope.json"),
            "--project", str(project_dir),
        ])
        assert rc == 1

    def test_graph_run_bad_spec_returns_1(self, project_dir, tmp_path, capsys):
        spec_file = tmp_path / "spec.json"
        spec_file.write_text(
            json.dumps({"nodes": [{"id": "a", "deps": ["ghost"], "action": "update_status"}]}),
            encoding="utf-8",
        )
        rc = main([
            "graph", "run", str(spec_file),
            "--project", str(project_dir),
        ])
        assert rc == 1
        assert "error" in json.loads(capsys.readouterr().out)


# ===== MCP: rag_graph_run (MCP surface) =====

class TestMcpGraph:
    def test_tool_is_advertised(self):
        names = {t["name"] for t in TOOLS}
        assert "rag_graph_run" in names

    def test_tool_call_runs_graph(self, booted):
        server = MCPServer(booted)
        resp = server._handle_tools_call(
            {"name": "rag_graph_run", "arguments": {"nodes": chain_spec()}}
        )
        assert not resp.get("isError")
        payload = json.loads(resp["content"][0]["text"])
        assert payload["complete"] is True
        assert payload["done"] == ["a", "b", "c"]

    def test_tool_call_levels(self, booted):
        server = MCPServer(booted)
        resp = server._handle_tools_call({
            "name": "rag_graph_run",
            "arguments": {"nodes": chain_spec(), "schedule": "levels"},
        })
        assert json.loads(resp["content"][0]["text"])["schedule"] == "levels"

    def test_tool_call_bad_schedule_surfaces_error(self, booted):
        server = MCPServer(booted)
        resp = server._handle_tools_call({
            "name": "rag_graph_run",
            "arguments": {"nodes": chain_spec(), "schedule": "process_levels"},
        })
        # run_graph returns {"error": ...} which serializes cleanly (not isError).
        assert "error" in json.loads(resp["content"][0]["text"])
