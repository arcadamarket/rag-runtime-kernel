"""Tests for the GRAPH-ORCH increment-6 process-parallel scheduler.

Schedule.PROCESS_LEVELS (INS-027): a level's work-bearing nodes run their PURE,
picklable ``work`` callable in OS subprocesses, while the parent stays the sole
writer and commits every node through the one serialized propose -> validate ->
commit pipeline in deterministic sorted-id order. This file proves:

  * the off-process worker contract (picklable callable + args; identity
    unaffected; non-callable work rejected at construction),
  * worker payloads are committed, and override a node's static payload,
  * PROCESS_LEVELS is equivalent to LEVELS and SEQUENTIAL (executed order, final
    HOT, WAL graph-event sequence) — the parallelism is a scheduling property,
  * commit happens in sorted-id order, NOT worker-completion order,
  * single-writer is enforced — a commit fails without an actively-held project
    lock (missing / foreign) [INS-033 intent 1],
  * a worker exception / non-Mapping routes the node to the same deterministic
    failure-closure (and opt-in rollback) path as a kernel-rejected proposal
    [INS-033 intent 2],
  * kernel-integration invariants (schema-valid WAL, checkpoint-per-node, READY).

Worker functions are module-level so they pickle to subprocesses under the
default start method (fork on Linux/WSL — the sanctioned test runner).
"""

import json
import time

import pytest

from rag_kernel.api import KernelApp
from rag_kernel.concurrency import LOCK_FILENAME
from rag_kernel.graph_orchestrator import (
    ExecutionDAG,
    OrchestratorNode,
    NodeStatus,
    GraphExecutor,
    GraphExecutionError,
    DAGBuildError,
    Schedule,
    GRAPH_NODE_EVENT,
)
from rag_kernel.schemas import validate_event
from rag_kernel.state_machine import State


# ===== Module-level (picklable) worker functions =====

def work_identity(nid):
    """Pure worker reproducing the static-node payload {k_<nid>: nid}."""
    return {f"k_{nid}": nid}


def work_value(nid, value):
    """Pure worker committing key k_<nid> = an arbitrary value."""
    return {f"k_{nid}": value}


def work_sleep_identity(nid, delay):
    """Pure worker that sleeps (to vary completion order) then returns payload."""
    time.sleep(delay)
    return {f"k_{nid}": nid}


def work_boom(nid):
    """Worker that raises — its node must fail deterministically."""
    raise RuntimeError(f"worker {nid} exploded")


def work_returns_list(nid):
    """Worker returning a non-Mapping — its node must fail with a helpful error."""
    return ["not", "a", "mapping"]


# ===== Fixtures / helpers =====

def _make_project(tmp_path, name="RAG", session="S-PROC"):
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
    app = KernelApp(project_dir, session_id="S-PROC")
    app.boot()
    return app


def boot_app(tmp_path, name, session):
    app = KernelApp(_make_project(tmp_path, name=name, session=session),
                    session_id=session)
    app.boot()
    return app


def snode(nid, deps=(), action="update_status", payload=None):
    """A static (no-work) node, identical to the LEVELS/SEQUENTIAL tests."""
    return OrchestratorNode(
        id=nid, deps=frozenset(deps), action=action,
        payload=payload if payload is not None else {f"k_{nid}": nid},
    )


def wnode(nid, deps=(), action="update_status", work=work_identity, work_args=None):
    """A work-bearing node; default work reproduces the static payload."""
    return OrchestratorNode(
        id=nid, deps=frozenset(deps), action=action,
        work=work, work_args=(nid,) if work_args is None else work_args,
    )


def bad_conflict_node(nid, deps=()):
    """Proposal the kernel REJECTS at validation -> node FAILS (kernel-side)."""
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


def diamond_work():
    # a -> {b, c} -> d, all work-bearing (work reproduces the static payload).
    return [
        wnode("a"),
        wnode("b", deps=["a"]),
        wnode("c", deps=["a"]),
        wnode("d", deps=["b", "c"]),
    ]


def result_for(ex, node_id):
    return next(r for r in ex.results if r.node_id == node_id)


# ===== Worker contract =====

