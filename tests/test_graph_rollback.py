"""Tests for GRAPH-ORCH increment 4 — transactional rollback/recovery.

The pure DAG core (increment 1) is covered by test_graph_orchestrator.py, the
sequential engine (increment 2) by test_graph_executor.py, and deterministic
levels (increment 3) by test_graph_levels.py. This file covers the opt-in
``rollback_on_failure`` mode: on a node failure the whole run is undone back to
the pre-run baseline through the kernel's RECOVERY path
(KernelApp.rollback_to_snapshot), so a DAG commits all-or-nothing.

Layers:
  * Default-off — without the flag, behaviour is exactly increments 2-3
    (committed prefix kept, no rollback, no GRAPH_ROLLBACK event).
  * Restore-to-baseline — committed nodes are undone; pre-existing HOT keys and
    on-disk HOT are restored; report/flags reflect the rollback.
  * RECOVERY path — a schema-valid GRAPH_ROLLBACK WAL event is written and the
    kernel ends in READY (passed through RECOVERY via the sanctioned bypass).
  * Both schedules — SEQUENTIAL and LEVELS both honour the mode.
  * No-failure — armed but all-success run never rolls back.
  * Determinism — identical runs roll back identically.
"""

import json

import pytest

from rag_kernel.api import KernelApp
from rag_kernel.graph_orchestrator import (
    ExecutionDAG,
    OrchestratorNode,
    NodeStatus,
    GraphExecutor,
    Schedule,
    GRAPH_NODE_EVENT,
)
from rag_kernel.schemas import validate_event
from rag_kernel.state_machine import State


# ===== Fixtures / helpers =====

def _make_project(tmp_path, name="RAG", session="S-RB"):
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
    app = KernelApp(project_dir, session_id="S-RB")
    app.boot()
    return app


def node(nid, deps=(), action="update_status", payload=None):
    return OrchestratorNode(
        id=nid,
        deps=frozenset(deps),
        action=action,
        payload=payload if payload is not None else {f"k_{nid}": nid},
    )


def bad_node(nid, deps=()):
    """Proposal the kernel REJECTS at validation -> node FAILS deterministically."""
    return OrchestratorNode(
        id=nid, deps=frozenset(deps), action="add_conflict", payload={}
    )


def rollback_events(app):
    return [
        e.to_dict() for e in app.wal.replay()
        if e.to_dict()["event"] == "GRAPH_ROLLBACK"
    ]


def chain_then_fail():
    # a -> b -> bad(fail) ; a and b commit, bad fails after them.
    return [
        node("a"),
        node("b", deps=["a"]),
        bad_node("bad", deps=["b"]),
    ]


# ===== Default-off: behaviour identical to increments 2-3 =====

class TestDefaultOff:
    def test_no_rollback_flag_keeps_committed_prefix(self, booted):
        dag = ExecutionDAG(chain_then_fail())
        report = GraphExecutor(dag, booted).run()  # rollback_on_failure default False
        assert report["rolled_back"] is False
        assert report["rollback"] is None
        # committed prefix survives, failed node skipped-closure semantics intact
        assert report["done"] == ["a", "b"]
        assert report["failed"] == ["bad"]
        hot = booted.get_hot()
        assert hot.get("k_a") == "a" and hot.get("k_b") == "b"
        assert rollback_events(booted) == []

    def test_report_shape_has_rollback_keys(self, booted):
        dag = ExecutionDAG([node("a")])
        report = GraphExecutor(dag, booted).run()
        assert report["rolled_back"] is False
        assert report["rollback"] is None


# ===== Restore-to-baseline =====

