"""Tests for the GRAPH-ORCH increment-2 execution engine (GraphExecutor).

The pure DAG core (increment 1) is covered by test_graph_orchestrator.py.
This file covers the execution engine that drives a DAG through the kernel's
propose -> validate -> commit pipeline with a checkpoint after every committed
node (the guarded CHECKPOINTING transition) and a per-node GRAPH_NODE_EXECUTED
WAL event — mirroring the M-009 enforce_context_policy integration tests.

Layers:
  * Construction / misuse guards (fail-loud, double-run).
  * Happy paths (linear chain, diamond) — deterministic order, all DONE,
    checkpoint-per-node, dependents promoted.
  * Failure propagation — a FAILED node SKIPs its downstream closure while
    independent branches still run; stop_on_failure halts.
  * Kernel-integration invariants — state ends READY, WAL events are schema-
    valid, checkpoints are recorded, full-vs-delta honored.
  * Determinism — identical runs produce identical order/results.
"""

import json

import pytest

from rag_kernel.api import KernelApp
from rag_kernel.graph_orchestrator import (
    ExecutionDAG,
    OrchestratorNode,
    NodeStatus,
    GraphExecutor,
    NodeExecutionResult,
    GraphExecutionError,
    GRAPH_NODE_EVENT,
)
from rag_kernel.schemas import validate_event
from rag_kernel.state_machine import State


# ===== Fixtures =====

