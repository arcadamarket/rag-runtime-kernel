"""FV-PHASE4 — runtime enforcement of the TLA+-generated guards.

FV-PHASE3 generated rag_kernel/generated_guards.py from the formally-verified
model but deliberately left the runtime untouched. FV-PHASE4 wires the runtime
to *consume* that artifact:

  * state_machine.TRANSITIONS is DERIVED from generated_guards.GENERATED_TRANSITIONS
    (one source of truth -> no silent drift).
  * StateMachine.transition() enforces legality through the generated
    legal_transition() predicate (non-bypassable structural guard).
  * generated_guards + guardgen are registered in the module system
    (_KERNEL_MODULES, discover(), cmd_health) — INS-019.

These tests pin those guarantees so a future refactor can't quietly unwire them.
"""

from __future__ import annotations

import rag_kernel
import rag_kernel.generated_guards as gg
from rag_kernel.state_machine import (
    TRANSITIONS,
    GUARDS_SOURCE_SHA256,
    State,
    StateMachine,
)


class TestRuntimeDerivesFromModel:
    def test_transitions_is_projection_of_generated(self):
        """Runtime TRANSITIONS must equal the generated table, string-mapped."""
        runtime_as_strings = {
            state.value: frozenset(t.value for t in targets)
            for state, targets in TRANSITIONS.items()
        }
        assert runtime_as_strings == gg.GENERATED_TRANSITIONS

    def test_state_space_matches_generated(self):
        """The State enum and the generated table describe the same states."""
        assert {s.value for s in State} == set(gg.GENERATED_TRANSITIONS.keys())

    def test_source_sha_reexported_and_consistent(self):
        """state_machine re-exports the model SHA and it matches the artifact."""
        assert GUARDS_SOURCE_SHA256 == gg.SOURCE_SHA256
        assert len(GUARDS_SOURCE_SHA256) == 64  # sha256 hex digest


class TestNonBypassableEnforcement:
    def test_legal_transition_is_the_gate(self):
        """Every accepted/rejected transition agrees with legal_transition()."""
        for src in State:
            sm = StateMachine(initial_state=src)
            for tgt in State:
                expected = gg.legal_transition(src.value, tgt.value)
                # fresh machine per attempt so prior success can't shift state
                sm2 = StateMachine(initial_state=src)
                assert sm2.transition(tgt) is expected

    def test_illegal_transition_logged_and_state_unchanged(self):
        sm = StateMachine(initial_state=State.READY)
        assert sm.transition(State.BOOTING) is False  # READY -> BOOTING illegal
        assert sm.current == State.READY
        assert any(
            e.event_type.name == "INVALID_TRANSITION" for e in sm.event_log
        )

    def test_force_state_remains_the_only_bypass(self):
        """force_state still bypasses the structural gate (recovery path)."""
        sm = StateMachine(initial_state=State.READY)
        sm.force_state(State.BOOTING, reason="test recovery")
        assert sm.current == State.BOOTING