class TestWorkerContract:
    def test_noncallable_work_rejected(self):
        with pytest.raises(DAGBuildError, match="must be callable"):
            OrchestratorNode(id="a", action="update_status", work=123)

    def test_work_args_normalized_to_tuple(self):
        n = OrchestratorNode(
            id="a", action="update_status", work=work_identity, work_args=["a"]
        )
        assert isinstance(n.work_args, tuple)
        assert n.work_args == ("a",)

    def test_work_excluded_from_identity(self):
        plain = OrchestratorNode(id="x", action="update_status")
        with_work = OrchestratorNode(
            id="x", action="update_status", work=work_identity, work_args=("x",)
        )
        # work / work_args are compare=False -> identity is still the id/structure.
        assert plain == with_work
        assert hash(plain) == hash(with_work)


# ===== Happy path =====

class TestHappyPath:
    def test_process_levels_commits_worker_payloads(self, booted):
        dag = ExecutionDAG(diamond_work())
        ex = GraphExecutor(dag, booted, schedule=Schedule.PROCESS_LEVELS)
        report = ex.run()
        assert report["complete"] is True
        assert report["schedule"] == "process_levels"
        assert report["executed_order"] == ["a", "b", "c", "d"]
        assert report["levels_executed"] == [["a"], ["b", "c"], ["d"]]
        hot = booted.get_hot()
        for nid in ("a", "b", "c", "d"):
            assert hot.get(f"k_{nid}") == nid

    def test_static_nodes_run_without_pool(self, booted):
        # No work-bearing nodes -> no subprocess pool; behaves like LEVELS.
        dag = ExecutionDAG([snode("a"), snode("b", deps=["a"])])
        report = GraphExecutor(
            dag, booted, schedule=Schedule.PROCESS_LEVELS
        ).run()
        assert report["complete"] is True
        assert report["executed_order"] == ["a", "b"]
        hot = booted.get_hot()
        assert hot.get("k_a") == "a" and hot.get("k_b") == "b"

    def test_worker_payload_overrides_static(self, booted):
        dag = ExecutionDAG([
            wnode("a", work=work_value, work_args=("a", "CUSTOM")),
        ])
        GraphExecutor(dag, booted, schedule=Schedule.PROCESS_LEVELS).run()
        assert booted.get_hot().get("k_a") == "CUSTOM"

    def test_mixed_work_and_static_nodes(self, booted):
        dag = ExecutionDAG([
            wnode("a"),                       # work-bearing root
            snode("b", deps=["a"]),           # static dependent
            wnode("c", deps=["a"]),           # work-bearing dependent
        ])
        report = GraphExecutor(
            dag, booted, schedule=Schedule.PROCESS_LEVELS
        ).run()
        assert report["done"] == ["a", "b", "c"]
        hot = booted.get_hot()
        assert hot.get("k_a") == "a"
        assert hot.get("k_b") == "b"
        assert hot.get("k_c") == "c"


# ===== Equivalence with LEVELS / SEQUENTIAL (the INS-027 guarantee) =====

class TestEquivalence:
    @pytest.mark.parametrize("static_fn,work_fn", [
        (
            lambda: [snode("a"), snode("b", deps=["a"]), snode("c", deps=["a"]),
                     snode("d", deps=["b", "c"])],
            diamond_work,
        ),
        (
            lambda: [snode("r1"), snode("r2"), snode("x", deps=["r1", "r2"])],
            lambda: [wnode("r1"), wnode("r2"), wnode("x", deps=["r1", "r2"])],
        ),
        (
            lambda: [snode("a"), snode("b", deps=["a"]), snode("c", deps=["a"]),
                     snode("d", deps=["b"]), snode("e", deps=["b", "c"]),
                     snode("f", deps=["d", "e"])],
            lambda: [wnode("a"), wnode("b", deps=["a"]), wnode("c", deps=["a"]),
                     wnode("d", deps=["b"]), wnode("e", deps=["b", "c"]),
                     wnode("f", deps=["d", "e"])],
        ),
    ])
    def test_process_levels_equivalent(self, tmp_path, static_fn, work_fn):
        seq_app = boot_app(tmp_path, "seq", "S-SEQ")
        lvl_app = boot_app(tmp_path, "lvl", "S-LVL")
        prc_app = boot_app(tmp_path, "prc", "S-PRC")

        seq = GraphExecutor(ExecutionDAG(static_fn()), seq_app,
                            schedule=Schedule.SEQUENTIAL).run()
        lvl = GraphExecutor(ExecutionDAG(static_fn()), lvl_app,
                            schedule=Schedule.LEVELS).run()
        prc = GraphExecutor(ExecutionDAG(work_fn()), prc_app,
                            schedule=Schedule.PROCESS_LEVELS).run()

        # Identical executed order, completion, and status tally across schedules.
        assert seq["executed_order"] == lvl["executed_order"] == prc["executed_order"]
        assert seq["complete"] == lvl["complete"] == prc["complete"] is True
        assert seq["counts"] == lvl["counts"] == prc["counts"]
        # Identical WAL graph-event sequence -> state evolved identically.
        assert graph_event_seq(seq_app) == graph_event_seq(prc_app)
        assert graph_event_seq(lvl_app) == graph_event_seq(prc_app)
        # Identical committed payloads landed in HOT.
        seq_hot, prc_hot = seq_app.get_hot(), prc_app.get_hot()
        for nid in ExecutionDAG(static_fn()).node_ids:
            assert seq_hot.get(f"k_{nid}") == prc_hot.get(f"k_{nid}") == nid

    def test_failure_closure_equivalent(self, tmp_path):
        def build_static():
            return [bad_conflict_node("a"), snode("b", deps=["a"]),
                    snode("c", deps=["b"]), snode("d"), snode("e", deps=["d"])]

        def build_work():
            return [bad_conflict_node("a"), wnode("b", deps=["a"]),
                    wnode("c", deps=["b"]), wnode("d"), wnode("e", deps=["d"])]

        seq_app = boot_app(tmp_path, "seq", "S-SEQ")
        prc_app = boot_app(tmp_path, "prc", "S-PRC")
        seq = GraphExecutor(ExecutionDAG(build_static()), seq_app,
                            schedule=Schedule.SEQUENTIAL).run()
        prc = GraphExecutor(ExecutionDAG(build_work()), prc_app,
                            schedule=Schedule.PROCESS_LEVELS).run()
        assert seq["failed"] == prc["failed"] == ["a"]
        assert seq["skipped"] == prc["skipped"] == ["b", "c"]
        assert seq["done"] == prc["done"] == ["d", "e"]
        assert graph_event_seq(seq_app) == graph_event_seq(prc_app)


