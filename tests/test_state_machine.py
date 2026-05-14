"""Tests for the RAG Runtime Kernel state machine engine.

Coverage targets:
- All valid transitions (positive paths)
- All invalid transitions (negative paths — rejection + logging)
- Guard enforcement (pass and fail)
- Terminal state behavior (CLOSING has no exits)
- Recovery paths (RECOVERY -> READY, RECOVERY -> BOOTING)
- force_state bypass
- Event log integrity (ordering, content)
- Thread safety under concurrent transitions
- Import-time transition table validation
"""

import threading
import time

import pytest

from rag_kernel.state_machine import (
    TRANSITIONS,
    EventType,
    Guard,
    State,
    StateMachine,
    TransitionEvent,
)


# ===== Fixtures =====

@pytest.fixture
def sm() -> StateMachine:
    """Fresh state machine in BOOTING."""
    return StateMachine()


@pytest.fixture
def sm_ready() -> StateMachine:
    """State machine already transitioned to READY."""
    sm = StateMachine()
    sm.transition(State.READY)
    return sm


# ===== State enum =====

class TestStateEnum:
    def test_all_states_defined(self):
        expected = {"BOOTING", "READY", "INGESTING", "WORKING",
                    "CHECKPOINTING", "CLOSING", "RECOVERY"}
        actual = {s.value for s in State}
        assert actual == expected

    def test_state_count(self):
        assert len(State) == 7


# ===== Transition table =====

class TestTransitionTable:
    def test_all_states_have_entries(self):
        for state in State:
            assert state in TRANSITIONS

    def test_closing_is_terminal(self):
        assert TRANSITIONS[State.CLOSING] == frozenset()

    def test_booting_can_reach_ready(self):
        assert State.READY in TRANSITIONS[State.BOOTING]

    def test_booting_can_reach_recovery(self):
        assert State.RECOVERY in TRANSITIONS[State.BOOTING]

    def test_ready_has_four_exits(self):
        assert len(TRANSITIONS[State.READY]) == 4
        assert TRANSITIONS[State.READY] == frozenset({
            State.INGESTING, State.WORKING,
            State.CHECKPOINTING, State.CLOSING,
        })

    def test_recovery_can_reach_ready_or_booting(self):
        assert TRANSITIONS[State.RECOVERY] == frozenset({
            State.READY, State.BOOTING,
        })

    def test_no_self_transitions(self):
        """No state can transition to itself (spec requires explicit moves)."""
        for state, targets in TRANSITIONS.items():
            assert state not in targets, f"{state.value} has self-transition"

    def test_all_targets_are_valid_states(self):
        all_states = set(State)
        for state, targets in TRANSITIONS.items():
            assert targets.issubset(all_states), (
                f"{state.value} has invalid targets"
            )

    def test_tables_are_frozensets(self):
        """Transition sets must be immutable (no accidental mutation)."""
        for state, targets in TRANSITIONS.items():
            assert isinstance(targets, frozenset), (
                f"{state.value} targets are {type(targets)}, not frozenset"
            )


# ===== Valid transitions =====

