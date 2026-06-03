"""Tests for the GRAPH-ORCH increment-3 deterministic-levels scheduler.

The pure DAG core (increment 1) is covered by test_graph_orchestrator.py and the
sequential execution engine (increment 2) by test_graph_executor.py. This file
covers Schedule.LEVELS: walking the DAG one topological level at a time, exposing
each level's parallel-eligible batch, while still committing every node through
the one serialized propose -> validate -> commit pipeline in deterministic id
order under the project file-mutex.

Layers:
  * Level scheduling — per-level batches match levels(); wide levels run fully.
  * Equivalence — LEVELS produces the SAME executed order, final HOT, and WAL
    graph-event sequence as SEQUENTIAL (the core INS-023 determinism guarantee).
  * Single-writer — the scheduler refuses to run unless the kernel holds the
    project lock for its own session (missing / foreign lock => fail-loud).
  * Failure propagation under levels — an early-level failure SKIPs its
    downstream closure across later levels; independent branches still run.
  * Kernel-integration invariants — schema-valid WAL, checkpoint-per-node,
    full-vs-delta, state ends READY, misuse guards, determinism.
"""

import json

import pytest

from rag_kernel.api import KernelApp
from rag_kernel.concurrency import LOCK_FILENAME
from rag_kernel.graph_orchestrator import (
    ExecutionDAG,
    OrchestratorNode,
    NodeStatus,
    GraphExecutor,
    GraphExecutionError,
    Schedule,
    GRAPH_NODE_EVENT,
)
from rag_kernel.schemas import validate_event
from rag_kernel.state_machine import State


# ===== Fixtures / helpers =====

