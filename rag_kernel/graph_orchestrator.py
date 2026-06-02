"""Graph Orchestrator — deterministic DAG core for the RAG Runtime Kernel.

GRAPH-ORCH (v4.0), increment 1 of N: the *pure* directed-acyclic-graph core.
This module models a workflow as a DAG of nodes with explicit dependency
edges and provides deterministic, crash-safe-friendly primitives for ordering
and scheduling that execution:

  * fail-loud construction (unique ids, every dependency resolvable, NO cycles),
  * a deterministic topological order,
  * topological *level* assignment — the set of nodes eligible to run in
    parallel at each depth (the "deterministic levels" scheduling model),
  * a guarded per-node status lifecycle
    (PENDING -> READY -> RUNNING -> DONE | FAILED, plus SKIPPED),
  * pure ready/next-ready queries and deterministic failure propagation.

SCOPE BOUNDARY (deliberate, mirrors FV-PHASE3):
    This is the PURE core only. It does NOT execute nodes, spawn threads, write
    the WAL, or touch KernelApp / the state machine. Execution + checkpoint-per-
    node (through the guarded CHECKPOINTING transition and CONTEXT-style WAL
    events) and rollback are later increments. Accordingly this module is NOT
    yet registered in rag_kernel.__init__._KERNEL_MODULES / discover() /
    cmd_health — wiring lands with the execution engine. The @rag-kernel-manifest
    block below is present and discovery-ready for that step.

DESIGN POSTURE (dual-POV):
    CS lens — a DAG is an adjacency list; ordering is a topological sort; cycle
    detection is Kahn's algorithm (a stalled queue == a cycle). Construction is
    total and fail-loud: an invalid graph can never be partially built. Node
    status transitions are themselves a small guarded state machine, so an
    illegal lifecycle move (e.g. DONE without RUNNING) is rejected, not silently
    tolerated — the same discipline state_machine.py applies to sessions.
    ML lens — same-level nodes are "parallel-eligible", but this module only
    decides *scheduling eligibility*; the eventual execution engine will commit
    each node's result through the serialized propose -> validate -> commit
    pipeline so state mutations stay deterministic and WAL-recoverable even when
    execution is concurrent. Concurrency is a scheduling property here, never a
    state-mutation race. LLM proposes (which nodes/work), system decides
    (legal order + legal status moves), state persists (later increments).

Spec reference: ROADMAP.md — v4.0 Graph Orchestrator
Design doc reference: v3.2_ARCHITECTURE_DESIGN.md (orchestration section, TBD)

@rag-kernel-manifest
{
  "module": "rag_kernel.graph_orchestrator",
  "capability": "graph_orchestration",
  "description": "Deterministic DAG core: fail-loud build, topological order + level scheduling, guarded node-status lifecycle",
  "states": ["PENDING", "READY", "RUNNING", "DONE", "FAILED", "SKIPPED"],
  "exports": ["NodeStatus", "OrchestratorNode", "ExecutionDAG", "DAGBuildError", "NodeStateError"],
  "use_when": "Modeling, ordering, or scheduling a dependency graph of work units",
  "never_bypass": false
}
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, Mapping, Optional


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class DAGBuildError(ValueError):
    """Raised at construction when the node set cannot form a valid DAG.

    Causes: duplicate node ids, a dependency referencing an unknown node, or a
    cycle. Fail-loud — an invalid graph is never partially constructed.
    """


class NodeStateError(RuntimeError):
    """Raised when an illegal node-status transition is attempted.

    The node-status lifecycle is a guarded state machine; this is its
    equivalent of state_machine.TransitionError.
    """


# ---------------------------------------------------------------------------
# Node status — a small guarded lifecycle state machine
# ---------------------------------------------------------------------------


class NodeStatus(Enum):
    """Lifecycle status of a single orchestrator node.

    PENDING  — created; one or more dependencies not yet DONE.
    READY    — every dependency is DONE; eligible to run.
    RUNNING  — execution in progress (set by the future execution engine).
    DONE     — completed successfully.
    FAILED   — execution failed; descendants become SKIPPED.
    SKIPPED  — an upstream dependency FAILED (or was SKIPPED); cannot run.
    """

    PENDING = "PENDING"
    READY = "READY"
    RUNNING = "RUNNING"
    DONE = "DONE"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


# Legal node-status transitions. Mirrors state_machine.TRANSITIONS in shape:
# an adjacency list over the status space. DONE / FAILED / SKIPPED are terminal.
#
# CS lens: total + explicit. Every status has an entry (terminals map to the
# empty set) so a typo or a new status without a rule fails the import-time
# validator below rather than silently permitting an illegal move.
_STATUS_TRANSITIONS: dict[NodeStatus, frozenset[NodeStatus]] = {
    NodeStatus.PENDING: frozenset({NodeStatus.READY, NodeStatus.SKIPPED}),
    NodeStatus.READY: frozenset({NodeStatus.RUNNING, NodeStatus.SKIPPED}),
    NodeStatus.RUNNING: frozenset({NodeStatus.DONE, NodeStatus.FAILED}),
    NodeStatus.DONE: frozenset(),
    NodeStatus.FAILED: frozenset(),
    NodeStatus.SKIPPED: frozenset(),
}

#: Statuses with no outgoing transitions.
TERMINAL_STATUSES: frozenset[NodeStatus] = frozenset(
    s for s, t in _STATUS_TRANSITIONS.items() if not t
)


def _validate_status_table() -> None:
    """Assert the status-transition table covers the whole status space."""
    all_statuses = set(NodeStatus)
    missing = all_statuses - set(_STATUS_TRANSITIONS)
    if missing:  # pragma: no cover - guards future edits
        raise RuntimeError(
            f"_STATUS_TRANSITIONS missing statuses: {[s.value for s in missing]}"
        )
    for src, targets in _STATUS_TRANSITIONS.items():
        invalid = targets - all_statuses
        if invalid:  # pragma: no cover - guards future edits
            raise RuntimeError(
                f"status {src.value} references invalid targets: "
                f"{[s.value for s in invalid]}"
            )


_validate_status_table()


def status_transition_allowed(src: NodeStatus, dst: NodeStatus) -> bool:
    """Pure predicate: is the status move src -> dst legal?"""
    return dst in _STATUS_TRANSITIONS[src]


# ---------------------------------------------------------------------------
# Node
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OrchestratorNode:
    """An immutable descriptor of one unit of work in the DAG.

    Identity and structure are immutable; runtime *status* is tracked
    separately by the ExecutionDAG (status map), exactly as StateMachine keeps
    session state separate from the static TRANSITIONS table. This keeps a node
    a pure value object: safe to hash, compare, and reuse across runs.

    Fields:
        id:       unique node identifier within a DAG.
        deps:     ids this node depends on (must all be DONE before it is READY).
        action:   optional proposal-action name the execution engine will route
                  through propose -> validate -> commit (e.g. "update_status").
                  Purely descriptive in this increment.
        payload:  optional opaque data for the action. Not interpreted here.
                  Excluded from equality/hash so a node's identity is its id
                  (+ structure): nodes stay hashable despite a mutable-typed
                  payload, and ids are unique within a DAG anyway.
    """

    id: str
    deps: frozenset[str] = field(default_factory=frozenset)
    action: Optional[str] = None
    payload: Mapping[str, object] = field(default_factory=dict, compare=False)

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id:
            raise DAGBuildError(f"node id must be a non-empty string, got {self.id!r}")
        # Normalize deps to a frozenset of strings regardless of input iterable.
        deps = frozenset(self.deps)
        for d in deps:
            if not isinstance(d, str) or not d:
                raise DAGBuildError(
                    f"node {self.id!r} has an invalid dependency id {d!r}"
                )
        if self.id in deps:
            raise DAGBuildError(f"node {self.id!r} depends on itself")
        # frozen dataclass: assign through object.__setattr__
        object.__setattr__(self, "deps", deps)


# ---------------------------------------------------------------------------
# Execution DAG
# ---------------------------------------------------------------------------


class ExecutionDAG:
    """A validated directed acyclic graph of OrchestratorNodes.

    Construction is fail-loud: duplicate ids, dangling dependencies, and cycles
    all raise DAGBuildError, so a constructed ExecutionDAG is *always* a valid
    DAG. After construction the graph topology is immutable; only per-node
    status evolves, through guarded transitions.

    This increment is execution-free: it answers "what is the legal order?",
    "which nodes may run now?", and "is this status move legal?" — it never runs
    a node itself.
    """

    def __init__(self, nodes: Iterable[OrchestratorNode]) -> None:
        node_list = list(nodes)

        # 1. Unique ids.
        self._nodes: dict[str, OrchestratorNode] = {}
        for n in node_list:
            if n.id in self._nodes:
                raise DAGBuildError(f"duplicate node id: {n.id!r}")
            self._nodes[n.id] = n

        # 2. Every dependency must resolve to a known node.
        for n in self._nodes.values():
            for d in n.deps:
                if d not in self._nodes:
                    raise DAGBuildError(
                        f"node {n.id!r} depends on unknown node {d!r}"
                    )

        # 3. No cycles — Kahn's algorithm. Computing the topological order here
        #    both proves acyclicity and caches the deterministic order/levels.
        self._topo_order, self._levels = self._kahn()

        # 4. Initial status: every node PENDING. Roots become READY immediately.
        self._status: dict[str, NodeStatus] = {
            nid: NodeStatus.PENDING for nid in self._nodes
        }
        self._refresh_ready()

    # -- Construction-time graph analysis ----------------------------------

    def _kahn(self) -> tuple[list[str], list[list[str]]]:
        """Kahn topological sort with deterministic tie-breaking.

        Returns (flat_topo_order, levels). Within any independent set, nodes are
        ordered by id (sorted), so the output is fully reproducible. A remaining
        node count after the queue drains means a cycle -> DAGBuildError.
        """
        indegree: dict[str, int] = {nid: 0 for nid in self._nodes}
        # dependents[d] = nodes that list d as a dependency (edge d -> dependent)
        dependents: dict[str, list[str]] = {nid: [] for nid in self._nodes}
        for n in self._nodes.values():
            indegree[n.id] = len(n.deps)
            for d in n.deps:
                dependents[d].append(n.id)

        order: list[str] = []
        levels: list[list[str]] = []
        # current frontier = indegree-0 nodes, sorted for determinism
        frontier = sorted(nid for nid, deg in indegree.items() if deg == 0)

        processed = 0
        while frontier:
            level = list(frontier)  # already sorted
            levels.append(level)
            next_frontier: list[str] = []
            for nid in level:
                order.append(nid)
                processed += 1
                for dep in dependents[nid]:
                    indegree[dep] -= 1
                    if indegree[dep] == 0:
                        next_frontier.append(dep)
            frontier = sorted(next_frontier)

        if processed != len(self._nodes):
            remaining = sorted(
                nid for nid, deg in indegree.items() if deg > 0
            )
            raise DAGBuildError(
                f"cycle detected; nodes not topologically orderable: {remaining}"
            )
        return order, levels

    # -- Topology (immutable, read-only) -----------------------------------

    @property
    def node_ids(self) -> frozenset[str]:
        """All node ids in the graph."""
        return frozenset(self._nodes)

    def node(self, node_id: str) -> OrchestratorNode:
        """Return the node descriptor for an id (KeyError if absent)."""
        return self._nodes[node_id]

    def topological_order(self) -> list[str]:
        """Deterministic flat topological order of all node ids."""
        return list(self._topo_order)

    def levels(self) -> list[list[str]]:
        """Topological levels: levels()[k] is the set of nodes whose deepest
        dependency chain has length k. All nodes in one level are mutually
        independent and therefore parallel-eligible. Deterministic (ids sorted
        within each level).
        """
        return [list(level) for level in self._levels]

    @property
    def depth(self) -> int:
        """Number of topological levels (longest dependency chain + 1)."""
        return len(self._levels)

    def dependents_of(self, node_id: str) -> frozenset[str]:
        """Direct dependents (nodes that depend on node_id)."""
        if node_id not in self._nodes:
            raise KeyError(node_id)
        return frozenset(
            n.id for n in self._nodes.values() if node_id in n.deps
        )

    def descendants_of(self, node_id: str) -> frozenset[str]:
        """All transitive dependents of node_id (its downstream closure)."""
        if node_id not in self._nodes:
            raise KeyError(node_id)
        seen: set[str] = set()
        stack = [node_id]
        while stack:
            cur = stack.pop()
            for dep in self.dependents_of(cur):
                if dep not in seen:
                    seen.add(dep)
                    stack.append(dep)
        return frozenset(seen)

    # -- Status lifecycle (guarded, mutable) -------------------------------

    def status_of(self, node_id: str) -> NodeStatus:
        """Current status of a node (KeyError if absent)."""
        return self._status[node_id]

    def status_map(self) -> dict[str, NodeStatus]:
        """Copy of the full id -> status map."""
        return dict(self._status)

    def _deps_done(self, node_id: str) -> bool:
        return all(
            self._status[d] is NodeStatus.DONE
            for d in self._nodes[node_id].deps
        )

    def _refresh_ready(self) -> None:
        """Promote PENDING nodes whose dependencies are all DONE to READY.

        Pure-ish bookkeeping: only PENDING -> READY moves happen here, and only
        when legal. Idempotent.
        """
        for nid, st in self._status.items():
            if st is NodeStatus.PENDING and self._deps_done(nid):
                self._status[nid] = NodeStatus.READY

    def _set_status(self, node_id: str, dst: NodeStatus) -> None:
        if node_id not in self._status:
            raise KeyError(node_id)
        src = self._status[node_id]
        if not status_transition_allowed(src, dst):
            raise NodeStateError(
                f"illegal status transition for node {node_id!r}: "
                f"{src.value} -> {dst.value}"
            )
        self._status[node_id] = dst

    def mark_running(self, node_id: str) -> None:
        """Mark a READY node as RUNNING."""
        if self._status[node_id] is not NodeStatus.READY:
            raise NodeStateError(
                f"node {node_id!r} must be READY to run, is "
                f"{self._status[node_id].value}"
            )
        self._set_status(node_id, NodeStatus.RUNNING)

    def mark_done(self, node_id: str) -> None:
        """Mark a RUNNING node DONE and promote any newly-eligible dependents."""
        self._set_status(node_id, NodeStatus.DONE)
        self._refresh_ready()

    def mark_failed(self, node_id: str) -> frozenset[str]:
        """Mark a RUNNING node FAILED and SKIP its entire downstream closure.

        Returns the set of node ids that were skipped as a result. Deterministic
        failure propagation: every transitive dependent that has not already
        reached a terminal status becomes SKIPPED (it can never satisfy its
        dependencies).
        """
        self._set_status(node_id, NodeStatus.FAILED)
        skipped: set[str] = set()
        for dep in self.descendants_of(node_id):
            if self._status[dep] not in TERMINAL_STATUSES:
                self._status[dep] = NodeStatus.SKIPPED
                skipped.add(dep)
        return frozenset(skipped)

    # -- Scheduling queries (pure) -----------------------------------------

    def ready_nodes(self) -> list[str]:
        """Ids currently READY to run, deterministically ordered by id.

        These are exactly the nodes a scheduler may dispatch now; if more than
        one is returned they are mutually independent (parallel-eligible).
        """
        return sorted(
            nid for nid, st in self._status.items() if st is NodeStatus.READY
        )

    def next_ready(self) -> Optional[str]:
        """The single deterministic next node to run (lowest id of ready set),
        or None if nothing is currently runnable.
        """
        ready = self.ready_nodes()
        return ready[0] if ready else None

    def is_complete(self) -> bool:
        """True when every node has reached a terminal status."""
        return all(st in TERMINAL_STATUSES for st in self._status.values())

    def counts(self) -> dict[str, int]:
        """Tally of node counts by status value (handy for progress/telemetry)."""
        tally: dict[str, int] = {s.value: 0 for s in NodeStatus}
        for st in self._status.values():
            tally[st.value] += 1
        return tally

    # -- Introspection ------------------------------------------------------

    def __len__(self) -> int:
        return len(self._nodes)

    def __contains__(self, node_id: object) -> bool:
        return node_id in self._nodes

    def __repr__(self) -> str:
        return (
            f"ExecutionDAG(nodes={len(self._nodes)}, depth={self.depth}, "
            f"complete={self.is_complete()})"
        )
