"""Unit tests for rag_kernel.graph_orchestrator (GRAPH-ORCH increment 1).

Covers the pure DAG core: fail-loud construction, deterministic topological
order + level assignment, the guarded node-status lifecycle, scheduling
queries, and deterministic failure propagation. Execution / kernel wiring is a
later increment and is intentionally out of scope here.
"""

from __future__ import annotations

import pytest

from rag_kernel.graph_orchestrator import (
    DAGBuildError,
    ExecutionDAG,
    NodeStateError,
    NodeStatus,
    OrchestratorNode,
    TERMINAL_STATUSES,
    status_transition_allowed,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _n(node_id, *deps, action=None, payload=None):
    return OrchestratorNode(
        id=node_id,
        deps=frozenset(deps),
        action=action,
        payload=payload or {},
    )


def _linear_chain(n=4):
    """a -> b -> c -> d (each depends on the previous)."""
    nodes = [_n("n0")]
    for i in range(1, n):
        nodes.append(_n(f"n{i}", f"n{i-1}"))
    return ExecutionDAG(nodes)


def _diamond():
    """a -> {b, c} -> d."""
    return ExecutionDAG([
        _n("a"),
        _n("b", "a"),
        _n("c", "a"),
        _n("d", "b", "c"),
    ])


def _run_to_done(dag, node_id):
    dag.mark_running(node_id)
    dag.mark_done(node_id)


# ---------------------------------------------------------------------------
# OrchestratorNode validation
# ---------------------------------------------------------------------------


class TestNode:
    def test_basic_node(self):
        node = _n("x", "y", "z")
        assert node.id == "x"
        assert node.deps == frozenset({"y", "z"})

    def test_deps_normalized_to_frozenset(self):
        node = OrchestratorNode(id="x", deps=["a", "b", "a"])
        assert node.deps == frozenset({"a", "b"})

    def test_empty_id_rejected(self):
        with pytest.raises(DAGBuildError):
            OrchestratorNode(id="")

    def test_non_string_id_rejected(self):
        with pytest.raises(DAGBuildError):
            OrchestratorNode(id=123)  # type: ignore[arg-type]

    def test_self_dependency_rejected(self):
        with pytest.raises(DAGBuildError):
            _n("x", "x")

    def test_invalid_dep_id_rejected(self):
        with pytest.raises(DAGBuildError):
            OrchestratorNode(id="x", deps=["", "y"])

    def test_node_is_hashable_and_frozen(self):
        node = _n("x")
        {node}  # hashable
        with pytest.raises(Exception):
            node.id = "y"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Construction / fail-loud validation
# ---------------------------------------------------------------------------


class TestConstruction:
    def test_empty_dag(self):
        dag = ExecutionDAG([])
        assert len(dag) == 0
        assert dag.topological_order() == []
        assert dag.levels() == []
        assert dag.is_complete() is True  # vacuously
        assert dag.next_ready() is None

    def test_single_node(self):
        dag = ExecutionDAG([_n("solo")])
        assert len(dag) == 1
        assert dag.topological_order() == ["solo"]
        assert dag.status_of("solo") is NodeStatus.READY

    def test_duplicate_id_rejected(self):
        with pytest.raises(DAGBuildError, match="duplicate"):
            ExecutionDAG([_n("x"), _n("x")])

    def test_dangling_dependency_rejected(self):
        with pytest.raises(DAGBuildError, match="unknown node"):
            ExecutionDAG([_n("x", "ghost")])

    def test_simple_cycle_rejected(self):
        with pytest.raises(DAGBuildError, match="cycle"):
            ExecutionDAG([_n("a", "b"), _n("b", "a")])

    def test_self_cycle_rejected_at_node_level(self):
        # self-dependency is caught even earlier, at node construction
        with pytest.raises(DAGBuildError):
            ExecutionDAG([_n("a", "a")])

    def test_longer_cycle_rejected(self):
        with pytest.raises(DAGBuildError, match="cycle"):
            ExecutionDAG([_n("a", "c"), _n("b", "a"), _n("c", "b")])

    def test_contains_and_node_lookup(self):
        dag = _diamond()
        assert "a" in dag
        assert "zzz" not in dag
        assert dag.node("b").deps == frozenset({"a"})


# ---------------------------------------------------------------------------
# Topological order + levels (determinism)
# ---------------------------------------------------------------------------


class TestTopology:
    def test_linear_order(self):
        dag = _linear_chain(4)
        assert dag.topological_order() == ["n0", "n1", "n2", "n3"]
        assert dag.levels() == [["n0"], ["n1"], ["n2"], ["n3"]]
        assert dag.depth == 4

    def test_diamond_levels(self):
        dag = _diamond()
        assert dag.levels() == [["a"], ["b", "c"], ["d"]]
        assert dag.depth == 3
        # b and c are parallel-eligible (same level), order is deterministic
        assert dag.levels()[1] == ["b", "c"]

    def test_topo_order_respects_dependencies(self):
        dag = _diamond()
        order = dag.topological_order()
        assert order.index("a") < order.index("b")
        assert order.index("a") < order.index("c")
        assert order.index("b") < order.index("d")
        assert order.index("c") < order.index("d")

    def test_determinism_across_input_orderings(self):
        forward = ExecutionDAG([
            _n("a"), _n("b", "a"), _n("c", "a"), _n("d", "b", "c"),
        ])
        shuffled = ExecutionDAG([
            _n("d", "b", "c"), _n("c", "a"), _n("b", "a"), _n("a"),
        ])
        assert forward.topological_order() == shuffled.topological_order()
        assert forward.levels() == shuffled.levels()

    def test_independent_roots_same_level(self):
        dag = ExecutionDAG([_n("z"), _n("a"), _n("m")])
        assert dag.levels() == [["a", "m", "z"]]  # sorted within level

    def test_dependents_and_descendants(self):
        dag = _diamond()
        assert dag.dependents_of("a") == frozenset({"b", "c"})
        assert dag.descendants_of("a") == frozenset({"b", "c", "d"})
        assert dag.descendants_of("d") == frozenset()


# ---------------------------------------------------------------------------
# Node-status lifecycle (guarded)
# ---------------------------------------------------------------------------


class TestStatusLifecycle:
    def test_status_transition_table_predicate(self):
        assert status_transition_allowed(NodeStatus.PENDING, NodeStatus.READY)
        assert status_transition_allowed(NodeStatus.READY, NodeStatus.RUNNING)
        assert status_transition_allowed(NodeStatus.RUNNING, NodeStatus.DONE)
        assert status_transition_allowed(NodeStatus.RUNNING, NodeStatus.FAILED)
        assert not status_transition_allowed(NodeStatus.PENDING, NodeStatus.DONE)
        assert not status_transition_allowed(NodeStatus.DONE, NodeStatus.RUNNING)

    def test_terminal_statuses(self):
        assert TERMINAL_STATUSES == frozenset(
            {NodeStatus.DONE, NodeStatus.FAILED, NodeStatus.SKIPPED}
        )

    def test_happy_path_single(self):
        dag = ExecutionDAG([_n("x")])
        assert dag.status_of("x") is NodeStatus.READY
        dag.mark_running("x")
        assert dag.status_of("x") is NodeStatus.RUNNING
        dag.mark_done("x")
        assert dag.status_of("x") is NodeStatus.DONE
        assert dag.is_complete()

    def test_cannot_run_pending_node(self):
        dag = _linear_chain(2)  # n1 is PENDING (depends on n0)
        assert dag.status_of("n1") is NodeStatus.PENDING
        with pytest.raises(NodeStateError):
            dag.mark_running("n1")

    def test_cannot_done_without_running(self):
        dag = ExecutionDAG([_n("x")])  # READY, not RUNNING
        with pytest.raises(NodeStateError):
            dag.mark_done("x")

    def test_cannot_rerun_done_node(self):
        dag = ExecutionDAG([_n("x")])
        _run_to_done(dag, "x")
        with pytest.raises(NodeStateError):
            dag.mark_running("x")

    def test_unknown_node_raises(self):
        dag = ExecutionDAG([_n("x")])
        with pytest.raises(KeyError):
            dag.mark_running("nope")

    def test_completion_promotes_dependents(self):
        dag = _linear_chain(3)
        assert dag.ready_nodes() == ["n0"]
        _run_to_done(dag, "n0")
        assert dag.ready_nodes() == ["n1"]  # n1 promoted PENDING -> READY
        _run_to_done(dag, "n1")
        assert dag.ready_nodes() == ["n2"]

    def test_diamond_join_waits_for_both_parents(self):
        dag = _diamond()
        _run_to_done(dag, "a")
        assert sorted(dag.ready_nodes()) == ["b", "c"]
        _run_to_done(dag, "b")
        # d still blocked: c not done yet
        assert dag.status_of("d") is NodeStatus.PENDING
        assert dag.ready_nodes() == ["c"]
        _run_to_done(dag, "c")
        assert dag.ready_nodes() == ["d"]


# ---------------------------------------------------------------------------
# Scheduling queries
# ---------------------------------------------------------------------------


class TestScheduling:
    def test_ready_nodes_sorted(self):
        dag = ExecutionDAG([_n("c"), _n("a"), _n("b")])
        assert dag.ready_nodes() == ["a", "b", "c"]

    def test_next_ready_is_lowest_id(self):
        dag = ExecutionDAG([_n("c"), _n("a"), _n("b")])
        assert dag.next_ready() == "a"

    def test_next_ready_none_when_blocked(self):
        dag = _linear_chain(2)
        dag.mark_running("n0")  # now nothing READY (n0 RUNNING, n1 PENDING)
        assert dag.next_ready() is None

    def test_counts(self):
        dag = _diamond()
        counts = dag.counts()
        assert counts["READY"] == 1   # only a
        assert counts["PENDING"] == 3
        assert counts["DONE"] == 0
        _run_to_done(dag, "a")
        counts = dag.counts()
        assert counts["DONE"] == 1
        assert counts["READY"] == 2  # b, c

    def test_is_complete_progression(self):
        dag = _linear_chain(2)
        assert not dag.is_complete()
        _run_to_done(dag, "n0")
        _run_to_done(dag, "n1")
        assert dag.is_complete()


# ---------------------------------------------------------------------------
# Failure propagation
# ---------------------------------------------------------------------------


class TestFailurePropagation:
    def test_failure_skips_descendants(self):
        dag = _diamond()
        dag.mark_running("a")
        skipped = dag.mark_failed("a")
        assert skipped == frozenset({"b", "c", "d"})
        assert dag.status_of("a") is NodeStatus.FAILED
        for nid in ("b", "c", "d"):
            assert dag.status_of(nid) is NodeStatus.SKIPPED
        assert dag.is_complete()  # all terminal

    def test_partial_failure_only_affects_downstream(self):
        # a -> b -> d ; a -> c (c independent of b)
        dag = ExecutionDAG([
            _n("a"),
            _n("b", "a"),
            _n("c", "a"),
            _n("d", "b"),
        ])
        _run_to_done(dag, "a")
        dag.mark_running("b")
        skipped = dag.mark_failed("b")
        assert skipped == frozenset({"d"})       # only b's downstream
        assert dag.status_of("c") is NodeStatus.READY  # c untouched
        assert dag.status_of("d") is NodeStatus.SKIPPED

    def test_cannot_fail_a_ready_node(self):
        dag = ExecutionDAG([_n("x")])  # READY, not RUNNING
        with pytest.raises(NodeStateError):
            dag.mark_failed("x")

    def test_failure_does_not_skip_already_done(self):
        # a -> b ; b already DONE before an unrelated failure path
        dag = ExecutionDAG([
            _n("a"),
            _n("b", "a"),
            _n("c", "a"),
        ])
        _run_to_done(dag, "a")
        _run_to_done(dag, "b")  # b DONE
        dag.mark_running("c")
        skipped = dag.mark_failed("c")
        # b is a sibling, not a descendant of c, and stays DONE regardless
        assert skipped == frozenset()
        assert dag.status_of("b") is NodeStatus.DONE


# ---------------------------------------------------------------------------
# Determinism / reproducibility end-to-end
# ---------------------------------------------------------------------------


class TestDeterminism:
    def test_repeated_builds_identical(self):
        specs = [
            _n("d", "b", "c"), _n("b", "a"), _n("c", "a"), _n("a"),
            _n("e", "d"),
        ]
        runs = [ExecutionDAG(list(specs)) for _ in range(5)]
        orders = {tuple(r.topological_order()) for r in runs}
        levels = {tuple(tuple(l) for l in r.levels()) for r in runs}
        assert len(orders) == 1
        assert len(levels) == 1

    def test_repr(self):
        dag = _diamond()
        assert "ExecutionDAG(nodes=4" in repr(dag)