@pytest.fixture
def project_dir(tmp_path):
    d = tmp_path / "RAG"
    d.mkdir()
    (d / "RAG_MASTER.json").write_text(
        json.dumps({
            "meta": {"session_id": "S-EXEC", "state_hash": ""},
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
    app = KernelApp(project_dir, session_id="S-EXEC")
    app.boot()
    return app


# ===== Helpers =====

def node(nid, deps=(), action="update_status", payload=None):
    """A runnable node. Default action is a valid, side-effect-light mutation."""
    return OrchestratorNode(
        id=nid,
        deps=frozenset(deps),
        action=action,
        payload=payload if payload is not None else {f"k_{nid}": nid},
    )


def bad_conflict_node(nid, deps=()):
    """A node whose proposal the kernel will REJECT at validation.

    action 'add_conflict' with an empty payload fails validate_conflict_payload
    inside KernelApp.propose, so the node FAILS deterministically.
    """
    return OrchestratorNode(
        id=nid, deps=frozenset(deps), action="add_conflict", payload={}
    )


def graph_events(app):
    """All GRAPH_NODE_EXECUTED WAL entries, in order."""
    return [
        e.to_dict() for e in app.wal.replay()
        if e.to_dict()["event"] == GRAPH_NODE_EVENT
    ]


# ===== Construction / misuse =====

class TestConstruction:
    def test_node_without_action_is_fail_loud(self, booted):
        dag = ExecutionDAG([OrchestratorNode(id="a", action=None)])
        with pytest.raises(GraphExecutionError):
            GraphExecutor(dag, booted)

    def test_empty_action_string_is_fail_loud(self, booted):
        dag = ExecutionDAG([OrchestratorNode(id="a", action="")])
        with pytest.raises(GraphExecutionError):
            GraphExecutor(dag, booted)

    def test_double_run_raises(self, booted):
        dag = ExecutionDAG([node("a")])
        ex = GraphExecutor(dag, booted)
        ex.run()
        with pytest.raises(GraphExecutionError):
            ex.run()

    def test_repr_mentions_run_state(self, booted):
        dag = ExecutionDAG([node("a")])
        ex = GraphExecutor(dag, booted)
        assert "ran=False" in repr(ex)
        ex.run()
        assert "ran=True" in repr(ex)


# ===== Happy paths =====

class TestLinearChain:
    def test_chain_all_done_in_order(self, booted):
        dag = ExecutionDAG([
            node("a"),
            node("b", deps=["a"]),
            node("c", deps=["b"]),
        ])
        report = GraphExecutor(dag, booted).run()
        assert report["complete"] is True
        assert report["executed_order"] == ["a", "b", "c"]
        assert report["done"] == ["a", "b", "c"]
        assert report["failed"] == []
        assert report["skipped"] == []
        assert dag.counts()["DONE"] == 3

    def test_every_committed_node_is_checkpointed(self, booted):
        dag = ExecutionDAG([node("a"), node("b", deps=["a"])])
        ex = GraphExecutor(dag, booted)
        ex.run()
        for r in ex.results:
            assert r.committed is True
            assert r.checkpoint_seq is not None

    def test_state_returns_to_ready_after_run(self, booted):
        dag = ExecutionDAG([node("a"), node("b", deps=["a"])])
        GraphExecutor(dag, booted).run()
        assert booted.state_machine.current is State.READY

    def test_payloads_committed_to_hot(self, booted):
        dag = ExecutionDAG([
            node("a", payload={"alpha": 1}),
            node("b", deps=["a"], payload={"beta": 2}),
        ])
        GraphExecutor(dag, booted).run()
        hot = booted.get_hot()
        assert hot["alpha"] == 1
        assert hot["beta"] == 2


class TestDiamond:
    def test_diamond_deterministic_order(self, booted):
        # a -> {b, c} -> d
        dag = ExecutionDAG([
            node("a"),
            node("b", deps=["a"]),
            node("c", deps=["a"]),
            node("d", deps=["b", "c"]),
        ])
        report = GraphExecutor(dag, booted).run()
        assert report["complete"] is True
        # b and c are same-level; deterministic tie-break is by id.
        assert report["executed_order"] == ["a", "b", "c", "d"]
        assert report["done"] == ["a", "b", "c", "d"]


# ===== Failure propagation =====

class TestFailurePropagation:
    def test_failed_node_skips_downstream_closure(self, booted):
        # a(fail) -> b -> c ; d is independent and must still run.
        dag = ExecutionDAG([
            bad_conflict_node("a"),
            node("b", deps=["a"]),
            node("c", deps=["b"]),
            node("d"),
        ])
        report = GraphExecutor(dag, booted).run()
        assert report["complete"] is True
        assert report["failed"] == ["a"]
        assert report["skipped"] == ["b", "c"]
        assert report["done"] == ["d"]

    def test_failed_result_carries_errors_and_skipped(self, booted):
        dag = ExecutionDAG([bad_conflict_node("a"), node("b", deps=["a"])])
        ex = GraphExecutor(dag, booted)
        ex.run()
        a = next(r for r in ex.results if r.node_id == "a")
        assert a.status is NodeStatus.FAILED
        assert a.committed is False
        assert a.checkpoint_seq is None
        assert a.errors  # non-empty validation errors
        assert "b" in a.skipped

    def test_stop_on_failure_halts_early(self, booted):
        # Two independent roots; the lower id fails first.
        dag = ExecutionDAG([bad_conflict_node("a"), node("z")])
        report = GraphExecutor(dag, booted, stop_on_failure=True).run()
        assert "a" in report["failed"]
        # z never ran because we stopped after the failure.
        assert report["executed_order"] == ["a"]
        assert report["complete"] is False

    def test_failed_node_does_not_corrupt_hot(self, booted):
        dag = ExecutionDAG([bad_conflict_node("a")])
        before = json.dumps(booted.get_hot(), sort_keys=True)
        GraphExecutor(dag, booted).run()
        after = json.dumps(booted.get_hot(), sort_keys=True)
        assert before == after  # rejected proposal never mutated HOT


# ===== Kernel-integration invariants =====

class TestWALAndCheckpoints:
    def test_one_graph_event_per_node(self, booted):
        dag = ExecutionDAG([node("a"), node("b", deps=["a"]), node("c", deps=["a"])])
        GraphExecutor(dag, booted).run()
        evs = graph_events(booted)
        assert len(evs) == 3
        # WALEntry.to_dict flattens kwargs to the top level.
        assert {e["node_id"] for e in evs} == {"a", "b", "c"}
        assert all(e["status"] == "DONE" for e in evs)

    def test_graph_events_are_schema_valid(self, booted):
        dag = ExecutionDAG([node("a"), bad_conflict_node("b")])
        GraphExecutor(dag, booted).run()
        for e in graph_events(booted):
            ok, errors = validate_event(e)
            assert ok, errors

    def test_checkpoint_events_present(self, booted):
        dag = ExecutionDAG([node("a"), node("b", deps=["a"])])
        GraphExecutor(dag, booted).run()
        events = [e.to_dict()["event"] for e in booted.wal.replay()]
        assert "CHECKPOINT" in events
        assert "PROPOSAL_COMMITTED" in events

    def test_force_full_checkpoint(self, booted):
        dag = ExecutionDAG([node("a"), node("b", deps=["a"])])
        GraphExecutor(dag, booted, force_full_checkpoint=True).run()
        ckpts = [
            e.to_dict() for e in booted.wal.replay()
            if e.to_dict()["event"] == "CHECKPOINT"
        ]
        assert ckpts
        # WALEntry.to_dict flattens kwargs to the top level.
        for c in ckpts:
            assert c.get("checkpoint_type") == "full"


# ===== Determinism =====

class TestDeterminism:
    def _build(self):
        return ExecutionDAG([
            node("a"),
            node("b", deps=["a"]),
            node("c", deps=["a"]),
            node("d", deps=["b", "c"]),
        ])

    def test_identical_runs_same_order(self, tmp_path):
        orders = []
        for i in range(2):
            d = tmp_path / f"RAG{i}"
            d.mkdir()
            (d / "RAG_MASTER.json").write_text(
                json.dumps({"meta": {"session_id": f"S{i}", "state_hash": ""}}),
                encoding="utf-8",
            )
            (d / "RAG_COLD.json").write_text(
                json.dumps({"meta": {"type": "RAG_COLD"}}), encoding="utf-8"
            )
            app = KernelApp(d, session_id=f"S{i}")
            app.boot()
            report = GraphExecutor(self._build(), app).run()
            orders.append(report["executed_order"])
        assert orders[0] == orders[1] == ["a", "b", "c", "d"]