class TestValidTransitions:
    def test_boot_to_ready(self, sm):
        assert sm.transition(State.READY)
        assert sm.current == State.READY

    def test_boot_to_recovery(self, sm):
        assert sm.transition(State.RECOVERY)
        assert sm.current == State.RECOVERY

    def test_ready_to_ingesting(self, sm_ready):
        assert sm_ready.transition(State.INGESTING)
        assert sm_ready.current == State.INGESTING

    def test_ready_to_working(self, sm_ready):
        assert sm_ready.transition(State.WORKING)
        assert sm_ready.current == State.WORKING

    def test_ready_to_checkpointing(self, sm_ready):
        assert sm_ready.transition(State.CHECKPOINTING)
        assert sm_ready.current == State.CHECKPOINTING

    def test_ready_to_closing(self, sm_ready):
        assert sm_ready.transition(State.CLOSING)
        assert sm_ready.current == State.CLOSING

    def test_ingesting_to_ready(self, sm_ready):
        sm_ready.transition(State.INGESTING)
        assert sm_ready.transition(State.READY)
        assert sm_ready.current == State.READY

    def test_ingesting_to_checkpointing(self, sm_ready):
        sm_ready.transition(State.INGESTING)
        assert sm_ready.transition(State.CHECKPOINTING)

    def test_ingesting_to_recovery(self, sm_ready):
        sm_ready.transition(State.INGESTING)
        assert sm_ready.transition(State.RECOVERY)

    def test_working_to_ready(self, sm_ready):
        sm_ready.transition(State.WORKING)
        assert sm_ready.transition(State.READY)

    def test_working_to_checkpointing(self, sm_ready):
        sm_ready.transition(State.WORKING)
        assert sm_ready.transition(State.CHECKPOINTING)

    def test_working_to_recovery(self, sm_ready):
        sm_ready.transition(State.WORKING)
        assert sm_ready.transition(State.RECOVERY)

    def test_checkpointing_to_ready(self, sm_ready):
        sm_ready.transition(State.CHECKPOINTING)
        assert sm_ready.transition(State.READY)

    def test_checkpointing_to_closing(self, sm_ready):
        sm_ready.transition(State.CHECKPOINTING)
        assert sm_ready.transition(State.CLOSING)

    def test_checkpointing_to_recovery(self, sm_ready):
        sm_ready.transition(State.CHECKPOINTING)
        assert sm_ready.transition(State.RECOVERY)

    def test_recovery_to_ready(self, sm):
        sm.transition(State.RECOVERY)
        assert sm.transition(State.READY)

    def test_recovery_to_booting(self, sm):
        sm.transition(State.RECOVERY)
        assert sm.transition(State.BOOTING)

    def test_full_happy_path(self, sm):
        """BOOTING -> READY -> WORKING -> CHECKPOINTING -> CLOSING."""
        assert sm.transition(State.READY)
        assert sm.transition(State.WORKING)
        assert sm.transition(State.CHECKPOINTING)
        assert sm.transition(State.CLOSING)
        assert sm.current == State.CLOSING
        assert sm.is_terminal

    def test_ingest_cycle(self, sm):
        """BOOTING -> READY -> INGESTING -> READY -> WORKING -> ..."""
        sm.transition(State.READY)
        sm.transition(State.INGESTING)
        assert sm.transition(State.READY)
        assert sm.transition(State.WORKING)


# ===== Invalid transitions =====

class TestInvalidTransitions:
    def test_boot_to_working(self, sm):
        assert not sm.transition(State.WORKING)
        assert sm.current == State.BOOTING  # unchanged

    def test_boot_to_closing(self, sm):
        assert not sm.transition(State.CLOSING)

    def test_boot_to_ingesting(self, sm):
        assert not sm.transition(State.INGESTING)

    def test_boot_to_checkpointing(self, sm):
        assert not sm.transition(State.CHECKPOINTING)

    def test_ready_to_booting(self, sm_ready):
        assert not sm_ready.transition(State.BOOTING)

    def test_ready_to_recovery(self, sm_ready):
        assert not sm_ready.transition(State.RECOVERY)

    def test_closing_to_anything(self, sm_ready):
        sm_ready.transition(State.CLOSING)
        for state in State:
            assert not sm_ready.transition(state)
        assert sm_ready.current == State.CLOSING

    def test_self_transition_rejected(self, sm):
        """BOOTING -> BOOTING should fail."""
        assert not sm.transition(State.BOOTING)

    def test_invalid_transition_logs_event(self, sm):
        sm.transition(State.WORKING)  # invalid
        log = sm.event_log
        # Last event (after creation event) should be INVALID_TRANSITION
        invalid_events = [e for e in log if e.event_type == EventType.INVALID_TRANSITION]
        assert len(invalid_events) == 1
        assert invalid_events[0].from_state == State.BOOTING
        assert invalid_events[0].to_state == State.WORKING
        assert not invalid_events[0].success


# ===== Guards =====

