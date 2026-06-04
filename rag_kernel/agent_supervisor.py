"""Agent Supervisor — observable spawn/monitor layer over off-process work.

GRAPH-ORCH (v4.0), increment 7 of 7 (INS-030) — the LAST core increment.

WHAT THIS IS
    A thin supervisor over the increment-6 off-process workers. Given a batch of
    PURE, picklable ``work`` callables (the same contract Schedule.PROCESS_LEVELS
    uses), it:
      * spawns each as an OS subprocess (stdlib ``multiprocessing.Process``),
      * MONITORS them live — per-worker PID, lifecycle state
        (PENDING -> RUNNING -> DONE | FAILED), start/finish timing, and OS exit
        code — exposed as an ``AgentView`` snapshot that can be rendered as a
        text "agent view" while work is still in flight,
      * COLLECTS each worker's returned payload (or its error) and hands the
        results back to the caller, keyed by node id.

WHAT THIS IS NOT (the load-bearing boundary)
    The supervisor owns NO authoritative state. It never touches HOT, the WAL,
    checkpoints, the project lock, or the state machine — it has no kernel handle
    at all. It only spawns, observes, and collects. The PARENT kernel remains the
    SOLE writer: GraphExecutor still commits every node's payload through the one
    serialized propose -> validate -> commit pipeline in deterministic sorted-id
    order. So plugging the supervisor into PROCESS_LEVELS changes only HOW the
    work phase is run and observed — never the committed order, the final HOT,
    the WAL sequence, or the checkpoints (all byte-identical to LEVELS and
    SEQUENTIAL). No schema / WAL / TLA+ / guardgen change is required.

DESIGN POSTURE (dual-POV)
    CS lens — concurrency is confined to the pure, side-effect-free work phase;
    every worker is a value-in / value-out function handed no shared state, so
    there is nothing to race on. Liveness/exit observation is read-only OS
    introspection (Process.pid / is_alive() / exitcode). The single-writer +
    WAL-recoverable guarantees are untouched because this module cannot write
    authoritative state — it structurally has no handle to it.
    ML lens — a genuine "agent view": real OS-process parallelism for wide,
    I/O-bound levels with live status/PIDs/exit codes, the substrate an
    orchestrator-of-agents wants. Substrate is stdlib-only and deterministic, so
    an external viewer (tmux pane, dashboard) can consume ``AgentView`` snapshots
    as an OPTIONAL read-projection later (the deferred INS-032 path) without the
    core depending on it. LLM proposes (which work), system decides (legal
    commit order + single-writer, in the parent), state persists (in the kernel,
    never here).

Spec reference: ROADMAP.md — v4.0 Graph Orchestrator, increment 7
Pairs with: graph_orchestrator.GraphExecutor (Schedule.PROCESS_LEVELS, supervisor=)

@rag-kernel-manifest
{
  "module": "rag_kernel.agent_supervisor",
  "capability": "agent_supervision",
  "description": "Observable spawn/monitor/collect layer over pure off-process node work: live per-worker PID + lifecycle state + exit code as an AgentView, owning no authoritative state (the parent kernel stays sole writer)",
  "states": ["PENDING", "RUNNING", "DONE", "FAILED"],
  "exports": ["WorkerState", "WorkerObservation", "WorkerResult", "AgentView", "AgentSupervisor"],
  "use_when": "Spawning, live-monitoring (PIDs/exit codes), and collecting results from pure off-process DAG node work; an 'agent view' that never writes authoritative state",
  "never_bypass": false
}
"""

from __future__ import annotations

import collections.abc as _abc
import multiprocessing as _mp
import os
import time
from dataclasses import dataclass, field
from enum import Enum
from multiprocessing.connection import wait as _mp_wait
from typing import Callable, Iterable, Mapping, Optional


# ---------------------------------------------------------------------------
# Worker lifecycle — a small read-only status enum (mirrors NodeStatus shape)
# ---------------------------------------------------------------------------


class WorkerState(Enum):
    """Observed lifecycle state of a single off-process worker.

    PENDING  — created but not yet started (e.g. waiting for a free slot).
    RUNNING  — the OS subprocess has been started and not yet collected.
    DONE     — the worker returned a result successfully.
    FAILED   — the worker raised, died, or returned an unusable result.
    """

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    DONE = "DONE"
    FAILED = "FAILED"


# ---------------------------------------------------------------------------
# Immutable observation records (the "agent view" data)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WorkerObservation:
    """An immutable point-in-time observation of one worker.

    Pure monitoring metadata — never the payload. Safe to snapshot, render, and
    hand to an external viewer without exposing authoritative state.
    """

    node_id: str
    state: WorkerState
    pid: Optional[int] = None
    submitted_at: Optional[float] = None
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    exitcode: Optional[int] = None
    ok: bool = False
    error: Optional[str] = None

    @property
    def duration_s(self) -> Optional[float]:
        """Wall-clock seconds the worker ran, or None if not yet finished."""
        if self.started_at is None or self.finished_at is None:
            return None
        return self.finished_at - self.started_at

    def to_dict(self) -> dict:
        return {
            "node_id": self.node_id,
            "state": self.state.value,
            "pid": self.pid,
            "exitcode": self.exitcode,
            "ok": self.ok,
            "error": self.error,
            "duration_s": self.duration_s,
        }


