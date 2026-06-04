"""Tests for the GRAPH-ORCH increment-7 agent/session supervisor (INS-030).

The AgentSupervisor is a thin, observable spawn/monitor/collect layer over the
SAME pure off-process work contract Schedule.PROCESS_LEVELS uses. It owns no
authoritative state and holds no kernel handle. This file proves:

  * standalone supervisor behaviour — payloads collected, live observations
    (PID / state / exit code / duration), AgentView snapshot + render,
    bounded max_workers, deterministic result keying regardless of finish
    order, and the progress callback,
  * worker failure surfaces as a WorkerResult error (exception / death), while
    a non-Mapping payload is returned verbatim (Mapping validation is the
    caller's job),
  * GraphExecutor(..., supervisor=...) under PROCESS_LEVELS is EQUIVALENT to the
    bare-pool PROCESS_LEVELS and to LEVELS/SEQUENTIAL (executed order, final HOT,
    WAL graph-event sequence) — the supervisor changes only HOW the work phase
    runs and is observed, never the committed result,
  * worker failure / non-Mapping / rollback / stop_on_failure / single-writer
    all behave identically on the supervised path,
  * the supervisor never writes authoritative state (it has no kernel handle).

Worker functions are module-level so they pickle to subprocesses under the
default start method (fork on Linux/WSL — the sanctioned test runner).
"""

import json
import os
import time

import pytest

from rag_kernel.api import KernelApp
from rag_kernel.concurrency import LOCK_FILENAME
from rag_kernel.agent_supervisor import (
    AgentSupervisor,
    AgentView,
    WorkerObservation,
    WorkerResult,
    WorkerState,
    is_mapping,
)
from rag_kernel.graph_orchestrator import (
    ExecutionDAG,
    OrchestratorNode,
    NodeStatus,
    GraphExecutor,
    GraphExecutionError,
    Schedule,
    GRAPH_NODE_EVENT,
)


# ===== Module-level (picklable) worker functions =====

def work_identity(nid):
    return {f"k_{nid}": nid}


def work_value(nid, value):
    return {f"k_{nid}": value}


def work_sleep_identity(nid, delay):
    time.sleep(delay)
    return {f"k_{nid}": nid}


def work_pid(nid):
    """Return the worker's own PID so the test can confirm it ran off-process."""
    return {f"k_{nid}": nid, "_pid": os.getpid()}


def work_boom(nid):
    raise RuntimeError(f"worker {nid} exploded")


def work_returns_list(nid):
    return ["not", "a", "mapping"]


# ===== Fixtures / helpers (mirror test_graph_process.py) =====

def _make_project(tmp_path, name="RAG", session="S-SUP"):
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
        json.dumps({"meta": {"type": "RAG_COLD"}}), encoding="utf-8",
    )
    return d


@pytest.fixture
def project_dir(tmp_path):
    return _make_project(tmp_path)


@pytest.fixture
def booted(project_dir):
    app = KernelApp(project_dir, session_id="S-SUP")
    app.boot()
    return app


def boot_app(tmp_path, name, session):
    app = KernelApp(_make_project(tmp_path, name=name, session=session),
                    session_id=session)
    app.boot()
    return app


def snode(nid, deps=(), action="update_status", payload=None):
    return OrchestratorNode(
        id=nid, deps=frozenset(deps), action=action,
        payload=payload if payload is not None else {f"k_{nid}": nid},
    )


def wnode(nid, deps=(), action="update_status", work=work_identity, work_args=None):
    return OrchestratorNode(
        id=nid, deps=frozenset(deps), action=action,
        work=work, work_args=(nid,) if work_args is None else work_args,
    )


def bad_conflict_node(nid, deps=()):
    return OrchestratorNode(
        id=nid, deps=frozenset(deps), action="add_conflict", payload={}
    )


def graph_event_seq(app):
    return [
        (e.to_dict()["node_id"], e.to_dict()["status"])
        for e in app.wal.replay()
        if e.to_dict()["event"] == GRAPH_NODE_EVENT
    ]


def diamond_work():
    return [
        wnode("a"), wnode("b", deps=["a"]),
        wnode("c", deps=["a"]), wnode("d", deps=["b", "c"]),
    ]


def result_for(ex, node_id):
    return next(r for r in ex.results if r.node_id == node_id)


def tasks_from(*nids):
    return [(nid, work_identity, (nid,)) for nid in nids]


# ===== Standalone supervisor =====