class TestGuards:
    def test_guard_allows_transition(self, sm):
        def allow_all(current, target, ctx):
            return True, ""
        sm.add_guard(allow_all)
        assert sm.transition(State.READY)

    def test_guard_blocks_transition(self, sm):
        def block_all(current, target, ctx):
            return False, "blocked by test guard"
        sm.add_guard(block_all)
        assert not sm.transition(State.READY)
        assert sm.current == State.BOOTING

    def test_guard_failure_logged(self, sm):
        def block_all(current, target, ctx):
            return False, "test guard says no"
        sm.add_guard(block_all)
        sm.transition(State.READY)
        guard_failures = [
            e for e in sm.event_log
            if e.event_type == EventType.GUARD_FAILURE
        ]
        assert len(guard_failures) == 1
        assert "test guard says no" in guard_failures[0].reason

    def test_multiple_guards_all_must_pass(self, sm):
        calls = []

        def guard_a(current, target, ctx):
            calls.append("a")
            return True, ""

        def guard_b(current, target, ctx):
            calls.append("b")
            return False, "b blocks"

        sm.add_guard(guard_a)
        sm.add_guard(guard_b)
        assert not sm.transition(State.READY)
        assert calls == ["a", "b"]

    def test_guard_short_circuits_on_first_failure(self, sm):
        calls = []

        def guard_fail(current, target, ctx):
            calls.append("fail")
            return False, "first fails"

        def guard_never_called(current, target, ctx):
            calls.append("never")
            return True, ""

        sm.add_guard(guard_fail)
        sm.add_guard(guard_never_called)
        sm.transition(State.READY)
        assert calls == ["fail"]  # second guard never ran

    def test_guard_receives_context(self, sm):
        received = {}

        def capture_guard(current, target, ctx):
            received.update(ctx)
            return True, ""

        sm.add_guard(capture_guard)
        sm.transition(State.READY, context={"session_id": "S9", "dirty": True})
        assert received == {"session_id": "S9", "dirty": True}

    def test_guard_only_runs_for_valid_transitions(self, sm):
        calls = []

        def tracking_guard(current, target, ctx):
            calls.append((current.value, target.value))
            return True, ""

        sm.add_guard(tracking_guard)
        sm.transition(State.WORKING)  # invalid — guard should NOT run
        assert calls == []


# ===== Event log =====

class TestEventLog:
    def test_creation_event_logged(self, sm):
        log = sm.event_log
        assert len(log) >= 1
        assert log[0].event_type == EventType.STATE_MACHINE_CREATED

    def test_successful_transition_logged(self, sm):
        sm.transition(State.READY)
        transitions = [e for e in sm.event_log if e.event_type == EventType.TRANSITION]
        # Creation event is STATE_MACHINE_CREATED, not TRANSITION.
        # So only 1 TRANSITION event after one transition call.
        assert len(transitions) == 1
        last = transitions[-1]
        assert last.from_state == State.BOOTING
        assert last.to_state == State.READY
        assert last.success

    def test_event_log_is_a_copy(self, sm):
        log1 = sm.event_log
        sm.transition(State.READY)
        log2 = sm.event_log
        assert len(log2) > len(log1)  # log2 has the new event
        assert len(log1) == 1  # original copy unchanged

    def test_event_sequence_monotonic(self, sm):
        sm.transition(State.READY)
        sm.transition(State.WORKING)
        sm.transition(State.CHECKPOINTING)
        log = sm.event_log
        monos = [e.timestamp_mono for e in log]
        assert monos == sorted(monos)

    def test_event_to_dict(self, sm):
        sm.transition(State.READY)
        event = sm.event_log[-1]
        d = event.to_dict()
        assert d["event"] == "TRANSITION"
        assert d["from"] == "BOOTING"
        assert d["to"] == "READY"
        assert d["ok"] is True
        assert "ts_wall" in d
        assert "ts_mono" in d

    def test_seq_increments(self, sm):
        initial_seq = sm.seq  # after creation event = 1
        sm.transition(State.READY)
        assert sm.seq == initial_seq + 1
        sm.transition(State.WORKING)
        assert sm.seq == initial_seq + 2


# ===== Terminal state =====

