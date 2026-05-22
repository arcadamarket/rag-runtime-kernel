"""State machine engine for the RAG Runtime Kernel.

Enforces deterministic state transitions with guards and event logging.
Every session is a finite state machine: BOOTING -> READY -> ... -> CLOSING.
Invalid transitions are rejected and logged. All state changes are auditable.

Design doc reference: v3.2_ARCHITECTURE_DESIGN.md §6
Spec reference: architecture.md — State Machine section

@rag-kernel-manifest
{
  "module": "rag_kernel.state_machine",
  "capability": "state_machine",
  "description": "Deterministic finite state machine with guarded transitions",
  "states": ["BOOTING", "READY", "INGESTING", "WORKING", "CHECKPOINTING", "CLOSING", "RECOVERY"],
  "exports": ["State", "Event", "StateMachine", "TransitionError"],
  "use_when": "Any session lifecycle operation — boot, state change, close, recovery",
  "never_bypass": true
}
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional


class State(Enum):
    """All legal session states.

    The state machine is total: every (current, target) pair has a defined
    outcome — either a successful transition or a logged rejection.
    """

    BOOTING = "BOOTING"
    READY = "READY"
    INGESTING = "INGESTING"
    WORKING = "WORKING"
    CHECKPOINTING = "CHECKPOINTING"
    CLOSING = "CLOSING"
    RECOVERY = "RECOVERY"


class EventType(Enum):
    """Event types recorded in the transition log.

    These map 1:1 to WAL event types when persistence.py integrates.
    """

    TRANSITION = "TRANSITION"
    INVALID_TRANSITION = "INVALID_TRANSITION"
    GUARD_FAILURE = "GUARD_FAILURE"
    STATE_MACHINE_CREATED = "STATE_MACHINE_CREATED"


# ---------------------------------------------------------------------------
# Transition table — the single source of truth for legal state moves.
#
# CS lens: This is a directed graph adjacency list. Every node (state) has
# an explicit set of reachable neighbors. CLOSING is a terminal (sink) node.
#
# ML lens: Compact representation — fits in a single LLM tool-call response.
# No ambiguity for the LLM to misinterpret.
# ---------------------------------------------------------------------------

TRANSITIONS: dict[State, frozenset[State]] = {
    State.BOOTING: frozenset({State.READY, State.RECOVERY}),
    State.READY: frozenset(
        {State.INGESTING, State.WORKING, State.CHECKPOINTING, State.CLOSING}
    ),
    State.INGESTING: frozenset(
        {State.READY, State.CHECKPOINTING, State.RECOVERY}
    ),
    State.WORKING: frozenset(
        {State.READY, State.CHECKPOINTING, State.RECOVERY}
    ),
    State.CHECKPOINTING: frozenset(
        {State.READY, State.CLOSING, State.RECOVERY}
    ),
    State.CLOSING: frozenset(),  # terminal — no exits
    State.RECOVERY: frozenset({State.READY, State.BOOTING}),
}


@dataclass(frozen=True)
class TransitionEvent:
    """Immutable record of a state transition attempt.

    Serializes cleanly to JSON for WAL integration. Timestamps are
    monotonic floats (time.monotonic) for ordering within a session,
    plus wall-clock ISO strings for cross-session correlation.
    """

    event_type: EventType
    from_state: State
    to_state: State
    timestamp_mono: float
    timestamp_wall: str
    success: bool
    reason: str = ""

    def to_dict(self) -> dict:
        """Serialize to a WAL-compatible dict."""
        return {
            "event": self.event_type.value,
            "from": self.from_state.value,
            "to": self.to_state.value,
            "ts_mono": self.timestamp_mono,
            "ts_wall": self.timestamp_wall,
            "ok": self.success,
            "reason": self.reason,
        }


# Type alias for guard functions.
# A guard receives (current_state, target_state, context_dict) and returns
# (allowed: bool, reason: str). Guards run BEFORE the transition.
Guard = Callable[[State, State, dict], tuple[bool, str]]


class StateMachine:
    """Deterministic session state machine with transition guards.

    Thread-safe: all state reads/writes are protected by a lock.
    This is necessary because api.py will serve concurrent HTTP requests
    that may attempt transitions simultaneously.

    Usage:
        sm = StateMachine()
        assert sm.current == State.BOOTING
        sm.transition(State.READY)
        sm.transition(State.WORKING)
        sm.transition(State.CHECKPOINTING)
        sm.transition(State.CLOSING)
    """

    def __init__(
        self,
        initial_state: State = State.BOOTING,
        guards: Optional[list[Guard]] = None,
    ) -> None:
        self._state = initial_state
        self._guards: list[Guard] = guards or []
        self._log: list[TransitionEvent] = []
        self._lock = threading.Lock()
        self._seq = 0  # monotonic event counter

        # Record creation event
        self._append_event(
            EventType.STATE_MACHINE_CREATED,
            initial_state,
            initial_state,
            success=True,
            reason=f"initialized in {initial_state.value}",
        )

    # -- Public interface ---------------------------------------------------

    @property
    def current(self) -> State:
        """Current state. Thread-safe read."""
        with self._lock:
            return self._state

    @property
    def is_terminal(self) -> bool:
        """True if in a terminal state (no outgoing transitions)."""
        with self._lock:
            return len(TRANSITIONS[self._state]) == 0

    @property
    def available_transitions(self) -> frozenset[State]:
        """States reachable from current state."""
        with self._lock:
            return TRANSITIONS[self._state]

    @property
    def event_log(self) -> list[TransitionEvent]:
        """Copy of the event log. Thread-safe."""
        with self._lock:
            return list(self._log)

    @property
    def seq(self) -> int:
        """Current sequence number (monotonically increasing)."""
        with self._lock:
            return self._seq

    def transition(self, target: State, context: Optional[dict] = None) -> bool:
        """Attempt a state transition.

        Args:
            target: The desired next state.
            context: Optional dict passed to guard functions for
                     context-dependent validation (e.g., "has unsaved changes").

        Returns:
            True if the transition succeeded, False otherwise.

        Thread-safe. On failure, the state is unchanged and an event
        is logged with the rejection reason.
        """
        ctx = context or {}

        with self._lock:
            current = self._state

            # 1. Check transition legality (graph edge exists?)
            if target not in TRANSITIONS[current]:
                self._append_event(
                    EventType.INVALID_TRANSITION,
                    current,
                    target,
                    success=False,
                    reason=f"{current.value} -> {target.value} not in transition table",
                )
                return False

            # 2. Run guards (all must pass)
            for guard in self._guards:
                allowed, reason = guard(current, target, ctx)
                if not allowed:
                    self._append_event(
                        EventType.GUARD_FAILURE,
                        current,
                        target,
                        success=False,
                        reason=reason,
                    )
                    return False

            # 3. Commit transition
            self._state = target
            self._append_event(
                EventType.TRANSITION,
                current,
                target,
                success=True,
            )
            return True

    def add_guard(self, guard: Guard) -> None:
        """Register a transition guard. Guards run in registration order."""
        with self._lock:
            self._guards.append(guard)

    def force_state(self, state: State, reason: str = "") -> None:
        """Force state without guard checks. For recovery scenarios ONLY.

        This bypasses the transition table — use only when the normal
        transition path is impossible (e.g., crash recovery where the
        previous state is unknown or corrupted).

        Logged as a TRANSITION event with the reason recorded.
        """
        with self._lock:
            old = self._state
            self._state = state
            self._append_event(
                EventType.TRANSITION,
                old,
                state,
                success=True,
                reason=f"FORCED: {reason}" if reason else "FORCED",
            )

    # -- Private helpers ----------------------------------------------------

    def _append_event(
        self,
        event_type: EventType,
        from_state: State,
        to_state: State,
        success: bool,
        reason: str = "",
    ) -> None:
        """Append an event to the internal log.

        Must be called with self._lock held.
        """
        self._seq += 1
        event = TransitionEvent(
            event_type=event_type,
            from_state=from_state,
            to_state=to_state,
            timestamp_mono=time.monotonic(),
            timestamp_wall=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            success=success,
            reason=reason,
        )
        self._log.append(event)

    # -- Introspection ------------------------------------------------------

    def __repr__(self) -> str:
        with self._lock:
            return (
                f"StateMachine(state={self._state.value}, "
                f"seq={self._seq}, "
                f"guards={len(self._guards)})"
            )


# ---------------------------------------------------------------------------
# Convenience: validate that the transition table is well-formed at import.
#
# CS lens: This is a static assertion — catches table typos at module load,
# not at runtime. Every state must appear as a key. Every target must be
# a valid State.
# ---------------------------------------------------------------------------

def _validate_transition_table() -> None:
    """Assert transition table covers all states and references only valid states."""
    all_states = set(State)

    # Every state must have an entry (even if empty for terminals)
    missing = all_states - set(TRANSITIONS.keys())
    if missing:
        raise RuntimeError(
            f"Transition table missing states: {[s.value for s in missing]}"
        )

    # Every target must be a valid state
    for source, targets in TRANSITIONS.items():
        invalid = targets - all_states
        if invalid:
            raise RuntimeError(
                f"State {source.value} references invalid targets: "
                f"{[s.value for s in invalid]}"
            )

    # Terminal states must have empty target sets
    # (Currently only CLOSING, but check generically)
    for state, targets in TRANSITIONS.items():
        if len(targets) == 0 and state != State.CLOSING:
            raise RuntimeError(
                f"State {state.value} has no transitions but is not CLOSING — "
                f"is this intentional?"
            )


_validate_transition_table()