class TestSupervisorBasics:
    def test_collects_payloads_keyed_by_node_id(self):
        results = AgentSupervisor().run_batch(tasks_from("a", "b", "c"))
        assert set(results) == {"a", "b", "c"}
        for nid in ("a", "b", "c"):
            assert results[nid].ok is True
            assert results[nid].payload == {f"k_{nid}": nid}

    def test_empty_batch_returns_empty(self):
        assert AgentSupervisor().run_batch([]) == {}

    def test_observation_records_pid_state_duration(self):
        res = AgentSupervisor().run_batch(tasks_from("a"))["a"]
        obs = res.observation
        assert isinstance(obs, WorkerObservation)
        assert obs.state is WorkerState.DONE
        assert isinstance(obs.pid, int) and obs.pid > 0
        assert obs.exitcode == 0
        assert obs.duration_s is not None and obs.duration_s >= 0.0

    def test_worker_actually_runs_off_process(self):
        # The worker reports its own PID; it must differ from the parent's.
        res = AgentSupervisor().run_batch([("a", work_pid, ("a",))])["a"]
        assert res.ok is True
        assert res.payload["_pid"] != os.getpid()

    def test_results_keyed_regardless_of_finish_order(self):
        # id-first 'a' sleeps longest -> finishes last; keying is by id, not time.
        tasks = [
            ("a", work_sleep_identity, ("a", 0.20)),
            ("b", work_sleep_identity, ("b", 0.0)),
            ("c", work_sleep_identity, ("c", 0.0)),
        ]
        results = AgentSupervisor().run_batch(tasks)
        assert set(results) == {"a", "b", "c"}
        assert all(results[n].ok for n in ("a", "b", "c"))

    def test_max_workers_bounds_concurrency(self):
        # 4 tasks, 1 slot -> still all complete (run in waves).
        results = AgentSupervisor(max_workers=1).run_batch(
            tasks_from("a", "b", "c", "d")
        )
        assert set(results) == {"a", "b", "c", "d"}
        assert all(results[n].ok for n in results)

    def test_invalid_max_workers_rejected(self):
        with pytest.raises(ValueError, match="max_workers"):
            AgentSupervisor(max_workers=0)
        with pytest.raises(ValueError, match="max_workers"):
            AgentSupervisor(max_workers=-3)

    def test_invalid_poll_interval_rejected(self):
        with pytest.raises(ValueError, match="poll_interval"):
            AgentSupervisor(poll_interval=0)

    def test_progress_callback_receives_agent_views(self):
        seen = []
        AgentSupervisor().run_batch(
            tasks_from("a", "b"), progress=lambda view: seen.append(view)
        )
        assert seen, "progress callback was never called"
        assert all(isinstance(v, AgentView) for v in seen)
        # The final view shows both workers terminal (DONE).
        assert seen[-1].counts()["DONE"] == 2


class TestSupervisorFailures:
    def test_worker_exception_is_reported_not_raised(self):
        res = AgentSupervisor().run_batch([("a", work_boom, ("a",))])["a"]
        assert res.ok is False
        assert res.payload is None
        assert "RuntimeError" in res.error and "exploded" in res.error
        assert res.observation.state is WorkerState.FAILED

    def test_nonmapping_payload_returned_verbatim(self):
        # The supervisor does not enforce the Mapping contract — that's the
        # caller's job; it returns whatever the worker produced.
        res = AgentSupervisor().run_batch([("a", work_returns_list, ("a",))])["a"]
        assert res.ok is True
        assert res.payload == ["not", "a", "mapping"]
        assert is_mapping(res.payload) is False

    def test_mixed_success_and_failure(self):
        results = AgentSupervisor().run_batch([
            ("a", work_identity, ("a",)),
            ("b", work_boom, ("b",)),
        ])
        assert results["a"].ok is True
        assert results["b"].ok is False


class TestAgentView:
    def test_snapshot_and_counts(self):
        sup = AgentSupervisor()
        sup.run_batch(tasks_from("a", "b"))
        view = sup.snapshot()
        assert isinstance(view, AgentView)
        assert view.counts()["DONE"] == 2
        assert [o.node_id for o in view.observations] == ["a", "b"]

    def test_render_is_text_table(self):
        sup = AgentSupervisor()
        sup.run_batch(tasks_from("a"))
        rendered = sup.snapshot().render()
        assert "AGENT VIEW" in rendered
        assert "NODE" in rendered and "PID" in rendered and "STATE" in rendered
        assert "a" in rendered

    def test_view_to_dict_roundtrip(self):
        sup = AgentSupervisor()
        sup.run_batch(tasks_from("a"))
        d = sup.snapshot().to_dict()
        assert d["counts"]["DONE"] == 1
        assert d["workers"][0]["node_id"] == "a"
        assert d["workers"][0]["state"] == "DONE"