class TestModuleRegistration:
    def test_new_modules_in_kernel_modules(self):
        assert "rag_kernel.generated_guards" in rag_kernel._KERNEL_MODULES
        assert "rag_kernel.guardgen" in rag_kernel._KERNEL_MODULES

    def test_discover_surfaces_new_modules(self):
        registry = rag_kernel.discover()
        assert "generated_guards" in registry["modules"]
        assert "guardgen" in registry["modules"]

    def test_generated_guards_is_critical(self):
        """generated_guards declares never_bypass -> appears in critical list."""
        registry = rag_kernel.discover()
        assert "generated_guards" in registry["critical_modules"]

    def test_manifest_module_count_is_twenty(self):
        """The functional-capability count (manifest dict) is 20.

        FV-PHASE4 reconciled the count to 12; M-009 added context_policy as
        the 13th functional module; GRAPH-ORCH increment 5 (INS-025)
        registered graph_orchestrator as the 14th; GRAPH-ORCH increment 7
        (INS-030) registered agent_supervisor as the 15th; DRIFT-ELIM
        increment 3 registered drift_control as the 16th and drift_store as
        the 17th; DRIFT-ELIM increment 4 registered drift_render as the 18th;
        DRIFT-ELIM increment 5 registered drift_audit as the 19th;
        KA-SCHEMA-MIGRATE registered schema_migrate as the 20th.
        """
        registry = rag_kernel.discover()
        manifest_modules = registry["package"]["modules"]
        assert len(manifest_modules) == 20
        assert "schema_migrate" in manifest_modules
        assert "generated_guards" in manifest_modules
        assert "guardgen" in manifest_modules
        assert "context_policy" in manifest_modules
        assert "graph_orchestrator" in manifest_modules
        assert "drift_control" in manifest_modules
        assert "drift_store" in manifest_modules
        assert "drift_render" in manifest_modules
        assert "drift_audit" in manifest_modules

    def test_graph_orchestrator_registered(self):
        """GRAPH-ORCH increment 5 (INS-025): graph_orchestrator is wired into
        _KERNEL_MODULES and surfaced by discover() as a capability module."""
        assert "rag_kernel.graph_orchestrator" in rag_kernel._KERNEL_MODULES
        registry = rag_kernel.discover()
        assert "graph_orchestrator" in registry["modules"]
        assert "graph_orchestration" in registry["capabilities"]

    def test_agent_supervisor_registered(self):
        """GRAPH-ORCH increment 7 (INS-030): agent_supervisor is wired into
        _KERNEL_MODULES and surfaced by discover() as a capability module."""
        assert "rag_kernel.agent_supervisor" in rag_kernel._KERNEL_MODULES
        registry = rag_kernel.discover()
        assert "agent_supervisor" in registry["modules"]
        assert "agent_supervision" in registry["capabilities"]

    def test_drift_control_registered(self):
        """DRIFT-ELIM increment 3: drift_control is wired into _KERNEL_MODULES
        and surfaced by discover() as a capability module, and is critical
        (never_bypass)."""
        assert "rag_kernel.drift_control" in rag_kernel._KERNEL_MODULES
        registry = rag_kernel.discover()
        assert "drift_control" in registry["modules"]
        assert "item_lifecycle" in registry["capabilities"]
        assert "drift_control" in registry["critical_modules"]

    def test_drift_store_registered(self):
        """DRIFT-ELIM increment 3: drift_store is wired into _KERNEL_MODULES
        and surfaced by discover() as a capability module, and is critical
        (never_bypass)."""
        assert "rag_kernel.drift_store" in rag_kernel._KERNEL_MODULES
        registry = rag_kernel.discover()
        assert "drift_store" in registry["modules"]
        assert "item_store" in registry["capabilities"]
        assert "drift_store" in registry["critical_modules"]

    def test_drift_render_registered(self):
        """DRIFT-ELIM increment 4: drift_render is wired into _KERNEL_MODULES
        and surfaced by discover() as a capability module, and is critical
        (never_bypass — renders must not be hand-authored)."""
        assert "rag_kernel.drift_render" in rag_kernel._KERNEL_MODULES
        registry = rag_kernel.discover()
        assert "drift_render" in registry["modules"]
        assert "state_render" in registry["capabilities"]
        assert "drift_render" in registry["critical_modules"]

    def test_drift_audit_registered(self):
        """DRIFT-ELIM increment 5: drift_audit is wired into _KERNEL_MODULES
        and surfaced by discover() as a capability module, and is critical
        (never_bypass — the session-boundary fail-loud auditor)."""
        assert "rag_kernel.drift_audit" in rag_kernel._KERNEL_MODULES
        registry = rag_kernel.discover()
        assert "drift_audit" in registry["modules"]
        assert "state_audit" in registry["capabilities"]
        assert "drift_audit" in registry["critical_modules"]