def _make_project(tmp_path, name="RAG", session="S-LVL"):
    d = tmp_path / name
    d.mkdir()
    (d / "RAG_MASTER.json").write_text(
        json.dumps({
            "meta": {"session_id": session, "state_hash": ""},
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
def project_dir(tmp_path):
    return _make_project(tmp_path)


@pytest.fixture
def booted(project_dir):
    app = KernelApp(project_dir, session_id="S-LVL")
    app.boot()
    return app


def boot_app(tmp_path, name, session):
    app = KernelApp(_make_project(tmp_path, name=name, session=session),
                    session_id=session)
    app.boot()
    return app


def node(nid, deps=(), action="update_status", payload=None):
    return OrchestratorNode(
        id=nid,
        deps=frozenset(deps),
        action=action,
        payload=payload if payload is not None else {f"k_{nid}": nid},
    )


def bad_conflict_node(nid, deps=()):
    """Proposal the kernel REJECTS at validation -> node FAILS deterministically."""
    return OrchestratorNode(
        id=nid, deps=frozenset(deps), action="add_conflict", payload={}
    )


def graph_event_seq(app):
    """Ordered (node_id, status) of every GRAPH_NODE_EXECUTED WAL entry."""
    return [
        (e.to_dict()["node_id"], e.to_dict()["status"])
        for e in app.wal.replay()
        if e.to_dict()["event"] == GRAPH_NODE_EVENT
    ]


def diamond():
    # a -> {b, c} -> d
    return [
        node("a"),
        node("b", deps=["a"]),
        node("c", deps=["a"]),
        node("d", deps=["b", "c"]),
    ]


# ===== Level scheduling =====

class TestLevelScheduling:
    def test_linear_chain_one_node_per_level(self, booted):
        dag = ExecutionDAG([node("a"), node("b", deps=["a"]), node("c", deps=["b"])])
        report = GraphExecutor(dag, booted, schedule=Schedule.LEVELS).run()
        assert report["complete"] is True
        assert report["schedule"] == "levels"
        assert report["executed_order"] == ["a", "b", "c"]
        assert report["levels_executed"] == [["a"], ["b"], ["c"]]

    def test_diamond_batches_match_levels(self, booted):
        dag = ExecutionDAG(diamond())
        ex = GraphExecutor(dag, booted, schedule=Schedule.LEVELS)
        report = ex.run()
        assert report["complete"] is True
        assert report["executed_order"] == ["a", "b", "c", "d"]
        # middle level holds the two parallel-eligible nodes, sorted by id.
        assert report["levels_executed"] == [["a"], ["b", "c"], ["d"]]
        assert ex.levels_executed == [["a"], ["b", "c"], ["d"]]

    def test_wide_level_runs_all_roots_in_id_order(self, booted):
        # three independent roots => one level of three parallel-eligible nodes.
        dag = ExecutionDAG([node("z"), node("a"), node("m")])
        report = GraphExecutor(dag, booted, schedule=Schedule.LEVELS).run()
        assert report["levels_executed"] == [["a", "m", "z"]]
        assert report["executed_order"] == ["a", "m", "z"]
        assert report["done"] == ["a", "m", "z"]


# ===== Equivalence with the SEQUENTIAL schedule (the INS-023 guarantee) =====

class TestEquivalence:
    @pytest.mark.parametrize("nodes_fn", [
        diamond,
        lambda: [node("a"), node("b", deps=["a"]), node("c", deps=["a"]),
                 node("d", deps=["b"]), node("e", deps=["b", "c"]),
                 node("f", deps=["d", "e"])],
        lambda: [node("r1"), node("r2"), node("x", deps=["r1", "r2"])],
    ])
    def test_levels_equivalent_to_sequential(self, tmp_path, nodes_fn):
        seq_app = boot_app(tmp_path, "seq", "S-SEQ")
        lvl_app = boot_app(tmp_path, "lvl", "S-LVL")

        seq = GraphExecutor(ExecutionDAG(nodes_fn()), seq_app,
                            schedule=Schedule.SEQUENTIAL).run()
        lvl = GraphExecutor(ExecutionDAG(nodes_fn()), lvl_app,
                            schedule=Schedule.LEVELS).run()

        # Same executed order, same completion, same status tally.
        assert seq["executed_order"] == lvl["executed_order"]
        assert seq["complete"] == lvl["complete"]
        assert seq["counts"] == lvl["counts"]
        # Same WAL graph-event sequence (node_id, status) — state evolves identically.
        assert graph_event_seq(seq_app) == graph_event_seq(lvl_app)
        # Same committed payloads landed in HOT.
        seq_hot, lvl_hot = seq_app.get_hot(), lvl_app.get_hot()
        for nid in ExecutionDAG(nodes_fn()).node_ids:
            key = f"k_{nid}"
            assert seq_hot.get(key) == lvl_hot.get(key) == nid

    def test_failure_closure_equivalent(self, tmp_path):
        def build():
            # a(fail) -> b -> c ; independent d, e(dep d)
            return [
                bad_conflict_node("a"),
                node("b", deps=["a"]),
                node("c", deps=["b"]),
                node("d"),
                node("e", deps=["d"]),
            ]
        seq_app = boot_app(tmp_path, "seq", "S-SEQ")
        lvl_app = boot_app(tmp_path, "lvl", "S-LVL")
        seq = GraphExecutor(ExecutionDAG(build()), seq_app,
                            schedule=Schedule.SEQUENTIAL).run()
        lvl = GraphExecutor(ExecutionDAG(build()), lvl_app,
                            schedule=Schedule.LEVELS).run()
        assert seq["failed"] == lvl["failed"] == ["a"]
        assert seq["skipped"] == lvl["skipped"] == ["b", "c"]
        assert seq["done"] == lvl["done"] == ["d", "e"]
        assert graph_event_seq(seq_app) == graph_event_seq(lvl_app)


# ===== Single-writer enforcement (project file-mutex) =====

class TestSingleWriter:
    def test_foreign_lock_refuses_to_run(self, booted, project_dir):
        # Overwrite the lock with a different, live session => single-writer broken.
        import os
        (project_dir / LOCK_FILENAME).write_text(
            json.dumps({
                "session_id": "S-OTHER",
                "pid": os.getpid(),  # alive, so not reclaimable as stale
                "acquired_at": "2026-01-01T00:00:00Z",
            }),
            encoding="utf-8",
        )
        dag = ExecutionDAG([node("a")])
        with pytest.raises(GraphExecutionError, match="different session"):
            GraphExecutor(dag, booted, schedule=Schedule.LEVELS).run()

    def test_missing_lock_refuses_to_run(self, booted):
        booted.lock.release()  # drop the boot lock
        dag = ExecutionDAG([node("a")])
        with pytest.raises(GraphExecutionError, match="not held"):
            GraphExecutor(dag, booted, schedule=Schedule.LEVELS).run()

    def test_sequential_does_not_require_lock(self, booted):
        # The single-writer assertion is specific to LEVELS; SEQUENTIAL is
        # unchanged from increment 2 and must still run with no lock.
        booted.lock.release()
        dag = ExecutionDAG([node("a")])
        report = GraphExecutor(dag, booted, schedule=Schedule.SEQUENTIAL).run()
        assert report["complete"] is True


# ===== Failure propagation across levels =====

class TestFailureAcrossLevels:
    def test_early_level_failure_skips_downstream(self, booted):
        # level0: a(fail), d ; level1: b(dep a), e(dep d) ; level2: c(dep b)
        dag = ExecutionDAG([
            bad_conflict_node("a"),
            node("d"),
            node("b", deps=["a"]),
            node("e", deps=["d"]),
            node("c", deps=["b"]),
        ])
        report = GraphExecutor(dag, booted, schedule=Schedule.LEVELS).run()
        assert report["complete"] is True
        assert report["failed"] == ["a"]
        assert report["skipped"] == ["b", "c"]
        assert report["done"] == ["d", "e"]
        # a and d share level 0; b/e share level 1 but b is skipped (not dispatched).
        assert report["levels_executed"][0] == ["a", "d"]
        assert "b" not in report["executed_order"]
        assert "e" in report["executed_order"]

    def test_stop_on_failure_halts_within_levels(self, booted):
        # 'a' (id-first in level 0) fails; stop before dispatching 'z'.
        dag = ExecutionDAG([bad_conflict_node("a"), node("z")])
        report = GraphExecutor(
            dag, booted, schedule=Schedule.LEVELS, stop_on_failure=True
        ).run()
        assert report["failed"] == ["a"]
        assert report["executed_order"] == ["a"]
        assert report["levels_executed"] == [["a"]]
        assert report["complete"] is False

    def test_failed_node_does_not_corrupt_hot(self, booted):
        dag = ExecutionDAG([bad_conflict_node("a")])
        before = json.dumps(booted.get_hot(), sort_keys=True)
        GraphExecutor(dag, booted, schedule=Schedule.LEVELS).run()
        after = json.dumps(booted.get_hot(), sort_keys=True)
        assert before == after


# ===== Kernel-integration invariants =====

class TestKernelInvariants:
    def test_graph_events_schema_valid(self, booted):
        dag = ExecutionDAG([node("a"), bad_conflict_node("b"), node("c", deps=["a"])])
        GraphExecutor(dag, booted, schedule=Schedule.LEVELS).run()
        evs = [
            e.to_dict() for e in booted.wal.replay()
            if e.to_dict()["event"] == GRAPH_NODE_EVENT
        ]
        assert evs
        for e in evs:
            ok, errors = validate_event(e)
            assert ok, errors

    def test_checkpoint_per_committed_node(self, booted):
        dag = ExecutionDAG(diamond())
        ex = GraphExecutor(dag, booted, schedule=Schedule.LEVELS)
        ex.run()
        for r in ex.results:
            assert r.committed is True
            assert r.checkpoint_seq is not None

    def test_force_full_checkpoint_honored(self, booted):
        dag = ExecutionDAG([node("a"), node("b", deps=["a"])])
        GraphExecutor(
            dag, booted, schedule=Schedule.LEVELS, force_full_checkpoint=True
        ).run()
        ckpts = [
            e.to_dict() for e in booted.wal.replay()
            if e.to_dict()["event"] == "CHECKPOINT"
        ]
        assert ckpts
        for c in ckpts:
            assert c.get("checkpoint_type") == "full"

    def test_state_returns_to_ready(self, booted):
        dag = ExecutionDAG(diamond())
        GraphExecutor(dag, booted, schedule=Schedule.LEVELS).run()
        assert booted.state_machine.current is State.READY

    def test_double_run_raises(self, booted):
        dag = ExecutionDAG([node("a")])
        ex = GraphExecutor(dag, booted, schedule=Schedule.LEVELS)
        ex.run()
        with pytest.raises(GraphExecutionError):
            ex.run()

    def test_repr_mentions_schedule(self, booted):
        dag = ExecutionDAG([node("a")])
        ex = GraphExecutor(dag, booted, schedule=Schedule.LEVELS)
        assert "schedule=levels" in repr(ex)


# ===== Report shape & determinism =====

class TestReportAndDeterminism:
    def test_sequential_report_has_empty_levels(self, booted):
        dag = ExecutionDAG([node("a"), node("b", deps=["a"])])
        report = GraphExecutor(dag, booted, schedule=Schedule.SEQUENTIAL).run()
        assert report["schedule"] == "sequential"
        assert report["levels_executed"] == []

    def test_identical_runs_same_order_and_levels(self, tmp_path):
        results = []
        for i in range(2):
            app = boot_app(tmp_path, f"r{i}", f"S{i}")
            report = GraphExecutor(
                ExecutionDAG(diamond()), app, schedule=Schedule.LEVELS
            ).run()
            results.append((report["executed_order"], report["levels_executed"]))
        assert results[0] == results[1]
        assert results[0] == (["a", "b", "c", "d"], [["a"], ["b", "c"], ["d"]])