# ===== GraphExecutor integration (the increment-7 wiring) =====

class TestExecutorIntegration:
    def test_supervised_process_levels_commits_payloads(self, booted):
        dag = ExecutionDAG(diamond_work())
        ex = GraphExecutor(
            dag, booted, schedule=Schedule.PROCESS_LEVELS,
            supervisor=AgentSupervisor(),
        )
        report = ex.run()
        assert report["complete"] is True
        assert report["executed_order"] == ["a", "b", "c", "d"]
        assert report["levels_executed"] == [["a"], ["b", "c"], ["d"]]
        hot = booted.get_hot()
        for nid in ("a", "b", "c", "d"):
            assert hot.get(f"k_{nid}") == nid

    def test_agent_view_property_exposed(self, booted):
        dag = ExecutionDAG(diamond_work())
        ex = GraphExecutor(
            dag, booted, schedule=Schedule.PROCESS_LEVELS,
            supervisor=AgentSupervisor(),
        )
        ex.run()
        view = ex.agent_view
        assert isinstance(view, AgentView)
        # 'd' has no parallel peers but still ran off-process; all observed DONE.
        assert view.counts()["DONE"] >= 1

    def test_agent_view_none_without_supervisor(self, booted):
        dag = ExecutionDAG([wnode("a")])
        ex = GraphExecutor(dag, booted, schedule=Schedule.PROCESS_LEVELS)
        ex.run()
        assert ex.agent_view is None

    def test_worker_payload_overrides_static(self, booted):
        dag = ExecutionDAG([wnode("a", work=work_value, work_args=("a", "CUSTOM"))])
        GraphExecutor(
            dag, booted, schedule=Schedule.PROCESS_LEVELS,
            supervisor=AgentSupervisor(),
        ).run()
        assert booted.get_hot().get("k_a") == "CUSTOM"

    def test_commit_in_sorted_id_not_completion_order(self, booted):
        dag = ExecutionDAG([
            wnode("a", work=work_sleep_identity, work_args=("a", 0.25)),
            wnode("b", work=work_sleep_identity, work_args=("b", 0.0)),
            wnode("c", work=work_sleep_identity, work_args=("c", 0.0)),
        ])
        report = GraphExecutor(
            dag, booted, schedule=Schedule.PROCESS_LEVELS,
            supervisor=AgentSupervisor(),
        ).run()
        assert report["executed_order"] == ["a", "b", "c"]
        assert graph_event_seq(booted) == [
            ("a", "DONE"), ("b", "DONE"), ("c", "DONE")
        ]


class TestSupervisedEquivalence:
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
    ])
    def test_supervised_equivalent_to_levels_and_pool(
        self, tmp_path, static_fn, work_fn
    ):
        lvl_app = boot_app(tmp_path, "lvl", "S-LVL")
        pool_app = boot_app(tmp_path, "pool", "S-POOL")
        sup_app = boot_app(tmp_path, "sup", "S-SUP")

        lvl = GraphExecutor(ExecutionDAG(static_fn()), lvl_app,
                            schedule=Schedule.LEVELS).run()
        pool = GraphExecutor(ExecutionDAG(work_fn()), pool_app,
                             schedule=Schedule.PROCESS_LEVELS).run()
        sup = GraphExecutor(ExecutionDAG(work_fn()), sup_app,
                            schedule=Schedule.PROCESS_LEVELS,
                            supervisor=AgentSupervisor()).run()

        assert lvl["executed_order"] == pool["executed_order"] == sup["executed_order"]
        assert lvl["counts"] == pool["counts"] == sup["counts"]
        # Identical WAL graph-event sequence => state evolved identically.
        assert graph_event_seq(pool_app) == graph_event_seq(sup_app)
        assert graph_event_seq(lvl_app) == graph_event_seq(sup_app)
        # Identical committed payloads landed in HOT.
        pool_hot, sup_hot = pool_app.get_hot(), sup_app.get_hot()
        for nid in ExecutionDAG(work_fn()).node_ids:
            assert pool_hot.get(f"k_{nid}") == sup_hot.get(f"k_{nid}") == nid

    def test_failure_closure_equivalent(self, tmp_path):
        def build_work():
            return [bad_conflict_node("a"), wnode("b", deps=["a"]),
                    wnode("c", deps=["b"]), wnode("d"), wnode("e", deps=["d"])]

        pool_app = boot_app(tmp_path, "pool", "S-POOL")
        sup_app = boot_app(tmp_path, "sup", "S-SUP")
        pool = GraphExecutor(ExecutionDAG(build_work()), pool_app,
                             schedule=Schedule.PROCESS_LEVELS).run()
        sup = GraphExecutor(ExecutionDAG(build_work()), sup_app,
                            schedule=Schedule.PROCESS_LEVELS,
                            supervisor=AgentSupervisor()).run()
        assert pool["failed"] == sup["failed"] == ["a"]
        assert pool["skipped"] == sup["skipped"] == ["b", "c"]
        assert pool["done"] == sup["done"] == ["d", "e"]
        assert graph_event_seq(pool_app) == graph_event_seq(sup_app)