# ===== Commit order is sorted id, NOT worker-completion order =====

class TestCommitOrder:
    def test_commit_in_sorted_id_not_completion_order(self, booted):
        # One wide level of three roots; the id-FIRST node sleeps the LONGEST,
        # so it finishes work LAST. Commit must still happen in sorted id order.
        dag = ExecutionDAG([
            wnode("a", work=work_sleep_identity, work_args=("a", 0.25)),
            wnode("b", work=work_sleep_identity, work_args=("b", 0.0)),
            wnode("c", work=work_sleep_identity, work_args=("c", 0.0)),
        ])
        report = GraphExecutor(
            dag, booted, schedule=Schedule.PROCESS_LEVELS
        ).run()
        assert report["executed_order"] == ["a", "b", "c"]
        assert report["levels_executed"] == [["a", "b", "c"]]
        assert graph_event_seq(booted) == [
            ("a", "DONE"), ("b", "DONE"), ("c", "DONE")
        ]


# ===== Single-writer enforcement (project file-mutex) [INS-033 intent 1] =====

class TestSingleWriter:
    def test_missing_lock_refuses_to_run(self, booted):
        booted.lock.release()  # drop the boot lock
        dag = ExecutionDAG([wnode("a")])
        with pytest.raises(GraphExecutionError, match="not held"):
            GraphExecutor(dag, booted, schedule=Schedule.PROCESS_LEVELS).run()

    def test_foreign_lock_refuses_to_run(self, booted, project_dir):
        import os
        (project_dir / LOCK_FILENAME).write_text(
            json.dumps({
                "session_id": "S-OTHER",
                "pid": os.getpid(),  # alive -> not reclaimable as stale
                "acquired_at": "2026-01-01T00:00:00Z",
            }),
            encoding="utf-8",
        )
        dag = ExecutionDAG([wnode("a")])
        with pytest.raises(GraphExecutionError, match="different session"):
            GraphExecutor(dag, booted, schedule=Schedule.PROCESS_LEVELS).run()


# ===== Worker failure routes to the deterministic fallback [INS-033 intent 2] =====

