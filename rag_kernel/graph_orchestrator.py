"""Graph Orchestrator — deterministic DAG core + execution engine.

GRAPH-ORCH (v4.0), increments 1–4 of N:

  Increment 1 — the *pure* directed-acyclic-graph core (ExecutionDAG):
    * fail-loud construction (unique ids, every dependency resolvable, NO cycles),
    * a deterministic topological order,
    * topological *level* assignment — the set of nodes eligible to run in
      parallel at each depth (the "deterministic levels" scheduling model),
    * a guarded per-node status lifecycle
      (PENDING -> READY -> RUNNING -> DONE | FAILED, plus SKIPPED),
    * pure ready/next-ready queries and deterministic failure propagation.

  Increment 2 — the execution engine (GraphExecutor):
    * drives nodes through the kernel's propose -> validate -> commit pipeline
      (a node's "work" IS its proposal — no arbitrary code is executed here),
    * a checkpoint after every committed node through the guarded CHECKPOINTING
      transition (KernelApp.checkpoint), making each completed node a durable
      crash-recovery boundary,
    * a per-node WAL ``GRAPH_NODE_EXECUTED`` event for an auditable trail
      (mirrors enforce_context_policy / M-009: persist a safe point, then log),
    * deterministic sequential scheduling (lowest ready id first) with
      deterministic failure propagation (a FAILED node SKIPs its downstream
      closure; independent branches still run).

  Increment 3 — deterministic-levels parallel scheduling (Schedule.LEVELS):
    * schedules the DAG one topological *level* at a time (ExecutionDAG.levels);
      the nodes within a level are mutually independent and therefore
      "parallel-eligible" — the schedule names that batch explicitly,
    * but every node STILL commits through the one serialized
      propose -> validate -> commit pipeline, in deterministic id order, so the
      LEVELS schedule is provably equivalent to SEQUENTIAL: identical executed
      order, identical final HOT, identical WAL event sequence,
    * single-writer is made explicit and enforced: the executor asserts it holds
      the project file-mutex (concurrency.ProjectLock, acquired at boot) for its
      own session before committing a level, so concurrency can only ever be a
      *scheduling* property, never a state-mutation race,
    * failure propagation is unchanged — a FAILED node in an earlier level SKIPs
      its downstream closure in later levels; independent branches still run.

  Increment 4 — transactional rollback/recovery (rollback_on_failure):
    * an opt-in mode (default OFF, so the keep-committed-prefix behaviour of
      increments 2-3 is unchanged) that makes a DAG run all-or-nothing,
    * at run start it snapshots the pre-run HOT as a baseline; on ANY node
      FAILED it undoes the whole run by restoring that baseline through the
      kernel's RECOVERY path (KernelApp.rollback_to_snapshot): force_state into
      RECOVERY (the sanctioned escape — READY->RECOVERY is not a normal
      transition), atomic HOT restore (refreshes .bak), a GRAPH_ROLLBACK WAL
      event, delta-base reset, then a legal RECOVERY->READY,
    * the kernel — never the executor — owns the state mutation, so the
      single-writer + WAL-recoverable guarantees are preserved; no TLA+/guardgen
      change is needed because the RECOVERY transitions already exist.

  Increment 5 — registration (INS-025):
    * this module is now REGISTERED in rag_kernel.__init__._KERNEL_MODULES,
      discover(), and cmd_health — it is the 14th capability module (health
      15/15 incl. __main__), and the package manifest `modules` dict lists it,
    * the functional module count is reconciled 13 -> 14 with the documented
      convention in __init__.py, and README/CHANGELOG/ROADMAP are aligned per
      Rule 11. The scope boundary held FV-PHASE3 -> FV-PHASE4 style across
      increments 1-4 and is now closed.

DESIGN NOTE:
    GraphExecutor depends on a KernelApp only structurally (duck-typed; imported
    under TYPE_CHECKING) so this module never imports api.py at runtime — no
    import cycle, even though it is now part of the discovered module set.

DESIGN POSTURE (dual-POV):
    CS lens — a DAG is an adjacency list; ordering is a topological sort; cycle
    detection is Kahn's algorithm (a stalled queue == a cycle). Construction is
    total and fail-loud: an invalid graph can never be partially built. Node
    status transitions are themselves a small guarded state machine, so an
    illegal lifecycle move (e.g. DONE without RUNNING) is rejected, not silently
    tolerated — the same discipline state_machine.py applies to sessions. The
    execution engine never mutates state directly: it routes every node through
    the serialized propose -> validate -> commit pipeline and checkpoints each
    committed node, so progress is deterministic and WAL-recoverable.
    ML lens — same-level nodes are "parallel-eligible"; Schedule.LEVELS makes
    that batch structure explicit, while still executing each node through the
    one serialized pipeline in deterministic id order. Concurrency is therefore a
    scheduling property layered on top, never a state-mutation race, because every
    result commits through the single pipeline under the project file-mutex. The
    LEVELS schedule is, by construction, equivalent to SEQUENTIAL (same order,
    same final state, same WAL). Checkpoint-per-node trades a little IO for
    durability; using the delta-checkpoint manager keeps that cost a small delta
    (full rewrite only every N) rather than a full write per node. LLM proposes
    (which nodes/work), system decides (legal order + legal status moves + legal
    transitions + single-writer), state persists (checkpoint-per-node + WAL).

Spec reference: ROADMAP.md — v4.0 Graph Orchestrator
Design doc reference: v3.2_ARCHITECTURE_DESIGN.md (orchestration section, TBD)

@rag-kernel-manifest
{
  "module": "rag_kernel.graph_orchestrator",
  "capability": "graph_orchestration",
  "description": "Deterministic DAG core + execution engine: fail-loud build, topological order + deterministic-levels scheduling, guarded node-status lifecycle, propose->validate->commit execution with checkpoint-per-node under a single-writer file-mutex",
  "states": ["PENDING", "READY", "RUNNING", "DONE", "FAILED", "SKIPPED"],
  "exports": ["NodeStatus", "OrchestratorNode", "ExecutionDAG", "DAGBuildError", "NodeStateError", "GraphExecutor", "NodeExecutionResult", "GraphExecutionError", "Schedule"],
  "use_when": "Modeling, ordering, scheduling, or executing a dependency graph of work units",
  "never_bypass": false
}
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Iterable, Mapping, Optional

if TYPE_CHECKING:  # pragma: no cover - typing only, no runtime import cycle
    from rag_kernel.api import KernelApp


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


# ---------------------------------------------------------------------------
# Execution engine (increment 2)
# ---------------------------------------------------------------------------


class GraphExecutionError(RuntimeError):
    """Raised when the executor is misconfigured or misused.

    Distinct from a *node* failure (which is normal, recoverable control flow
    captured in the execution report): this signals a programming error such as
    a node with no action to run, or re-running an executor that has already
    run.
    """


#: WAL event type emitted once per executed node. Mirrors the way M-009 added
#: ``CONTEXT_TRUNCATION``; registered in schemas.VALID_EVENT_TYPES.
GRAPH_NODE_EVENT = "GRAPH_NODE_EXECUTED"


class Schedule(Enum):
    """How the executor walks the DAG.

    SEQUENTIAL — increment 2: repeatedly dispatch the single lowest-id ready
        node (``ExecutionDAG.next_ready``) until the graph is complete.
    LEVELS — increment 3: walk one topological *level* at a time
        (``ExecutionDAG.levels``). The ready nodes within a level are mutually
        independent ("parallel-eligible"), but they are still committed through
        the one serialized propose -> validate -> commit pipeline in deterministic
        id order, under the project file-mutex. Provably equivalent to SEQUENTIAL
        in executed order, final state, and WAL sequence — the parallelism is a
        scheduling property, never a state-mutation race.
    """

    SEQUENTIAL = "sequential"
    LEVELS = "levels"


@dataclass(frozen=True)
class NodeExecutionResult:
    """Immutable record of one node's trip through the execution pipeline.

    Fields:
        node_id:        the node executed.
        action:         the proposal action routed through the kernel.
        status:         terminal NodeStatus reached (DONE / FAILED / SKIPPED).
        proposal_id:    kernel proposal id, if one was created (None if the
                        proposal was rejected at validation).
        committed:      whether the proposal committed to HOT.
        checkpoint_seq: WAL seq of the per-node checkpoint (None if the node
                        did not commit, so no checkpoint was taken).
        skipped:        downstream node ids SKIPPED as a result of this node
                        FAILING (empty unless status is FAILED).
        errors:         validation/commit error strings (empty on success).
    """

    node_id: str
    action: Optional[str]
    status: NodeStatus
    proposal_id: Optional[str] = None
    committed: bool = False
    checkpoint_seq: Optional[int] = None
    skipped: frozenset[str] = field(default_factory=frozenset)
    errors: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "node_id": self.node_id,
            "action": self.action,
            "status": self.status.value,
            "proposal_id": self.proposal_id,
            "committed": self.committed,
            "checkpoint_seq": self.checkpoint_seq,
            "skipped": sorted(self.skipped),
            "errors": list(self.errors),
        }


class GraphExecutor:
    """Drives an ExecutionDAG through the kernel's propose -> commit pipeline.

    The executor owns *control flow*, never state mutation. For each node it:

      1. marks the node RUNNING (guarded status transition),
      2. submits ``{"action": node.action, "payload": node.payload}`` via
         ``app.propose`` — the kernel validates it (state legality, tier/echo
         gates, schema),
      3. on a valid proposal, ``app.commit`` applies it to HOT atomically,
      4. marks the node DONE and takes a per-node checkpoint through the guarded
         CHECKPOINTING transition (``app.checkpoint``) — every committed node
         becomes a durable crash-recovery boundary,
      5. appends a ``GRAPH_NODE_EXECUTED`` WAL event for the audit trail.

    A rejected proposal or a failed commit marks the node FAILED, which
    deterministically SKIPs its entire downstream closure (those nodes can never
    satisfy their dependencies). Independent branches keep running unless
    ``stop_on_failure`` is set. With ``rollback_on_failure`` (default off,
    increment 4) the run is instead transactional: the first node failure undoes
    the whole run back to the pre-run baseline via the kernel's RECOVERY path,
    so the DAG commits all-or-nothing.

    Scheduling (``schedule``): SEQUENTIAL (default, increment 2) dispatches the
    single lowest-id ready node at a time. LEVELS (increment 3) walks one
    topological level at a time, exposing the parallel-eligible batch per level
    while still committing every node through the one serialized pipeline in
    deterministic id order under the project file-mutex. The two schedules are
    equivalent by construction (same order, final state, and WAL).

    Determinism: nodes run in the DAG's deterministic ready order (lowest id
    first). Given the same DAG and the same kernel responses, the executed order,
    the per-node results, and the WAL event sequence are identical run to run.

    The ``app`` argument is duck-typed (a KernelApp); only ``propose``,
    ``commit``, ``checkpoint``, ``wal``, ``lock`` and ``session_id`` are used, so
    this module never imports api.py at runtime.
    """

    def __init__(
        self,
        dag: ExecutionDAG,
        app: "KernelApp",
        *,
        force_full_checkpoint: bool = False,
        stop_on_failure: bool = False,
        schedule: Schedule = Schedule.SEQUENTIAL,
        rollback_on_failure: bool = False,
    ) -> None:
        # Fail-loud: every node must carry an action to route through the
        # pipeline. Increment 1 allows action=None (purely descriptive); the
        # executor cannot run such a node, so reject up front rather than fail
        # mid-run (mirrors the module's "never partially built" posture).
        missing = sorted(
            nid for nid in dag.node_ids if not dag.node(nid).action
        )
        if missing:
            raise GraphExecutionError(
                f"cannot execute: nodes have no action to run: {missing}"
            )

        self.dag = dag
        self.app = app
        self.force_full_checkpoint = force_full_checkpoint
        self.stop_on_failure = stop_on_failure
        self.schedule = schedule
        self.rollback_on_failure = rollback_on_failure

        self._results: list[NodeExecutionResult] = []
        self._executed_order: list[str] = []
        self._levels_executed: list[list[str]] = []
        self._has_run = False
        # Transactional-mode bookkeeping (rollback_on_failure).
        self._baseline_hot: Optional[dict] = None
        self._rolled_back = False
        self._rollback_info: Optional[dict] = None

    # -- Execution ----------------------------------------------------------

    def run(self) -> dict:
        """Execute the whole DAG using the configured schedule.

        Dispatches to the SEQUENTIAL (increment 2) or LEVELS (increment 3)
        scheduler. Returns an execution report dict. Idempotent guard: a second
        call raises GraphExecutionError (build a fresh executor to re-run).
        """
        if self._has_run:
            raise GraphExecutionError("executor has already run")
        self._has_run = True

        # Transactional mode: snapshot the pre-run HOT as the rollback baseline.
        # Captured only when armed, so the default path perturbs nothing.
        if self.rollback_on_failure:
            self._baseline_hot = copy.deepcopy(self.app.get_hot())

        if self.schedule is Schedule.LEVELS:
            self._run_levels()
        else:
            self._run_sequential()

        return self.report()

    def _run_sequential(self) -> None:
        """Increment-2 schedule: dispatch the lowest-id ready node until done."""
        while True:
            node_id = self.dag.next_ready()
            if node_id is None:
                break
            result = self._run_one(node_id)
            self._results.append(result)
            self._executed_order.append(node_id)
            if self._maybe_rollback(result):
                break
            if self.stop_on_failure and result.status is NodeStatus.FAILED:
                break

    def _run_levels(self) -> None:
        """Increment-3 schedule: walk one topological level at a time.

        For each level (in topological order), the nodes that are currently READY
        form the parallel-eligible batch. They are committed through the one
        serialized pipeline in deterministic id order — concurrency is a
        scheduling property here, never a state-mutation race. Single-writer is
        asserted up front via the project file-mutex.

        Determinism note: ``levels()`` is the same Kahn decomposition that drives
        ``next_ready``; filtering each level to its READY members and committing
        them in sorted id order yields exactly the SEQUENTIAL executed order. A
        node in a level can only be non-READY because an *earlier-level* ancestor
        FAILED (so it was SKIPPED); same-level nodes are mutually independent, so
        running one never changes another's readiness.
        """
        self._assert_single_writer()

        for level in self.dag.levels():
            # 'level' is already deterministically sorted by id; keep only the
            # nodes still READY (a SKIPPED node had an ancestor fail earlier).
            batch = [
                nid for nid in level
                if self.dag.status_of(nid) is NodeStatus.READY
            ]
            if not batch:
                continue
            executed_in_level: list[str] = []
            for node_id in batch:
                result = self._run_one(node_id)
                self._results.append(result)
                self._executed_order.append(node_id)
                executed_in_level.append(node_id)
                if self._maybe_rollback(result):
                    self._levels_executed.append(executed_in_level)
                    return
                if self.stop_on_failure and result.status is NodeStatus.FAILED:
                    self._levels_executed.append(executed_in_level)
                    return
            self._levels_executed.append(executed_in_level)

    def _assert_single_writer(self) -> None:
        """Enforce the single-writer precondition via the project file-mutex.

        The LEVELS schedule names parallel-eligible batches, so we make the
        serialization guarantee explicit: this executor's kernel must hold the
        project lock (concurrency.ProjectLock) for *its own* session before any
        level is committed. If the lock is missing or owned by a different
        session, refusing to run is the safe, deterministic choice — a foreign
        writer could otherwise interleave commits and break WAL recoverability.

        Duck-typed: if the app exposes no ``lock``, the check is skipped (the
        executor stays usable against a minimal kernel stub).
        """
        lock = getattr(self.app, "lock", None)
        if lock is None:
            return
        session_id = getattr(self.app, "session_id", None)
        try:
            info = lock.read_lock()
        except Exception as exc:  # corrupt/unreadable lock file
            raise GraphExecutionError(
                f"cannot verify single-writer: project lock unreadable ({exc})"
            ) from exc
        if info is None:
            raise GraphExecutionError(
                "cannot run LEVELS schedule: project lock is not held "
                "(boot the kernel so it owns the file-mutex)"
            )
        if info.session_id != session_id:
            raise GraphExecutionError(
                "cannot run LEVELS schedule: project lock is held by a "
                f"different session ({info.session_id!r} != {session_id!r}); "
                "single-writer guarantee would be violated"
            )

    def _maybe_rollback(self, result: NodeExecutionResult) -> bool:
        """Transactional mode: undo the whole run on a node failure.

        When ``rollback_on_failure`` is set and ``result`` is FAILED, restore the
        kernel to the pre-run baseline through the kernel's RECOVERY path
        (KernelApp.rollback_to_snapshot) — making the DAG all-or-nothing — and
        signal the caller to stop dispatching. A no-op (returns False) in the
        default mode or for a successful node, so existing behaviour is
        unchanged. The executor never mutates state itself: the kernel owns the
        restore, the WAL event, and the RECOVERY->READY transition.
        """
        if not (self.rollback_on_failure
                and result.status is NodeStatus.FAILED):
            return False
        info = self.app.rollback_to_snapshot(
            self._baseline_hot,
            reason=f"graph node {result.node_id!r} failed",
        )
        self._rolled_back = True
        self._rollback_info = {"trigger_node": result.node_id, **info}
        return True

    def _run_one(self, node_id: str) -> NodeExecutionResult:
        """Run a single READY node through propose -> commit -> checkpoint."""
        node = self.dag.node(node_id)
        action = node.action

        # 1. Guarded lifecycle move: READY -> RUNNING.
        self.dag.mark_running(node_id)

        # 2. Propose (kernel validates: state legality, gates, schema).
        proposal = {"action": action, "payload": dict(node.payload)}
        prop = self.app.propose(proposal)
        proposal_id = prop.get("proposal_id")

        if not prop.get("valid"):
            return self._fail(
                node_id, action, proposal_id,
                errors=tuple(prop.get("errors", [])),
            )

        # 3. Commit (atomic HOT write + PROPOSAL_COMMITTED WAL).
        commit = self.app.commit(proposal_id)
        if not commit.get("committed"):
            return self._fail(
                node_id, action, proposal_id,
                errors=(commit.get("error", "commit failed"),),
            )

        # 4. Success: mark DONE (promotes newly-eligible dependents) and take a
        #    per-node checkpoint through the guarded CHECKPOINTING transition.
        self.dag.mark_done(node_id)
        ckpt = self.app.checkpoint(force_full=self.force_full_checkpoint)
        checkpoint_seq = ckpt.get("wal_seq") if ckpt.get("checkpointed") else None

        # 5. Auditable per-node WAL event (mirrors M-009's CONTEXT_TRUNCATION).
        self.app.wal.append(
            GRAPH_NODE_EVENT,
            session_id=getattr(self.app, "session_id", None),
            node_id=node_id,
            action=action,
            status=NodeStatus.DONE.value,
            proposal_id=proposal_id,
            committed=True,
            checkpoint_seq=checkpoint_seq,
        )

        return NodeExecutionResult(
            node_id=node_id,
            action=action,
            status=NodeStatus.DONE,
            proposal_id=proposal_id,
            committed=True,
            checkpoint_seq=checkpoint_seq,
        )

    def _fail(
        self,
        node_id: str,
        action: Optional[str],
        proposal_id: Optional[str],
        *,
        errors: tuple[str, ...],
    ) -> NodeExecutionResult:
        """Mark a RUNNING node FAILED, SKIP its closure, and WAL-log it.

        No checkpoint is taken: the node never committed, so HOT is unchanged;
        the WAL event itself records the failure and the skipped closure.
        """
        skipped = self.dag.mark_failed(node_id)
        self.app.wal.append(
            GRAPH_NODE_EVENT,
            session_id=getattr(self.app, "session_id", None),
            node_id=node_id,
            action=action,
            status=NodeStatus.FAILED.value,
            proposal_id=proposal_id,
            committed=False,
            checkpoint_seq=None,
            skipped=sorted(skipped),
        )
        return NodeExecutionResult(
            node_id=node_id,
            action=action,
            status=NodeStatus.FAILED,
            proposal_id=proposal_id,
            committed=False,
            checkpoint_seq=None,
            skipped=skipped,
            errors=errors,
        )

    # -- Reporting ----------------------------------------------------------

    @property
    def results(self) -> list[NodeExecutionResult]:
        """Per-node results in execution order."""
        return list(self._results)

    @property
    def executed_order(self) -> list[str]:
        """Node ids in the order they were dispatched."""
        return list(self._executed_order)

    @property
    def rolled_back(self) -> bool:
        """True if a transactional rollback to baseline occurred during the run."""
        return self._rolled_back

    @property
    def rollback_info(self) -> Optional[dict]:
        """Details of the rollback (trigger node + kernel restore result), or None."""
        return self._rollback_info

    @property
    def levels_executed(self) -> list[list[str]]:
        """Per-level batches of node ids actually dispatched (LEVELS schedule).

        Empty for the SEQUENTIAL schedule. Each inner list is one topological
        level's parallel-eligible batch, in deterministic id order.
        """
        return [list(b) for b in self._levels_executed]

    def report(self) -> dict:
        """Summarize the run: per-node results, status tally, completion."""
        status_map = self.dag.status_map()
        return {
            "complete": self.dag.is_complete(),
            "schedule": self.schedule.value,
            "rolled_back": self._rolled_back,
            "rollback": self._rollback_info,
            "executed_order": list(self._executed_order),
            "levels_executed": [list(b) for b in self._levels_executed],
            "counts": self.dag.counts(),
            "results": [r.to_dict() for r in self._results],
            "done": sorted(
                nid for nid, st in status_map.items() if st is NodeStatus.DONE
            ),
            "failed": sorted(
                nid for nid, st in status_map.items()
                if st is NodeStatus.FAILED
            ),
            "skipped": sorted(
                nid for nid, st in status_map.items()
                if st is NodeStatus.SKIPPED
            ),
        }

    def __repr__(self) -> str:
        return (
            f"GraphExecutor(dag={self.dag!r}, schedule={self.schedule.value}, "
            f"ran={self._has_run}, executed={len(self._executed_order)})"
        )