class TestTerminalState:
    def test_closing_is_terminal(self, sm_ready):
        sm_ready.transition(State.CLOSING)
        assert sm_ready.is_terminal

    def test_non_terminal_states(self, sm):
        assert not sm.is_terminal  # BOOTING has exits

    def test_available_transitions_empty_for_terminal(self, sm_ready):
        sm_ready.transition(State.CLOSING)
        assert sm_ready.available_transitions == frozenset()


# ===== force_state =====

class TestForceState:
    def test_force_bypasses_transition_table(self, sm):
        # BOOTING -> CLOSING is not in transition table
        sm.force_state(State.CLOSING, reason="crash recovery test")
        assert sm.current == State.CLOSING

    def test_force_logs_event(self, sm):
        sm.force_state(State.READY, reason="test")
        events = [e for e in sm.event_log if "FORCED" in e.reason]
        assert len(events) == 1

    def test_force_bypasses_guards(self, sm):
        def block_all(current, target, ctx):
            return False, "blocked"
        sm.add_guard(block_all)
        sm.force_state(State.READY, reason="bypass guards")
        assert sm.current == State.READY


# ===== Thread safety =====

class TestThreadSafety:
    def test_concurrent_transitions(self, sm):
        """Multiple threads racing to transition. No crashes, state consistent."""
        sm.transition(State.READY)
        results = []
        barrier = threading.Barrier(10)

        def race(target):
            barrier.wait()
            ok = sm.transition(target)
            results.append((target, ok))

        threads = []
        targets = [State.WORKING, State.INGESTING] * 5
        for t in targets:
            th = threading.Thread(target=race, args=(t,))
            threads.append(th)
            th.start()
        for th in threads:
            th.join()

        # Exactly one transition should succeed from READY
        successes = [r for r in results if r[1]]
        assert len(successes) == 1
        assert sm.current in (State.WORKING, State.INGESTING)

    def test_concurrent_reads_safe(self, sm_ready):
        """Reading state from multiple threads doesn't crash."""
        results = []

        def read_state():
            for _ in range(100):
                results.append(sm_ready.current)

        threads = [threading.Thread(target=read_state) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(results) == 500
        assert all(s == State.READY for s in results)


# ===== Repr =====

class TestRepr:
    def test_repr_format(self, sm):
        r = repr(sm)
        assert "BOOTING" in r
        assert "StateMachine" in r

    def test_repr_after_transition(self, sm_ready):
        r = repr(sm_ready)
        assert "READY" in r


# ===== Recovery paths =====

class TestRecoveryPaths:
    def test_recovery_loop(self, sm):
        """BOOTING -> RECOVERY -> BOOTING -> READY (restart cycle)."""
        assert sm.transition(State.RECOVERY)
        assert sm.transition(State.BOOTING)
        assert sm.transition(State.READY)

    def test_recovery_from_working(self, sm_ready):
        """READY -> WORKING -> RECOVERY -> READY."""
        sm_ready.transition(State.WORKING)
        assert sm_ready.transition(State.RECOVERY)
        assert sm_ready.transition(State.READY)

    def test_recovery_from_ingesting(self, sm_ready):
        sm_ready.transition(State.INGESTING)
        assert sm_ready.transition(State.RECOVERY)
        assert sm_ready.current == State.RECOVERY

    def test_recovery_from_checkpointing(self, sm_ready):
        sm_ready.transition(State.CHECKPOINTING)
        assert sm_ready.transition(State.RECOVERY)
        assert sm_ready.current == State.RECOVERY


# ===== Edge cases =====

class TestEdgeCases:
    def test_initial_state_custom(self):
        """Can start in a non-BOOTING state (for testing/recovery)."""
        sm = StateMachine(initial_state=State.RECOVERY)
        assert sm.current == State.RECOVERY
        assert sm.transition(State.READY)

    def test_transition_with_empty_context(self, sm):
        assert sm.transition(State.READY, context={})

    def test_transition_with_none_context(self, sm):
        assert sm.transition(State.READY, context=None)

    def test_many_transitions(self, sm):
        """Cycle through multiple valid transitions without error."""
        sm.transition(State.READY)
        for _ in range(50):
            sm.transition(State.WORKING)
            sm.transition(State.CHECKPOINTING)
            sm.transition(State.READY)
        assert sm.current == State.READY
        assert sm.seq > 100