@dataclass(frozen=True)
class WorkerResult:
    """The collected outcome of one worker: payload on success, error on failure.

    ``payload`` is whatever the work callable returned (validated as a Mapping by
    the caller before it is committed). ``observation`` is the final monitoring
    record for the worker.
    """

    node_id: str
    ok: bool
    payload: Optional[Mapping[str, object]]
    error: Optional[str]
    observation: WorkerObservation

    def to_dict(self) -> dict:
        return {
            "node_id": self.node_id,
            "ok": self.ok,
            "error": self.error,
            "observation": self.observation.to_dict(),
        }


@dataclass(frozen=True)
class AgentView:
    """A snapshot of every worker in a batch — the renderable "agent view"."""

    observations: tuple[WorkerObservation, ...] = ()

    def counts(self) -> dict[str, int]:
        """Tally of workers by state value."""
        tally = {s.value: 0 for s in WorkerState}
        for obs in self.observations:
            tally[obs.state.value] += 1
        return tally

    def to_dict(self) -> dict:
        return {
            "counts": self.counts(),
            "workers": [o.to_dict() for o in self.observations],
        }

    def render(self) -> str:
        """A compact text table — the human-facing agent view."""
        c = self.counts()
        header = (
            f"AGENT VIEW — {len(self.observations)} worker(s) "
            f"(RUNNING {c['RUNNING']} · DONE {c['DONE']} · "
            f"FAILED {c['FAILED']} · PENDING {c['PENDING']})"
        )
        rows = [header, f"{'NODE':<16}{'PID':>8}  {'STATE':<8}{'DUR(s)':>8}"]
        for obs in self.observations:
            dur = obs.duration_s
            dur_s = f"{dur:.2f}" if dur is not None else "—"
            pid_s = str(obs.pid) if obs.pid is not None else "—"
            rows.append(
                f"{obs.node_id:<16}{pid_s:>8}  {obs.state.value:<8}{dur_s:>8}"
            )
        return "\n".join(rows)


# ---------------------------------------------------------------------------
# Off-process worker entry point (top-level so it is picklable for spawn)
# ---------------------------------------------------------------------------


def _run_worker(send_conn, work: Callable[..., object], work_args: tuple) -> None:
    """Run one pure ``work(*work_args)`` in a subprocess and report the outcome.

    Sends exactly one ``(kind, pid, data)`` tuple back over ``send_conn``:
      * ("ok", pid, payload)  — work returned ``payload``,
      * ("err", pid, message) — work raised, or the payload was unpicklable.
    The worker is handed NO kernel handle: ``work`` is a pure function of its
    args, so it cannot race on or mutate any shared state.
    """
    pid = os.getpid()
    try:
        out = work(*work_args)
    except Exception as exc:  # logical failure — reported, not raised
        msg = ("err", pid, f"{type(exc).__name__}: {exc}")
    else:
        msg = ("ok", pid, out)
    try:
        send_conn.send(msg)
    except Exception as exc:  # payload not picklable
        try:
            send_conn.send(
                ("err", pid, f"unpicklable result: {type(exc).__name__}: {exc}")
            )
        except Exception:
            pass
    finally:
        send_conn.close()


# ---------------------------------------------------------------------------
# Internal mutable per-worker handle (never exposed; snapshotted to frozen obs)
# ---------------------------------------------------------------------------


class _Worker:
    __slots__ = (
        "node_id", "process", "recv_conn", "pid", "state",
        "submitted_at", "started_at", "finished_at", "exitcode", "ok", "error",
    )

    def __init__(self, node_id: str, submitted_at: float) -> None:
        self.node_id = node_id
        self.process = None
        self.recv_conn = None
        self.pid: Optional[int] = None
        self.state = WorkerState.PENDING
        self.submitted_at = submitted_at
        self.started_at: Optional[float] = None
        self.finished_at: Optional[float] = None
        self.exitcode: Optional[int] = None
        self.ok = False
        self.error: Optional[str] = None

    def observe(self) -> WorkerObservation:
        return WorkerObservation(
            node_id=self.node_id,
            state=self.state,
            pid=self.pid,
            submitted_at=self.submitted_at,
            started_at=self.started_at,
            finished_at=self.finished_at,
            exitcode=self.exitcode,
            ok=self.ok,
            error=self.error,
        )


# ---------------------------------------------------------------------------
# The supervisor
# ---------------------------------------------------------------------------