class TestWorkerFailure:
    def test_worker_exception_fails_node_and_skips_closure(self, booted):
        # level0: a(boom), d(ok) ; a -> b -> c
        dag = ExecutionDAG([
            wnode("a", work=work_boom),
            wnode("b", deps=["a"]),
            wnode("c", deps=["b"]),
            wnode("d"),
        ])
        ex = GraphExecutor(dag, booted, schedule=Schedule.PROCESS_LEVELS)
        report = ex.run()
        assert report["failed"] == ["a"]
        assert report["skipped"] == ["b", "c"]
        assert report["done"] == ["d"]
        assert report["complete"] is True
        ra = result_for(ex, "a")
        assert ra.status is NodeStatus.FAILED
        assert ra.committed is False
        assert any("worker error" in e and "exploded" in e for e in ra.errors)
        hot = booted.get_hot()
        assert hot.get("k_d") == "d"
        assert "k_a" not in hot and "k_b" not in hot and "k_c" not in hot

    def test_worker_nonmapping_payload_fails_node(self, booted):
        dag = ExecutionDAG([wnode("a", work=work_returns_list)])
        ex = GraphExecutor(dag, booted, schedule=Schedule.PROCESS_LEVELS)
        report = ex.run()
        assert report["failed"] == ["a"]
        ra = result_for(ex, "a")
        assert any("non-Mapping" in e for e in ra.errors)

    def test_kernel_rejection_under_process(self, booted):
        # A node with no work whose proposal the kernel rejects still fails the
        # same way (failure source is the kernel, not the worker).
        before = json.dumps(booted.get_hot(), sort_keys=True)
        dag = ExecutionDAG([bad_conflict_node("a")])
        report = GraphExecutor(
            dag, booted, schedule=Schedule.PROCESS_LEVELS
        ).run()
        assert report["failed"] == ["a"]
        assert json.dumps(booted.get_hot(), sort_keys=True) == before

    def test_rollback_on_failure_under_process(self, booted):
        # a commits at level0; b (depends a) fails at level1 -> whole run undone.
        dag = ExecutionDAG([
            wnode("a"),
            wnode("b", deps=["a"], work=work_boom),
        ])
        ex = GraphExecutor(
            dag, booted,
            schedule=Schedule.PROCESS_LEVELS,
            rollback_on_failure=True,
        )
        report = ex.run()
        assert ex.rolled_back is True
        assert report["rolled_back"] is True
        # The whole run is undone: node a's committed payload is gone from HOT
        # (meta legitimately advances via the GRAPH_ROLLBACK recovery write).
        assert "k_a" not in booted.get_hot()

    def test_stop_on_failure_under_process(self, booted):
        # a (id-first in level0) fails; stop before committing independent z.
        dag = ExecutionDAG([wnode("a", work=work_boom), wnode("z")])
        report = GraphExecutor(
            dag, booted,
            schedule=Schedule.PROCESS_LEVELS,
            stop_on_failure=True,
        ).run()
        assert report["failed"] == ["a"]
        assert report["executed_order"] == ["a"]
        assert "z" not in report["executed_order"]
        assert report["complete"] is False


# ===== Kernel-integration invariants =====

class TestKernelInvariants:
    def test_graph_events_schema_valid(self, booted):
        dag = ExecutionDAG([
            wnode("a"), wnode("b", work=work_boom), wnode("c", deps=["a"]),
        ])
        GraphExecutor(dag, booted, schedule=Schedule.PROCESS_LEVELS).run()
        evs = [
            e.to_dict() for e in booted.wal.replay()
            if e.to_dict()["event"] == GRAPH_NODE_EVENT
        ]
        assert evs
        for e in evs:
            ok, errors = validate_event(e)
            assert ok, errors

    def test_checkpoint_per_committed_node(self, booted):
        dag = ExecutionDAG(diamond_work())
        ex = GraphExecutor(dag, booted, schedule=Schedule.PROCESS_LEVELS)
        ex.run()
        for r in ex.results:
            assert r.committed is True
            assert r.checkpoint_seq is not None

    def test_state_returns_to_ready(self, booted):
        dag = ExecutionDAG(diamond_work())
        GraphExecutor(dag, booted, schedule=Schedule.PROCESS_LEVELS).run()
        assert booted.state_machine.current is State.READY

    def test_double_run_raises(self, booted):
        dag = ExecutionDAG([wnode("a")])
        ex = GraphExecutor(dag, booted, schedule=Schedule.PROCESS_LEVELS)
        ex.run()
        with pytest.raises(GraphExecutionError):
            ex.run()

    def test_report_schedule_value(self, booted):
        dag = ExecutionDAG([wnode("a")])
        report = GraphExecutor(
            dag, booted, schedule=Schedule.PROCESS_LEVELS
        ).run()
        assert report["schedule"] == "process_levels"

    def test_max_workers_param_respected(self, booted):
        dag = ExecutionDAG(diamond_work())
        report = GraphExecutor(
            dag, booted, schedule=Schedule.PROCESS_LEVELS, max_workers=1
        ).run()
        assert report["complete"] is True