class TestSupervisedFailureModes:
    def test_worker_exception_fails_node_and_skips_closure(self, booted):
        dag = ExecutionDAG([
            wnode("a", work=work_boom), wnode("b", deps=["a"]),
            wnode("c", deps=["b"]), wnode("d"),
        ])
        ex = GraphExecutor(
            dag, booted, schedule=Schedule.PROCESS_LEVELS,
            supervisor=AgentSupervisor(),
        )
        report = ex.run()
        assert report["failed"] == ["a"]
        assert report["skipped"] == ["b", "c"]
        assert report["done"] == ["d"]
        ra = result_for(ex, "a")
        assert ra.status is NodeStatus.FAILED
        assert ra.committed is False
        assert any("exploded" in e for e in ra.errors)
        hot = booted.get_hot()
        assert hot.get("k_d") == "d"
        assert "k_a" not in hot and "k_b" not in hot and "k_c" not in hot

    def test_nonmapping_payload_fails_node(self, booted):
        dag = ExecutionDAG([wnode("a", work=work_returns_list)])
        ex = GraphExecutor(
            dag, booted, schedule=Schedule.PROCESS_LEVELS,
            supervisor=AgentSupervisor(),
        )
        report = ex.run()
        assert report["failed"] == ["a"]
        assert any("non-Mapping" in e for e in result_for(ex, "a").errors)

    def test_rollback_on_failure_under_supervisor(self, booted):
        dag = ExecutionDAG([
            wnode("a"), wnode("b", deps=["a"], work=work_boom),
        ])
        ex = GraphExecutor(
            dag, booted, schedule=Schedule.PROCESS_LEVELS,
            supervisor=AgentSupervisor(), rollback_on_failure=True,
        )
        report = ex.run()
        assert ex.rolled_back is True
        assert report["rolled_back"] is True
        assert "k_a" not in booted.get_hot()

    def test_stop_on_failure_under_supervisor(self, booted):
        dag = ExecutionDAG([wnode("a", work=work_boom), wnode("z")])
        report = GraphExecutor(
            dag, booted, schedule=Schedule.PROCESS_LEVELS,
            supervisor=AgentSupervisor(), stop_on_failure=True,
        ).run()
        assert report["failed"] == ["a"]
        assert report["executed_order"] == ["a"]
        assert report["complete"] is False


class TestSupervisedSingleWriter:
    def test_missing_lock_refuses_to_run(self, booted):
        booted.lock.release()
        dag = ExecutionDAG([wnode("a")])
        with pytest.raises(GraphExecutionError, match="not held"):
            GraphExecutor(
                dag, booted, schedule=Schedule.PROCESS_LEVELS,
                supervisor=AgentSupervisor(),
            ).run()

    def test_foreign_lock_refuses_to_run(self, booted, project_dir):
        (project_dir / LOCK_FILENAME).write_text(
            json.dumps({
                "session_id": "S-OTHER",
                "pid": os.getpid(),
                "acquired_at": "2026-01-01T00:00:00Z",
            }),
            encoding="utf-8",
        )
        dag = ExecutionDAG([wnode("a")])
        with pytest.raises(GraphExecutionError, match="different session"):
            GraphExecutor(
                dag, booted, schedule=Schedule.PROCESS_LEVELS,
                supervisor=AgentSupervisor(),
            ).run()