class AgentSupervisor:
    """Spawns, live-monitors, and collects pure off-process work.

    Owns no authoritative state and holds no kernel handle. ``run_batch`` runs a
    batch of ``(node_id, work, work_args)`` tasks as OS subprocesses, bounded by
    ``max_workers`` (default: all at once), and returns a ``{node_id:
    WorkerResult}`` map. Completion order does not matter to correctness: the
    caller (GraphExecutor) commits results in deterministic sorted-id order, so
    the supervisor never imposes or depends on finish order.

    ``run_batch`` accepts an optional ``progress`` callback invoked with an
    ``AgentView`` as workers change state — the hook an external "agent view"
    (tmux pane, dashboard) renders from. ``snapshot`` returns the most recent
    view after a run.
    """

    def __init__(
        self,
        *,
        max_workers: Optional[int] = None,
        poll_interval: float = 0.05,
        mp_context=None,
    ) -> None:
        if max_workers is not None and (
            not isinstance(max_workers, int) or max_workers < 1
        ):
            raise ValueError(
                f"max_workers must be a positive int or None, got {max_workers!r}"
            )
        if poll_interval <= 0:
            raise ValueError(
                f"poll_interval must be > 0, got {poll_interval!r}"
            )
        self.max_workers = max_workers
        self.poll_interval = poll_interval
        # Default to the platform's default start method (matches the increment-6
        # ProcessPoolExecutor); callers may pass an explicit context for parity.
        self._ctx = mp_context or _mp.get_context()
        self._last_handles: dict[str, _Worker] = {}

    # -- public API ---------------------------------------------------------

    def run_batch(
        self,
        tasks: Iterable[tuple[str, Callable[..., object], tuple]],
        *,
        progress: Optional[Callable[["AgentView"], None]] = None,
    ) -> dict[str, WorkerResult]:
        """Run ``tasks`` off-process; return ``{node_id: WorkerResult}``.

        Each task is ``(node_id, work, work_args)``. ``work`` must be a pure,
        picklable callable; ``work_args`` picklable positional args. Blocks until
        every worker has reached a terminal state.
        """
        task_list = sorted(
            ((nid, work, tuple(args)) for nid, work, args in tasks),
            key=lambda t: t[0],
        )
        handles: dict[str, _Worker] = {}
        results: dict[str, WorkerResult] = {}
        pending = list(task_list)
        active: dict[object, str] = {}  # recv_conn -> node_id
        limit = self.max_workers or max(len(task_list), 1)
        self._last_handles = handles

        def emit() -> None:
            if progress is not None:
                progress(self._view(handles))

        def try_spawn(nid: str, work: Callable[..., object], args: tuple) -> None:
            w = _Worker(node_id=nid, submitted_at=time.monotonic())
            handles[nid] = w
            recv_conn, send_conn = self._ctx.Pipe(duplex=False)
            try:
                proc = self._ctx.Process(
                    target=_run_worker, args=(send_conn, work, args), daemon=False
                )
                proc.start()
            except Exception as exc:  # e.g. an unpicklable arg under spawn
                recv_conn.close()
                send_conn.close()
                w.state = WorkerState.FAILED
                w.finished_at = time.monotonic()
                w.error = f"worker spawn failed: {type(exc).__name__}: {exc}"
                results[nid] = WorkerResult(nid, False, None, w.error, w.observe())
                return
            send_conn.close()  # parent only receives
            w.process = proc
            w.recv_conn = recv_conn
            w.pid = proc.pid
            w.started_at = time.monotonic()
            w.state = WorkerState.RUNNING
            active[recv_conn] = nid

        # Initial wave, then backfill as workers finish.
        while pending and len(active) < limit:
            nid, work, args = pending.pop(0)
            try_spawn(nid, work, args)
        emit()

        while active:
            ready = _mp_wait(list(active.keys()), timeout=self.poll_interval)
            if not ready:
                emit()  # liveness tick for the agent view
                continue
            for conn in ready:
                nid = active.pop(conn)
                w = handles[nid]
                try:
                    kind, pid, data = conn.recv()
                except EOFError:
                    kind, pid, data = (
                        "err", None, "worker exited without sending a result"
                    )
                conn.close()
                if w.process is not None:
                    w.process.join()
                    w.exitcode = w.process.exitcode
                w.finished_at = time.monotonic()
                if pid is not None:
                    w.pid = pid
                if kind == "ok":
                    w.state = WorkerState.DONE
                    w.ok = True
                    results[nid] = WorkerResult(nid, True, data, None, w.observe())
                else:
                    w.state = WorkerState.FAILED
                    w.ok = False
                    w.error = str(data)
                    results[nid] = WorkerResult(
                        nid, False, None, w.error, w.observe()
                    )
                # Backfill a waiting task into the freed slot.
                while pending and len(active) < limit:
                    n2, wk2, a2 = pending.pop(0)
                    try_spawn(n2, wk2, a2)
            emit()

        return results

    def snapshot(self) -> AgentView:
        """The most recent agent view (after / during the last ``run_batch``)."""
        return self._view(self._last_handles)

    # -- internals ----------------------------------------------------------

    @staticmethod
    def _view(handles: Mapping[str, _Worker]) -> AgentView:
        obs = tuple(
            handles[nid].observe() for nid in sorted(handles)
        )
        return AgentView(observations=obs)


def is_mapping(value: object) -> bool:
    """True if ``value`` is a Mapping (the payload contract for a commit)."""
    return isinstance(value, _abc.Mapping)