class TestRestoreBaseline:
    def test_committed_nodes_undone_on_failure(self, booted):
        dag = ExecutionDAG(chain_then_fail())
        ex = GraphExecutor(dag, booted, rollback_on_failure=True)
        report = ex.run()
        assert report["rolled_back"] is True
        assert ex.rolled_back is True
        # a and b committed then were rolled back: their keys are gone from HOT.
        hot = booted.get_hot()
        assert "k_a" not in hot and "k_b" not in hot and "k_bad" not in hot

    def test_rollback_info_names_trigger_node(self, booted):
        dag = ExecutionDAG(chain_then_fail())
        ex = GraphExecutor(dag, booted, rollback_on_failure=True)
        report = ex.run()
        assert report["rollback"]["trigger_node"] == "bad"
        assert report["rollback"]["rolled_back"] is True
        assert ex.rollback_info["trigger_node"] == "bad"

    def test_preexisting_hot_key_preserved(self, booted):
        # Seed a key into HOT before the run; rollback must keep it (it is part
        # of the baseline) while dropping the run's own commits.
        booted._hot["preexisting"] = "keep-me"
        dag = ExecutionDAG(chain_then_fail())
        GraphExecutor(dag, booted, rollback_on_failure=True).run()
        hot = booted.get_hot()
        assert hot.get("preexisting") == "keep-me"
        assert "k_a" not in hot

    def test_on_disk_hot_matches_restored_state(self, booted, project_dir):
        dag = ExecutionDAG(chain_then_fail())
        GraphExecutor(dag, booted, rollback_on_failure=True).run()
        on_disk = json.loads(
            (project_dir / "RAG_MASTER.json").read_text(encoding="utf-8")
        )
        assert "k_a" not in on_disk and "k_b" not in on_disk

    def test_rollback_halts_further_dispatch(self, booted):
        # a -> b -> bad(fail), plus an independent root 'z' (id-sorted after
        # 'bad'). Rollback fires when 'bad' fails and must stop the run before
        # 'z' is dispatched, leaving the DAG incomplete (z never reaches a
        # terminal status). This proves rollback halts the whole run, not just
        # the failed branch's closure.
        dag = ExecutionDAG(chain_then_fail() + [node("z")])
        report = GraphExecutor(dag, booted, rollback_on_failure=True).run()
        assert report["rolled_back"] is True
        assert "z" not in report["executed_order"]
        assert report["complete"] is False


# ===== RECOVERY path / WAL audit =====

class TestRecoveryPath:
    def test_graph_rollback_event_written_and_valid(self, booted):
        dag = ExecutionDAG(chain_then_fail())
        GraphExecutor(dag, booted, rollback_on_failure=True).run()
        evs = rollback_events(booted)
        assert len(evs) == 1
        ok, errors = validate_event(evs[0])
        assert ok, errors
        assert evs[0]["reason"]

    def test_kernel_ends_in_ready(self, booted):
        dag = ExecutionDAG(chain_then_fail())
        GraphExecutor(dag, booted, rollback_on_failure=True).run()
        assert booted.state_machine.current is State.READY

    def test_node_executed_events_still_present(self, booted):
        # The committed nodes' GRAPH_NODE_EXECUTED events remain in the WAL
        # (the audit trail is append-only); rollback adds its own event on top.
        dag = ExecutionDAG(chain_then_fail())
        GraphExecutor(dag, booted, rollback_on_failure=True).run()
        node_evs = [
            e.to_dict() for e in booted.wal.replay()
            if e.to_dict()["event"] == GRAPH_NODE_EVENT
        ]
        statuses = {e["node_id"]: e["status"] for e in node_evs}
        assert statuses["a"] == "DONE" and statuses["b"] == "DONE"
        assert statuses["bad"] == "FAILED"


# ===== Both schedules honour the mode =====

class TestBothSchedules:
    def test_levels_schedule_rolls_back(self, booted):
        dag = ExecutionDAG(chain_then_fail())
        report = GraphExecutor(
            dag, booted, schedule=Schedule.LEVELS, rollback_on_failure=True
        ).run()
        assert report["rolled_back"] is True
        assert "k_a" not in booted.get_hot()

    def test_levels_wide_failure_rolls_back_whole_run(self, tmp_path):
        # root commits, then a second-level node fails -> entire run undone.
        app = KernelApp(_make_project(tmp_path, name="W", session="S-W"),
                        session_id="S-W")
        app.boot()
        dag = ExecutionDAG([
            node("root"),
            node("ok", deps=["root"]),
            bad_node("bad", deps=["root"]),
        ])
        report = GraphExecutor(
            dag, app, schedule=Schedule.LEVELS, rollback_on_failure=True
        ).run()
        assert report["rolled_back"] is True
        hot = app.get_hot()
        assert "k_root" not in hot and "k_ok" not in hot


# ===== Armed but no failure =====

class TestNoFailure:
    def test_all_success_never_rolls_back(self, booted):
        dag = ExecutionDAG([node("a"), node("b", deps=["a"])])
        ex = GraphExecutor(dag, booted, rollback_on_failure=True)
        report = ex.run()
        assert report["rolled_back"] is False
        assert report["complete"] is True
        hot = booted.get_hot()
        assert hot.get("k_a") == "a" and hot.get("k_b") == "b"
        assert rollback_events(booted) == []


# ===== Determinism =====

class TestDeterminism:
    def test_identical_runs_roll_back_identically(self, tmp_path):
        outs = []
        for i in range(2):
            app = KernelApp(_make_project(tmp_path, name=f"d{i}", session=f"S{i}"),
                            session_id=f"S{i}")
            app.boot()
            report = GraphExecutor(
                ExecutionDAG(chain_then_fail()), app, rollback_on_failure=True
            ).run()
            outs.append((
                report["rolled_back"],
                report["rollback"]["trigger_node"],
                report["executed_order"],
                sorted(app.get_hot().keys()),
            ))
        assert outs[0] == outs[1]
